"""FX Rate Monitor: Streamlit dashboard. Runs fully offline from the bundled snapshot (data/snapshot.json)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fxmon import DISCLAIMER
from fxmon.alerts import evaluate, load_config
from fxmon.analytics import daily_pct, movers, pair, rolling_vol, zscore_moves
from fxmon.convert import invoice_value, scenarios
from fxmon.providers import SnapshotProvider
from fxmon.store import Store

st.set_page_config(page_title="FX Rate Monitor", layout="wide")

# dataviz: categorical slots 1-4 (validated light/dark); ink/grid tokens per theme
PAL = {"light": {"series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"], "ink": "#0b0b0b", "muted": "#52514e", "grid": "#e7e6e1"},
       "dark": {"series": ["#3987e5", "#d95926", "#199e70", "#c98500"], "ink": "#f0efec", "muted": "#c3c2b7", "grid": "#2e2e2b"}}
st.markdown("""<style>
.block-container{padding-top:2rem;max-width:1180px}
h1{font-size:1.9rem!important;letter-spacing:-.01em} h2,h3{font-size:1.15rem!important;font-weight:650!important}
.fx-note{margin-bottom:1.1rem;border-left:4px solid #eda100;padding:.55rem .9rem;border-radius:6px;background:rgba(237,161,0,.10);font-size:.88rem}
.fx-big{font-size:2.1rem;font-weight:700;line-height:1.1} .fx-sub{opacity:.75;font-size:.9rem}
</style>""", unsafe_allow_html=True)


@st.cache_resource(show_spinner="Loading bundled snapshot...")
def load():
    snap = SnapshotProvider("data/snapshot.json").load()
    store = Store()
    store.upsert(snap)
    return snap, store.wide(snap.base), load_config("config.yaml")


snap, wide, cfg = load()
base = snap.base
theme = "dark" if getattr(getattr(st.context, "theme", None), "type", "light") == "dark" else "light"
C = PAL[theme]


def style(fig, h=360, legend=True):
    fig.update_layout(height=h, margin=dict(l=0, r=8, t=8, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=C["muted"], size=12), hovermode="x unified", showlegend=legend,
                      legend=dict(orientation="h", y=-0.18, font=dict(color=C["ink"])))
    fig.update_xaxes(showgrid=False, linecolor=C["grid"], tickfont=dict(color=C["muted"]))
    fig.update_yaxes(gridcolor=C["grid"], zeroline=False, tickfont=dict(color=C["muted"]))
    return fig


st.title("FX Rate Monitor")
st.caption(f"Data as of **{snap.last_date}** (latest ECB reference date) · snapshot fetched {snap.fetched_at:%Y-%m-%d %H:%M} UTC · source: {snap.source}")
st.markdown(f'<div class="fx-note">{DISCLAIMER}</div>', unsafe_allow_html=True)

ccys = [c for c in wide.columns]
c1, c2, c3 = st.columns([1, 1, 2])
b = c1.selectbox("Base", ccys, index=ccys.index("EUR"))
q = c2.selectbox("Quote", [c for c in ccys if c != b], index=[c for c in ccys if c != b].index("USD") if b != "USD" else 0)
span = c3.radio("Range", ["3M", "6M", "1Y", "All"], index=2, horizontal=True)
s = pair(wide, b, q)
n = {"3M": 63, "6M": 126, "1Y": 252, "All": len(s)}[span]
view = s.iloc[-n:]
z, vol, chg = zscore_moves(s), rolling_vol(s), daily_pct(s)

k = st.columns(4)
k[0].metric(f"{b}/{q}", f"{s.iloc[-1]:.4f}", f"{chg.iloc[-1]:+.2f}% vs prev. day", delta_color="off")
k[1].metric("30-day change", f"{(s.iloc[-1] / s.iloc[-31] - 1) * 100:+.2f}%")
k[2].metric("20-day volatility (ann.)", f"{vol.iloc[-1]:.1f}%")
k[3].metric("Latest move vs trailing 60d", f"z = {z.iloc[-1]:+.1f}", "unusual" if abs(z.iloc[-1]) >= cfg.get("zscore", 2.5) else "normal", delta_color="off", delta_arrow="off")

tab1, tab2, tab3 = st.tabs(["Rate", "Compare (indexed)", "Volatility"])
with tab1:
    fig = go.Figure(go.Scatter(x=view.index, y=view, mode="lines", name=f"{b}/{q}", line=dict(color=C["series"][0], width=2),
                               hovertemplate="%{y:.4f}"))
    zz = z.reindex(view.index)
    unusual = view[zz.abs() >= cfg.get("zscore", 2.5)]
    if len(unusual):
        fig.add_trace(go.Scatter(x=unusual.index, y=unusual, mode="markers", name=f"Unusual move (|z| ≥ {cfg.get('zscore', 2.5)})",
                                 marker=dict(color=C["series"][1], size=9, line=dict(color="rgba(0,0,0,0)", width=0), symbol="diamond"),
                                 hovertemplate="unusual move<extra></extra>"))
    st.plotly_chart(style(fig, legend=len(unusual) > 0), width="stretch")
with tab2:
    others = [c for c in ccys if c not in (b,)]
    pick = st.multiselect("Currencies (max 4)", others, default=[c for c in ["USD", "GBP", "CHF"] if c in others and c != b][:3], max_selections=4)
    fig = go.Figure()
    for i, c in enumerate(pick):
        ser = pair(wide, b, c).iloc[-n:]
        ser = ser / ser.iloc[0] * 100
        fig.add_trace(go.Scatter(x=ser.index, y=ser, mode="lines", name=f"{b}/{c}", line=dict(color=C["series"][i], width=2), hovertemplate="%{y:.1f}"))
        fig.add_annotation(x=ser.index[-1], y=ser.iloc[-1], text=c, showarrow=False, xanchor="left", xshift=6, font=dict(color=C["ink"], size=12))
    fig.update_yaxes(title_text=f"{b} = 100 at start of range")
    st.plotly_chart(style(fig, legend=len(pick) > 1), width="stretch")
    st.caption("Indexed so different-scale pairs share one axis. Line colour identifies the currency; labels at the line ends.")
with tab3:
    v = vol.iloc[-n:].dropna()
    fig = go.Figure(go.Scatter(x=v.index, y=v, mode="lines", name="20-day volatility (annualised %)", line=dict(color=C["series"][2], width=2),
                               hovertemplate="%{y:.1f}%"))
    st.plotly_chart(style(fig, legend=False), width="stretch")
    st.caption(f"Rolling 20-day standard deviation of daily log returns of {b}/{q}, annualised (×√252).")
with st.expander("Table view (last 15 days)"):
    t = pd.DataFrame({"rate": s, "1d %": chg, "z": z, "vol 20d %": vol}).tail(15).iloc[::-1]
    t.index = t.index.date
    st.dataframe(t.style.format("{:.4f}", subset=["rate"]).format("{:+.2f}", subset=["1d %", "z"]).format("{:.1f}", subset=["vol 20d %"]), width="stretch")

st.subheader("What is my invoice worth?")
i1, i2, i3 = st.columns(3)
amount = i1.number_input("Invoice amount", min_value=0.0, value=5000.0, step=500.0)
inv = i2.selectbox("Invoice currency", ccys, index=ccys.index("USD"))
home = i3.selectbox("My home currency", [c for c in ccys if c != inv], index=[c for c in ccys if c != inv].index("EUR") if inv != "EUR" else 0)
val, rate = invoice_value(wide, amount, inv, home)
st.markdown(f'<div class="fx-big">{val:,.2f} {home}</div><div class="fx-sub">{amount:,.2f} {inv} at {rate:.4f} {home}/{inv} '
            f'(ECB reference rate of {snap.last_date}; a bank or payment provider will quote a different, worse rate)</div>', unsafe_allow_html=True)
sc = scenarios(wide, amount, inv, home)
st.dataframe(sc.style.format({"rate": "{:.4f}", "value": "{:,.2f}", "vs today": "{:+,.2f}"}), hide_index=True, width="stretch",
             column_config={"value": f"value ({home})", "vs today": f"vs today ({home})"})
st.caption("Scenarios are illustrative: fixed rate shocks and the 5th/50th/95th percentile of this pair's own historical 30-day moves. Not a forecast.")

st.subheader("Alerts on the latest data")
al = evaluate(wide, cfg)
if al:
    for a in al:
        st.markdown(f"- **{a.kind.replace('_', ' ')}**: {a.message}")
else:
    st.success("No rule triggered on the latest reference date.")
st.caption("Rules (config.yaml): rate crosses a threshold, new 30-day high/low, unusual daily move (|z| ≥ 2.5). In production these go to a Slack/Discord webhook; here delivery is mocked.")

st.subheader("All currencies vs " + b)
m = movers(wide, b)
st.dataframe(m.style.format({"rate": "{:.4f}", "1d %": "{:+.2f}", "30d %": "{:+.2f}", "vol 20d %": "{:.1f}", "z (1d move)": "{:+.1f}", "30d range pos": "{:.0%}"}),
             width="stretch")
st.caption("30d range pos: 0% = at the 30-day low, 100% = at the 30-day high. " + DISCLAIMER)
