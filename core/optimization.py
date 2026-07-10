"""
===============================================================================
Castrol Global Supply Chain Planning Tool

Module:
    optimization.py

Description:
    Production and Distribution Optimization ("Balancing Act" case study).

    Reads the outputs of the upstream pipeline

        preprocessing.py  ->  inventory.py  ->  optimization.py

    and builds a single-period (Jan-2026) Mixed Integer Program that decides:

        1. How much of each SKU to produce at each plant (in 25 kL batches),
           respecting production-line capacity by pack-size category.
        2. How to route finished goods Plant -> Hub -> CFA at least cost.
        3. How much safety-stock buffer to retain at each hub.
        4. Which demand to leave unmet when total demand exceeds what the
           network can economically (or physically) supply, with contractual
           and tier-priority SKUs protected first.

    Part 1
    --------
    * Optimizer class
    * Workbook transformation (wide -> long cost / capacity tables)
    * Parameter dictionaries

    Part 2
    --------
    * Decision variables
    * Objective function

    Part 3
    --------
    * Constraints
        - Plant capacity by pack-size category
        - Plant/hub/CFA flow (inventory) balance
        - Demand satisfaction
        - Hub safety stock
        - 25 kL production batches

    Part 4
    --------
    * Solve
    * Extract results
    * Export outputs
    * Validation

-------------------------------------------------------------------------------
Key modelling assumptions (documented, since the case study is intentionally
open-ended and several inputs are not fully specified):

  A1. Single planning period. No opening/closing hub inventory is supplied in
      the data, so the model is a one-month snapshot: everything produced in
      the month must leave the plant, and hub stock is simply
      (inbound from plants) - (outbound to CFAs) for that month.

  A2. CFA opening inventory (Exhibit I) IS available and is netted off the
      January forecast (Exhibit J) before the demand-satisfaction constraint,
      i.e. effective_demand = max(0, Jan forecast - opening inventory).

  A3. Hub safety stock targets come straight from inventory.py's
      hub_safety_stock (98% hub service level, aggregated across all SKUs at
      each hub -- consistent with "hub service level for all grades to be
      kept at 98%" in the assignment brief). The model treats the target as
      a soft constraint: a shortfall is allowed but penalised, because
      Q3's mandate is "release cash" vs. "protect service" -- exactly the
      trade-off the CSO asked the team to quantify, not assume away.

  A4. Unmet demand carries the SKU's own penalty cost, taken directly from
      Exhibit D's "Penalty cost (per kL)" column -- no additional tier or
      contractual multiplier is applied on top of it. (An earlier version of
      this model added a tier-priority weight and a 5x contractual
      multiplier; those were removed because the case brief does not specify
      either figure, and Exhibit D's penalty cost is the only unmet-demand
      cost figure it actually gives.) Tier-based prioritization is still
      honored, but only where the case specifies it: through the
      tier-differentiated service levels (Exhibit F) used to size safety
      stock in inventory.py, not by scaling this objective's penalty term.
      Contractual SKUs are still flagged in every output (the "Contractual"
      column) so a planner can see which under-served SKUs carry that risk,
      even though it no longer changes the solver's cost calculation.

  A5. Pack size (Exhibit D, e.g. "20 X 900 ML", "1 X 210 LT") is parsed to
      the *individual container volume*, which is then bucketed into the
      plant's production-line capacity category (<=1.5 LT, 3-5 LT, 7-20 LT,
      50 LT, 180-210 LT) -- this is what actually constrains a production
      line, not the outer-case volume.

  A6. Hub and CFA names differ in casing/labels across sheets (e.g.
      "Kolkata CFA" in demand data vs. "Kolkata" in the transport-cost
      table; "Rest of India"/"East" in the lead-time sheet vs. "Mother Hub
      West (MHW)"/"Mother Hub East (MHE)" in the cost tables). All of these
      are normalised to canonical identifiers before being used as model
      keys, so the optimizer is robust to label drift in the source file.

  A7. Any plant may supply any hub, and any hub may supply any CFA (stated
      explicitly in Exhibits B and C) -- there is no hard regional
      restriction, so the routing variables are fully dense.

===============================================================================
"""

###############################################################################
# Imports
###############################################################################

import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from ortools.linear_solver import pywraplp


###############################################################################
# Logger
###############################################################################

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


###############################################################################
# Assumption-driven constants (A3 / A4 above)
###############################################################################

# Per-kL cost applied when a hub cannot hold its target safety stock.
# Documented assumption: since no explicit "cost of thin buffers" is given
# in the case data, this is set as a moderate fraction of the cheapest
# plant-to-hub-to-CFA distribution cost, so the model is willing to trade
# off a shortfall only when producing/holding the buffer is genuinely more
# expensive. Override via SupplyChainOptimizer(hub_shortfall_cost=...).
DEFAULT_HUB_SHORTFALL_COST_PER_KL = 2500.0

# Batch size mandated by the assignment (Exhibit A footnote).
BATCH_SIZE_KL = 25.0

# Canonical order of production-line capacity categories, and the liter
# thresholds used to bucket a SKU's individual container volume into one of
# them (A5 above). thresholds[i] is the *upper bound* of CAPACITY_CATEGORIES[i].
CAPACITY_CATEGORIES = [
    "Line Capacity <=1.5 LT (kl / month)",
    "Line Capacity 3- 5 LT (kl / month)",
    "Line Capacity 7- 20 LT (kl / month)",
    "Line Capacity 50 LT (kl / month)",
    "Line Capacity 180- 210LT (kl / month)",
]
CAPACITY_THRESHOLDS = [1.5, 5.0, 20.0, 50.0]  # last category = "everything above 50"


###############################################################################
# Small, standalone normalisation helpers (A5 / A6 above)
###############################################################################

def normalize_text(value) -> str:
    """Collapse whitespace / NaN into a clean, stripped string."""
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_cfa(value) -> str:
    """
    Canonicalize a CFA label so that "Kolkata CFA", "Kolkata", "KOLKATA  cfa"
    all resolve to the same key: "Kolkata".
    """
    text = normalize_text(value)
    text = re.sub(r"\s*CFA\s*$", "", text, flags=re.IGNORECASE).strip()
    return text


def normalize_hub(value) -> str:
    """
    Canonicalize a hub label to 'MHW' or 'MHE'.

    Handles every variant seen across the workbook:
        "MHW", "Mother Hub West", "To Mother Hub West (MHW)", "West",
        "Rest of India"                                    -> "MHW"
        "MHE", "Mother Hub East", "To Mother Hub East (MHE)", "East"
                                                             -> "MHE"
    """
    text = normalize_text(value).lower()
    if "mhw" in text or "west" in text or "rest of india" in text:
        return "MHW"
    if "mhe" in text or "east" in text:
        return "MHE"
    logger.warning(f"Could not normalize hub label '{value}'. Keeping as-is.")
    return normalize_text(value)


def parse_pack_liters(pack_size) -> float:
    """
    Extract the individual container volume (in liters) from a pack-size
    string such as "20 X 900 ML", "1 X 210 LT", "1 X 180 KG", "4 X 3.5 LT".

    KG is treated as liter-equivalent (density ~1 for lubricants is a
    simplifying, documented assumption -- only affects capacity-category
    bucketing, not any cost/weight calculation).
    """
    text = normalize_text(pack_size).upper()

    match = re.search(r"([\d.]+)\s*(ML|LT|L|KG)\b", text)
    if not match:
        return np.nan

    value = float(match.group(1))
    unit = match.group(2)

    if unit == "ML":
        return value / 1000.0

    return value


def bucket_pack_category(liters: float) -> str:
    """Map a container's liter volume onto its production-line category."""
    if pd.isna(liters):
        return CAPACITY_CATEGORIES[-1]

    for threshold, category in zip(CAPACITY_THRESHOLDS, CAPACITY_CATEGORIES):
        if liters <= threshold:
            return category

    return CAPACITY_CATEGORIES[-1]


def is_contractual(value) -> bool:
    """Parse the Exhibit D 'Contractual?' flag ('No' / 'YES — contractual')."""
    text = normalize_text(value).lower()
    return text.startswith("yes")


def is_placeholder_row(value) -> bool:
    """
    Detect footnote / narrative rows and the "Unknown" sentinel that
    preprocessing.py's clean_missing_values() fills into blank text cells.

    The Exhibit sheets each carry one or two trailing rows of free-text
    narrative (e.g. "Production runs in batches of 25 kl...") below the
    real data. Once missing values are filled with "Unknown", these rows
    are no longer NaN and would otherwise be mistaken for a real plant /
    hub / CFA. A real identifier in this workbook is always a short code
    or place name, so anything empty, literally "unknown", or unusually
    long (a sentence) is treated as a placeholder and dropped.
    """
    text = normalize_text(value)
    if text == "" or text.lower() == "unknown":
        return True
    if len(text) > 40:
        return True
    return False


###############################################################################
# Optimizer
###############################################################################

class SupplyChainOptimizer:

    """
    Optimizes the Levisol production and distribution network for a single
    planning month (January 2026).

    Inputs
    ------
    master_df
        Engineered dataset produced by preprocessing.py (one row per
        product x CFA, with tier, forecast, penalty, lead-time fields).

    inventory_norms
        SKU x CFA inventory norms produced by inventory.py
        (safety_stock, reorder_point, days_of_cover, ...).

    hub_inventory
        Hub-level safety-stock summary produced by inventory.py
        (source_hub, hub_safety_stock, ...).

    data
        Dictionary of raw, cleaned worksheets produced by
        DataPreprocessor.load() / .data, keyed by logical name, e.g.
        "plant_data", "plant_hub_cost", "hub_cfa_cost", "sku_master",
        "opening_inventory", "jan_forecast".

    hub_shortfall_cost
        Optional override for the assumption-driven hub safety-stock
        shortfall cost documented at the top of this module.

    Outputs (after .run())
    -----------------------
    production_plan     : SKU x Plant production quantities
    plant_hub_plan       : Plant -> Hub shipment quantities
    hub_cfa_plan         : Hub -> CFA shipment quantities
    hub_inventory_plan   : Ending safety-stock buffer retained at each hub
    unmet_plan           : Unmet demand by SKU x CFA
    cost_summary         : Total cost broken down by component
    """

    ###########################################################################
    # Constructor
    ###########################################################################

    def __init__(
        self,
        master_df: pd.DataFrame,
        inventory_norms: pd.DataFrame,
        hub_inventory: pd.DataFrame,
        data: Dict[str, pd.DataFrame],
        hub_shortfall_cost: float = DEFAULT_HUB_SHORTFALL_COST_PER_KL,
    ):

        logger.info("Initializing Supply Chain Optimizer...")

        #######################################################################
        # Store Inputs
        #######################################################################

        self.master = master_df.copy()
        self.inventory_norms = inventory_norms.copy()
        self.hub_inventory = hub_inventory.copy()
        self.data = data

        self.hub_shortfall_cost = float(hub_shortfall_cost)

        #######################################################################
        # OR-Tools Solver
        #######################################################################

        self.solver = pywraplp.Solver.CreateSolver("CBC")

        if self.solver is None:
            raise RuntimeError("Unable to initialize CBC MILP solver.")

        logger.info("CBC Solver initialized.")

        #######################################################################
        # Internal Containers (sets)
        #######################################################################

        self.products: List[str] = []
        self.plants: List[str] = []
        self.hubs: List[str] = []
        self.cfas: List[str] = []
        self.pack_categories: List[str] = list(CAPACITY_CATEGORIES)

        #######################################################################
        # Lookup Dictionaries (parameters)
        #######################################################################

        self.capacity: Dict[Tuple[str, str], float] = {}
        self.production_cost: Dict[str, float] = {}
        self.plant_hub_costs: Dict[Tuple[str, str], float] = {}
        self.hub_cfa_costs: Dict[Tuple[str, str], float] = {}

        self.demand: Dict[Tuple[str, str], float] = {}
        self.opening_inventory_cfa: Dict[Tuple[str, str], float] = {}
        self.effective_demand: Dict[Tuple[str, str], float] = {}

        self.penalty_cost: Dict[str, float] = {}
        self.contractual: Dict[str, bool] = {}
        self.tier: Dict[str, str] = {}
        self.unmet_weight: Dict[str, float] = {}

        self.hub_safety_target: Dict[str, float] = {}
        self.pack_mapping: Dict[str, str] = {}

        #######################################################################
        # Decision Variables
        #######################################################################

        self.batch_count = {}
        self.production = {}
        self.plant_hub = {}
        self.hub_cfa = {}
        self.hub_stock = {}
        self.shortfall = {}
        self.unmet = {}

        #######################################################################
        # Objective Component Expressions (populated in build_objective)
        #######################################################################

        self.production_cost_expr = None
        self.plant_hub_cost_expr = None
        self.hub_cfa_cost_expr = None
        self.penalty_cost_expr = None
        self.shortfall_cost_expr = None

        #######################################################################
        # Output DataFrames
        #######################################################################

        self.production_plan = pd.DataFrame()
        self.plant_hub_plan = pd.DataFrame()
        self.hub_cfa_plan = pd.DataFrame()
        self.hub_inventory_plan = pd.DataFrame()
        self.unmet_plan = pd.DataFrame()
        self.cost_summary = pd.DataFrame()

        #######################################################################
        # Initialize
        #######################################################################

        self.validate_inputs()

        logger.info("Optimizer initialized successfully.")

    ###########################################################################
    # Validation
    ###########################################################################

    def validate_inputs(self):

        logger.info("Validating optimizer inputs...")

        if self.master.empty:
            raise ValueError("Master dataset is empty.")

        if self.inventory_norms.empty:
            raise ValueError("Inventory norms dataframe is empty.")

        if self.hub_inventory.empty:
            raise ValueError("Hub inventory dataframe is empty.")

        required_tables = [
            "plant_data",
            "plant_hub_cost",
            "hub_cfa_cost",
            "sku_master",
            "jan_forecast",
        ]

        missing_tables = [t for t in required_tables if t not in self.data]

        if missing_tables:
            raise ValueError(f"Missing required raw tables: {missing_tables}")

        if "product" not in self.master.columns:
            raise ValueError("Master dataset missing required column 'product'.")

        logger.info("Input validation successful.")

    ###############################################################################
    # PART 1 -- Workbook Transformation / Long-Format Cost Tables
    ###############################################################################

    def transform_workbook(self):

        """
        Transform raw workbook sheets into normalized, long-format,
        optimization-ready tables. Handles the wide "matrix" layout of
        Exhibits A/B/C and normalizes CFA/hub labels (A6).
        """

        logger.info("Transforming workbook tables...")

        #######################################################################
        # Exhibit A -- Plant Production Data (capacity + production cost)
        #######################################################################

        plant_df = self.data["plant_data"].copy()

        plant_col = "Plant Code" if "Plant Code" in plant_df.columns else plant_df.columns[0]
        location_col = "Location" if "Location" in plant_df.columns else plant_df.columns[1]
        cost_col = next(
            (c for c in plant_df.columns if "production cost" in c.lower()),
            plant_df.columns[-1],
        )

        # Drop footnote / blank rows (Exhibit A has trailing narrative rows
        # that survive as "Unknown" once preprocessing.py fills blanks).
        plant_df = plant_df.dropna(subset=[plant_col, location_col])
        plant_df = plant_df[~plant_df[plant_col].apply(is_placeholder_row)]
        plant_df = plant_df[~plant_df[location_col].apply(is_placeholder_row)]

        plant_df["plant"] = plant_df[location_col].apply(normalize_text)
        plant_df["plant_code"] = plant_df[plant_col].apply(normalize_text)

        self.plant_code_to_location = dict(
            zip(plant_df["plant_code"], plant_df["plant"])
        )

        capacity_cols = [c for c in CAPACITY_CATEGORIES if c in plant_df.columns]
        if len(capacity_cols) != len(CAPACITY_CATEGORIES):
            logger.warning(
                "Some expected capacity columns were not found by exact name; "
                "falling back to positional matching for Exhibit A."
            )
            capacity_cols = list(plant_df.columns[3:8])

        capacity_long = plant_df.melt(
            id_vars=["plant"],
            value_vars=capacity_cols,
            var_name="pack_category",
            value_name="capacity",
        )
        capacity_long["capacity"] = pd.to_numeric(capacity_long["capacity"], errors="coerce")
        capacity_long = capacity_long.dropna(subset=["capacity"])

        # Re-map positional column names back to canonical category labels.
        col_rename = dict(zip(capacity_cols, CAPACITY_CATEGORIES))
        capacity_long["pack_category"] = capacity_long["pack_category"].map(col_rename)

        self.capacity_table = capacity_long

        self.plants = sorted(plant_df["plant"].unique())

        for _, row in plant_df.iterrows():
            self.production_cost[row["plant"]] = float(row[cost_col])

        logger.info(f"Capacity table       : {capacity_long.shape}")
        logger.info(f"Plants                : {self.plants}")

        #######################################################################
        # Exhibit B -- Plant -> Hub Transport Costs
        #######################################################################

        ph_df = self.data["plant_hub_cost"].copy()

        plant_col_b = "From Plant" if "From Plant" in ph_df.columns else ph_df.columns[0]
        hub_cols_b = [c for c in ph_df.columns if c != plant_col_b]

        ph_df = ph_df.dropna(subset=[plant_col_b])
        ph_df = ph_df[~ph_df[plant_col_b].apply(is_placeholder_row)]
        ph_df["plant"] = ph_df[plant_col_b].apply(normalize_text)

        plant_hub_long = ph_df.melt(
            id_vars=["plant"],
            value_vars=hub_cols_b,
            var_name="hub_raw",
            value_name="transport_cost",
        )
        plant_hub_long["transport_cost"] = pd.to_numeric(
            plant_hub_long["transport_cost"], errors="coerce"
        )
        plant_hub_long = plant_hub_long.dropna(subset=["transport_cost"])
        plant_hub_long["hub"] = plant_hub_long["hub_raw"].apply(normalize_hub)

        self.plant_hub_table = plant_hub_long
        self.hubs = sorted(plant_hub_long["hub"].unique())

        logger.info(f"Plant-Hub table      : {plant_hub_long.shape}")
        logger.info(f"Hubs                  : {self.hubs}")

        #######################################################################
        # Exhibit C -- Hub -> CFA Transport Costs
        #######################################################################

        hc_df = self.data["hub_cfa_cost"].copy()

        cfa_col = "cfa" if "cfa" in hc_df.columns else hc_df.columns[0]
        region_col = next(
            (c for c in hc_df.columns if c.lower() == "region"),
            hc_df.columns[1],
        )
        hub_cols_c = [c for c in hc_df.columns if c not in (cfa_col, region_col)]

        hc_df = hc_df.dropna(subset=[cfa_col])
        hc_df = hc_df[~hc_df[cfa_col].apply(is_placeholder_row)]
        hc_df["cfa"] = hc_df[cfa_col].apply(normalize_cfa)

        hub_cfa_long = hc_df.melt(
            id_vars=["cfa", region_col],
            value_vars=hub_cols_c,
            var_name="hub_raw",
            value_name="transport_cost",
        )
        hub_cfa_long["transport_cost"] = pd.to_numeric(
            hub_cfa_long["transport_cost"], errors="coerce"
        )
        hub_cfa_long = hub_cfa_long.dropna(subset=["transport_cost"])
        hub_cfa_long["hub"] = hub_cfa_long["hub_raw"].apply(normalize_hub)
        hub_cfa_long = hub_cfa_long.rename(columns={region_col: "region"})

        self.hub_cfa_table = hub_cfa_long
        self.cfas = sorted(hub_cfa_long["cfa"].unique())

        logger.info(f"Hub-CFA table        : {hub_cfa_long.shape}")
        logger.info(f"CFAs                  : {self.cfas}")

        #######################################################################
        # Products (from master, since that is where demand actually lives)
        #######################################################################

        self.products = sorted(self.master["product"].dropna().unique())

        #######################################################################
        # Cross-check CFAs present in master vs. transport table
        #######################################################################

        master_cfas = set(self.master["cfa"].apply(normalize_cfa).unique())
        missing_from_costs = master_cfas - set(self.cfas)
        if missing_from_costs:
            logger.warning(
                f"CFAs present in demand data but missing hub transport cost: "
                f"{missing_from_costs}. These will be excluded from the model."
            )
        self.cfas = sorted(master_cfas & set(self.cfas))

        logger.info("=" * 70)
        logger.info("Workbook transformation completed.")
        logger.info(f"Plants          : {len(self.plants)}")
        logger.info(f"Hubs            : {len(self.hubs)}")
        logger.info(f"CFAs            : {len(self.cfas)}")
        logger.info(f"Products        : {len(self.products)}")
        logger.info(f"Pack Categories : {len(self.pack_categories)}")
        logger.info("=" * 70)

    ###############################################################################
    # PART 1 -- Parameter Preparation
    ###############################################################################

    def prepare_parameters(self):

        """
        Build every lookup dictionary the MILP needs, straight from the
        long-format tables created in transform_workbook() plus the raw
        Exhibit sheets (used directly where possible, to sidestep any
        ambiguity introduced further upstream by merges in preprocessing.py).
        """

        logger.info("Preparing optimization parameters...")

        #######################################################################
        # Capacity Dictionary : (plant, pack_category) -> capacity (kL)
        #######################################################################

        self.capacity = {
            (row["plant"], row["pack_category"]): float(row["capacity"])
            for _, row in self.capacity_table.iterrows()
        }

        #######################################################################
        # Plant -> Hub Costs : (plant, hub) -> cost per kL
        #######################################################################

        self.plant_hub_costs = {
            (row["plant"], row["hub"]): float(row["transport_cost"])
            for _, row in self.plant_hub_table.iterrows()
        }

        #######################################################################
        # Hub -> CFA Costs : (hub, cfa) -> cost per kL
        #######################################################################

        self.hub_cfa_costs = {
            (row["hub"], row["cfa"]): float(row["transport_cost"])
            for _, row in self.hub_cfa_table.iterrows()
        }

        #######################################################################
        # SKU Master : Pack Category, Penalty Cost, Contractual Flag
        #######################################################################

        sku_df = self.data["sku_master"].copy()

        product_col = "product" if "product" in sku_df.columns else sku_df.columns[0]
        pack_col = next(
            (c for c in sku_df.columns if "pack" in c.lower()),
            sku_df.columns[1],
        )
        penalty_col = next(
            (c for c in sku_df.columns if "penalty" in c.lower()),
            None,
        )
        contractual_col = next(
            (c for c in sku_df.columns if "contract" in c.lower()),
            None,
        )

        for _, row in sku_df.iterrows():
            product = normalize_text(row[product_col])
            if product == "":
                continue

            liters = parse_pack_liters(row[pack_col])
            self.pack_mapping[product] = bucket_pack_category(liters)

            if penalty_col is not None:
                penalty_value = pd.to_numeric(row[penalty_col], errors="coerce")
                self.penalty_cost[product] = (
                    float(penalty_value) if pd.notna(penalty_value) else 0.0
                )

            if contractual_col is not None:
                self.contractual[product] = is_contractual(row[contractual_col])

        #######################################################################
        # Tier (from master, since it is only computed after ABC analysis)
        #######################################################################

        if "tier" in self.master.columns:
            tier_df = (
                self.master[["product", "tier"]]
                .dropna()
                .drop_duplicates(subset=["product"])
            )
            self.tier = dict(zip(tier_df["product"], tier_df["tier"]))

        #######################################################################
        # Unmet-Demand Penalty Weight per Product (A4)
        #
        # Directly the SKU's Exhibit D penalty cost -- no tier or contractual
        # multiplier. (Tier prioritization is still honored through Exhibit
        # F's differentiated service levels in the inventory-norms stage;
        # contractual status is still surfaced in every output table for
        # visibility, it just doesn't scale this cost term.)
        #######################################################################

        for product in self.products:
            self.unmet_weight[product] = self.penalty_cost.get(product, 0.0)

        #######################################################################
        # January Forecast & Opening Inventory (straight from raw Exhibits,
        # to avoid any ambiguity from upstream merge/rename bugs -- A2)
        #######################################################################

        jan_df = self.data["jan_forecast"].copy()
        jan_product_col = "product" if "product" in jan_df.columns else jan_df.columns[0]
        jan_cfa_col = "cfa" if "cfa" in jan_df.columns else jan_df.columns[3]
        jan_value_col = next(
            (c for c in jan_df.columns if "26" in c or "jan" in c.lower()),
            jan_df.columns[-1],
        )

        for _, row in jan_df.iterrows():
            product = normalize_text(row[jan_product_col])
            cfa = normalize_cfa(row[jan_cfa_col])
            if product == "" or cfa == "":
                continue
            qty = pd.to_numeric(row[jan_value_col], errors="coerce")
            self.demand[(product, cfa)] = float(qty) if pd.notna(qty) else 0.0

        if "opening_inventory" in self.data:
            open_df = self.data["opening_inventory"].copy()
            open_product_col = "product" if "product" in open_df.columns else open_df.columns[0]
            open_cfa_col = "cfa" if "cfa" in open_df.columns else open_df.columns[3]
            open_value_col = next(
                (c for c in open_df.columns if "26" in c or "jan" in c.lower()),
                open_df.columns[-1],
            )

            for _, row in open_df.iterrows():
                product = normalize_text(row[open_product_col])
                cfa = normalize_cfa(row[open_cfa_col])
                if product == "" or cfa == "":
                    continue
                qty = pd.to_numeric(row[open_value_col], errors="coerce")
                self.opening_inventory_cfa[(product, cfa)] = (
                    float(qty) if pd.notna(qty) else 0.0
                )

        for key, forecast_qty in self.demand.items():
            opening_qty = self.opening_inventory_cfa.get(key, 0.0)
            self.effective_demand[key] = max(0.0, forecast_qty - opening_qty)

        #######################################################################
        # Hub Safety Stock Targets (from inventory.py output -- A3)
        #######################################################################

        hub_col = next(
            (c for c in self.hub_inventory.columns if "source" in c.lower() or "hub" in c.lower()),
            self.hub_inventory.columns[0],
        )
        target_col = next(
            (c for c in self.hub_inventory.columns if "safety" in c.lower()),
            self.hub_inventory.columns[-1],
        )

        for _, row in self.hub_inventory.iterrows():
            hub = normalize_hub(row[hub_col])
            target = pd.to_numeric(row[target_col], errors="coerce")
            self.hub_safety_target[hub] = float(target) if pd.notna(target) else 0.0

        for hub in self.hubs:
            self.hub_safety_target.setdefault(hub, 0.0)

        #######################################################################
        # Summary
        #######################################################################

        logger.info("=" * 70)
        logger.info("Optimization Parameters Loaded")
        logger.info(f"Capacity Keys        : {len(self.capacity)}")
        logger.info(f"Production Costs     : {len(self.production_cost)}")
        logger.info(f"Plant-Hub Costs      : {len(self.plant_hub_costs)}")
        logger.info(f"Hub-CFA Costs        : {len(self.hub_cfa_costs)}")
        logger.info(f"Demand Records       : {len(self.demand)}")
        logger.info(f"Effective Demand     : {len(self.effective_demand)}")
        logger.info(f"Penalty Records      : {len(self.penalty_cost)}")
        logger.info(f"Contractual SKUs     : {sum(self.contractual.values())}")
        logger.info(f"Pack Mapping         : {len(self.pack_mapping)}")
        logger.info(f"Hub Safety Targets   : {self.hub_safety_target}")
        logger.info("=" * 70)

    ###############################################################################
    # PART 2 -- Decision Variables
    ###############################################################################

    def create_decision_variables(self):

        """
        Create all optimization decision variables.

        Variables
        ---------
        batch_count  : Integer number of 25 kL batches, per (plant, product)
        production   : Continuous production quantity (== 25 * batch_count)
        plant_hub    : Continuous shipment, Plant -> Hub, per product
        hub_cfa      : Continuous shipment, Hub -> CFA, per product
        hub_stock    : Continuous ending safety-stock buffer, per (hub, product)
        unmet        : Continuous unmet demand, per (product, CFA)
        shortfall    : Continuous hub safety-stock shortfall, per hub
        """

        logger.info("=" * 70)
        logger.info("Creating Decision Variables...")
        logger.info("=" * 70)

        solver = self.solver

        #######################################################################
        # Batch / Production Variables
        #######################################################################

        for plant in self.plants:
            for product in self.products:
                self.batch_count[(plant, product)] = solver.IntVar(
                    0, solver.infinity(), f"batch_{plant}_{product}"
                )
                self.production[(plant, product)] = solver.NumVar(
                    0, solver.infinity(), f"prod_{plant}_{product}"
                )

        #######################################################################
        # Plant -> Hub Variables
        #######################################################################

        for plant in self.plants:
            for hub in self.hubs:
                for product in self.products:
                    self.plant_hub[(plant, hub, product)] = solver.NumVar(
                        0, solver.infinity(), f"ph_{plant}_{hub}_{product}"
                    )

        #######################################################################
        # Hub -> CFA Variables
        #######################################################################

        for hub in self.hubs:
            for cfa in self.cfas:
                for product in self.products:
                    self.hub_cfa[(hub, cfa, product)] = solver.NumVar(
                        0, solver.infinity(), f"hc_{hub}_{cfa}_{product}"
                    )

        #######################################################################
        # Hub Ending Stock Variables
        #######################################################################

        for hub in self.hubs:
            for product in self.products:
                self.hub_stock[(hub, product)] = solver.NumVar(
                    0, solver.infinity(), f"stock_{hub}_{product}"
                )
            self.shortfall[hub] = solver.NumVar(
                0, solver.infinity(), f"shortfall_{hub}"
            )

        #######################################################################
        # Unmet Demand Variables
        #######################################################################

        for product in self.products:
            for cfa in self.cfas:
                self.unmet[(product, cfa)] = solver.NumVar(
                    0, solver.infinity(), f"unmet_{product}_{cfa}"
                )

        logger.info(f"Total variables created : {solver.NumVariables()}")

    ###############################################################################
    # PART 2 -- Objective Function
    ###############################################################################

    def build_objective(self):

        """
        Objective: minimise total network cost.

            Total Cost =
                Production Cost
              + Plant -> Hub Transport Cost
              + Hub -> CFA Transport Cost
              + Unmet-Demand Penalty Cost (tier + contractual weighted, A4)
              + Hub Safety-Stock Shortfall Cost (A3)
        """

        logger.info("Building objective function...")

        solver = self.solver

        #######################################################################
        # Production Cost
        #######################################################################

        production_terms = []
        for plant in self.plants:
            unit_cost = self.production_cost.get(plant, 0.0)
            for product in self.products:
                production_terms.append(
                    unit_cost * self.production[(plant, product)]
                )
        self.production_cost_expr = solver.Sum(production_terms)

        #######################################################################
        # Plant -> Hub Transport Cost
        #######################################################################

        plant_hub_terms = []
        for plant in self.plants:
            for hub in self.hubs:
                unit_cost = self.plant_hub_costs.get((plant, hub))
                if unit_cost is None:
                    continue
                for product in self.products:
                    plant_hub_terms.append(
                        unit_cost * self.plant_hub[(plant, hub, product)]
                    )
        self.plant_hub_cost_expr = solver.Sum(plant_hub_terms)

        #######################################################################
        # Hub -> CFA Transport Cost
        #######################################################################

        hub_cfa_terms = []
        for hub in self.hubs:
            for cfa in self.cfas:
                unit_cost = self.hub_cfa_costs.get((hub, cfa))
                if unit_cost is None:
                    continue
                for product in self.products:
                    hub_cfa_terms.append(
                        unit_cost * self.hub_cfa[(hub, cfa, product)]
                    )
        self.hub_cfa_cost_expr = solver.Sum(hub_cfa_terms)

        #######################################################################
        # Unmet-Demand Penalty Cost
        #######################################################################

        penalty_terms = []
        for product in self.products:
            weight = self.unmet_weight.get(product, 0.0)
            for cfa in self.cfas:
                penalty_terms.append(weight * self.unmet[(product, cfa)])
        self.penalty_cost_expr = solver.Sum(penalty_terms)

        #######################################################################
        # Hub Safety-Stock Shortfall Cost
        #######################################################################

        shortfall_terms = [
            self.hub_shortfall_cost * self.shortfall[hub] for hub in self.hubs
        ]
        self.shortfall_cost_expr = solver.Sum(shortfall_terms)

        #######################################################################
        # Total
        #######################################################################

        total_cost = solver.Sum([
            self.production_cost_expr,
            self.plant_hub_cost_expr,
            self.hub_cfa_cost_expr,
            self.penalty_cost_expr,
            self.shortfall_cost_expr,
        ])

        solver.Minimize(total_cost)

        logger.info("Objective function built.")

    ###############################################################################
    # PART 3 -- Constraints
    ###############################################################################

    def build_constraints(self):

        logger.info("Building constraints...")

        self._constraint_batch_size()
        self._constraint_plant_capacity()
        self._constraint_plant_hub_balance()
        self._constraint_hub_balance()
        self._constraint_demand_satisfaction()
        self._constraint_hub_safety_stock()

        logger.info("All constraints built.")

    #######################################################################
    # 25 kL Batches: production == 25 * batch_count
    #######################################################################

    def _constraint_batch_size(self):

        solver = self.solver

        for plant in self.plants:
            for product in self.products:
                solver.Add(
                    self.production[(plant, product)]
                    == BATCH_SIZE_KL * self.batch_count[(plant, product)],
                    f"batch_link_{plant}_{product}",
                )

        logger.info("Batch-size constraints added.")

    #######################################################################
    # Plant Capacity by Pack-Size Category
    #######################################################################

    def _constraint_plant_capacity(self):

        solver = self.solver

        for plant in self.plants:
            for category in self.pack_categories:
                capacity = self.capacity.get((plant, category), 0.0)

                products_in_category = [
                    product
                    for product in self.products
                    if self.pack_mapping.get(product) == category
                ]

                if not products_in_category:
                    continue

                total_production = solver.Sum([
                    self.production[(plant, product)]
                    for product in products_in_category
                ])

                solver.Add(
                    total_production <= capacity,
                    f"capacity_{plant}_{category}",
                )

        logger.info("Plant capacity constraints added.")

    #######################################################################
    # Plant -> Hub Balance: all production must leave the plant (A1)
    #######################################################################

    def _constraint_plant_hub_balance(self):

        solver = self.solver

        for plant in self.plants:
            for product in self.products:
                outbound = solver.Sum([
                    self.plant_hub[(plant, hub, product)]
                    for hub in self.hubs
                ])
                solver.Add(
                    outbound == self.production[(plant, product)],
                    f"plant_outbound_{plant}_{product}",
                )

        logger.info("Plant -> Hub balance constraints added.")

    #######################################################################
    # Hub Balance: inbound = outbound to CFAs + ending safety-stock buffer
    #######################################################################

    def _constraint_hub_balance(self):

        solver = self.solver

        for hub in self.hubs:
            for product in self.products:
                inbound = solver.Sum([
                    self.plant_hub[(plant, hub, product)]
                    for plant in self.plants
                ])
                outbound = solver.Sum([
                    self.hub_cfa[(hub, cfa, product)]
                    for cfa in self.cfas
                ])
                solver.Add(
                    inbound == outbound + self.hub_stock[(hub, product)],
                    f"hub_balance_{hub}_{product}",
                )

        logger.info("Hub balance constraints added.")

    #######################################################################
    # Demand Satisfaction (net of CFA opening inventory, A2)
    #######################################################################

    def _constraint_demand_satisfaction(self):

        solver = self.solver

        for product in self.products:
            for cfa in self.cfas:
                required = self.effective_demand.get((product, cfa), 0.0)

                supplied = solver.Sum([
                    self.hub_cfa[(hub, cfa, product)]
                    for hub in self.hubs
                ])

                solver.Add(
                    supplied + self.unmet[(product, cfa)] == required,
                    f"demand_{product}_{cfa}",
                )

        logger.info("Demand-satisfaction constraints added.")

    #######################################################################
    # Hub Safety Stock (soft constraint via shortfall variable, A3)
    #######################################################################

    def _constraint_hub_safety_stock(self):

        solver = self.solver

        for hub in self.hubs:
            total_stock = solver.Sum([
                self.hub_stock[(hub, product)]
                for product in self.products
            ])

            target = self.hub_safety_target.get(hub, 0.0)

            solver.Add(
                total_stock + self.shortfall[hub] >= target,
                f"hub_safety_stock_{hub}",
            )

        logger.info("Hub safety-stock constraints added.")

    ###############################################################################
    # PART 4 -- Solve
    ###############################################################################

    def solve(self, time_limit_seconds: int = 120):

        logger.info("Solving MILP...")

        self.solver.SetTimeLimit(time_limit_seconds * 1000)

        status = self.solver.Solve()

        status_map = {
            pywraplp.Solver.OPTIMAL: "OPTIMAL",
            pywraplp.Solver.FEASIBLE: "FEASIBLE",
            pywraplp.Solver.INFEASIBLE: "INFEASIBLE",
            pywraplp.Solver.UNBOUNDED: "UNBOUNDED",
            pywraplp.Solver.ABNORMAL: "ABNORMAL",
            pywraplp.Solver.NOT_SOLVED: "NOT_SOLVED",
        }

        self.solve_status = status_map.get(status, str(status))
        logger.info(f"Solver status : {self.solve_status}")

        if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            logger.error(
                "No feasible solution found. This model always has slack "
                "(unmet demand, hub shortfall) so infeasibility usually "
                "indicates a data problem (e.g. negative capacities) rather "
                "than a genuinely infeasible network -- check inputs."
            )

        return self.solve_status

    ###############################################################################
    # PART 4 -- Extract Results
    ###############################################################################

    def extract_results(self):

        logger.info("Extracting results...")

        solved = self.solve_status in ("OPTIMAL", "FEASIBLE")

        def value(var):
            return var.solution_value() if solved else 0.0

        #######################################################################
        # Production Plan
        #######################################################################

        production_rows = []
        for plant in self.plants:
            for product in self.products:
                qty = value(self.production[(plant, product)])
                if qty > 1e-6:
                    production_rows.append({
                        "Plant": plant,
                        "Product": product,
                        "Batches (25 kL)": value(self.batch_count[(plant, product)]),
                        "Production (kL)": qty,
                    })
        self.production_plan = pd.DataFrame(production_rows)

        #######################################################################
        # Plant -> Hub Plan
        #######################################################################

        plant_hub_rows = []
        for plant in self.plants:
            for hub in self.hubs:
                for product in self.products:
                    qty = value(self.plant_hub[(plant, hub, product)])
                    if qty > 1e-6:
                        plant_hub_rows.append({
                            "Plant": plant,
                            "Hub": hub,
                            "Product": product,
                            "Volume (kL)": qty,
                        })
        self.plant_hub_plan = pd.DataFrame(plant_hub_rows)

        #######################################################################
        # Hub -> CFA Plan
        #######################################################################

        hub_cfa_rows = []
        for hub in self.hubs:
            for cfa in self.cfas:
                for product in self.products:
                    qty = value(self.hub_cfa[(hub, cfa, product)])
                    if qty > 1e-6:
                        hub_cfa_rows.append({
                            "Hub": hub,
                            "CFA": cfa,
                            "Product": product,
                            "Volume (kL)": qty,
                        })
        self.hub_cfa_plan = pd.DataFrame(hub_cfa_rows)

        #######################################################################
        # Hub Ending Inventory (Safety-Stock Buffer)
        #######################################################################

        inventory_rows = []
        for hub in self.hubs:
            for product in self.products:
                qty = value(self.hub_stock[(hub, product)])
                if qty > 1e-6:
                    inventory_rows.append({
                        "Hub": hub,
                        "Product": product,
                        "Ending Inventory (kL)": qty,
                    })
        for hub in self.hubs:
            inventory_rows.append({
                "Hub": hub,
                "Product": "__TOTAL__",
                "Ending Inventory (kL)": sum(
                    value(self.hub_stock[(hub, product)]) for product in self.products
                ),
            })
            inventory_rows.append({
                "Hub": hub,
                "Product": "__TARGET__",
                "Ending Inventory (kL)": self.hub_safety_target.get(hub, 0.0),
            })
            inventory_rows.append({
                "Hub": hub,
                "Product": "__SHORTFALL__",
                "Ending Inventory (kL)": value(self.shortfall[hub]),
            })
        self.hub_inventory_plan = pd.DataFrame(inventory_rows)

        #######################################################################
        # Unmet Demand
        #######################################################################

        unmet_rows = []
        for product in self.products:
            for cfa in self.cfas:
                qty = value(self.unmet[(product, cfa)])
                if qty > 1e-6:
                    penalty_rate = self.penalty_cost.get(product, 0.0)
                    is_contractual = self.contractual.get(product, False)
                    unmet_rows.append({
                        "Product": product,
                        "CFA": cfa,
                        "Tier": self.tier.get(product) or "",
                        "Contractual": is_contractual,
                        "Unmet Demand (kL)": qty,
                        "Penalty Cost (per kL)": penalty_rate,
                        "Cost of Unmet Demand": qty * penalty_rate,
                    })
        self.unmet_plan = pd.DataFrame(unmet_rows)

        logger.info("Results extracted.")

    ###############################################################################
    # PART 4 -- Cost Summary
    ###############################################################################

    def create_cost_summary(self):

        logger.info("Creating cost summary...")

        solved = self.solve_status in ("OPTIMAL", "FEASIBLE")

        def value(expr):
            return expr.solution_value() if solved else 0.0

        summary = {
            "Production Cost": value(self.production_cost_expr),
            "Plant-Hub Transport Cost": value(self.plant_hub_cost_expr),
            "Hub-CFA Transport Cost": value(self.hub_cfa_cost_expr),
            "Unmet-Demand Penalty Cost": value(self.penalty_cost_expr),
            "Hub Safety-Stock Shortfall Cost": value(self.shortfall_cost_expr),
            "Total Cost": (
                self.solver.Objective().Value() if solved else float("nan")
            ),
        }

        self.cost_summary = pd.DataFrame(
            summary.items(), columns=["Cost Component", "Amount (INR)"]
        )

    ###############################################################################
    # PART 4 -- Export
    ###############################################################################

    def export_results(self, output_dir="outputs"):

        logger.info("Exporting results...")

        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)

        exports = {
            "production_plan.csv": self.production_plan,
            "plant_hub_plan.csv": self.plant_hub_plan,
            "hub_cfa_plan.csv": self.hub_cfa_plan,
            "hub_inventory.csv": self.hub_inventory_plan,
            "unmet_demand.csv": self.unmet_plan,
            "cost_summary.csv": self.cost_summary,
        }

        for filename, df in exports.items():
            df.to_csv(output_dir / filename, index=False)

        with pd.ExcelWriter(output_dir / "Optimization_Report.xlsx") as writer:
            self.production_plan.to_excel(writer, sheet_name="Production", index=False)
            self.plant_hub_plan.to_excel(writer, sheet_name="Plant_Hub", index=False)
            self.hub_cfa_plan.to_excel(writer, sheet_name="Hub_CFA", index=False)
            self.hub_inventory_plan.to_excel(writer, sheet_name="Hub_Inventory", index=False)
            self.unmet_plan.to_excel(writer, sheet_name="Unmet_Demand", index=False)
            self.cost_summary.to_excel(writer, sheet_name="Cost_Summary", index=False)

        logger.info(f"Results exported to {output_dir.resolve()}")

    ###############################################################################
    # PART 4 -- Validation
    ###############################################################################

    def validate_solution(self):

        logger.info("=" * 70)
        logger.info("Solution Validation")
        logger.info("=" * 70)

        logger.info(f"Solver Status        : {self.solve_status}")
        logger.info(f"Production Rows      : {len(self.production_plan)}")
        logger.info(f"Plant-Hub Rows       : {len(self.plant_hub_plan)}")
        logger.info(f"Hub-CFA Rows         : {len(self.hub_cfa_plan)}")
        logger.info(f"Unmet Demand Rows    : {len(self.unmet_plan)}")

        if self.solve_status in ("OPTIMAL", "FEASIBLE"):
            total_demand = sum(self.effective_demand.values())
            total_unmet = (
                self.unmet_plan["Unmet Demand (kL)"].sum()
                if not self.unmet_plan.empty
                else 0.0
            )
            fill_rate = (
                1 - (total_unmet / total_demand) if total_demand > 0 else 1.0
            )

            logger.info(f"Total Effective Demand (kL) : {total_demand:,.2f}")
            logger.info(f"Total Unmet Demand (kL)     : {total_unmet:,.2f}")
            logger.info(f"Overall Fill Rate           : {fill_rate:.2%}")
            logger.info(
                f"Total Cost                  : "
                f"INR {self.solver.Objective().Value():,.2f}"
            )
        else:
            logger.warning("Skipping numeric validation -- no feasible solution.")

        logger.info("=" * 70)

    ###############################################################################
    # Run Complete Pipeline
    ###############################################################################

    def run(self, output_dir="outputs", time_limit_seconds: int = 120):

        self.transform_workbook()
        self.prepare_parameters()
        self.create_decision_variables()
        self.build_objective()
        self.build_constraints()
        self.solve(time_limit_seconds=time_limit_seconds)
        self.extract_results()
        self.create_cost_summary()
        self.export_results(output_dir=output_dir)
        self.validate_solution()

        return {
            "status": self.solve_status,
            "production_plan": self.production_plan,
            "plant_hub_plan": self.plant_hub_plan,
            "hub_cfa_plan": self.hub_cfa_plan,
            "hub_inventory": self.hub_inventory_plan,
            "unmet_demand": self.unmet_plan,
            "cost_summary": self.cost_summary,
        }


###############################################################################
# Standalone Execution
###############################################################################

if __name__ == "__main__":

    from Preprocessing import DataPreprocessor
    from inventory import InventoryPlanner

    DATA_PATH = Path(__file__).parent / "Data.xlsx"

    print("Running preprocessing...")
    processor = DataPreprocessor(DATA_PATH)
    master = processor.preprocess()

    print("Running inventory planning...")
    planner = InventoryPlanner(master)
    inventory_norms, hub_inventory = planner.run()

    print("Running production & distribution optimization...")
    optimizer = SupplyChainOptimizer(
        master_df=master,
        inventory_norms=inventory_norms,
        hub_inventory=hub_inventory,
        data=processor.data,
    )

    results = optimizer.run()

    print("\nCost Summary")
    print(results["cost_summary"])

    print("\nProduction Plan (head)")
    print(results["production_plan"].head())

    print("\nUnmet Demand (head)")
    print(results["unmet_demand"].head())
