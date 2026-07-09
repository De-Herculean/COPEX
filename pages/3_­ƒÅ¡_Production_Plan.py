import streamlit as st
import pandas as pd

from core.export import build_full_plan_workbook

st.set_page_config(page_title="Production Plan", page_icon="🏭", layout="wide")
st.title("🏭 Production & Distribution Plan — Component 2")

results = st.session_state.get("results")
if results is None:
    st.info("Run the pipeline from the **Home** page first.")
    st.stop()

status_emoji = "✅" if results.is_feasible else "⚠️"
st.markdown(f"**Solver status:** {status_emoji} `{results.solve_status}`")

if not results.is_feasible:
    st.error(
        "No feasible plan was found for this input set. The tables below may be empty. "
        "Check the Data Inputs page for a capacity, cost, or demand value that may have "
        "gone negative, zero, or otherwise invalid."
    )
    for w in results.warnings:
        st.warning(w)
    st.stop()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Fill rate", f"{results.fill_rate:.2%}")
m2.metric("Total demand (kL)", f"{results.total_effective_demand_kl:,.0f}")
m3.metric("Unmet demand (kL)", f"{results.total_unmet_kl:,.1f}")
total_cost = results.cost_summary.set_index("Cost Component").loc["Total Cost", "Amount (INR)"]
m4.metric("Total cost (₹)", f"{total_cost:,.0f}")

st.download_button(
    "⬇ Download the full plan (one Excel workbook: production, routing, cost, inventory norms)",
    build_full_plan_workbook(results),
    file_name="levisol_plan.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

tab1, tab2, tab3, tab4 = st.tabs(
    ["Production by Plant", "Plant → Hub", "Hub → CFA", "Unmet Demand"]
)

with tab1:
    st.caption("How many kL of each SKU to produce at each plant, respecting the 25 kL batch size.")
    prod = results.production_plan
    plants = sorted(prod["Plant"].unique()) if not prod.empty else []
    plant_filter = st.multiselect("Filter by plant", plants, default=[], key="prod_plant_filter")
    view = prod[prod["Plant"].isin(plant_filter)] if plant_filter else prod
    st.dataframe(view, use_container_width=True, height=400)
    st.download_button("⬇ Download production plan (CSV)", view.to_csv(index=False).encode(),
                        "production_plan.csv", "text/csv")

    if not prod.empty:
        by_plant = prod.groupby("Plant", as_index=False)["Production (kL)"].sum()
        st.bar_chart(by_plant.set_index("Plant"))

with tab2:
    st.caption("Total volume routed from each plant to each hub.")
    ph = results.plant_hub_plan
    st.dataframe(ph, use_container_width=True, height=350)
    st.download_button("⬇ Download plant→hub plan (CSV)", ph.to_csv(index=False).encode(),
                        "plant_hub_plan.csv", "text/csv")

with tab3:
    st.caption("Volume dispatched from each hub to each CFA.")
    hc = results.hub_cfa_plan
    hubs = sorted(hc["Hub"].unique()) if not hc.empty else []
    hub_filter = st.multiselect("Filter by hub", hubs, default=[], key="hc_hub_filter")
    view = hc[hc["Hub"].isin(hub_filter)] if hub_filter else hc
    st.dataframe(view, use_container_width=True, height=400)
    st.download_button("⬇ Download hub→CFA plan (CSV)", view.to_csv(index=False).encode(),
                        "hub_cfa_plan.csv", "text/csv")

with tab4:
    unmet = results.unmet_demand
    if unmet.empty:
        st.success("All demand was met in this plan — nothing to show here.")
    else:
        st.caption(
            "Every product/CFA combination the model chose not to fully supply, and why it cost "
            "what it cost. Contractual SKUs carry a materially higher penalty."
        )
        st.dataframe(unmet, use_container_width=True, height=350)
        st.download_button("⬇ Download unmet demand (CSV)", unmet.to_csv(index=False).encode(),
                            "unmet_demand.csv", "text/csv")

        contractual_unmet = unmet[unmet["Contractual"] == True] if "Contractual" in unmet.columns else pd.DataFrame()
        if not contractual_unmet.empty:
            st.warning(
                f"{len(contractual_unmet)} contractual SKU/CFA combination(s) are under-served, "
                f"totalling {contractual_unmet['Unmet Demand (kL)'].sum():,.1f} kL. These carry "
                f"financial and reputational consequences beyond the modeled penalty cost."
            )

st.divider()
st.subheader("Hub Ending Inventory vs. Safety-Stock Target")
hub_inv = results.hub_inventory_plan
if not hub_inv.empty:
    real = hub_inv[~hub_inv["Product"].isin(["__TOTAL__", "__TARGET__", "__SHORTFALL__"])]
    special = hub_inv[hub_inv["Product"].isin(["__TOTAL__", "__TARGET__", "__SHORTFALL__"])]
    special_pivot = special.pivot_table(index="Hub", columns="Product", values="Ending Inventory (kL)", aggfunc="sum").rename(
        columns={"__TOTAL__": "Actual Ending Stock (kL)", "__TARGET__": "Safety-Stock Target (kL)", "__SHORTFALL__": "Shortfall (kL)"}
    )
    st.dataframe(special_pivot, use_container_width=True)
    if (special_pivot.get("Shortfall (kL)", pd.Series(dtype=float)) > 1e-6).any():
        st.warning(
            "One or more hubs are ending the month below their safety-stock target. This is "
            "allowed (as slack) but incurs a shortfall cost — see the Cost Summary page."
        )
    with st.expander("Per-SKU ending inventory at each hub"):
        st.dataframe(real, use_container_width=True, height=300)
