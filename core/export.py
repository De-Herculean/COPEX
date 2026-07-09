"""
Consolidated export: bundles every output table (inventory norms, production
plan, routing, cost summary) into a single multi-sheet Excel workbook, so a
planner has one file to save/email/act on instead of hunting across pages for
individual CSV downloads.
"""

from __future__ import annotations

from io import BytesIO

import pandas as pd


def build_full_plan_workbook(results) -> bytes:
    """Return the bytes of an .xlsx workbook summarizing a PlanningResults."""

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        summary_rows = [
            {"Metric": "Solver status", "Value": results.solve_status},
            {"Metric": "Fill rate", "Value": f"{results.fill_rate:.2%}"},
            {"Metric": "Total demand (kL)", "Value": f"{results.total_effective_demand_kl:,.1f}"},
            {"Metric": "Unmet demand (kL)", "Value": f"{results.total_unmet_kl:,.1f}"},
            {"Metric": "Hub safety-stock service level", "Value": f"{results.hub_service_level:.0%}"},
            {"Metric": "Total trucks", "Value": results.total_trucks},
        ]
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Summary", index=False)

        results.cost_summary.to_excel(writer, sheet_name="Cost Summary", index=False)
        results.inventory_norms.to_excel(writer, sheet_name="Inventory Norms (SKU x CFA)", index=False)
        results.hub_inventory_norms.to_excel(writer, sheet_name="Hub Inventory Norms", index=False)
        results.production_plan.to_excel(writer, sheet_name="Production Plan", index=False)
        results.plant_hub_plan.to_excel(writer, sheet_name="Plant to Hub Plan", index=False)
        results.hub_cfa_plan.to_excel(writer, sheet_name="Hub to CFA Plan", index=False)

        unmet = results.unmet_demand
        (unmet if not unmet.empty else pd.DataFrame(columns=["Product", "CFA", "Unmet Demand (kL)"])) \
            .to_excel(writer, sheet_name="Unmet Demand", index=False)

        if not results.network_summary.empty:
            results.network_summary.to_excel(writer, sheet_name="Lane Summary", index=False)
        if not results.routes.empty:
            results.routes.to_excel(writer, sheet_name="Routing Detail", index=False)
        if not results.plant_cost_summary.empty:
            results.plant_cost_summary.to_excel(writer, sheet_name="Cost by Plant", index=False)
        if not results.hub_cost_summary.empty:
            results.hub_cost_summary.to_excel(writer, sheet_name="Cost by Hub", index=False)
        if not results.product_cost_summary.empty:
            results.product_cost_summary.to_excel(writer, sheet_name="Cost by Product", index=False)

    return buffer.getvalue()
