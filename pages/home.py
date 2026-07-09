"""
Levisol Supply Chain Planning Tool
Castrol POWER UP 4.0 — "Balancing Act" case submission

Home page: case context + the single button that runs the full pipeline
(Preprocessing -> Inventory Norms -> Production & Distribution Optimization
-> Cost Allocation -> Routing) and stores the result in session_state for
every other page to read.
"""

import logging
import shutil
import tempfile
import time
import traceback
from pathlib import Path

import streamlit as st

from core.pipeline import run_full_pipeline
from core.export import build_full_plan_workbook

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

DEFAULT_DATA_PATH = Path(__file__).parent.parent / "data" / "Data.xlsx"
WORKDIR = Path(tempfile.gettempdir()) / "levisol_planner"
WORKDIR.mkdir(exist_ok=True)


def _init_state():
    st.session_state.setdefault("workbook_path", str(DEFAULT_DATA_PATH))
    st.session_state.setdefault("results", None)
    st.session_state.setdefault("last_run_seconds", None)
    st.session_state.setdefault("scenarios", {})
    st.session_state.setdefault("time_limit_seconds", 60)
    st.session_state.setdefault("truck_capacity", 25.0)
    st.session_state.setdefault("hub_service_level", 0.98)


_init_state()

st.title("📦 Levisol Supply Chain Planning Tool")
st.caption("Castrol POWER UP 4.0 · \"Balancing Act\" — Optimizing Production and Distribution")

st.markdown(
    """
Levisol produces at **3 plants**, buffers stock at **2 regional hubs**, and dispatches to **10 CFA
warehouses** across India. This tool redesigns inventory norms, solves the monthly production &
distribution plan, and shows the full cost and routing picture — for a planner, not a data scientist.

**Use the sidebar to navigate once you've run a plan:**
1. **Data Inputs** — review or edit demand, capacities, and costs
2. **Inventory Norms** — safety stock, reorder point, days of cover per SKU × CFA *(Component 1)*
3. **Production Plan** — what to make where, and what (if anything) goes unmet *(Component 2)*
4. **Routing & Map** — plant→hub→CFA dispatch and truckloads
5. **Cost Summary** — production / transport / penalty cost breakdown
6. **Scenario Compare** — save and diff two planning scenarios
    """
)

st.divider()

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Run the plan")

    uploaded = st.file_uploader(
        "Optionally upload a different workbook (same sheet layout as Data.xlsx). "
        "Otherwise the current working dataset is used.",
        type=["xlsx"],
    )
    if uploaded is not None:
        saved_path = WORKDIR / "uploaded_data.xlsx"
        with open(saved_path, "wb") as f:
            f.write(uploaded.getbuffer())
        st.session_state["workbook_path"] = str(saved_path)
        st.success(f"Using uploaded file: {uploaded.name}")

    st.caption(f"Current dataset: `{Path(st.session_state['workbook_path']).name}`")

    with st.expander("Solver & inventory settings"):
        st.session_state["time_limit_seconds"] = st.slider(
            "MILP time limit (seconds)", 10, 180, st.session_state["time_limit_seconds"],
            help="30–60s is normally enough for this network size. Raise it if the solver "
                 "returns NOT_SOLVED / ABNORMAL on a heavily edited scenario.",
        )
        st.session_state["truck_capacity"] = st.number_input(
            "Truck capacity (kL per truckload)", value=st.session_state["truck_capacity"], step=1.0,
        )
        st.session_state["hub_service_level"] = st.slider(
            "Hub safety-stock service level (%)", 80, 99, int(st.session_state["hub_service_level"] * 100),
            help="Target service level used to size the safety-stock buffer at both Mother Hubs. "
                 "Higher = more buffer stock held at the hubs, lower cash released. This was "
                 "previously a fixed 98% in the underlying model; it's now a planning lever.",
        ) / 100

    run_clicked = st.button("▶ Run Full Planning Pipeline", type="primary", use_container_width=True)

with col2:
    st.subheader("Status")
    if st.session_state["results"] is None:
        st.info("No plan has been run yet.")
    else:
        res = st.session_state["results"]
        status_emoji = "✅" if res.is_feasible else "⚠️"
        st.metric("Solver status", f"{status_emoji} {res.solve_status}")
        st.metric("Fill rate", f"{res.fill_rate:.2%}")
        st.metric("Total cost (₹)", f"{res.cost_summary.set_index('Cost Component').loc['Total Cost', 'Amount (INR)']:,.0f}"
                   if not res.cost_summary.empty else "—")
        if st.session_state["last_run_seconds"] is not None:
            st.caption(f"Last run took {st.session_state['last_run_seconds']:.1f}s")
        if res.is_feasible:
            st.download_button(
                "⬇ Download full plan (Excel)",
                build_full_plan_workbook(res),
                file_name="levisol_plan.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="status_panel_download",
            )

if run_clicked:
    progress = st.progress(0, text="Starting pipeline...")
    start = time.time()
    try:
        progress.progress(10, text="Preprocessing workbook & calculating inventory norms...")
        results = run_full_pipeline(
            st.session_state["workbook_path"],
            time_limit_seconds=st.session_state["time_limit_seconds"],
            truck_capacity=st.session_state["truck_capacity"],
            hub_service_level=st.session_state["hub_service_level"],
        )
        progress.progress(90, text="Finalizing...")
        st.session_state["results"] = results
        st.session_state["last_run_seconds"] = time.time() - start
        progress.progress(100, text="Done.")

        if results.is_feasible:
            st.success(
                f"Plan generated — {results.solve_status}, fill rate {results.fill_rate:.2%}, "
                f"total cost ₹{results.cost_summary.set_index('Cost Component').loc['Total Cost','Amount (INR)']:,.0f}. "
                f"Open **Inventory Norms**, **Production Plan**, **Routing & Map**, or **Cost Summary** "
                f"from the sidebar."
            )
            st.download_button(
                "⬇ Download the full plan (one Excel workbook)",
                build_full_plan_workbook(results),
                file_name="levisol_plan.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        else:
            st.error(
                f"Solver status: {results.solve_status}. No feasible plan was found — see the "
                f"warnings below. This model always allows unmet demand and hub shortfalls as "
                f"slack, so this usually points to a data issue (e.g. a capacity or cost that "
                f"became negative or zero after an edit)."
            )

        for w in results.warnings:
            st.warning(w)

    except Exception as exc:
        progress.empty()
        st.error(f"The pipeline could not complete: {exc}")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())

st.divider()
with st.expander("About this tool / methodology notes"):
    st.markdown(
        """
- **Inventory norms** (safety stock, reorder point, days of cover) are computed per SKU × CFA using
  demand variability (std. dev. of forecast error over Jul–Dec 2025) and lead-time variability
  (production + transit variability), combined as
  `SS = z × √(LT × σ_demand² + demand² × σ_LT²)`, with tier-specific service levels (98/97/92/92%)
  converted to z-scores. Hub safety stock uses a fixed 98% service level across all hubs.
- **Production & distribution** is solved as a mixed-integer program (OR-Tools CBC) minimizing
  production + plant→hub transport + hub→CFA transport + unmet-demand penalty + hub safety-stock
  shortfall cost, subject to: 25 kL batch-size production, plant line capacities by pack-size band,
  hub mass-balance, and demand satisfaction with slack (unmet demand is allowed but penalized,
  more heavily for contractual SKUs).
- **Handling infeasibility**: the model always has slack variables, so a solver status other than
  OPTIMAL/FEASIBLE almost always signals a data problem, not a genuinely infeasible network — the
  app surfaces this directly rather than crashing.
- Data flows through the same modular pipeline you'd run from the command line
  (`preprocessing → inventory → optimization → costing → routing`); this app is a front end over it.
        """
    )
