"""
===============================================================================
Castrol Global Supply Chain Planning Tool

Module:
    pipeline.py

Description:
    Orchestrates the full monthly planning run:

        Preprocessing -> Inventory Norms -> Production & Distribution
        Optimization -> Cost Allocation -> Routing / Dispatch

    This module also contains the schema adapters needed because the
    individual modules were authored against slightly different column-naming
    conventions. Rather than rewrite each module's internals (higher risk of
    introducing new bugs), the adapters here translate between them at the
    handoff points:

        optimization.py outputs -> costing.py inputs
            "Production (kL)"          -> "Production_kL"
            "Batches (25 kL)"          -> "Batches"
            "Volume (kL)"              -> "Quantity_kL"
            "Ending Inventory (kL)"    -> "Ending Inventory"
            "Unmet Demand (kL)"        -> "Unmet Demand"
            "Amount (INR)"             -> "Amount"
            cost component names remapped to what costing.py expects

    costing.py is also missing a `create_cost_breakdown` method and never
    receives unmet-demand data in its constructor (its `allocate_remaining_
    costs` checks `hasattr(self, "unmet_demand")`, which is otherwise always
    False). Both are patched here via a thin subclass, `PatchedCostAnalyzer`,
    instead of editing costing.py directly.

    NOTE: the optimizer's cost_summary has no separate "holding cost" concept
    -- inventory carrying cost is not distinct from the hub safety-stock
    shortfall cost it already tracks. "Holding Cost" is therefore reported as
    0 in the cost breakdown; this is a modeling limitation to flag to
    reviewers, not a bug in the adapter.
===============================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from core.preprocessing import DataPreprocessor
from core.inventory import InventoryPlanner
from core.optimization import SupplyChainOptimizer
from core.costing import CostAnalyzer
from core.routing import RoutingPlanner

logger = logging.getLogger(__name__)

BOOKKEEPING_ROWS = ["__TOTAL__", "__TARGET__", "__SHORTFALL__"]

# Approximate lat/lon for the map view (Component 3 richer UX).
LOCATIONS = {
    # Plants
    "Mumbai":     (19.0760, 72.8777),
    "Ahmedabad":  (23.0225, 72.5714),
    "Kolkata":    (22.5726, 88.3639),
    # Hubs
    "MHW":        (19.9975, 73.7898),   # Mother Hub West (Nashik area, central-west)
    "MHE":        (22.5726, 88.3639),   # Mother Hub East (Kolkata)
    # CFAs
    "Guwahati":   (26.1445, 91.7362),
    "Kanpur":     (26.4499, 80.3319),
    "Haryana":    (29.0588, 76.0856),
    "Rajpura":    (30.4844, 76.5941),
    "Bhiwandi":   (19.3002, 73.0629),
    "Bangalore":  (12.9716, 77.5946),
    "Hyderabad":  (17.3850, 78.4867),
}


###############################################################################
# Patched Cost Analyzer
###############################################################################

class PatchedCostAnalyzer(CostAnalyzer):
    """CostAnalyzer with the missing create_cost_breakdown implementation."""

    def create_cost_breakdown(self):
        prod = self.production_cost_detail.copy()
        prod["Stage"] = "Production"
        prod["Amount"] = prod["Allocated Cost"]

        ph = self.plant_hub_cost_detail.copy()
        ph["Stage"] = "Plant → Hub Transport"
        ph["Amount"] = ph["Allocated Cost"]

        hc = self.hub_cfa_cost_detail.copy()
        hc["Stage"] = "Hub → CFA Transport"
        hc["Amount"] = hc["Allocated Cost"]

        self.cost_breakdown = pd.concat(
            [prod, ph, hc], ignore_index=True, sort=False
        )
        return self.cost_breakdown


###############################################################################
# Result container
###############################################################################

@dataclass
class PlanningResults:
    master: pd.DataFrame
    inventory_norms: pd.DataFrame
    hub_inventory_norms: pd.DataFrame
    solve_status: str
    production_plan: pd.DataFrame
    plant_hub_plan: pd.DataFrame
    hub_cfa_plan: pd.DataFrame
    hub_inventory_plan: pd.DataFrame
    unmet_demand: pd.DataFrame
    cost_summary: pd.DataFrame
    plant_cost_summary: pd.DataFrame
    hub_cost_summary: pd.DataFrame
    product_cost_summary: pd.DataFrame
    cost_kpis: pd.DataFrame
    routes: pd.DataFrame
    network_summary: pd.DataFrame
    total_trucks: int
    total_dispatch_volume: float
    warnings: list = field(default_factory=list)

    @property
    def is_feasible(self) -> bool:
        return self.solve_status in ("OPTIMAL", "FEASIBLE")

    @property
    def total_unmet_kl(self) -> float:
        if self.unmet_demand.empty:
            return 0.0
        return float(self.unmet_demand["Unmet Demand (kL)"].sum())

    @property
    def total_effective_demand_kl(self) -> float:
        # Effective demand = what was actually produced/delivered + unmet
        delivered = self.hub_cfa_plan["Volume (kL)"].sum() if not self.hub_cfa_plan.empty else 0.0
        return float(delivered + self.total_unmet_kl)

    @property
    def fill_rate(self) -> float:
        total = self.total_effective_demand_kl
        if total <= 0:
            return 1.0
        return 1 - (self.total_unmet_kl / total)


###############################################################################
# Adapters between optimizer output and costing.py's expected schema
###############################################################################

def _adapt_for_costing(results: dict):
    production_plan = results["production_plan"].rename(columns={
        "Production (kL)": "Production_kL",
        "Batches (25 kL)": "Batches",
    })
    plant_hub_plan = results["plant_hub_plan"].rename(columns={"Volume (kL)": "Quantity_kL"})
    hub_cfa_plan = results["hub_cfa_plan"].rename(columns={"Volume (kL)": "Quantity_kL"})

    hub_inv = results["hub_inventory"]
    if not hub_inv.empty and "Product" in hub_inv.columns:
        hub_inv = hub_inv[~hub_inv["Product"].isin(BOOKKEEPING_ROWS)]
    hub_inventory = hub_inv.rename(columns={"Ending Inventory (kL)": "Ending Inventory"})

    unmet = results["unmet_demand"]
    if unmet.empty:
        unmet = pd.DataFrame(columns=["Product", "CFA", "Unmet Demand"])
    else:
        unmet = unmet.rename(columns={"Unmet Demand (kL)": "Unmet Demand"})

    cs = results["cost_summary"].copy()
    name_map = {
        "Production Cost": "Production Cost",
        "Plant-Hub Transport Cost": "Plant-Hub Cost",
        "Hub-CFA Transport Cost": "Hub-CFA Cost",
        "Unmet-Demand Penalty Cost": "Penalty Cost",
        "Hub Safety-Stock Shortfall Cost": "Safety Stock Shortfall Cost",
        "Total Cost": "Total Cost",
    }
    cs["Cost Component"] = cs["Cost Component"].map(name_map).fillna(cs["Cost Component"])
    cs = cs.rename(columns={"Amount (INR)": "Amount"})
    if "Holding Cost" not in cs["Cost Component"].values:
        cs = pd.concat(
            [cs, pd.DataFrame([{"Cost Component": "Holding Cost", "Amount": 0.0}])],
            ignore_index=True,
        )

    return production_plan, plant_hub_plan, hub_cfa_plan, hub_inventory, unmet, cs


###############################################################################
# Main pipeline entry point
###############################################################################

def run_full_pipeline(
    excel_path: str | Path,
    time_limit_seconds: int = 60,
    truck_capacity: float = 25.0,
) -> PlanningResults:
    """
    Run the complete Levisol/Castrol monthly planning pipeline end to end.

    Parameters
    ----------
    excel_path : path to the input workbook (Data.xlsx or an edited copy).
    time_limit_seconds : MILP solver time limit. 30-60s is enough for this
        network size (3 plants x 2 hubs x 10 CFAs x 100 SKUs); raise it if a
        scenario comes back NOT_SOLVED / ABNORMAL.
    truck_capacity : kL per truckload, used for the routing/dispatch view.
    """

    warnings: list[str] = []

    # ---- Component 1: preprocessing + inventory norms -------------------
    logger.info("Stage 1/5: Preprocessing input workbook...")
    processor = DataPreprocessor(excel_path)
    master = processor.preprocess()

    logger.info("Stage 2/5: Calculating inventory norms...")
    inv_planner = InventoryPlanner(master)
    inventory_norms, hub_inventory_norms = inv_planner.run()

    # ---- Component 2: production & distribution optimization ------------
    logger.info("Stage 3/5: Solving production & distribution MILP...")
    optimizer = SupplyChainOptimizer(
        master_df=master,
        inventory_norms=inventory_norms,
        hub_inventory=hub_inventory_norms,
        data=processor.data,
    )
    opt_results = optimizer.run(time_limit_seconds=time_limit_seconds)

    solve_status = opt_results["status"]
    if solve_status not in ("OPTIMAL", "FEASIBLE"):
        warnings.append(
            f"Solver returned status '{solve_status}' -- no feasible plan was "
            f"found. This model always has slack variables (unmet demand, hub "
            f"shortfall), so this usually means a data problem (e.g. a "
            f"negative or zero capacity/cost after your edits) rather than a "
            f"genuinely infeasible network. Check the Data Inputs page."
        )
        # Return early with whatever (likely empty) frames extract_results produced,
        # so the app can render a clear "no solution" state instead of crashing.

    # ---- Cost allocation (best-effort; core cost_summary always available) -
    logger.info("Stage 4/5: Allocating costs by plant / hub / product...")
    plant_cost_summary = pd.DataFrame()
    hub_cost_summary = pd.DataFrame()
    product_cost_summary = pd.DataFrame()
    cost_kpis = pd.DataFrame()

    if solve_status in ("OPTIMAL", "FEASIBLE") and not opt_results["production_plan"].empty:
        try:
            prod_p, ph_p, hc_p, hub_inv_p, unmet_p, cs_p = _adapt_for_costing(opt_results)
            analyzer = PatchedCostAnalyzer(
                production_plan=prod_p,
                plant_hub_plan=ph_p,
                hub_cfa_plan=hc_p,
                hub_inventory=hub_inv_p,
                cost_summary=cs_p,
            )
            analyzer.unmet_demand = unmet_p
            cost_out = analyzer.run()
            plant_cost_summary = cost_out["plant_cost_summary"]
            hub_cost_summary = cost_out["hub_cost_summary"]
            product_cost_summary = cost_out["product_cost_summary"]
            cost_kpis = cost_out["cost_kpis"]
        except Exception as exc:  # pragma: no cover - defensive, surfaced in UI
            logger.exception("Cost allocation failed")
            warnings.append(
                f"Detailed plant/hub/product cost allocation could not be "
                f"computed ({exc}). The headline cost summary (production, "
                f"transport, penalty, shortfall, total) is still shown -- "
                f"only the extra breakdown views are affected."
            )

    # ---- Routing / dispatch view -----------------------------------------
    logger.info("Stage 5/5: Building routing & dispatch plan...")
    routes = pd.DataFrame()
    network_summary = pd.DataFrame()
    total_trucks = 0
    total_dispatch_volume = 0.0

    if solve_status in ("OPTIMAL", "FEASIBLE"):
        try:
            router = RoutingPlanner(
                production_plan=opt_results["production_plan"],
                plant_hub_plan=opt_results["plant_hub_plan"],
                hub_cfa_plan=opt_results["hub_cfa_plan"],
                hub_inventory=opt_results["hub_inventory"],
                cost_summary=opt_results["cost_summary"],
                truck_capacity=truck_capacity,
            )
            route_out = router.run()
            routes = route_out["routes"]
            network_summary = route_out["network_summary"]
            total_trucks = route_out["total_trucks"]
            total_dispatch_volume = route_out["total_dispatch_volume"]
        except Exception as exc:  # pragma: no cover - defensive, surfaced in UI
            logger.exception("Routing failed")
            warnings.append(f"Routing/dispatch view could not be built ({exc}).")

    return PlanningResults(
        master=master,
        inventory_norms=inventory_norms,
        hub_inventory_norms=hub_inventory_norms,
        solve_status=solve_status,
        production_plan=opt_results["production_plan"],
        plant_hub_plan=opt_results["plant_hub_plan"],
        hub_cfa_plan=opt_results["hub_cfa_plan"],
        hub_inventory_plan=opt_results["hub_inventory"],
        unmet_demand=opt_results["unmet_demand"],
        cost_summary=opt_results["cost_summary"],
        plant_cost_summary=plant_cost_summary,
        hub_cost_summary=hub_cost_summary,
        product_cost_summary=product_cost_summary,
        cost_kpis=cost_kpis,
        routes=routes,
        network_summary=network_summary,
        total_trucks=total_trucks,
        total_dispatch_volume=total_dispatch_volume,
        warnings=warnings,
    )
