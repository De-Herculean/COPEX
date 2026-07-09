import streamlit as st
import plotly.graph_objects as go

from core.pipeline import LOCATIONS

st.title("🗺️ Routing & Dispatch")
st.caption("Plant → Hub → CFA flows for the current plan, with truckload counts by lane.")

results = st.session_state.get("results")
if results is None:
    st.info("Run the pipeline from the **Home** page first.")
    st.stop()

if not results.is_feasible or results.routes.empty:
    st.warning("No routes to show — the last run did not produce a feasible plan.")
    st.stop()

m1, m2 = st.columns(2)
m1.metric("Total dispatch volume (kL)", f"{results.total_dispatch_volume:,.0f}")
m2.metric("Total truckloads", f"{results.total_trucks:,}")

st.subheader("Network Map")

network = results.network_summary
missing_locations = [
    loc for loc in set(network["From"]).union(network["To"]) if loc not in LOCATIONS
]
if missing_locations:
    st.caption(f"(No map coordinates for: {', '.join(missing_locations)} — shown in tables only.)")

fig = go.Figure()

# Draw lane lines, scaled by volume
max_vol = network["Volume (kL)"].max() if not network.empty else 1
for _, row in network.iterrows():
    frm, to = row["From"], row["To"]
    if frm not in LOCATIONS or to not in LOCATIONS:
        continue
    lat0, lon0 = LOCATIONS[frm]
    lat1, lon1 = LOCATIONS[to]
    width = 1 + 6 * (row["Volume (kL)"] / max_vol) if max_vol > 0 else 1
    color = "#00693C" if row["Stage"] == "Plant→Hub" else "#F2A900"
    fig.add_trace(go.Scattergeo(
        lat=[lat0, lat1], lon=[lon0, lon1],
        mode="lines",
        line=dict(width=width, color=color),
        opacity=0.7,
        hoverinfo="text",
        text=f"{frm} → {to}<br>{row['Volume (kL)']:.0f} kL · {int(row['Truckloads'])} trucks",
        showlegend=False,
    ))

# Draw nodes
node_names = list(LOCATIONS.keys())
fig.add_trace(go.Scattergeo(
    lat=[LOCATIONS[n][0] for n in node_names],
    lon=[LOCATIONS[n][1] for n in node_names],
    text=node_names,
    mode="markers+text",
    textposition="top center",
    marker=dict(size=9, color="#1A1A1A"),
    showlegend=False,
))

fig.update_geos(
    scope="asia",
    center=dict(lat=22, lon=80),
    projection_scale=4.2,
    showland=True, landcolor="#F2F5F3",
    showcountries=True, countrycolor="#CCCCCC",
    showcoastlines=True, coastlinecolor="#CCCCCC",
)
fig.update_layout(height=560, margin=dict(l=0, r=0, t=10, b=0))

st.plotly_chart(fig, use_container_width=True)
st.caption("🟢 Plant → Hub    🟡 Hub → CFA   (line thickness ∝ volume)")

st.divider()
st.subheader("Lane Summary")
st.dataframe(
    network.rename(columns={"Volume (kL)": "Volume (kL)", "Truckloads": "Truckloads"}),
    use_container_width=True,
)
st.download_button(
    "⬇ Download lane summary (CSV)",
    network.to_csv(index=False).encode(),
    "network_summary.csv",
    "text/csv",
)

with st.expander("Full route-level detail (by SKU)"):
    st.dataframe(results.routes, use_container_width=True, height=400)
    st.download_button(
        "⬇ Download full routing detail (CSV)",
        results.routes.to_csv(index=False).encode(),
        "routing_detail.csv",
        "text/csv",
    )
