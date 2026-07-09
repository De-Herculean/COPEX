import tempfile
import time
import traceback
from pathlib import Path

import streamlit as st

from core.data_io import load_editable_sheets, write_workbook, FRIENDLY_NAMES, DISPLAY_ORDER
from core.pipeline import run_full_pipeline

WORKDIR = Path(tempfile.gettempdir()) / "levisol_planner"
WORKDIR.mkdir(exist_ok=True)

st.title("📊 Data Inputs")
st.caption(
    "Every number the monthly plan depends on lives here: demand, plant capacities, "
    "production costs, and transport costs. Edit a cell, then re-run — no code required. "
    "Rows are fixed (no add/delete) because these tables are linked across sheets by "
    "Product/CFA — adding a SKU here without matching rows elsewhere would silently break "
    "the join. To add or retire a SKU/CFA, edit the source workbook directly and re-upload it "
    "from the Home page."
)

workbook_path = st.session_state.get("workbook_path")
if not workbook_path:
    st.warning("Go to the Home page first to select a dataset.")
    st.stop()

if "edited_sheets" not in st.session_state or st.session_state.get("edited_sheets_source") != workbook_path:
    with st.spinner("Loading workbook..."):
        sheets, sheet_map = load_editable_sheets(workbook_path)
    st.session_state["edited_sheets"] = sheets
    st.session_state["sheet_map"] = sheet_map
    st.session_state["edited_sheets_source"] = workbook_path

sheets = st.session_state["edited_sheets"]
sheet_map = st.session_state["sheet_map"]

st.subheader("Hub safety-stock requirement")
st.session_state.setdefault("hub_service_level", 0.98)
st.session_state["hub_service_level"] = st.slider(
    "Target service level for hub safety stock (%)",
    80, 99, int(st.session_state["hub_service_level"] * 100),
    help="Applied to both Mother Hub West and Mother Hub East when sizing the hub safety-stock "
         "buffer. This is a model parameter (not a workbook cell), included here alongside the "
         "other editable inputs since it's one of the five levers Component 3 calls out.",
) / 100

st.divider()

tabs = st.tabs([FRIENDLY_NAMES.get(k, k) for k in DISPLAY_ORDER if k in sheets])

for tab, key in zip(tabs, [k for k in DISPLAY_ORDER if k in sheets]):
    with tab:
        n_rows = len(sheets[key])
        st.caption(f"{n_rows:,} row(s). Edit any cell directly in the table.")
        edited = st.data_editor(
            sheets[key],
            use_container_width=True,
            num_rows="fixed",
            key=f"editor_{key}",
            height=min(70 + 35 * n_rows, 500) if n_rows else 100,
        )
        sheets[key] = edited

st.session_state["edited_sheets"] = sheets

st.divider()

col1, col2, col3 = st.columns([1, 1, 2])

with col1:
    reset_clicked = st.button("↩ Reset to last loaded values", use_container_width=True)
with col2:
    apply_clicked = st.button("✅ Apply Changes & Re-run Plan", type="primary", use_container_width=True)

if reset_clicked:
    for key in ["edited_sheets", "sheet_map", "edited_sheets_source"]:
        st.session_state.pop(key, None)
    st.rerun()

if apply_clicked:
    progress = st.progress(0, text="Rebuilding workbook from your edits...")
    start = time.time()
    try:
        out_path = WORKDIR / f"edited_scenario_{int(time.time())}.xlsx"
        write_workbook(sheets, sheet_map, out_path)

        progress.progress(30, text="Re-running the full pipeline with your edits...")
        results = run_full_pipeline(
            out_path,
            time_limit_seconds=st.session_state.get("time_limit_seconds", 60),
            truck_capacity=st.session_state.get("truck_capacity", 25.0),
            hub_service_level=st.session_state.get("hub_service_level", 0.98),
        )
        st.session_state["results"] = results
        st.session_state["workbook_path"] = str(out_path)
        st.session_state["last_run_seconds"] = time.time() - start
        progress.progress(100, text="Done.")

        if results.is_feasible:
            st.success(
                f"Re-run complete — {results.solve_status}, fill rate {results.fill_rate:.2%}. "
                f"Check **Production Plan**, **Cost Summary**, or **Scenario Compare** to see the impact."
            )
        else:
            st.error(f"Solver status: {results.solve_status}. See warnings below.")
        for w in results.warnings:
            st.warning(w)

    except Exception as exc:
        progress.empty()
        st.error(f"Could not apply changes: {exc}")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())

with st.expander("What's editable here vs. what drives what"):
    st.markdown(
        """
- **Plant Capacities & Production Cost** and the two **Transport Cost** tabs are exactly the levers
  called out for the live demo ("a change in one or more plant capacities... a shift in SKU demand...
  a change in transport cost").
- **January 2026 Forecast** is the demand the production/distribution plan is solved against.
- **Sales History** / **Forecast History** (Jul–Dec 2025) drive the *inventory norms* (Component 1) —
  demand variability is measured from the forecast-error standard deviation over these six months.
- **SKU Portfolio** carries the per-SKU penalty cost and contractual flag used when the model has to
  choose what to under-serve.
- **Service Level Targets** map SKU tiers (A/B/C/D) to fill-rate targets, which drive the z-scores in
  the safety-stock formula.
        """
    )
