"""
app.py — Dashboard Streamlit do carrego do IMA-B 5 e IMA-B.

Iniciar com:
    streamlit run app.py

O seletor no sidebar alterna entre IMA-B 5 e IMA-B (índice completo).
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import COLOR_CARREGO, COLOR_CDI, COLOR_REAL
from src.db import (
    get_ultimo_carrego,
    init_db,
    init_db_fiinfra,
    init_db_imab,
    load_carrego_historico,
    load_composicao,
)
from src.fiinfra_ui import render_regua_fiinfra

# ── Configuração da página ─────────────────────────────────────
st.set_page_config(
    page_title="IMA-B | Carrego",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    div[data-testid="metric-container"] {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 16px 20px;
    }
    .stMetric label { font-size: 0.78rem; color: #64748b; text-transform: uppercase; letter-spacing: .05em; }
</style>
""", unsafe_allow_html=True)


# ── Init DB ────────────────────────────────────────────────────
init_db()
init_db_imab()
init_db_fiinfra()

with st.sidebar:
    tela_sel = st.radio(
        "Tela",
        options=["Carrego IMA-B", "Regua FI-Infra"],
        index=0,
    )
    st.divider()

if tela_sel == "Regua FI-Infra":
    render_regua_fiinfra()
    st.stop()


# ─────────────────────────────────────────────────────────────────
# Configuração por índice
# ─────────────────────────────────────────────────────────────────
_INDICES = {
    "IMA-B 5": {
        "etl_flag":        [],                          # sem flag extra
        "carrego_table":   "carrego_historico",
        "composicao_table":"composicao_imab5",
        "icon":            "📊",
        "subtitulo":       "NTN-Bs com vencimento ≤ 5 anos",
        "help_ytm":        "Média ponderada da taxa indicativa das NTN-Bs dentro do horizonte de 5 anos.",
        "help_universo":   "horizonte de 5 anos",
    },
    "IMA-B": {
        "etl_flag":        ["--index", "imab"],
        "carrego_table":   "carrego_historico_imab",
        "composicao_table":"composicao_imab",
        "icon":            "📈",
        "subtitulo":       "Todos os NTN-Bs (sem corte de prazo)",
        "help_ytm":        "Média ponderada por duration da taxa indicativa de todos os NTN-Bs.",
        "help_universo":   "todos os vencimentos",
    },
}


# ─────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 IMA-B")
    st.caption("Carrego diário ex-ante")
    st.divider()

    indice_sel = st.radio(
        "Índice",
        options=list(_INDICES.keys()),
        index=0,
        horizontal=True,
    )
    cfg = _INDICES[indice_sel]

    st.divider()

    _ROOT = str(__import__("pathlib").Path(__file__).parent)

    if st.button("⟳  Atualizar hoje", width="stretch", type="primary"):
        with st.spinner("Buscando dados do dia…"):
            result = subprocess.run(
                [sys.executable, "etl.py"] + cfg["etl_flag"],
                capture_output=True, text=True, cwd=_ROOT,
            )
        if result.returncode == 0:
            st.success("Dados do dia atualizados!")
        else:
            st.error(f"Erro:\n{result.stderr[-400:]}")
        st.rerun()

    from src.db import load_carrego_historico as _lch
    n_dias_banco = len(_lch(days=99_999, table=cfg["carrego_table"]))
    st.caption(f"Histórico no banco: **{n_dias_banco} dias úteis**")

    anos_opcoes = {
        "6 meses (~126 d.u.)":  126,
        "1 ano   (~252 d.u.)":  252,
        "2 anos  (~504 d.u.)":  504,
        "5 anos  (~1260 d.u.)": 1260,
        "Max. disponível":      99_999,
    }
    anos_sel = st.selectbox(
        "Período do backfill",
        options=list(anos_opcoes.keys()),
        index=0,
        help="ANBIMA disponibiliza dados de NTN-B a partir de 2020.",
    )
    n_backfill = anos_opcoes[anos_sel]
    tempo_est  = round(min(n_backfill, 1596) * 0.5 / 60, 1)

    lbl = anos_sel.split()[0] + " " + anos_sel.split()[1] if len(anos_sel.split()) > 1 else anos_sel
    if st.button(f"📥  Baixar histórico ({lbl})", width="stretch"):
        placeholder = st.empty()
        placeholder.info(f"Buscando dados na ANBIMA… (~{tempo_est} min)")
        result = subprocess.run(
            [sys.executable, "etl.py"] + cfg["etl_flag"] + ["--backfill", "--days", str(n_backfill)],
            capture_output=True, text=True, cwd=_ROOT,
        )
        if result.returncode == 0:
            placeholder.success("Histórico carregado! Recarregando…")
        else:
            placeholder.error(f"Erro:\n{result.stderr[-400:]}")
        st.rerun()

    st.divider()

    periodo = st.radio(
        "Período",
        options=["3M", "6M", "1A", "2A", "Tudo"],
        index=2,
        horizontal=False,
    )
    periodo_dias = {"3M": 63, "6M": 126, "1A": 252, "2A": 504, "Tudo": 99_999}[periodo]

    st.divider()
    st.caption(
        "**Fontes:** ANBIMA (taxas NTN-B), BCB (CDI/IPCA Focus)\n\n"
        "**Pesos:** proxy por duration — não são os pesos oficiais ANBIMA"
    )


# ─────────────────────────────────────────────────────────────────
# Carga de dados
# ─────────────────────────────────────────────────────────────────
ultimo = get_ultimo_carrego(table=cfg["carrego_table"])
hist   = load_carrego_historico(days=periodo_dias, table=cfg["carrego_table"])
compos = load_composicao(table=cfg["composicao_table"])


# ─────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────
st.title(f"Carrego do {indice_sel}")
st.caption(cfg["subtitulo"])

if ultimo:
    st.caption(
        f"Última atualização: **{ultimo['data']}** · "
        f"{ultimo['n_bonds']} título(s) · "
        f"pesos por {ultimo['metodo_peso']} · "
        f"IPCA usado no carrego: {ultimo['fonte_ipca']}"
    )
else:
    flag_str = " ".join(cfg["etl_flag"])
    st.warning(
        f"Nenhum dado no banco. Clique em **Atualizar dados** no menu lateral "
        f"ou execute `python etl.py {flag_str} --backfill` no terminal."
    )
    st.stop()


# ─────────────────────────────────────────────────────────────────
# KPI Cards
# ─────────────────────────────────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)

with c1:
    st.metric(
        label=f"Taxa Real ({indice_sel} proxy)",
        value=f"IPCA + {ultimo['ytm_real']:.2f}%",
        help=cfg["help_ytm"],
    )

with c2:
    st.metric(
        label="IPCA Focus 12m",
        value=f"{ultimo['ipca_focus']:.2f}%" if ultimo.get("ipca_focus") is not None else "N/D",
        help=f"Mediana suavizada do BCB Focus. Data-base: {ultimo.get('ipca_focus_data') or 'N/D'}.",
    )

with c3:
    st.metric(
        label=f"Inflação implícita ({indice_sel})",
        value=f"{ultimo['ipca_implicita']:.2f}%" if ultimo.get("ipca_implicita") is not None else "N/D",
        help="Breakeven ponderado das NTN-Bs do universo do índice; varia com vencimentos e pesos.",
    )

with c4:
    st.metric(
        label="Carrego Nominal Bruto",
        value=f"{ultimo['carrego_nominal']:.2f}% a.a.",
        help=f"(1 + taxa real) × (1 + IPCA usado) − 1. Fonte: {ultimo['fonte_ipca']}.",
    )

with c5:
    if ultimo["premio_vs_cdi"] is not None:
        delta_str = f"CDI: {ultimo['cdi_anual']:.2f}% a.a."
        st.metric(
            label="Prêmio vs CDI",
            value=f"{ultimo['premio_vs_cdi']:+.2f} p.p.",
            delta=delta_str,
            delta_color="normal" if ultimo["premio_vs_cdi"] >= 0 else "inverse",
            help="Carrego nominal menos CDI (DI Over) anualizado.",
        )
    else:
        st.metric(
            label="Prêmio vs CDI",
            value="N/D",
            help="CDI não disponível para este período.",
        )

st.divider()


# ─────────────────────────────────────────────────────────────────
# Gráfico principal — histórico de carrego vs CDI
# ─────────────────────────────────────────────────────────────────
st.subheader("Histórico")

if "ipca_focus" in hist and hist["ipca_focus"].isna().any():
    st.caption(
        "Mudança metodológica em 09/07/2026: registros anteriores calculavam o "
        "carrego com a inflação implícita. A partir dessa data, o IPCA Focus 12m "
        "é a premissa principal; a implícita permanece como série separada."
    )

if hist.empty:
    st.info("Sem histórico suficiente. Execute o backfill para popular.")
else:
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=hist["data"],
        y=hist["carrego_nominal"],
        name="Carrego Nominal",
        connectgaps=True,
        line=dict(color=COLOR_CARREGO, width=2.5),
        hovertemplate="%{y:.2f}% a.a.<extra>Carrego Nominal</extra>",
    ))

    if hist["cdi_anual"].notna().any():
        fig.add_trace(go.Scatter(
            x=hist["data"],
            y=hist["cdi_anual"],
            name="CDI a.a.",
            connectgaps=True,
            line=dict(color=COLOR_CDI, width=1.8, dash="dot"),
            hovertemplate="%{y:.2f}% a.a.<extra>CDI</extra>",
        ))

    fig.add_trace(go.Scatter(
        x=hist["data"],
        y=hist["ytm_real"],
        name="Taxa Real (IPCA+)",
        connectgaps=True,
        line=dict(color=COLOR_REAL, width=1.5, dash="dash"),
        hovertemplate="IPCA+%{y:.2f}%<extra>Taxa Real</extra>",
        visible="legendonly",
    ))

    if "ipca_focus" in hist and hist["ipca_focus"].notna().any():
        fig.add_trace(go.Scatter(
            x=hist["data"], y=hist["ipca_focus"], name="IPCA Focus 12m",
            connectgaps=False, line=dict(color="#7c3aed", width=1.5),
            hovertemplate="%{y:.2f}%<extra>IPCA Focus 12m</extra>",
            visible="legendonly",
        ))
    if "ipca_implicita" in hist and hist["ipca_implicita"].notna().any():
        fig.add_trace(go.Scatter(
            x=hist["data"], y=hist["ipca_implicita"], name="Inflação implícita",
            connectgaps=False, line=dict(color="#ea580c", width=1.5, dash="dash"),
            hovertemplate="%{y:.2f}%<extra>Inflação implícita</extra>",
            visible="legendonly",
        ))

    if hist["cdi_anual"].notna().any():
        fig.add_trace(go.Scatter(
            x=pd.concat([hist["data"], hist["data"][::-1]]),
            y=pd.concat([hist["carrego_nominal"], hist["cdi_anual"][::-1]]),
            fill="toself",
            fillcolor="rgba(37,99,235,0.08)",
            line=dict(width=0),
            hoverinfo="skip",
            showlegend=False,
            name="Área prêmio",
        ))

    fig.update_layout(
        height=380,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(ticksuffix="% a.a.", gridcolor="#f1f5f9", title=""),
        xaxis=dict(gridcolor="#f1f5f9", rangebreaks=[dict(bounds=["sat", "mon"])]),
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=0, r=0, t=10, b=0),
    )
    st.plotly_chart(fig, width="stretch")

st.divider()


# ─────────────────────────────────────────────────────────────────
# Barras: prêmio vs CDI
# ─────────────────────────────────────────────────────────────────
st.subheader("Prêmio vs CDI (p.p.)")

premio_series = hist["premio_vs_cdi"].dropna()

if premio_series.empty:
    st.info("Dados de CDI não disponíveis para calcular o prêmio.")
else:
    hist_premio = hist[hist["premio_vs_cdi"].notna()].copy()
    cores_barras = ["#16A34A" if v >= 0 else "#DC2626" for v in hist_premio["premio_vs_cdi"]]

    fig2 = go.Figure(go.Bar(
        x=hist_premio["data"],
        y=hist_premio["premio_vs_cdi"],
        marker_color=cores_barras,
        hovertemplate="%{y:+.2f} p.p.<extra>Prêmio vs CDI</extra>",
    ))
    fig2.add_hline(y=0, line_color="#334155", line_width=1)
    fig2.update_layout(
        height=200,
        yaxis=dict(ticksuffix=" p.p.", gridcolor="#f1f5f9", title=""),
        xaxis=dict(gridcolor="#f1f5f9", rangebreaks=[dict(bounds=["sat", "mon"])]),
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig2, width="stretch")

st.divider()


# ─────────────────────────────────────────────────────────────────
# Composição atual
# ─────────────────────────────────────────────────────────────────
st.subheader("Composição da Carteira Proxy")

if compos.empty:
    st.info("Dados de composição ainda não disponíveis.")
else:
    col_tabela, col_contrib = st.columns([1.1, 1])

    with col_tabela:
        display = compos[["data_vencimento", "taxa_indicativa", "peso", "duration", "inflacao_implicita"]].copy()
        display.columns = ["Vencimento", "Taxa Real (% a.a.)", "Peso (%)", "Duration (anos)", "Infl. Impl. (% a.a.)"]
        display["Taxa Real (% a.a.)"] = display["Taxa Real (% a.a.)"].apply(
            lambda x: f"IPCA + {x:.2f}%" if pd.notna(x) else "—"
        )
        display["Peso (%)"] = (display["Peso (%)"] * 100).round(1).apply(lambda x: f"{x:.1f}%")
        display["Duration (anos)"] = display["Duration (anos)"].apply(
            lambda x: f"{x:.2f}" if pd.notna(x) else "—"
        )
        display["Infl. Impl. (% a.a.)"] = display["Infl. Impl. (% a.a.)"].apply(
            lambda x: f"{x:.2f}%" if pd.notna(x) else "—"
        )
        st.dataframe(display, width="stretch", hide_index=True)

    with col_contrib:
        compos_plot = compos.copy()
        compos_plot["contribuicao"] = compos_plot["peso"] * compos_plot["taxa_indicativa"]
        compos_plot["label"] = compos_plot["data_vencimento"].str[:7]

        fig3 = go.Figure(go.Bar(
            x=compos_plot["label"],
            y=compos_plot["contribuicao"],
            marker_color=COLOR_CARREGO,
            text=compos_plot["contribuicao"].apply(lambda x: f"{x:.3f}%"),
            textposition="outside",
            hovertemplate="Venc: %{x}<br>Contribuição: %{y:.3f} p.p.<extra></extra>",
        ))
        fig3.update_layout(
            title=dict(text="Contribuição p/ YTM (p.p.)", font=dict(size=13)),
            height=320,
            yaxis=dict(ticksuffix="%", gridcolor="#f1f5f9", title=""),
            xaxis=dict(title="Vencimento"),
            plot_bgcolor="white",
            paper_bgcolor="white",
            margin=dict(l=0, r=0, t=40, b=0),
            showlegend=False,
        )
        st.plotly_chart(fig3, width="stretch")


# ─────────────────────────────────────────────────────────────────
# Rodapé metodológico
# ─────────────────────────────────────────────────────────────────
st.divider()
universo = "NTN-Bs com vencimento ≤ 5 anos" if indice_sel == "IMA-B 5" else "todos os NTN-Bs sem corte de prazo"
st.caption(
    f"⚠️ **Aviso metodológico:** este dashboard é um **proxy** do {indice_sel}, não o índice oficial. "
    f"Os pesos são calculados por *duration* — não refletem a carteira teórica divulgada pela ANBIMA. "
    f"Universo: {universo}. "
    "A inflação projetada usa a inflação implícita da curva (breakeven) como fonte primária. "
    "O carrego exclui marcação a mercado (variações de taxa real). "
    "Dados históricos via [pyield](https://github.com/crdcj/PYield) · "
    "ANBIMA taxas indicativas · BCB CDI."
)
