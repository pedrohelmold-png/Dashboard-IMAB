from __future__ import annotations

from datetime import date, datetime
import io
import math
import re
from typing import Optional
from uuid import uuid4

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from src.collector import fetch_fiinfra_fundos_result, fetch_fiinfra_macro
from src.db import (
    get_ultimo_carrego,
    get_fiinfra_snapshot,
    get_ultimo_fiinfra_snapshot,
    insert_fiinfra_tranche,
    load_fiinfra_fundos,
    load_fiinfra_revisions,
    load_fiinfra_snapshots,
    load_fiinfra_thresholds,
    load_fiinfra_tranches,
    restore_fiinfra_revision,
    save_fiinfra_thresholds,
    upsert_fiinfra_snapshot,
)
from src.regua_fiinfra import (
    ESTADO_BARATO,
    ESTADO_CARO,
    ESTADO_NEUTRO,
    FUNDOS_PADRAO,
    METODOLOGIA_VERSION,
    MANDATO_CAIXA,
    MANDATO_JURO_REAL,
    MANDATO_RENDA,
    MANDATOS,
    ZONA_CARREGAR,
    ZONA_COMPRAR,
    ZONA_REDUZIR,
    avaliar_sinais,
    calcular_cdi_liquido_real,
    calcular_retorno_real_liquido_de_taxa_real,
    calcular_yield_fundo_real,
    preparar_fundos,
    recomendar_execucao,
    validar_thresholds,
)


_ZONA_STYLE = {
    ZONA_COMPRAR: ("#14532d", "#dcfce7", "#86efac"),
    ZONA_CARREGAR: ("#1e3a8a", "#dbeafe", "#93c5fd"),
    ZONA_REDUZIR: ("#7f1d1d", "#fee2e2", "#fca5a5"),
}

_ESTADO_COLOR = {
    ESTADO_BARATO: "#16a34a",
    ESTADO_NEUTRO: "#64748b",
    ESTADO_CARO: "#dc2626",
}


def render_regua_fiinfra() -> None:
    thresholds = load_fiinfra_thresholds()
    ultimo_snapshot = get_ultimo_fiinfra_snapshot()
    ultimo_imab = get_ultimo_carrego(table="carrego_historico_imab")
    if ultimo_imab is None:
        ultimo_imab = get_ultimo_carrego(table="carrego_historico")

    st.title("Regua de Ciclo FI-Infra")
    st.caption("FI-Infra listados: juro real, spread de credito e excesso de desconto.")

    thresholds = _render_threshold_editor(thresholds)

    st.subheader("Snapshot semanal")
    update_cols = st.columns([1, 1, 3])
    with update_cols[0]:
        ref_date = st.date_input("Data", value=date.today(), max_value=date.today())
    with update_cols[1]:
        st.write("")
        st.write("")
        atualizar = st.button("Atualizar dados oficiais", type="primary", width="stretch")

    collection = st.session_state.get("fiinfra_collection")
    if collection and collection.get("data_solicitada") != ref_date:
        st.session_state.pop("fiinfra_collection", None)
        collection = None
        st.info("A data mudou. Atualize os dados oficiais para esta nova referencia.")

    if atualizar:
        with st.spinner("Baixando dados ANBIMA, BCB, B3 e CVM..."):
            batch = {
                "collection_id": uuid4().hex,
                "data_solicitada": ref_date,
                "coletado_em": datetime.now().isoformat(timespec="seconds"),
                "macro": {},
                "fundos": [],
                "erros": [],
            }
            target_duration = _duration_alvo()
            try:
                batch["macro"] = fetch_fiinfra_macro(
                    ref_date, target_duration=target_duration, force_refresh=True
                )
            except Exception as exc:
                batch["erros"].append(f"macro: {exc}")
            fundos_result = fetch_fiinfra_fundos_result(ref_date, force_refresh=True)
            batch["fundos"] = fundos_result["fundos"]
            batch["erros"].extend(
                f"{fonte}: {erro}" for fonte, erro in fundos_result["erros"].items()
            )
            st.session_state["fiinfra_collection"] = batch
            collection = batch

    collection = collection or {}
    auto_macro = collection.get("macro", {})
    auto_fundos = collection.get("fundos", [])
    collection_id = collection.get("collection_id", f"manual_{ref_date.isoformat()}")
    if collection:
        _render_update_result(auto_macro, auto_fundos, collection.get("erros", []))
    input_cols = st.columns([1, 1, 1, 1])
    with input_cols[0]:
        ntnb = st.number_input(
            "NTN-B longa (% a.a.)",
            value=_auto_or_fallback(auto_macro, "ntnb", ultimo_snapshot, "ntnb", 6.5),
            step=0.05,
            format="%.2f",
            key=f"fiinfra_ntnb_{collection_id}",
        )
    with input_cols[1]:
        spread = st.number_input(
            "Spread IDA-Infra (bps)",
            value=_fallback_float(ultimo_snapshot, "spread", 100.0),
            step=5.0,
            format="%.0f",
            help="Mantido manual ate haver serie ANBIMA estruturada e estavel.",
            key=f"fiinfra_spread_{collection_id}",
        )
    with input_cols[2]:
        mandato = st.selectbox(
            "Mandato",
            options=list(MANDATOS),
            index=_mandato_index(ultimo_snapshot),
            key=f"fiinfra_mandato_{collection_id}",
        )
    with input_cols[3]:
        st.metric("Fonte macro", auto_macro.get("fonte", "Ultimo snapshot/manual"))

    if auto_macro:
        vencimento = auto_macro.get("ntnb_vencimento")
        st.caption(
            f"NTN-B de referencia: {vencimento or 'indisponivel'} | "
            f"duration { _fmt(auto_macro.get('ntnb_duration'), ' anos') } | "
            f"data-base {auto_macro.get('ntnb_data') or 'N/D'} | "
            f"status {auto_macro.get('ntnb_status', 'FALLBACK')}. "
            f"CDI: {auto_macro.get('cdi_data') or 'N/D'} "
            f"({auto_macro.get('cdi_status', 'FALLBACK')})."
        )

    macro_cols = st.columns([1, 1, 1, 1])
    with macro_cols[0]:
        cdi = st.number_input(
            "CDI (% a.a.)",
            value=_auto_or_fallback(
                auto_macro, "cdi", ultimo_snapshot, "cdi",
                _ultimo_imab_value(ultimo_imab, "cdi_anual", 11.0),
            ),
            step=0.05,
            format="%.2f",
            key=f"fiinfra_cdi_{collection_id}",
        )
    with macro_cols[1]:
        ipca_focus = st.number_input(
            "IPCA Focus 12m (% a.a.)",
            value=_auto_or_fallback(
                auto_macro, "ipca_focus", ultimo_snapshot, "ipca_focus",
                _ultimo_imab_value(ultimo_imab, "ipca_focus", 4.5),
            ),
            step=0.05,
            format="%.2f",
            help="Mediana suavizada oficial do BCB; usada para deflacionar o CDI.",
            key=f"fiinfra_focus_{collection_id}",
        )
    with macro_cols[2]:
        inflacao_implicita = st.number_input(
            "Inflacao implicita (% a.a.)",
            value=_fallback_float(
                auto_macro or ultimo_snapshot,
                "inflacao_implicita",
                _fallback_float(ultimo_snapshot, "inflacao_implicita",
                                _ultimo_imab_value(ultimo_imab, "ipca_proj", 4.5)),
            ),
            step=0.05,
            format="%.2f",
            key=f"fiinfra_implicita_{collection_id}",
        )
    with macro_cols[3]:
        aliquota_pct = st.number_input(
            "Aliquota alternativa (%)",
            value=_fallback_float(ultimo_snapshot, "aliquota", 0.15) * 100,
            min_value=0.0,
            max_value=35.0,
            step=1.0,
            format="%.1f",
            key=f"fiinfra_aliquota_{collection_id}",
        )
    aliquota = aliquota_pct / 100
    focus_disponivel = auto_macro.get("ipca_focus") is not None or (
        ultimo_snapshot is not None and ultimo_snapshot.get("ipca_focus") is not None
    ) or (ultimo_imab is not None and ultimo_imab.get("ipca_focus") is not None)
    inflacao_usada = ipca_focus if focus_disponivel else inflacao_implicita
    inflacao_usada_fonte = "focus" if focus_disponivel else "implicita_fallback"
    st.caption(
        f"Inflação usada na alternativa real: {inflacao_usada:.2f}% "
        f"({inflacao_usada_fonte}). Focus data-base: "
        f"{auto_macro.get('ipca_focus_data') or 'último valor salvo/manual'} "
        f"({auto_macro.get('ipca_focus_status', 'FALLBACK')})."
    )

    if mandato == MANDATO_JURO_REAL:
        imab_real = st.number_input(
            "IMA-B real bruto para rotacao (% a.a.)",
            value=_ultimo_imab_value(ultimo_imab, "ytm_real", ntnb),
            step=0.05,
            format="%.2f",
            key=f"fiinfra_imab_{collection_id}",
        )
        alternativa_liquida_real = calcular_retorno_real_liquido_de_taxa_real(
            imab_real, aliquota, inflacao_usada
        )
        st.caption(
            "Alternativa IMA-B: taxa real bruta convertida para retorno nominal "
            "pela inflacao usada, tributada e deflacionada de volta."
        )
    else:
        alternativa_liquida_real = calcular_cdi_liquido_real(cdi, aliquota, inflacao_usada)

    st.subheader("Fundos monitorados")
    fundos_base = _fundos_base(auto_fundos)
    fundos_base = _render_premissas_lote(fundos_base, collection_id)
    fundos_editados = st.data_editor(
        fundos_base,
        hide_index=True,
        num_rows="fixed",
        width="stretch",
        column_config={
            "ticker": st.column_config.TextColumn("Ticker", disabled=True),
            "cota_mercado": st.column_config.NumberColumn("Cota mercado", format="%.2f"),
            "cota_patrimonial": st.column_config.NumberColumn("Cota patrimonial", format="%.2f"),
            "taxa_total_aa": st.column_config.NumberColumn("Taxa total (% a.a.)", format="%.2f"),
            "duration": st.column_config.NumberColumn("Duration", format="%.2f"),
            "mercado_data": st.column_config.DateColumn("Data mercado", disabled=True),
            "patrimonial_data": st.column_config.DateColumn("Data patrimonial", disabled=True),
            "mercado_status": st.column_config.TextColumn("Status mercado", disabled=True),
            "patrimonial_status": st.column_config.TextColumn("Status patrimonial", disabled=True),
            "cnpj": st.column_config.TextColumn("CNPJ", disabled=True),
            "cota_mercado_fonte": st.column_config.TextColumn("Fonte mercado", disabled=True),
            "cota_patrimonial_fonte": st.column_config.TextColumn("Fonte patrimonial", disabled=True),
            "taxa_total_status": st.column_config.TextColumn("Status taxa", disabled=True),
            "duration_status": st.column_config.TextColumn("Status duration", disabled=True),
            "cota_mercado_original": None,
            "cota_patrimonial_original": None,
        },
        key=f"fiinfra_fundos_editor_{collection_id}",
    )

    fundos_calc, excesso_mediano, duration_mediana = preparar_fundos(
        fundos_editados.to_dict("records")
    )
    fundos_df = pd.DataFrame(fundos_calc)
    cobertura = sum(bool(row.get("elegivel")) for row in fundos_calc)
    macro_values = {
        "ntnb": ntnb,
        "cdi": cdi,
        "inflacao_implicita": inflacao_implicita,
        "ipca_focus": ipca_focus,
    }
    existente = get_fiinfra_snapshot(ref_date)
    revisions = load_fiinfra_revisions(ref_date)
    qualidade = _quality_summary(
        auto_macro=auto_macro,
        fundos_calc=fundos_calc,
        collection=collection,
        macro_values=macro_values,
        existing_snapshot=existente,
        next_revision=len(revisions) + 1,
    )
    st.caption(f"Cobertura do sinal de desconto: {cobertura}/{len(fundos_calc)} fundos válidos.")

    if cobertura < 3:
        _render_quality_panel(auto_macro, macro_values, fundos_calc, collection, cobertura, qualidade)
        st.warning(
            "Cobertura insuficiente para uma recomendacao operacional. "
            "Sao necessarios pelo menos 3 fundos com dados completos."
        )
        _render_history(thresholds)
        _render_revisions(ref_date)
        _render_tranches()
        return

    avaliacao = avaliar_sinais(ntnb, spread, excesso_mediano, thresholds)
    yield_fundo_real = calcular_yield_fundo_real(ntnb, spread, excesso_mediano, duration_mediana)
    execucao = recomendar_execucao(
        avaliacao["zona"],
        mandato,
        yield_fundo_real,
        alternativa_liquida_real,
    )

    _render_zona(avaliacao, execucao)
    _render_signal_grid(
        ntnb,
        spread,
        excesso_mediano,
        duration_mediana,
        yield_fundo_real,
        alternativa_liquida_real,
        avaliacao,
    )
    _render_fundos_calculados(fundos_df)
    _render_quality_panel(auto_macro, macro_values, fundos_calc, collection, cobertura, qualidade)

    observacao = st.text_area("Observacao do snapshot", height=80)
    quality_issues = qualidade["issues"]

    confirmar = True
    if quality_issues:
        st.warning("Revisao necessaria antes de salvar: " + " | ".join(quality_issues))
        confirmar = st.checkbox(
            "Confirmo os fallbacks, overrides e/ou substituicao descritos acima.",
            key=f"fiinfra_confirm_quality_{collection_id}",
        )

    if st.button(
        "Salvar snapshot semanal", type="primary", disabled=not confirmar
    ):
        fundos_para_salvar = _confirmar_estimativas_fundos(fundos_calc)
        snapshot = _snapshot_payload(
            ref_date=ref_date,
            ntnb=ntnb,
            spread=spread,
            excesso_mediano=excesso_mediano,
            duration_mediana=duration_mediana,
            cobertura_fundos=cobertura,
            avaliacao=avaliacao,
            mandato=mandato,
            cdi=cdi,
            aliquota=aliquota,
            inflacao_implicita=inflacao_implicita,
            ipca_focus=ipca_focus,
            inflacao_usada=inflacao_usada,
            inflacao_usada_fonte=inflacao_usada_fonte,
            alternativa_liquida_real=alternativa_liquida_real,
            yield_fundo_real=yield_fundo_real,
            execucao=execucao,
            observacao=observacao,
            auto_macro=auto_macro,
            collection=collection,
        )
        upsert_fiinfra_snapshot(snapshot, fundos_para_salvar)
        st.success("Snapshot FI-Infra salvo.")
        st.rerun()

    _render_history(thresholds)
    _render_revisions(ref_date)
    _render_tranches()


def _render_threshold_editor(thresholds: dict) -> dict:
    with st.expander("Limiares editaveis", expanded=False):
        col1, col2, col3 = st.columns(3)
        editados = dict(thresholds)
        with col1:
            editados["juro_real_caro"] = st.number_input(
                "Juro caro", value=float(thresholds["juro_real_caro"]), step=0.05, format="%.2f"
            )
            editados["juro_real_barato"] = st.number_input(
                "Juro barato", value=float(thresholds["juro_real_barato"]), step=0.05, format="%.2f"
            )
        with col2:
            editados["spread_caro"] = st.number_input(
                "Spread caro", value=float(thresholds["spread_caro"]), step=5.0, format="%.0f"
            )
            editados["spread_barato"] = st.number_input(
                "Spread barato", value=float(thresholds["spread_barato"]), step=5.0, format="%.0f"
            )
        with col3:
            editados["excesso_caro"] = st.number_input(
                "Excesso caro", value=float(thresholds["excesso_caro"]), step=0.25, format="%.2f"
            )
            editados["excesso_barato"] = st.number_input(
                "Excesso barato", value=float(thresholds["excesso_barato"]), step=0.25, format="%.2f"
            )

        try:
            validar_thresholds(editados)
        except ValueError as exc:
            st.error(str(exc))
        else:
            if st.button("Salvar limiares"):
                save_fiinfra_thresholds(editados)
                st.success("Limiares salvos.")
                st.rerun()
    return thresholds


def _render_zona(avaliacao: dict, execucao: dict) -> None:
    zona = avaliacao["zona"]
    text_color, bg_color, border_color = _ZONA_STYLE[zona]
    st.markdown(
        f"""
        <div style="
            border: 1px solid {border_color};
            background: {bg_color};
            color: {text_color};
            border-radius: 8px;
            padding: 18px 22px;
            margin: 12px 0 18px 0;">
            <div style="font-size: 0.78rem; text-transform: uppercase; letter-spacing: .08em;">
                Zona atual
            </div>
            <div style="font-size: 2.2rem; font-weight: 800; line-height: 1.1; margin-top: 4px;">
                {zona}
            </div>
            <div style="font-size: 1rem; margin-top: 8px;">
                {execucao["acao"]} -> {execucao["destino"]}. {execucao["mensagem"]}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_signal_grid(
    ntnb: float,
    spread: float,
    excesso_mediano: float,
    duration_mediana: Optional[float],
    yield_fundo_real: Optional[float],
    alternativa_liquida_real: Optional[float],
    avaliacao: dict,
) -> None:
    posicoes = avaliacao["posicoes"]
    estados = avaliacao["estados"]
    values = [
        ("Juro real", f"IPCA+{ntnb:.2f}%", posicoes["juro_real"], estados["juro_real"]),
        ("Spread", f"{spread:.0f} bps", posicoes["spread"], estados["spread"]),
        ("Excesso desconto", f"{excesso_mediano:.2f} p.p.", posicoes["excesso_desconto"], estados["excesso_desconto"]),
    ]

    cols = st.columns(3)
    for col, (label, value, posicao, estado) in zip(cols, values):
        with col:
            st.metric(label, value, delta=estado)
            st.progress(float(posicao), text="barato -> caro")
            st.markdown(
                f"<div style='height: 4px; border-radius: 8px; background: {_ESTADO_COLOR[estado]};'></div>",
                unsafe_allow_html=True,
            )

    carry_cols = st.columns(3)
    carry_cols[0].metric("Duration mediana", _fmt(duration_mediana, " anos"))
    carry_cols[1].metric("Yield fundo real", _fmt(yield_fundo_real, "% a.a."))
    carry_cols[2].metric("Alternativa real", _fmt(alternativa_liquida_real, "% a.a."))


def _render_fundos_calculados(fundos_df: pd.DataFrame) -> None:
    st.subheader("Desconto ajustado por fundo")
    display = fundos_df.copy()
    st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        column_config={
            "ticker": st.column_config.TextColumn("Ticker"),
            "cota_mercado": st.column_config.NumberColumn("Cota mercado", format="%.2f"),
            "cota_patrimonial": st.column_config.NumberColumn("Cota patrimonial", format="%.2f"),
            "taxa_total_aa": st.column_config.NumberColumn("Taxa total", format="%.2f%%"),
            "duration": st.column_config.NumberColumn("Duration", format="%.2f"),
            "desconto_observado": st.column_config.NumberColumn("Desconto observado", format="%.2f p.p."),
            "desconto_justo": st.column_config.NumberColumn("Desconto justo", format="%.2f p.p."),
            "excesso_desconto": st.column_config.NumberColumn("Excesso", format="%.2f p.p."),
            "elegivel": st.column_config.CheckboxColumn("Elegível"),
            "motivo_exclusao": st.column_config.TextColumn("Motivo exclusão"),
            "cota_mercado_override": st.column_config.CheckboxColumn("Override mercado"),
            "cota_patrimonial_override": st.column_config.CheckboxColumn("Override patrimonial"),
        },
    )


def _render_premissas_lote(fundos_base: pd.DataFrame, collection_id: str) -> pd.DataFrame:
    with st.expander("Premissas taxa/duration em lote", expanded=False):
        st.caption(
            "Cole CSV com colunas ticker, taxa_total_aa e duration. "
            "Separador virgula ou ponto-e-virgula; aceita decimal com virgula."
        )
        texto = st.text_area(
            "Premissas em lote",
            height=110,
            placeholder="ticker;taxa_total_aa;duration\nIFRA11;0,50;8,0",
            key=f"fiinfra_premissas_lote_{collection_id}",
        )
        if not texto.strip():
            return fundos_base
        atualizados, mensagens = _apply_premissas_lote(fundos_base, texto)
        for mensagem in mensagens:
            if mensagem.startswith("Aplicado"):
                st.success(mensagem)
            else:
                st.warning(mensagem)
        return atualizados


def _apply_premissas_lote(fundos_base: pd.DataFrame, texto: str) -> tuple[pd.DataFrame, list[str]]:
    result = fundos_base.copy()
    mensagens = []
    try:
        raw = pd.read_csv(io.StringIO(texto.strip()), sep=None, engine="python", dtype=str)
    except Exception as exc:
        return result, [f"Nao foi possivel ler as premissas em lote: {exc}"]

    raw = _normalizar_colunas_premissas(raw)
    required = {"ticker", "taxa_total_aa", "duration"}
    missing = sorted(required - set(raw.columns))
    if missing:
        return result, ["Colunas obrigatorias ausentes: " + ", ".join(missing)]

    tickers_validos = set(result["ticker"].astype(str).str.upper())
    aplicados = 0
    for _, row in raw.iterrows():
        ticker = str(row.get("ticker", "")).upper().strip()
        if ticker not in tickers_validos:
            mensagens.append(f"Ticker ignorado: {ticker or 'vazio'}")
            continue
        taxa = _parse_premissa_float(row.get("taxa_total_aa"))
        duration = _parse_premissa_float(row.get("duration"))
        if taxa is None or not 0 <= taxa <= 5:
            mensagens.append(f"Taxa invalida para {ticker}: {row.get('taxa_total_aa')}")
            continue
        if duration is None or not 0 < duration <= 30:
            mensagens.append(f"Duration invalida para {ticker}: {row.get('duration')}")
            continue
        mask = result["ticker"].astype(str).str.upper() == ticker
        result.loc[mask, "taxa_total_aa"] = taxa
        result.loc[mask, "duration"] = duration
        result.loc[mask, "taxa_total_status"] = "IMPORTADO_LOTE"
        result.loc[mask, "duration_status"] = "IMPORTADO_LOTE"
        aplicados += 1

    if aplicados:
        mensagens.insert(0, f"Aplicado em {aplicados} fundo(s).")
    elif not mensagens:
        mensagens.append("Nenhuma premissa aplicada.")
    return result, mensagens


def _normalizar_colunas_premissas(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "ticker": "ticker",
        "ativo": "ticker",
        "fundo": "ticker",
        "taxa": "taxa_total_aa",
        "taxa_aa": "taxa_total_aa",
        "taxa_total": "taxa_total_aa",
        "taxa_total_aa": "taxa_total_aa",
        "duration": "duration",
        "dur": "duration",
        "duration_anos": "duration",
    }
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower()
        key = re.sub(r"[^a-z0-9_]+", "_", key)
        rename[col] = aliases.get(key, key)
    return df.rename(columns=rename)


def _parse_premissa_float(value) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _render_quality_panel(
    auto_macro: dict,
    macro_values: dict,
    fundos_calc: list[dict],
    collection: dict,
    cobertura: int,
    qualidade: dict,
) -> None:
    with st.expander("Qualidade dos dados", expanded=bool(qualidade["issues"])):
        cols = st.columns(4)
        lote = collection.get("collection_id", "manual")
        cols[0].metric("Lote", str(lote)[:8])
        cols[1].metric("Cobertura", f"{cobertura}/{len(fundos_calc)}")
        cols[2].metric("Overrides", qualidade["total_overrides"])
        cols[3].metric("Estimativas", qualidade["estimativas"])

        status = qualidade["status_counts"]
        st.caption(
            "Status: "
            f"{status.get('ATUALIZADO', 0)} na data, "
            f"{status.get('DENTRO_SLA', 0)} dentro do SLA, "
            f"{status.get('DEFASADO', 0)} defasados, "
            f"{status.get('INDISPONIVEL', 0)} indisponiveis."
        )

        macro_df = pd.DataFrame(_macro_quality_rows(auto_macro, macro_values))
        st.dataframe(
            macro_df,
            hide_index=True,
            width="stretch",
            column_config={
                "valor": st.column_config.NumberColumn("Valor", format="%.2f"),
                "original": st.column_config.NumberColumn("Original", format="%.2f"),
                "override": st.column_config.CheckboxColumn("Override"),
            },
        )

        fundos_df = pd.DataFrame(_fund_quality_rows(fundos_calc))
        if not fundos_df.empty:
            st.dataframe(
                fundos_df,
                hide_index=True,
                width="stretch",
                column_config={
                    "elegivel": st.column_config.CheckboxColumn("Elegivel"),
                    "override": st.column_config.CheckboxColumn("Override"),
                },
            )

        if qualidade["issues"]:
            st.caption("Pontos para revisar: " + " | ".join(qualidade["issues"]))


def _quality_summary(
    auto_macro: dict,
    fundos_calc: list[dict],
    collection: dict,
    macro_values: dict,
    existing_snapshot: Optional[dict],
    next_revision: int,
) -> dict:
    macro_rows = _macro_quality_rows(auto_macro, macro_values)
    macro_overrides = sum(int(bool(row["override"])) for row in macro_rows)
    fund_overrides = sum(
        int(bool(row.get("cota_mercado_override")))
        + int(bool(row.get("cota_patrimonial_override")))
        for row in fundos_calc
    )
    estimativas = sum(
        row.get("taxa_total_status") == "ESTIMATIVA_NAO_CONFIRMADA"
        or row.get("duration_status") == "ESTIMATIVA_NAO_CONFIRMADA"
        for row in fundos_calc
    )
    issues = []
    if not collection:
        issues.append("snapshot sem lote de coleta oficial para a data")
    issues.extend(collection.get("erros", []))
    total_overrides = macro_overrides + fund_overrides
    if total_overrides:
        issues.append(f"{total_overrides} campo(s) com override manual")
    if estimativas:
        issues.append(f"{estimativas} fundo(s) usam taxa/duration estimadas")
    if existing_snapshot:
        issues.append(
            f"ja existe snapshot nesta data; a versao atual sera arquivada "
            f"como revisao {next_revision}"
        )

    return {
        "issues": issues,
        "macro_overrides": macro_overrides,
        "fund_overrides": fund_overrides,
        "total_overrides": total_overrides,
        "estimativas": estimativas,
        "status_counts": _status_counts(auto_macro, fundos_calc),
    }


def _macro_quality_rows(auto_macro: dict, macro_values: dict) -> list[dict]:
    labels = {
        "ntnb": "NTN-B longa",
        "cdi": "CDI",
        "inflacao_implicita": "Inflacao implicita",
        "ipca_focus": "IPCA Focus 12m",
    }
    date_keys = {
        "ntnb": "ntnb_data",
        "cdi": "cdi_data",
        "inflacao_implicita": "inflacao_data",
        "ipca_focus": "ipca_focus_data",
    }
    rows = []
    for key, label in labels.items():
        meta = _field_provenance(auto_macro, key, macro_values.get(key))
        rows.append({
            "campo": label,
            "valor": macro_values.get(key),
            "original": meta["original"],
            "fonte": meta["fonte"],
            "data_base": auto_macro.get(date_keys[key]),
            "status": meta["status"],
            "override": meta["override"],
        })
    return rows


def _fund_quality_rows(fundos_calc: list[dict]) -> list[dict]:
    rows = []
    for row in fundos_calc:
        rows.append({
            "ticker": row.get("ticker"),
            "mercado": row.get("cota_mercado_status"),
            "patrimonial": row.get("cota_patrimonial_status"),
            "taxa": row.get("taxa_total_status"),
            "duration": row.get("duration_status"),
            "elegivel": bool(row.get("elegivel")),
            "override": bool(row.get("cota_mercado_override"))
            or bool(row.get("cota_patrimonial_override")),
            "motivo": row.get("motivo_exclusao"),
        })
    return rows


def _status_counts(auto_macro: dict, fundos_calc: list[dict]) -> dict:
    statuses = [
        auto_macro.get("ntnb_status", "INDISPONIVEL"),
        auto_macro.get("cdi_status", "INDISPONIVEL"),
        auto_macro.get("inflacao_status", "INDISPONIVEL"),
        auto_macro.get("ipca_focus_status", "INDISPONIVEL"),
    ]
    for fundo in fundos_calc:
        statuses.extend([
            fundo.get("cota_mercado_status", "INDISPONIVEL"),
            fundo.get("cota_patrimonial_status", "INDISPONIVEL"),
        ])
    return {status: statuses.count(status) for status in set(statuses)}


def _render_history(thresholds: dict) -> None:
    st.subheader("Historico e recalibracao")
    periodos = {
        "6M": 26,
        "1A": 52,
        "2A": 104,
        "Tudo": 9999,
    }
    periodo = st.radio("Periodo historico", options=list(periodos.keys()), horizontal=True, index=1)
    hist = load_fiinfra_snapshots(days=periodos[periodo])

    if hist.empty:
        st.info("Sem snapshots FI-Infra salvos ainda.")
        return

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        subplot_titles=("Juro real", "Spread", "Excesso de desconto"),
    )
    _add_signal_trace(
        fig, hist, 1, "ntnb", "Juro real",
        thresholds["juro_real_caro"], thresholds["juro_real_barato"], "%",
        caro_ref_col="juro_real_caro_ref", barato_ref_col="juro_real_barato_ref",
    )
    _add_signal_trace(
        fig, hist, 2, "spread", "Spread",
        thresholds["spread_caro"], thresholds["spread_barato"], " bps",
        caro_ref_col="spread_caro_ref", barato_ref_col="spread_barato_ref",
    )
    _add_signal_trace(
        fig,
        hist,
        3,
        "excesso_mediano",
        "Excesso",
        thresholds["excesso_caro"],
        thresholds["excesso_barato"],
        " p.p.",
        caro_ref_col="excesso_caro_ref",
        barato_ref_col="excesso_barato_ref",
    )
    fig.update_layout(
        height=620,
        hovermode="x unified",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=0, r=0, t=45, b=0),
        showlegend=False,
    )
    fig.update_xaxes(gridcolor="#f1f5f9")
    fig.update_yaxes(gridcolor="#f1f5f9")
    st.plotly_chart(fig, width="stretch")

    history_columns = [
        "data", "ntnb", "spread", "excesso_mediano", "ipca_focus",
        "inflacao_implicita", "inflacao_usada_fonte", "zona", "acao", "destino",
        "metodologia_version",
    ]
    tabela = hist[[col for col in history_columns if col in hist.columns]].copy()
    st.dataframe(tabela, hide_index=True, width="stretch")


def _render_revisions(ref_date: date) -> None:
    revisions = load_fiinfra_revisions(ref_date)
    if revisions.empty:
        return

    with st.expander(f"Revisoes arquivadas de {ref_date.isoformat()}"):
        display = revisions[[
            "revisao_num", "substituido_em", "zona", "metodologia_version",
            "fundos_count", "observacao",
        ]].copy()
        st.dataframe(
            display,
            hide_index=True,
            width="stretch",
            column_config={
                "revisao_num": st.column_config.NumberColumn("Revisao", format="%d"),
                "substituido_em": st.column_config.TextColumn("Arquivada em"),
                "zona": st.column_config.TextColumn("Zona"),
                "metodologia_version": st.column_config.TextColumn("Metodologia"),
                "fundos_count": st.column_config.NumberColumn("Fundos", format="%d"),
                "observacao": st.column_config.TextColumn("Observacao"),
            },
        )
        options = {
            f"Revisao {int(row.revisao_num)} - {row.substituido_em}": int(row.id)
            for row in revisions.itertuples(index=False)
        }
        selecionada = st.selectbox(
            "Revisao para restaurar",
            options=list(options),
            key=f"fiinfra_restore_revision_select_{ref_date.isoformat()}",
        )
        confirmar = st.checkbox(
            "Confirmo que quero substituir o snapshot atual por esta revisao.",
            key=f"fiinfra_restore_revision_confirm_{ref_date.isoformat()}",
        )
        if st.button(
            "Restaurar revisao selecionada",
            disabled=not confirmar,
            key=f"fiinfra_restore_revision_button_{ref_date.isoformat()}",
        ):
            restore_fiinfra_revision(options[selecionada])
            st.success("Revisao restaurada. O snapshot anterior foi arquivado.")
            st.rerun()


def _render_tranches() -> None:
    st.subheader("Registro de tranches")
    with st.form("form_tranche", clear_on_submit=True):
        cols = st.columns([1, 1, 1, 1, 1.5])
        with cols[0]:
            tipo = st.selectbox("Tipo", ["Compra", "Venda"])
        with cols[1]:
            data_tranche = st.date_input("Data tranche", value=date.today())
        with cols[2]:
            ticker = st.selectbox("Ticker", list(FUNDOS_PADRAO))
        with cols[3]:
            qtd = st.number_input("Quantidade", min_value=0.0, step=1.0, format="%.0f")
        with cols[4]:
            preco = st.number_input("Preco", min_value=0.0, step=0.01, format="%.2f")
        observacao = st.text_input("Observacao tranche")
        submitted = st.form_submit_button("Registrar tranche")

    if submitted:
        if qtd <= 0 or preco <= 0:
            st.error("Quantidade e preco precisam ser maiores que zero.")
        else:
            insert_fiinfra_tranche({
                "tipo": tipo,
                "data": data_tranche,
                "ticker": ticker,
                "qtd": qtd,
                "preco": preco,
                "observacao": observacao,
            })
            st.success("Tranche registrada.")
            st.rerun()

    tranches = load_fiinfra_tranches(limit=100)
    if tranches.empty:
        st.info("Nenhuma tranche registrada.")
    else:
        st.dataframe(tranches, hide_index=True, width="stretch")


def _add_signal_trace(
    fig: go.Figure,
    hist: pd.DataFrame,
    row: int,
    column: str,
    name: str,
    caro: float,
    barato: float,
    suffix: str,
    caro_ref_col: Optional[str] = None,
    barato_ref_col: Optional[str] = None,
) -> None:
    fig.add_trace(
        go.Scatter(
            x=hist["data"],
            y=hist[column],
            mode="lines+markers",
            name=name,
            line=dict(color="#2563eb", width=2),
            hovertemplate=f"%{{y:.2f}}{suffix}<extra>{name}</extra>",
        ),
        row=row,
        col=1,
    )
    barato_series = _historical_threshold(hist, barato_ref_col, barato)
    caro_series = _historical_threshold(hist, caro_ref_col, caro)
    fig.add_trace(
        go.Scatter(
            x=hist["data"],
            y=barato_series,
            mode="lines",
            name=f"{name} barato ref",
            line=dict(color="#16a34a", width=1.5, dash="dot"),
            hovertemplate=f"%{{y:.2f}}{suffix}<extra>{name} barato ref</extra>",
        ),
        row=row,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=hist["data"],
            y=caro_series,
            mode="lines",
            name=f"{name} caro ref",
            line=dict(color="#dc2626", width=1.5, dash="dot"),
            hovertemplate=f"%{{y:.2f}}{suffix}<extra>{name} caro ref</extra>",
        ),
        row=row,
        col=1,
    )


def _historical_threshold(
    hist: pd.DataFrame,
    ref_col: Optional[str],
    fallback: float,
) -> pd.Series:
    if ref_col and ref_col in hist.columns:
        series = pd.to_numeric(hist[ref_col], errors="coerce").fillna(float(fallback))
    else:
        series = pd.Series(float(fallback), index=hist.index)
    return series


def _fundos_base(dados_auto: Optional[list[dict]] = None) -> pd.DataFrame:
    latest = load_fiinfra_fundos()
    columns = [
        "ticker", "cnpj", "cota_mercado", "cota_mercado_original",
        "cota_mercado_data", "cota_mercado_fonte", "cota_mercado_status",
        "cota_patrimonial", "cota_patrimonial_original", "cota_patrimonial_data",
        "cota_patrimonial_fonte", "cota_patrimonial_status", "taxa_total_aa",
        "taxa_total_status", "duration", "duration_status",
    ]
    if dados_auto:
        anteriores = latest.set_index("ticker").to_dict("index") if not latest.empty else {}
        rows = []
        for fundo in dados_auto:
            ticker = fundo["ticker"]
            anterior = anteriores.get(ticker, {})
            rows.append({
                "ticker": ticker,
                "cnpj": fundo.get("cnpj"),
                "cota_mercado": fundo.get("cota_mercado"),
                "cota_mercado_original": fundo.get("cota_mercado"),
                "cota_patrimonial": fundo.get("cota_patrimonial"),
                "cota_patrimonial_original": fundo.get("cota_patrimonial"),
                "cota_mercado_fonte": fundo.get("cota_mercado_fonte"),
                "cota_patrimonial_fonte": fundo.get("cota_patrimonial_fonte"),
                "taxa_total_aa": _prior_or_default(anterior, "taxa_total_aa", 1.0),
                "duration": _prior_or_default(anterior, "duration", 8.0),
                "taxa_total_status": anterior.get("taxa_total_status") or (
                    "HISTORICO" if anterior else "ESTIMATIVA_NAO_CONFIRMADA"
                ),
                "duration_status": anterior.get("duration_status") or (
                    "HISTORICO" if anterior else "ESTIMATIVA_NAO_CONFIRMADA"
                ),
                "mercado_data": fundo.get("cota_mercado_data"),
                "patrimonial_data": fundo.get("cota_patrimonial_data"),
                "mercado_status": fundo.get("cota_mercado_status"),
                "patrimonial_status": fundo.get("cota_patrimonial_status"),
            })
        return pd.DataFrame(rows)
    if not latest.empty:
        result = latest[[col for col in columns if col in latest.columns]].copy()
        return result.rename(columns={
            "cota_mercado_data": "mercado_data",
            "cota_mercado_status": "mercado_status",
            "cota_patrimonial_data": "patrimonial_data",
            "cota_patrimonial_status": "patrimonial_status",
        })

    return pd.DataFrame([
        {
            "ticker": ticker,
            "cnpj": None,
            "cota_mercado": None,
            "cota_mercado_original": None,
            "cota_patrimonial": None,
            "cota_patrimonial_original": None,
            "taxa_total_aa": 1.0,
            "taxa_total_status": "ESTIMATIVA_NAO_CONFIRMADA",
            "duration": 8.0,
            "duration_status": "ESTIMATIVA_NAO_CONFIRMADA",
        }
        for ticker in FUNDOS_PADRAO
    ])


def _snapshot_payload(
    ref_date: date,
    ntnb: float,
    spread: float,
    excesso_mediano: float,
    duration_mediana: Optional[float],
    cobertura_fundos: int,
    avaliacao: dict,
    mandato: str,
    cdi: float,
    aliquota: float,
    inflacao_implicita: float,
    ipca_focus: float,
    inflacao_usada: float,
    inflacao_usada_fonte: str,
    alternativa_liquida_real: Optional[float],
    yield_fundo_real: Optional[float],
    execucao: dict,
    observacao: str,
    auto_macro: Optional[dict] = None,
    collection: Optional[dict] = None,
) -> dict:
    auto_macro = auto_macro or {}
    collection = collection or {}
    ntnb_meta = _field_provenance(auto_macro, "ntnb", ntnb)
    cdi_meta = _field_provenance(auto_macro, "cdi", cdi)
    implicita_meta = _field_provenance(auto_macro, "inflacao_implicita", inflacao_implicita)
    focus_meta = _field_provenance(auto_macro, "ipca_focus", ipca_focus)
    thresholds = avaliacao.get("thresholds", {})
    return {
        "data": ref_date,
        "collection_id": collection.get("collection_id"),
        "data_solicitada": collection.get("data_solicitada", ref_date),
        "metodologia_version": METODOLOGIA_VERSION,
        "cobertura_fundos": cobertura_fundos,
        "juro_real_caro_ref": thresholds.get("juro_real_caro"),
        "juro_real_barato_ref": thresholds.get("juro_real_barato"),
        "spread_caro_ref": thresholds.get("spread_caro"),
        "spread_barato_ref": thresholds.get("spread_barato"),
        "excesso_caro_ref": thresholds.get("excesso_caro"),
        "excesso_barato_ref": thresholds.get("excesso_barato"),
        "ntnb": ntnb,
        "ntnb_original": ntnb_meta["original"],
        "ntnb_fonte": ntnb_meta["fonte"],
        "ntnb_override": ntnb_meta["override"],
        "spread": spread,
        "excesso_mediano": excesso_mediano,
        "duration_mediana": duration_mediana,
        "zona": avaliacao["zona"],
        "juro_estado": avaliacao["estados"]["juro_real"],
        "spread_estado": avaliacao["estados"]["spread"],
        "excesso_estado": avaliacao["estados"]["excesso_desconto"],
        "juro_pos": avaliacao["posicoes"]["juro_real"],
        "spread_pos": avaliacao["posicoes"]["spread"],
        "excesso_pos": avaliacao["posicoes"]["excesso_desconto"],
        "mandato": mandato,
        "cdi": cdi,
        "cdi_original": cdi_meta["original"],
        "cdi_fonte": cdi_meta["fonte"],
        "cdi_override": cdi_meta["override"],
        "aliquota": aliquota,
        "inflacao_implicita": inflacao_implicita,
        "inflacao_original": implicita_meta["original"],
        "inflacao_fonte": implicita_meta["fonte"],
        "inflacao_override": implicita_meta["override"],
        "ipca_focus": ipca_focus,
        "ipca_focus_original": focus_meta["original"],
        "ipca_focus_fonte": focus_meta["fonte"],
        "ipca_focus_override": focus_meta["override"],
        "ipca_focus_data": auto_macro.get("ipca_focus_data"),
        "ipca_focus_status": focus_meta["status"],
        "inflacao_usada": inflacao_usada,
        "inflacao_usada_fonte": inflacao_usada_fonte,
        "alternativa_liquida_real": alternativa_liquida_real,
        "yield_fundo_real": yield_fundo_real,
        "acao": execucao["acao"],
        "destino": execucao["destino"],
        "venda_bloqueada": execucao["bloqueada"],
        "observacao": observacao,
        "ntnb_vencimento": auto_macro.get("ntnb_vencimento"),
        "ntnb_duration_ref": auto_macro.get("ntnb_duration"),
        "ntnb_data": auto_macro.get("ntnb_data"),
        "ntnb_status": ntnb_meta["status"],
        "cdi_data": auto_macro.get("cdi_data"),
        "cdi_status": cdi_meta["status"],
        "inflacao_data": auto_macro.get("inflacao_data"),
        "inflacao_status": implicita_meta["status"],
        "coletado_em": collection.get(
            "coletado_em", datetime.now().isoformat(timespec="seconds")
        ),
    }


def _confirmar_estimativas_fundos(fundos: list[dict]) -> list[dict]:
    confirmados = []
    for fundo in fundos:
        row = dict(fundo)
        if row.get("taxa_total_status") == "ESTIMATIVA_NAO_CONFIRMADA":
            row["taxa_total_status"] = "MANUAL_CONFIRMADO"
        if row.get("duration_status") == "ESTIMATIVA_NAO_CONFIRMADA":
            row["duration_status"] = "MANUAL_CONFIRMADO"
        confirmados.append(row)
    return confirmados


def _fallback_float(row: Optional[dict], key: str, fallback: float) -> float:
    if row is None:
        return float(fallback)
    value = row.get(key)
    if value is None or pd.isna(value):
        return float(fallback)
    return float(value)


def _auto_or_fallback(
    auto: dict,
    auto_key: str,
    row: Optional[dict],
    row_key: str,
    fallback: float,
) -> float:
    value = auto.get(auto_key)
    if value is not None and not pd.isna(value):
        return float(value)
    return _fallback_float(row, row_key, fallback)


def _prior_or_default(row: dict, key: str, fallback: float) -> float:
    value = row.get(key)
    if value is None or pd.isna(value):
        return float(fallback)
    return float(value)


def _field_provenance(auto: dict, key: str, effective: float) -> dict:
    original = auto.get(key)
    override = False
    if (
        original is not None
        and not pd.isna(original)
        and effective is not None
        and not pd.isna(effective)
    ):
        override = not math.isclose(
            float(effective), float(original), rel_tol=1e-9, abs_tol=1e-9
        )
    source_key = {
        "ntnb": "ntnb_fonte",
        "cdi": "cdi_fonte",
        "inflacao_implicita": "inflacao_fonte",
        "ipca_focus": "ipca_focus_fonte",
    }[key]
    status_key = {
        "ntnb": "ntnb_status",
        "cdi": "cdi_status",
        "inflacao_implicita": "inflacao_status",
        "ipca_focus": "ipca_focus_status",
    }[key]
    return {
        "original": original,
        "override": override,
        "fonte": auto.get(source_key) or "manual_ou_snapshot_anterior",
        "status": "OVERRIDE_MANUAL" if override else auto.get(status_key, "MANUAL"),
    }


def _duration_alvo() -> Optional[float]:
    latest = load_fiinfra_fundos()
    if latest.empty or "duration" not in latest:
        return 8.0
    values = pd.to_numeric(latest["duration"], errors="coerce").dropna()
    values = values[values > 0]
    return float(values.median()) if not values.empty else 8.0


def _render_update_result(macro: dict, fundos: list[dict], erros: list[str]) -> None:
    statuses = [
        macro.get("ntnb_status", "INDISPONIVEL"),
        macro.get("cdi_status", "INDISPONIVEL"),
        macro.get("inflacao_status", "INDISPONIVEL"),
        macro.get("ipca_focus_status", "INDISPONIVEL"),
    ]
    for fundo in fundos:
        statuses.extend([
            fundo.get("cota_mercado_status", "INDISPONIVEL"),
            fundo.get("cota_patrimonial_status", "INDISPONIVEL"),
        ])
    atualizados = statuses.count("ATUALIZADO")
    dentro_sla = statuses.count("DENTRO_SLA")
    defasados = statuses.count("DEFASADO")
    indisponiveis = statuses.count("INDISPONIVEL")
    mensagem = (
        f"Coleta concluida: {atualizados} na data, {dentro_sla} dentro do SLA, "
        f"{defasados} defasados e {indisponiveis} indisponiveis."
    )
    if erros or defasados or indisponiveis:
        detalhe = f" Falhas: {' | '.join(erros)}" if erros else ""
        st.warning(mensagem + detalhe)
    else:
        st.success(mensagem)


def _ultimo_imab_value(row: Optional[dict], key: str, fallback: float) -> float:
    if row is None:
        return float(fallback)
    value = row.get(key)
    if value is None or pd.isna(value):
        return float(fallback)
    return float(value)


def _mandato_index(snapshot: Optional[dict]) -> int:
    if snapshot and snapshot.get("mandato") in MANDATOS:
        return list(MANDATOS).index(snapshot["mandato"])
    return list(MANDATOS).index(MANDATO_JURO_REAL)


def _fmt(value: Optional[float], suffix: str) -> str:
    if value is None or pd.isna(value):
        return "N/D"
    return f"{value:.2f}{suffix}"
