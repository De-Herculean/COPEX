"""
===============================================================================
Castrol Global Supply Chain Planning Tool

Module:
    costing.py

Author:
    Team

Description:
    Cost Analytics & Financial Reporting

    Part 1
    -------
    • Imports
    • Logger
    • CostAnalyzer Class
    • Constructor
    • Validation
    • Initialization

===============================================================================
"""

###############################################################################
# Imports
###############################################################################

import logging
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd


###############################################################################
# Logger
###############################################################################

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


###############################################################################
# Cost Analyzer
###############################################################################

class CostAnalyzer:
    """
    Performs cost allocation and financial analysis
    using optimization outputs.

    Inputs
    ------
    production_plan
    plant_hub_plan
    hub_cfa_plan
    hub_inventory
    cost_summary

    Outputs
    -------
    cost_breakdown

    plant_cost_summary

    hub_cost_summary

    product_cost_summary

    cost_kpis

    executive_summary
    """

    ###########################################################################
    # Constructor
    ###########################################################################

    def __init__(

        self,

        production_plan: pd.DataFrame,

        plant_hub_plan: pd.DataFrame,

        hub_cfa_plan: pd.DataFrame,

        hub_inventory: pd.DataFrame,

        cost_summary: pd.DataFrame

    ):

        logger.info("=" * 70)
        logger.info("Initializing Cost Analyzer...")
        logger.info("=" * 70)

        #######################################################################
        # Store Inputs
        #######################################################################

        self.production_plan = production_plan.copy()

        self.plant_hub_plan = plant_hub_plan.copy()

        self.hub_cfa_plan = hub_cfa_plan.copy()

        self.hub_inventory = hub_inventory.copy()

        self.cost_summary = cost_summary.copy()

        #######################################################################
        # Output DataFrames
        #######################################################################

        self.cost_breakdown = pd.DataFrame()

        self.plant_cost_summary = pd.DataFrame()

        self.hub_cost_summary = pd.DataFrame()

        self.product_cost_summary = pd.DataFrame()

        self.cost_kpis = pd.DataFrame()

        self.executive_summary = pd.DataFrame()

        #######################################################################
        # Lookup Dictionaries
        #######################################################################

        self.production_lookup = {}

        self.transport_lookup = {}

        self.inventory_lookup = {}

        #######################################################################
        # Validation
        #######################################################################

        self.validate_inputs()

        self.initialize()

        logger.info("Cost Analyzer initialized successfully.")

    ###########################################################################
    # Validation
    ###########################################################################

    def validate_inputs(self):

        logger.info("Validating costing inputs...")

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

                raise TypeError(

                    f"{name} must be a pandas DataFrame."

                )

            if df.empty:

                logger.warning(

                    f"{name} is empty."

                )

        logger.info("Input validation successful.")

    ###########################################################################
    # Initialize
    ###########################################################################

    def initialize(self):

        logger.info("Initializing cost structures...")

        ###############################################################
        # Sets
        ###############################################################

        self.plants = sorted(

            self.production_plan["Plant"].unique()

        )

        self.products = sorted(

            self.production_plan["Product"].unique()

        )

        self.hubs = sorted(

            self.plant_hub_plan["Hub"].unique()

        )

        self.cfas = sorted(

            self.hub_cfa_plan["CFA"].unique()

        )

        ###############################################################
        # Totals
        ###############################################################

        self.total_production = 0.0

        self.total_transport = 0.0

        self.total_inventory = 0.0

        logger.info(f"Plants   : {len(self.plants)}")

        logger.info(f"Hubs     : {len(self.hubs)}")

        logger.info(f"CFAs     : {len(self.cfas)}")

        logger.info(f"Products : {len(self.products)}")

        logger.info("Cost Analyzer ready.")

###############################################################################
# Build Lookup Dictionaries
###############################################################################

    def build_lookup_tables(self):
        """
        Build lookup dictionaries from optimization outputs.
        """

        logger.info("=" * 70)
        logger.info("Building Cost Lookup Tables...")
        logger.info("=" * 70)

    ###############################################################
    # Production Lookup
    ###############################################################

        self.production_lookup = {}

        for _, row in self.production_plan.iterrows():

            key = (row["Plant"], row["Product"])

            self.production_lookup[key] = {

                "qty": float(row["Production_kL"]),

                "batches": int(row["Batches"])

            }

    ###############################################################
    # Plant → Hub Lookup
    ###############################################################

        self.plant_hub_lookup = {}

        for _, row in self.plant_hub_plan.iterrows():

            key = (

                row["Plant"],

                row["Hub"],

                row["Product"]

            )

            self.plant_hub_lookup[key] = float(

                row["Quantity_kL"]

            )

        ###############################################################
        # Hub → CFA Lookup
        ###############################################################

        self.hub_cfa_lookup = {}

        for _, row in self.hub_cfa_plan.iterrows():

            key = (

                row["Hub"],

                row["CFA"],

                row["Product"]

            )

            self.hub_cfa_lookup[key] = float(

                row["Quantity_kL"]

            )

        logger.info("Lookup tables created.")

###############################################################################
# Allocate Costs
###############################################################################

    def allocate_costs(self):
        """
        Allocate optimization costs to each shipment.
        """

        logger.info("=" * 70)
        logger.info("Allocating Costs...")
        logger.info("=" * 70)

    ###############################################################
    # Read Total Costs
    ###############################################################

        totals = dict(

            zip(

                self.cost_summary["Cost Component"],

                self.cost_summary["Amount"]

            )

        )

        total_production_cost = totals.get(

            "Production Cost",

            0

        )

        total_plant_hub_cost = totals.get(

            "Plant-Hub Cost",

            0

        )

        total_hub_cfa_cost = totals.get(

            "Hub-CFA Cost",

            0

        )

        ###############################################################
        # Total Volumes
        ###############################################################

        production_volume = self.production_plan[

            "Production_kL"

        ].sum()

        ph_volume = self.plant_hub_plan[

            "Quantity_kL"

        ].sum()

        hc_volume = self.hub_cfa_plan[

            "Quantity_kL"

        ].sum()

        ###############################################################
        # Unit Costs
        ###############################################################

        production_rate = (

            total_production_cost /

            production_volume

            if production_volume > 0 else 0

        )

        ph_rate = (

            total_plant_hub_cost /

            ph_volume

            if ph_volume > 0 else 0

        )

        hc_rate = (

            total_hub_cfa_cost /

            hc_volume

            if hc_volume > 0 else 0

        )

        ###############################################################
        # Production Allocation
        ###############################################################

        production = self.production_plan.copy()

        production["Allocated Cost"] = (

            production["Production_kL"]

            *

            production_rate

        )

        ###############################################################
        # Plant-Hub Allocation
        ###############################################################

        plant_hub = self.plant_hub_plan.copy()

        plant_hub["Allocated Cost"] = (

            plant_hub["Quantity_kL"]

            *

            ph_rate

        )

        ###############################################################
        # Hub-CFA Allocation
        ###############################################################

        hub_cfa = self.hub_cfa_plan.copy()

        hub_cfa["Allocated Cost"] = (

            hub_cfa["Quantity_kL"]

            *

            hc_rate

        )

        ###############################################################
        # Store Results
        ###############################################################

        self.production_cost_detail = production

        self.plant_hub_cost_detail = plant_hub

        self.hub_cfa_cost_detail = hub_cfa

        logger.info("Cost allocation completed.")

###############################################################################
# Allocate Remaining Costs
###############################################################################

    def allocate_remaining_costs(self):
        """
        Allocate holding, penalty and safety stock costs.
        """
        logger.info("=" * 70)
        logger.info("Allocating Remaining Costs...")
        logger.info("=" * 70)

    ###############################################################
    # Read Totals
    ###############################################################

        totals = dict(

            zip(

                self.cost_summary["Cost Component"],

                self.cost_summary["Amount"]

            )

        )

        holding_total = totals.get(

            "Holding Cost",

            0

        )

        penalty_total = totals.get(

            "Penalty Cost",

            0

        )

        safety_total = totals.get(

            "Safety Stock Shortfall Cost",

            0

        )

        ###############################################################
        # Holding Cost
        ###############################################################

        inventory = self.hub_inventory.copy()

        inventory_volume = inventory["Ending Inventory"].sum()

        if inventory_volume > 0:

            inventory["Allocated Holding Cost"] = (

                inventory["Ending Inventory"]

                /

                inventory_volume

            ) * holding_total

        else:

            inventory["Allocated Holding Cost"] = 0

        ###############################################################
        # Penalty Cost
        ###############################################################

        if hasattr(self, "unmet_demand"):

            unmet = self.unmet_demand.copy()

            unmet_qty = unmet["Unmet Demand"].sum()

            if unmet_qty > 0:

                unmet["Allocated Penalty"] = (

                    unmet["Unmet Demand"]

                    /

                    unmet_qty

                ) * penalty_total

            else:

                unmet["Allocated Penalty"] = 0

        else:

            unmet = pd.DataFrame()

        ###############################################################
        # Safety Stock Cost
        ###############################################################

        hub_summary = inventory.groupby(

            "Hub",

            as_index=False

        )["Ending Inventory"].sum()

        total_inventory = hub_summary["Ending Inventory"].sum()

        if total_inventory > 0:

            hub_summary["Allocated Safety Cost"] = (

                hub_summary["Ending Inventory"]

                /

                total_inventory

            ) * safety_total

        else:

            hub_summary["Allocated Safety Cost"] = 0

        ###############################################################
        # Store
        ###############################################################

        self.inventory_cost_detail = inventory

        self.penalty_cost_detail = unmet

        self.safety_cost_detail = hub_summary

        logger.info("Remaining costs allocated.")

###############################################################################
# Plant Cost Summary
###############################################################################

    def create_plant_summary(self):

        logger.info("Creating Plant Cost Summary...")

        plant_prod = self.production_cost_detail.groupby(

            "Plant",

            as_index=False

        ).agg(

            Production_kL=("Production_kL", "sum"),

            Production_Cost=("Allocated Cost", "sum")

        )

        plant_dispatch = self.plant_hub_cost_detail.groupby(

            "Plant",

            as_index=False

        )["Allocated Cost"].sum()

        plant_dispatch.rename(

            columns={"Allocated Cost": "Transport_Cost"},

            inplace=True

        )

        self.plant_cost_summary = plant_prod.merge(

            plant_dispatch,

            on="Plant",

            how="left"

        )

        self.plant_cost_summary.fillna(0, inplace=True)

        self.plant_cost_summary["Total Cost"] = (

            self.plant_cost_summary["Production_Cost"]

            +

            self.plant_cost_summary["Transport_Cost"]

        )


###############################################################################
# Hub Cost Summary
###############################################################################

    def create_hub_summary(self):

        logger.info("Creating Hub Cost Summary...")

        inbound = self.plant_hub_cost_detail.groupby(

            "Hub",

            as_index=False

        )["Allocated Cost"].sum()

        inbound.rename(

            columns={"Allocated Cost": "Inbound Cost"},

            inplace=True

        )

        outbound = self.hub_cfa_cost_detail.groupby(

            "Hub",

            as_index=False

        )["Allocated Cost"].sum()

        outbound.rename(

            columns={"Allocated Cost": "Outbound Cost"},

            inplace=True

        )

        inventory = self.inventory_cost_detail.groupby(

            "Hub",

            as_index=False

        )["Allocated Holding Cost"].sum()

        self.hub_cost_summary = inbound.merge(

            outbound,

            on="Hub",

            how="outer"

        )

        self.hub_cost_summary = self.hub_cost_summary.merge(

            inventory,

            on="Hub",

            how="left"

        )

        self.hub_cost_summary.fillna(0, inplace=True)

        self.hub_cost_summary["Total Cost"] = (

            self.hub_cost_summary["Inbound Cost"]

            +

            self.hub_cost_summary["Outbound Cost"]

            +

            self.hub_cost_summary["Allocated Holding Cost"]

        )


###############################################################################
# Product Cost Summary
###############################################################################

    def create_product_summary(self):

        logger.info("Creating Product Cost Summary...")

        production = self.production_cost_detail.groupby(

            "Product",

            as_index=False

        ).agg(

            Production_kL=("Production_kL", "sum"),

            Production_Cost=("Allocated Cost", "sum")

        )

        ph = self.plant_hub_cost_detail.groupby(

            "Product",

            as_index=False

        )["Allocated Cost"].sum()

        ph.rename(

            columns={"Allocated Cost": "PlantHub Cost"},

            inplace=True

        )

        hc = self.hub_cfa_cost_detail.groupby(

            "Product",

            as_index=False

        )["Allocated Cost"].sum()

        hc.rename(

            columns={"Allocated Cost": "HubCFA Cost"},

            inplace=True

        )

        self.product_cost_summary = production.merge(

            ph,

            on="Product",

            how="left"

        )

        self.product_cost_summary = self.product_cost_summary.merge(

            hc,

            on="Product",

            how="left"

        )

        self.product_cost_summary.fillna(0, inplace=True)

        self.product_cost_summary["Total Cost"] = (

            self.product_cost_summary["Production_Cost"]

            +

            self.product_cost_summary["PlantHub Cost"]

            +

            self.product_cost_summary["HubCFA Cost"]

        )


###############################################################################
# KPI Dashboard
###############################################################################

    def create_cost_kpis(self):

        logger.info("Generating Cost KPIs...")

        total_cost = self.cost_breakdown["Amount"].sum()

        total_volume = self.production_plan["Production_kL"].sum()

        kpis = [

            {

                "KPI": "Total Cost",

                "Value": total_cost

            },

            {

                "KPI": "Total Production (kL)",

                "Value": total_volume

            },

            {

                "KPI": "Cost per kL",

                "Value": (

                    total_cost / total_volume

                    if total_volume > 0 else 0

                )

            },

            {

                "KPI": "Highest Cost Plant",

                "Value": self.plant_cost_summary.loc[

                    self.plant_cost_summary["Total Cost"].idxmax(),

                    "Plant"

                ]

            },

            {

                "KPI": "Highest Cost Hub",

                "Value": self.hub_cost_summary.loc[

                    self.hub_cost_summary["Total Cost"].idxmax(),

                    "Hub"

                ]

            },

            {

                "KPI": "Highest Cost Product",

                "Value": self.product_cost_summary.loc[

                    self.product_cost_summary["Total Cost"].idxmax(),

                    "Product"

                ]

            }

        ]

        self.cost_kpis = pd.DataFrame(kpis)

        logger.info("Cost KPIs created.")

###############################################################################
# Export Results
###############################################################################

    def export_results(self, output_dir="outputs/costing"):

        logger.info("=" * 70)
        logger.info("Exporting Cost Reports...")
        logger.info("=" * 70)

        output_dir = Path(output_dir)

        output_dir.mkdir(

            parents=True,

            exist_ok=True

        )

        ###############################################################
        # CSV Exports
        ###############################################################

        self.cost_breakdown.to_csv(

            output_dir / "cost_breakdown.csv",

            index=False

        )

        self.plant_cost_summary.to_csv(

            output_dir / "plant_cost_summary.csv",

            index=False

        )

        self.hub_cost_summary.to_csv(

            output_dir / "hub_cost_summary.csv",

            index=False

        )

        self.product_cost_summary.to_csv(

            output_dir / "product_cost_summary.csv",

            index=False

        )

        self.cost_kpis.to_csv(

            output_dir / "cost_kpis.csv",

            index=False

        )

        ###############################################################
        # Excel Report
        ###############################################################

        report_path = output_dir / "Cost_Analysis_Report.xlsx"

        with pd.ExcelWriter(

            report_path,

            engine="openpyxl"

        ) as writer:

            self.cost_breakdown.to_excel(

                writer,

                sheet_name="Cost Breakdown",

                index=False

            )

            self.plant_cost_summary.to_excel(

                writer,

                sheet_name="Plant Summary",

                index=False

            )

            self.hub_cost_summary.to_excel(

                writer,

                sheet_name="Hub Summary",

                index=False

            )

            self.product_cost_summary.to_excel(

                writer,

                sheet_name="Product Summary",

                index=False

            )

            self.cost_kpis.to_excel(

                writer,

                sheet_name="KPIs",

                index=False

            )

        logger.info(f"Reports exported to {output_dir}")


###############################################################################
# Validation
###############################################################################

    def validate_outputs(self):

        logger.info("=" * 70)
        logger.info("Validating Cost Outputs...")
        logger.info("=" * 70)

        outputs = {

            "Cost Breakdown": self.cost_breakdown,

            "Plant Summary": self.plant_cost_summary,

            "Hub Summary": self.hub_cost_summary,

            "Product Summary": self.product_cost_summary,

            "Cost KPIs": self.cost_kpis

        }

        for name, df in outputs.items():

            if df.empty:

                logger.warning(f"{name} is empty.")

            else:

                logger.info(

                    f"{name:<20} {len(df)} rows"

                )

        logger.info("Validation completed.")


###############################################################################
# Run Pipeline
###############################################################################

    def run(self):

        logger.info("=" * 70)
        logger.info("Running Cost Analyzer...")
        logger.info("=" * 70)

        self.build_lookup_tables()

        self.allocate_costs()

        self.allocate_remaining_costs()

        self.create_cost_breakdown()

        self.create_plant_summary()

        self.create_hub_summary()

        self.create_product_summary()

        self.create_cost_kpis()

        self.export_results()

        self.validate_outputs()

        logger.info("=" * 70)
        logger.info("Cost Analysis Completed Successfully.")
        logger.info("=" * 70)

        return {

            "cost_breakdown": self.cost_breakdown,

            "plant_cost_summary": self.plant_cost_summary,

            "hub_cost_summary": self.hub_cost_summary,

            "product_cost_summary": self.product_cost_summary,

            "cost_kpis": self.cost_kpis

        }


###############################################################################
# Standalone Execution
###############################################################################

if __name__ == "__main__":

    logger.info(

        "costing.py is a module."

    )

    logger.info(

        "Instantiate CostAnalyzer from optimization outputs."

    )