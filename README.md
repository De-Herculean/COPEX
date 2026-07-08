# Levisol Supply Chain Planning Tool

A Streamlit front end over the Preprocessing → Inventory → Optimization → Costing → Routing
pipeline for the Castrol POWER UP 4.0 "Balancing Act" case.

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the URL Streamlit prints (usually `http://localhost:8501`). Click **Run Full Planning
Pipeline** on the Home page, then use the sidebar to move between Data Inputs, Inventory Norms,
Production Plan, Routing & Map, Cost Summary, and Scenario Compare.

## Deploy it so planners don't need to install anything

**Streamlit Community Cloud (free, ~2 minutes):**
1. Push this `app/` folder to a GitHub repo.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in, click "New app".
3. Point it at the repo, branch, and `app.py`.
4. Deploy — you get a public URL to hand to the planning team.

**Internal server (if data can't leave the network):** build a Docker image from this folder
(`FROM python:3.11-slim`, `pip install -r requirements.txt`, `CMD ["streamlit","run","app.py"]`)
and run it on any internal host; the workflow is identical either way.

## Project layout

```
app.py                      # Home: pipeline run button + status
core/
  preprocessing.py          # workbook loading, cleaning, master dataset (as submitted)
  inventory.py               # safety stock / reorder point / days of cover (as submitted)
  optimization.py            # OR-Tools MILP: production & distribution (as submitted)
  costing.py                 # plant/hub/product cost allocation (as submitted, see note below)
  routing.py                  # dispatch & truckload reporting (bugfixed, see note below)
  pipeline.py                 # orchestrates all five stages + schema adapters
  data_io.py                  # editable-sheet loading / workbook rebuild for Data Inputs page
pages/
  1_Data_Inputs.py            # editable demand / capacity / cost tables
  2_Inventory_Norms.py        # Component 1 deliverable
  3_Production_Plan.py        # Component 2 deliverable
  4_Routing_Map.py             # map + lane/truckload view
  5_Cost_Summary.py            # cost breakdown views
  6_Scenario_Compare.py        # save & diff two scenarios
data/Data.xlsx                # bundled default dataset
```

## Fixes applied to the original modules

- **`routing.py`**: `validate_inputs`, `initialize`, and `prepare_routing_data` were defined at
  module level instead of inside `RoutingPlanner` (an indentation slip), so the class would raise
  `AttributeError` the moment it was constructed. Re-indented as methods. Also updated the column
  names it reads (`Batches (25 kL)`, `Production (kL)`, `Volume (kL)`, `Ending Inventory (kL)`) to
  match what `optimization.py` actually outputs — the original referenced
  `Batches`/`Production_kL`/`Quantity_kL`/`"Ending Inventory"`, which don't exist in that output.
- **`costing.py`**: left as originally submitted, but it expects a different column schema than
  `optimization.py` produces (`Production_kL` vs `Production (kL)`, `Amount` vs `Amount (INR)`,
  etc.), is missing a `create_cost_breakdown` method that `run()` calls, and never receives
  unmet-demand data in its constructor. Rather than editing ~1,200 lines of allocation logic
  in place, `core/pipeline.py` adapts the schema at the handoff (`_adapt_for_costing`) and
  patches in `create_cost_breakdown` via a small subclass (`PatchedCostAnalyzer`). One real
  modeling gap surfaces honestly rather than being papered over: the optimizer has no separate
  "holding cost" line distinct from the hub safety-stock shortfall cost it already tracks, so
  that component reports ₹0 in the cost breakdown.
- If cost allocation or routing fails for any reason on a given run, the app catches it, shows a
  warning banner, and still renders the headline cost summary / production plan — it doesn't crash
  the whole page.

## Notes for the live demo

- The MILP always keeps unmet-demand and hub-shortfall slack variables, so an infeasible solver
  status almost always means a data problem (e.g. a capacity or cost that became negative or zero
  after an edit), not a genuinely infeasible network — the Home page surfaces this directly.
- Default MILP time limit is 60s; this network (3 plants × 2 hubs × 10 CFAs × 100 SKUs) typically
  solves to FEASIBLE well within that. Raise it in "Solver settings" on the Home page if a heavily
  edited scenario needs more time.
