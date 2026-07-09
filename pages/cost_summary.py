import streamlit as st
import pandas as pd

st.title("💰 Cost Summary")

results = st.session_state.get("results")
if results is None:
    st.info("Run the pipeline from the **Home** page first.")
    st.stop()

cs = results.cost_summary
if cs.empty:
    st.warning("No cost summary available — the last run did not produce a feasible plan.")
    st.stop()

components = cs[cs["Cost Component"] != "Total Cost"]
total_row = cs[cs["Cost Component"] == "Total Cost"]
total = total_row["Amount (INR)"].iloc[0] if not total_row.empty else components["Amount (INR)"].sum()

st.metric("Total Plan Cost", f"₹{total:,.0f}")

c1, c2 = st.columns([1, 1])
with c1:
    st.subheader("Cost Breakdown")
    st.bar_chart(components.set_index("Cost Component")["Amount (INR)"])
with c2:
    st.subheader("Share of Total")
    share = components.copy()
    share["Share"] = share["Amount (INR)"] / total if total else 0
    st.dataframe(
        share.assign(**{"Amount (INR)": share["Amount (INR)"].map(lambda x: f"₹{x:,.0f}"),
                         "Share": share["Share"].map(lambda x: f"{x:.1%}")}),
        use_container_width=True, hide_index=True,
    )

st.caption(
    "Note: 'Hub Safety-Stock Shortfall Cost' is the model's only inventory-carrying-style cost "
    "component — it does not separately model a routine holding/warehousing cost, so that line "
    "reads ₹0 in the detailed breakdown below rather than being silently omitted."
)

st.divider()

tab1, tab2, tab3 = st.tabs(["By Plant", "By Hub", "By Product"])

with tab1:
    if results.plant_cost_summary.empty:
        st.info("Plant-level cost breakdown isn't available for this run (see warnings on Home).")
    else:
        st.dataframe(results.plant_cost_summary, use_container_width=True)
        st.bar_chart(results.plant_cost_summary.set_index("Plant")[["Production_Cost", "Transport_Cost"]])

with tab2:
    if results.hub_cost_summary.empty:
        st.info("Hub-level cost breakdown isn't available for this run (see warnings on Home).")
    else:
        st.dataframe(results.hub_cost_summary, use_container_width=True)
        st.bar_chart(results.hub_cost_summary.set_index("Hub")[["Inbound Cost", "Outbound Cost"]])

with tab3:
    if results.product_cost_summary.empty:
        st.info("Product-level cost breakdown isn't available for this run (see warnings on Home).")
    else:
        top_n = st.slider("Show top N products by total cost", 5, 50, 15)
        top = results.product_cost_summary.sort_values("Total Cost", ascending=False).head(top_n)
        st.dataframe(top, use_container_width=True)
        st.bar_chart(top.set_index("Product")["Total Cost"])

if not results.cost_kpis.empty:
    st.divider()
    st.subheader("Headline KPIs")
    st.dataframe(results.cost_kpis, use_container_width=True, hide_index=True)

for w in results.warnings:
    st.warning(w)
