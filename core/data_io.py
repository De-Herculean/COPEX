"""
Data I/O helpers shared across Streamlit pages.

Loads the same logical sheets that Preprocessing.DataPreprocessor detects
(plant data, transport costs, SKU portfolio, service levels, sales/forecast
history, opening inventory, January forecast) into editable DataFrames, and
rebuilds a working copy of the workbook from any edits a planner makes on the
Data Inputs page. The rebuilt workbook keeps the original sheet *names*
(Preprocessing's sheet detection matches on sheet name keywords), but drops
the decorative title row above the header -- Preprocessing's header-row
detector re-locates the header purely from column content, so this is safe
and keeps the round-trip simple.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

from core.preprocessing import DataPreprocessor

# Friendly labels for the tabs on the Data Inputs page
FRIENDLY_NAMES = {
    "plant_data": "Plant Capacities & Production Cost",
    "plant_hub_cost": "Plant → Hub Transport Cost",
    "hub_cfa_cost": "Hub → CFA Transport Cost",
    "sku_master": "SKU Portfolio (Penalty & Contractual)",
    "service_levels": "Service Level Targets by Tier",
    "lead_time": "Sourcing & Lead-Time Matrix",
    "sales": "Sales History (Jul–Dec 2025)",
    "forecast": "Forecast History (Jul–Dec 2025)",
    "opening_inventory": "Opening Inventory (Jan-26)",
    "jan_forecast": "January 2026 Forecast",
}

# Order to display tabs in -- smallest / most-edited tables first
DISPLAY_ORDER = [
    "plant_data", "plant_hub_cost", "hub_cfa_cost", "service_levels",
    "sku_master", "jan_forecast", "opening_inventory",
    "lead_time", "sales", "forecast",
]


def clean_for_editor(df: pd.DataFrame) -> pd.DataFrame:
    """Drop decorative footnote / spacer rows (e.g. 'Production runs in
    batches of 25 kL...') that sometimes trail the real data in these
    exhibit-style sheets, so the editor only shows real records. Preprocessing
    itself already tolerates these rows, so this is purely cosmetic."""

    if df.empty:
        return df
    threshold = max(2, len(df.columns) // 2)
    mask = df.notna().sum(axis=1) >= threshold
    return df[mask].reset_index(drop=True)


def load_editable_sheets(excel_path: str | Path) -> Dict[str, pd.DataFrame]:
    """Load every sheet Preprocessing knows how to detect into a dict of
    logical_name -> DataFrame, using the exact same header-detection and
    column-cleaning logic Preprocessing itself uses, so edits stay compatible."""

    processor = DataPreprocessor(excel_path)
    processor.open_workbook()
    processor.detect_sheets()
    processor.load_sheets()
    sheets = {name: clean_for_editor(df.copy()) for name, df in processor.data.items()}
    return sheets, processor.sheet_map


def write_workbook(sheets: Dict[str, pd.DataFrame], sheet_map: Dict[str, str], out_path: str | Path) -> Path:
    """Write edited sheets back out to a fresh workbook, keeping original
    sheet names so Preprocessing's keyword-based sheet detection still works."""

    out_path = Path(out_path)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for logical_name, df in sheets.items():
            sheet_name = sheet_map.get(logical_name, logical_name)[:31]  # Excel sheet name limit
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    return out_path
