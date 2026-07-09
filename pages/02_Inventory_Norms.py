import streamlit as st
import pandas as pd

st.title("📦 Inventory Norms")
st.caption("Safety stock, reorder point, and days of cover per SKU × CFA, plus hub-level buffers.")

results = st.session_state.get("results")
if results is None:
    st.info("Run the pipeline from the **Home** page first.")
    st.stop()

norms = results.inventory_norms
hub_norms = results.hub_inventory_norms

st.subheader("SKU × CFA Inventory Norms")

c1, c2, c3 = st.columns(3)
with c1:
    products = sorted(norms["product"].unique()) if "product" in norms.columns else []
    product_filter = st.multiselect("Filter by SKU", products, default=[])
with c2:
    cfas = sorted(norms["cfa"].unique()) if "cfa" in norms.columns else []
    cfa_filter = st.multiselect("Filter by CFA", cfas, default=[])
with c3:
    tiers = sorted(norms["tier"].dropna().unique()) if "tier" in norms.columns else []
    tier_filter = st.multiselect("Filter by Tier", tiers, default=[])

view = norms.copy()
if product_filter:
    view = view[view["product"].isin(product_filter)]
if cfa_filter:
    view = view[view["cfa"].isin(cfa_filter)]
if tier_filter:
    view = view[view["tier"].isin(tier_filter)]

m1, m2, m3, m4 = st.columns(4)
m1.metric("SKU × CFA combinations", f"{len(norms):,}")
m2.metric("Avg. safety stock (kL)", f"{norms['safety_stock'].mean():,.1f}" if "safety_stock" in norms else "—")
m3.metric("Avg. reorder point (kL)", f"{norms['reorder_point'].mean():,.1f}" if "reorder_point" in norms else "—")
m4.metric("Avg. days of cover", f"{norms['days_of_cover'].mean():,.1f}" if "days_of_cover" in norms else "—")

st.dataframe(view, use_container_width=True, height=420)
st.download_button(
    "⬇ Download filtered inventory norms (CSV)",
    view.to_csv(index=False).encode(),
    file_name="inventory_norms.csv",
    mime="text/csv",
)

st.divider()
st.subheader("Hub Safety Stock")
st.caption(
    "Hub buffers use a fixed 98% service level across all hubs, applied to aggregated inbound "
    "demand and lead-time variability at each hub."
)
st.dataframe(hub_norms, use_container_width=True)
st.download_button(
    "⬇ Download hub inventory norms (CSV)",
    hub_norms.to_csv(index=False).encode(),
    file_name="hub_inventory_norms.csv",
    mime="text/csv",
)

with st.expander("How these are calculated"):
    st.markdown(
        r"""
For each SKU × CFA combination:

$$
\text{Safety Stock} = z \times \sqrt{\;LT \times \sigma_{\text{demand}}^2 \;+\; \bar{d}^2 \times \sigma_{LT}^2\;}
$$

- $z$ — z-score for the SKU's tier-based target fill rate (98% / 97% / 92% / 92% for tiers A–D)
- $LT$ — total replenishment lead time (production + transit, in days)
- $\sigma_{\text{demand}}$ — forecast error standard deviation, measured from Jul–Dec 2025 actual vs.
  forecast sales
- $\bar{d}$ — average daily demand over the same six months
- $\sigma_{LT}$ — lead-time variability (production + transit variability combined)

**Reorder point** = (average daily demand × total lead time) + safety stock.
**Days of cover** = reorder point ÷ average daily demand.

Hub safety stock uses the same formula with demand and forecast-error aggregated across all CFAs
served by that hub, at a fixed 98% service level.
        """
    )
