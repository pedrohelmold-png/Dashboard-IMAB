from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from src.collector import fetch_fiinfra_fundos, fetch_fiinfra_macro
from src.db import (
    get_ultimo_carrego,
    get_ultimo_fiinfra_snapshot,
    insert_fiinfra_tranche,
    load_fiinfra_fundos,
    load_fiinfra_snapshots,
    load_fiinfra_thresholds,
    load_fiinfra_tranches,
    save_fiinfra_thresholds,
    upsert_fiinfra_snapshot,
)
from src.regua_fiinfra import (
    ESTADO_BARATO,
    ESTADO_CARO,
    ESTADO_NEUTRO,
    FUNDOS_PADRAO,
    MANDATO_CAIXA,
    MANDATO_JURO_REAL,
    MANDATO_RENDA,
    MANDATOS,
    ZONA_CARREGAR,
    ZONA_COMPRAR,
    ZONA_REDUZIR,
    avaliar_sinais,
    calcular_cdi_liquido_real,
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
        ref_date = st.date_input("Data", value=date.today())
    with update_cols[1]:
        st.write("")
        st.write("")
        atualizar = st.button("Atualizar dados oficiais", type="primary", width="stretch")

    if atualizar:
        with st.spinner("Baixando dados ANBIMA, BCB, B3 e CVM..."):
            erros = []
            try:
                st.session_state["fiinfra_auto_macro"] = fetch_fiinfra_macro(ref_date)
            except Exception as exc:
                erros.append(f"macro: {exc}")
            try:
                st.session_state["fiinfra_auto_fundos"] = fetch_fiinfra_fundos(ref_date)
            except Exception as exc:
                erros.append(f"fundos: {exc}")
        if erros:
            st.warning("Atualizacao parcial. " + " | ".join(erros))
        else:
            st.success("Dados oficiais atualizados.")

    auto_macro = st.session_state.get("fiinfra_auto_macro", {})
    input_cols = st.columns([1, 1, 1, 1])
    with input_cols[0]:
        ntnb = st.number_input(
            "NTN-B longa (% a.a.)",
            value=_auto_or_fallback(auto_macro, "ntnb", ultimo_snapshot, "ntnb", 6.5),
            step=0.05,
            format="%.2f",
        )
    with input_cols[1]:
        spread = st.number_input(
            "Spread IDA-Infra (bps)",
            value=_fallback_float(ultimo_snapshot, "spread", 100.0),
            step=5.0,
            format="%.0f",
            help="Mantido manual ate haver serie ANBIMA estruturada e estavel.",
        )
    with input_cols[2]:
        mandato = st.selectbox(
            "Mandato",
            options=list(MANDATOS),
            index=_mandato_index(ultimo_snapshot),
        )
    with input_cols[3]:
        st.metric("Fonte macro", auto_macro.get("fonte", "Ultimo snapshot/manual"))

    macro_cols = st.columns([1, 1, 1])
    with macro_cols[0]:
        cdi = st.number_input(
            "CDI (% a.a.)",
            value=_auto_or_fallback(
                auto_macro, "cdi", ultimo_snapshot, "cdi",
                _ultimo_imab_value(ultimo_imab, "cdi_anual", 11.0),
            ),
            step=0.05,
            format="%.2f",
        )
    with macro_cols[1]:
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
        )
    with macro_cols[2]:
        aliquota_pct = st.number_input(
            "Aliquota alternativa (%)",
            value=_fallback_float(ultimo_snapshot, "aliquota", 0.15) * 100,
            min_value=0.0,
            max_value=35.0,
            step=1.0,
            format="%.1f",
        )
    aliquota = aliquota_pct / 100

    if mandato == MANDATO_JURO_REAL:
        imab_real = st.number_input(
            "IMA-B real bruto para rotacao (% a.a.)",
            value=_ultimo_imab_value(ultimo_imab, "ytm_real", ntnb),
            step=0.05,
            format="%.2f",
        )
        alternativa_liquida_real = imab_real * (1 - aliquota)
    else:
        alternativa_liquida_real = calcular_cdi_liquido_real(cdi, aliquota, inflacao_implicita)

    st.subheader("Fundos monitorados")
    fundos_base = _fundos_base(st.session_state.get("fiinfra_auto_fundos"))
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
        },
    )

    fundos_calc, excesso_mediano, duration_mediana = preparar_fundos(
        fundos_editados.to_dict("records")
    )
    fundos_df = pd.DataFrame(fundos_calc)

    if excesso_mediano is None:
        st.warning("Preencha cota de mercado, cota patrimonial, taxa total e duration de pelo menos um fundo.")
        _render_history(thresholds)
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

    observacao = st.text_area("Observacao do snapshot", height=80)
    if st.button("Salvar snapshot semanal", type="primary"):
        snapshot = _snapshot_payload(
            ref_date=ref_date,
            ntnb=ntnb,
            spread=spread,
            excesso_mediano=excesso_mediano,
            duration_mediana=duration_mediana,
            avaliacao=avaliacao,
            mandato=mandato,
            cdi=cdi,
            aliquota=aliquota,
            inflacao_implicita=inflacao_implicita,
            alternativa_liquida_real=alternativa_liquida_real,
            yield_fundo_real=yield_fundo_real,
            execucao=execucao,
            observacao=observacao,
        )
        upsert_fiinfra_snapshot(snapshot, fundos_calc)
        st.success("Snapshot FI-Infra salvo.")
        st.rerun()

    _render_history(thresholds)
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
    return editados


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
        },
    )


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
    _add_signal_trace(fig, hist, 1, "ntnb", "Juro real", thresholds["juro_real_caro"], thresholds["juro_real_barato"], "%")
    _add_signal_trace(fig, hist, 2, "spread", "Spread", thresholds["spread_caro"], thresholds["spread_barato"], " bps")
    _add_signal_trace(
        fig,
        hist,
        3,
        "excesso_mediano",
        "Excesso",
        thresholds["excesso_caro"],
        thresholds["excesso_barato"],
        " p.p.",
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

    tabela = hist[["data", "ntnb", "spread", "excesso_mediano", "zona", "acao", "destino"]].copy()
    st.dataframe(tabela, hide_index=True, width="stretch")


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
    fig.add_hline(y=barato, line_color="#16a34a", line_dash="dot", row=row, col=1)
    fig.add_hline(y=caro, line_color="#dc2626", line_dash="dot", row=row, col=1)


def _fundos_base(dados_auto: Optional[list[dict]] = None) -> pd.DataFrame:
    latest = load_fiinfra_fundos()
    columns = ["ticker", "cota_mercado", "cota_patrimonial", "taxa_total_aa", "duration"]
    if dados_auto:
        anteriores = latest.set_index("ticker").to_dict("index") if not latest.empty else {}
        rows = []
        for fundo in dados_auto:
            ticker = fundo["ticker"]
            anterior = anteriores.get(ticker, {})
            rows.append({
                "ticker": ticker,
                "cota_mercado": fundo.get("cota_mercado"),
                "cota_patrimonial": fundo.get("cota_patrimonial"),
                "taxa_total_aa": anterior.get("taxa_total_aa", 1.0),
                "duration": anterior.get("duration", 8.0),
                "mercado_data": fundo.get("cota_mercado_data"),
                "patrimonial_data": fundo.get("cota_patrimonial_data"),
            })
        return pd.DataFrame(rows)
    if not latest.empty:
        return latest[columns].copy()

    return pd.DataFrame([
        {
            "ticker": ticker,
            "cota_mercado": None,
            "cota_patrimonial": None,
            "taxa_total_aa": 1.0,
            "duration": 8.0,
        }
        for ticker in FUNDOS_PADRAO
    ])


def _snapshot_payload(
    ref_date: date,
    ntnb: float,
    spread: float,
    excesso_mediano: float,
    duration_mediana: Optional[float],
    avaliacao: dict,
    mandato: str,
    cdi: float,
    aliquota: float,
    inflacao_implicita: float,
    alternativa_liquida_real: Optional[float],
    yield_fundo_real: Optional[float],
    execucao: dict,
    observacao: str,
) -> dict:
    return {
        "data": ref_date,
        "ntnb": ntnb,
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
        "aliquota": aliquota,
        "inflacao_implicita": inflacao_implicita,
        "alternativa_liquida_real": alternativa_liquida_real,
        "yield_fundo_real": yield_fundo_real,
        "acao": execucao["acao"],
        "destino": execucao["destino"],
        "venda_bloqueada": execucao["bloqueada"],
        "observacao": observacao,
    }


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
