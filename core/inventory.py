"""
===============================================================================
Castrol Global Supply Chain Planning Tool

Module:
    inventory.py

Description:
    Inventory Norm Calculation

    • Safety Stock
    • Reorder Point
    • Days of Cover
    • Hub Safety Stock

===============================================================================
"""

import logging
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import norm

logger = logging.getLogger(__name__)


###############################################################################
# Inventory Planner
###############################################################################

class InventoryPlanner:

    ###########################################################################
    # Constructor
    ###########################################################################

    def __init__(self, master_df, hub_service_level: float = 0.98):

        self.master = master_df.copy()

        self.inventory = None

        # Hub safety-stock target service level. Exposed as a constructor
        # parameter (was previously hardcoded to 0.98 inside
        # calculate_hub_safety_stock) so it can be edited from the planning
        # tool per Component 3's requirement that hub safety-stock
        # requirements be editable.
        self.hub_service_level = hub_service_level

        self.validate_master()

    ###########################################################################
    # Validation
    ###########################################################################

    def validate_master(self):

        logger.info("Validating master dataset...")

        required = [

            "product",

            "cfa",

            "avg_daily_demand",

            "forecast_error_std",

            "total_lead_time",

            "lead_time_std",

            "z_score",

            "hub_z_score"

        ]

        missing = []

        for col in required:

            if col not in self.master.columns:

                missing.append(col)

        if missing:

            raise ValueError(
                f"Missing required columns : {missing}"
            )

        logger.info("Validation successful.")

    ###########################################################################
    # Negative values
    ###########################################################################

    def clean_negative_values(self):

        logger.info("Checking negative values...")

        numeric = [

            "avg_daily_demand",

            "forecast_error_std",

            "total_lead_time",

            "lead_time_std"

        ]

        for col in numeric:

            self.master[col] = self.master[col].clip(lower=0)

    ###########################################################################
    # Safety Stock Formula
    ###########################################################################

    def calculate_safety_stock(self):

        logger.info("Calculating Safety Stock...")

        # Debug: report input columns used in safety stock calculation
        cols_to_check = [
            "tier",
            "service_level",
            "z_score",
            "forecast_error_std",
            "avg_daily_demand",
            "total_lead_time",
            "lead_time_std"
        ]

        logger.info("Safety Stock input diagnostics:")
        for col in cols_to_check:
            if col not in self.master.columns:
                logger.info(f"  {col}: MISSING")
                continue
            ser = pd.to_numeric(self.master[col], errors="coerce")
            n_null = ser.isna().sum()
            n_zero = (ser == 0).sum()
            if ser.dropna().empty:
                mins = maxs = means = "NA"
            else:
                mins = ser.min()
                maxs = ser.max()
                means = ser.mean()
            logger.info(f"  {col}: nulls={n_null}, zeros={int(n_zero)}, min={mins}, max={maxs}, mean={means}")

        # For traceability, if service_levels sheet exists in inventory context, print mapping
        try:
            # Attempt to show mapping if available in the master or external data
            if "tier" in self.master.columns and "service_level" in self.master.columns:
                mapping = self.master.loc[:, ["tier", "service_level", "z_score"]].drop_duplicates().sort_values("tier")
                logger.info("Service level mapping (from master):")
                logger.info("\n" + mapping.to_string(index=False))
        except Exception:
            pass

        # Debug: print first 20 forecast error columns if present
        error_cols = [c for c in self.master.columns if c.lower().endswith("_error")]
        if error_cols:
            cols_to_print = error_cols + ["forecast_error_std"] if "forecast_error_std" in self.master.columns else error_cols
            logger.info("First 20 rows of forecast error columns and forecast_error_std:")
            try:
                logger.info("\n" + self.master.loc[:, cols_to_print].head(20).to_string(index=False))
            except Exception:
                logger.info("Unable to print forecast error sample due to formatting error.")

        demand_term = (

            self.master["total_lead_time"]

            *

            (

                self.master["forecast_error_std"]

                ** 2

            )

        )

        lead_time_term = (

            (

                self.master["avg_daily_demand"]

                ** 2

            )

            *

            (

                self.master["lead_time_std"]

                ** 2

            )

        )

        self.master["safety_stock"] = (

            self.master["z_score"]

            *

            np.sqrt(

                demand_term

                +

                lead_time_term

            )

        )

        self.master["safety_stock"] = (

            self.master["safety_stock"]

            .fillna(0)

            .clip(lower=0)

        )

        logger.info("Safety Stock completed.")

        # Print first 20 rows after safety stock calculation
        cols_after = [
            "product",
            "tier",
            "service_level",
            "z_score",
            "forecast_error_std",
            "lead_time_std",
            "total_lead_time",
            "avg_daily_demand",
            "safety_stock"
        ]
        available = [c for c in cols_after if c in self.master.columns]
        logger.info("First 20 rows after Safety Stock calculation:")
        try:
            logger.info("\n" + self.master.loc[:, available].head(20).to_string(index=False))
        except Exception:
            logger.info("Unable to print post-safety-stock rows due to formatting error.")

    ###########################################################################
    # Safety Stock Summary
    ###########################################################################

    def safety_stock_summary(self):

        logger.info("=" * 60)

        logger.info("Safety Stock Summary")

        logger.info("=" * 60)

        logger.info(

            f"Average Safety Stock : "

            f"{self.master['safety_stock'].mean():.2f}"

        )

        logger.info(

            f"Maximum Safety Stock : "

            f"{self.master['safety_stock'].max():.2f}"

        )

        logger.info(

            f"Minimum Safety Stock : "

            f"{self.master['safety_stock'].min():.2f}"

        )

        logger.info("=" * 60)

###############################################################################
# Reorder Point
###############################################################################

    def calculate_reorder_point(self):

        logger.info("Calculating Reorder Point...")

        self.master["lead_time_demand"] = (

            self.master["avg_daily_demand"]

            *

            self.master["total_lead_time"]

        )

        self.master["reorder_point"] = (

            self.master["lead_time_demand"]

            +

            self.master["safety_stock"]

        )

        self.master["reorder_point"] = (

            self.master["reorder_point"]

            .clip(lower=0)

            .fillna(0)

        )

        logger.info("Reorder Point completed.")

    ###########################################################################
    # Days of Cover
    ###########################################################################

    def calculate_days_of_cover(self):

        logger.info("Calculating Days of Cover...")

        self.master["days_of_cover"] = np.where(

            self.master["avg_daily_demand"] > 0,

            self.master["reorder_point"]

            /

            self.master["avg_daily_demand"],

            0

        )

        self.master["days_of_cover"] = (

            self.master["days_of_cover"]

            .replace(np.inf, 0)

            .fillna(0)

        )

        logger.info("Days of Cover completed.")

    ###########################################################################
    # Hub Safety Stock
    ###########################################################################

    def calculate_hub_safety_stock(self):

        logger.info("Calculating Hub Safety Stock...")

        source_column = None
        # Hub service level is now a configurable parameter (see __init__)
        hub_service_level = self.hub_service_level
        hub_z = float(norm.ppf(hub_service_level))
        if "source_hub" in self.master.columns:
            source_column = "source_hub"
        elif any("source hub" in str(col).strip().lower() for col in self.master.columns):
            source_column = next(
                col for col in self.master.columns
                if "source hub" in str(col).strip().lower()
            )
        elif "source" in self.master.columns:
            source_column = "source"

        if source_column is None:

            logger.warning(
                "No source hub column detected. "
                "Skipping hub aggregation."
            )

            self.hub_summary = pd.DataFrame()

            return

        hub = (

            self.master

            .groupby(source_column)

            .agg(

                avg_daily_demand=("avg_daily_demand", "sum"),

                forecast_error_std=("forecast_error_std", "sum"),

                lead_time_std=("lead_time_std", "mean"),

                total_lead_time=("total_lead_time", "mean"),

                hub_z_score=("hub_z_score", "first")

            )

            .reset_index()

        )

        # Ensure hub_z_score is set to fixed value for all hubs
        hub["hub_z_score"] = hub_z

        hub["hub_safety_stock"] = (
            hub["hub_z_score"] * np.sqrt(
                (hub["total_lead_time"] * (hub["forecast_error_std"] ** 2))
                + (hub["avg_daily_demand"] ** 2 * (hub["lead_time_std"] ** 2))
            )
        )

        self.hub_summary = hub

        logger.info("Hub Safety Stock completed.")

    def create_inventory_norms(self):

        logger.info("Creating inventory norms...")

        columns = [
            "product",
            "cfa",
            "avg_daily_demand",
            "forecast_error_std",
            "total_lead_time",
            "lead_time_std",
            "z_score",
            "hub_z_score",
            "safety_stock",
            "reorder_point",
            "days_of_cover"
        ]

        available = [col for col in columns if col in self.master.columns]
        self.inventory_norms = self.master[available].copy()

        logger.info("Inventory norms created.")

    def export_inventory(self,
                         sku_file="inventory_norms.csv",
                         hub_file="hub_inventory.csv"):

        logger.info("Exporting inventory outputs...")

        self.inventory_norms.to_csv(
            sku_file,
            index=False
        )

        self.hub_summary.to_csv(
            hub_file,
            index=False
        )

        logger.info("Inventory export completed.")

    def run(self):

        self.clean_negative_values()

        self.calculate_safety_stock()

        self.calculate_reorder_point()

        self.calculate_days_of_cover()

        self.calculate_hub_safety_stock()

        self.create_inventory_norms()

        self.safety_stock_summary()

        return self.inventory_norms, self.hub_summary


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )
    logger.info("inventory.py executed directly. No processing pipeline is defined here.")
    print("inventory.py loaded. Use InventoryPlanner(master_df) in another script.")

