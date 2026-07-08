"""
===============================================================================
Castrol Global Supply Chain Planning Tool

Module:
    routing.py

Description:
    Routing & Dispatch Planner

    Converts optimizer output (production / plant-hub / hub-cfa plans) into
    planner-friendly dispatch and truck-allocation reports.

    Fixes applied vs. original draft:
      1. validate_inputs / initialize / prepare_routing_data were defined at
         module level instead of inside the class -- re-indented as methods.
      2. Column names now match what optimization.py actually emits:
           Batches (25 kL), Production (kL)  [production_plan]
           Volume (kL)                       [plant_hub_plan / hub_cfa_plan]
           Ending Inventory (kL)             [hub_inventory]
         (the original referenced Batches/Production_kL/Quantity_kL/
         "Ending Inventory", which don't exist in the optimizer's output.)
      3. hub_inventory may contain bookkeeping rows (Product ==
         __TOTAL__/__TARGET__/__SHORTFALL__) which are now filtered out
         before building the per-SKU inventory lookup.
===============================================================================
"""

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


###############################################################################
# Routing Planner
###############################################################################

class RoutingPlanner:
    """
    Creates planner-friendly routing reports from optimization outputs.
    """

    def __init__(
        self,
        production_plan: pd.DataFrame,
        plant_hub_plan: pd.DataFrame,
        hub_cfa_plan: pd.DataFrame,
        hub_inventory: pd.DataFrame,
        cost_summary: pd.DataFrame,
        truck_capacity: float = 25.0
    ):
        logger.info("Initializing Routing Planner...")

        self.production_plan = production_plan.copy()
        self.plant_hub_plan = plant_hub_plan.copy()
        self.hub_cfa_plan = hub_cfa_plan.copy()

        # Drop optimizer bookkeeping rows before anything else touches them
        if not hub_inventory.empty and "Product" in hub_inventory.columns:
            self.hub_inventory = hub_inventory[
                ~hub_inventory["Product"].isin(
                    ["__TOTAL__", "__TARGET__", "__SHORTFALL__"]
                )
            ].copy()
        else:
            self.hub_inventory = hub_inventory.copy()

        self.cost_summary = cost_summary.copy()

        # One truck = one 25 kL batch
        self.truck_capacity = truck_capacity

        self.plant_dispatch = pd.DataFrame()
        self.hub_dispatch = pd.DataFrame()
        self.truck_summary = pd.DataFrame()
        self.route_summary = pd.DataFrame()
        self.network_summary = pd.DataFrame()

        logger.info(f"Truck Capacity : {self.truck_capacity} kL")

        self.validate_inputs()
        self.initialize()

    ###########################################################################
    # Validation
    ###########################################################################

    def validate_inputs(self):

        logger.info("Validating routing inputs...")

        required = {
            "production_plan": self.production_plan,
            "plant_hub_plan": self.plant_hub_plan,
            "hub_cfa_plan": self.hub_cfa_plan,
            "hub_inventory": self.hub_inventory,
            "cost_summary": self.cost_summary
        }

        for name, df in required.items():
            if df is None:
                raise ValueError(f"{name} is None.")
            if not isinstance(df, pd.DataFrame):
                raise TypeError(f"{name} must be a pandas DataFrame.")
            if df.empty:
                logger.warning(f"{name} is empty.")

        logger.info("Input validation completed.")

    ###########################################################################
    # Initialization
    ###########################################################################

    def initialize(self):

        logger.info("Initializing routing structures...")

        self.plants = sorted(self.production_plan["Plant"].unique()) \
            if not self.production_plan.empty else []

        self.hubs = sorted(self.plant_hub_plan["Hub"].unique()) \
            if not self.plant_hub_plan.empty else []

        self.cfas = sorted(self.hub_cfa_plan["CFA"].unique()) \
            if not self.hub_cfa_plan.empty else []

        self.products = sorted(self.production_plan["Product"].unique()) \
            if not self.production_plan.empty else []

        self.production_lookup = {}
        self.plant_hub_lookup = {}
        self.hub_cfa_lookup = {}

        self.total_trucks = 0
        self.total_dispatch_volume = 0.0

        logger.info(f"Plants   : {len(self.plants)}")
        logger.info(f"Hubs     : {len(self.hubs)}")
        logger.info(f"CFAs     : {len(self.cfas)}")
        logger.info(f"Products : {len(self.products)}")

        logger.info("Routing initialization completed.")

    ###########################################################################
    # Prepare Routing Data
    ###########################################################################

    def prepare_routing_data(self):

        logger.info("Preparing Routing Data...")

        # Production Lookup
        self.production_lookup = {}
        for _, row in self.production_plan.iterrows():
            key = (row["Plant"], row["Product"])
            self.production_lookup[key] = {
                "batches": row["Batches (25 kL)"],
                "production": row["Production (kL)"],
            }

        # Plant -> Hub Lookup
        self.plant_hub_lookup = {}
        for _, row in self.plant_hub_plan.iterrows():
            key = (row["Plant"], row["Hub"], row["Product"])
            self.plant_hub_lookup[key] = row["Volume (kL)"]

        # Hub -> CFA Lookup
        self.hub_cfa_lookup = {}
        for _, row in self.hub_cfa_plan.iterrows():
            key = (row["Hub"], row["CFA"], row["Product"])
            self.hub_cfa_lookup[key] = row["Volume (kL)"]

        # Hub Inventory Lookup
        self.inventory_lookup = {}
        for _, row in self.hub_inventory.iterrows():
            key = (row["Hub"], row["Product"])
            self.inventory_lookup[key] = row["Ending Inventory (kL)"]

        # Dispatch tables with truckload counts
        self.plant_dispatch = self.plant_hub_plan.copy()
        self.hub_dispatch = self.hub_cfa_plan.copy()

        if not self.plant_dispatch.empty:
            self.plant_dispatch["Truckloads"] = np.ceil(
                self.plant_dispatch["Volume (kL)"] / self.truck_capacity
            )
        else:
            self.plant_dispatch["Truckloads"] = pd.Series(dtype=float)

        if not self.hub_dispatch.empty:
            self.hub_dispatch["Truckloads"] = np.ceil(
                self.hub_dispatch["Volume (kL)"] / self.truck_capacity
            )
        else:
            self.hub_dispatch["Truckloads"] = pd.Series(dtype=float)

        self.routes = pd.concat(
            [
                self.plant_dispatch.assign(Stage="Plant→Hub").rename(
                    columns={"Plant": "From", "Hub": "To"}
                ),
                self.hub_dispatch.assign(Stage="Hub→CFA").rename(
                    columns={"Hub": "From", "CFA": "To"}
                ),
            ],
            ignore_index=True,
            sort=False,
        )

        self.total_dispatch_volume = (
            self.plant_dispatch["Volume (kL)"].sum()
            + self.hub_dispatch["Volume (kL)"].sum()
        )

        self.total_trucks = int(
            self.plant_dispatch["Truckloads"].sum()
            + self.hub_dispatch["Truckloads"].sum()
        )

        logger.info(f"Production Lookup : {len(self.production_lookup)}")
        logger.info(f"Plant-Hub Lookup  : {len(self.plant_hub_lookup)}")
        logger.info(f"Hub-CFA Lookup    : {len(self.hub_cfa_lookup)}")
        logger.info(f"Inventory Lookup  : {len(self.inventory_lookup)}")
        logger.info(f"Total Trucks      : {self.total_trucks}")
        logger.info("Routing data prepared.")

        return self.routes

    ###########################################################################
    # Network Summary
    ###########################################################################

    def create_network_summary(self):
        """Aggregate volume, cost, and truckloads by lane (From -> To)."""

        if self.routes.empty:
            self.network_summary = pd.DataFrame(
                columns=["Stage", "From", "To", "Volume (kL)", "Truckloads"]
            )
            return self.network_summary

        self.network_summary = (
            self.routes
            .groupby(["Stage", "From", "To"], as_index=False)
            .agg(**{
                "Volume (kL)": ("Volume (kL)", "sum"),
                "Truckloads": ("Truckloads", "sum"),
            })
            .sort_values("Volume (kL)", ascending=False)
        )

        return self.network_summary

    ###########################################################################
    # Run
    ###########################################################################

    def run(self):
        self.prepare_routing_data()
        self.create_network_summary()
        return {
            "routes": self.routes,
            "network_summary": self.network_summary,
            "total_trucks": self.total_trucks,
            "total_dispatch_volume": self.total_dispatch_volume,
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    print("Routing module loaded successfully.")
