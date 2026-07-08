"""
===============================================================================
Castrol Global Supply Chain Planning Tool

Module:
    preprocessing.py

Author:
    Team

Description:
    Data loading, validation, cleaning and feature engineering.

===============================================================================
"""

import logging
import re
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd
from scipy.stats import norm


###############################################################################
# Configuration
###############################################################################

WORKING_DAYS = 30

SERVICE_LEVELS = {
    "A": 0.98,
    "B": 0.97,
    "C": 0.92,
    "D": 0.92
}

Z_SCORE = {
    "A": 2.054,
    "B": 1.881,
    "C": 1.405,
    "D": 1.405,
    "HUB": 2.054
}


###############################################################################
# Logger
###############################################################################

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


###############################################################################
# Main Class
###############################################################################
class DataPreprocessor:

    ###########################################################################
    # Constructor
    ###########################################################################

    def __init__(self, excel_path):

        self.excel_path = Path(excel_path)

        if not self.excel_path.exists():
            raise FileNotFoundError(self.excel_path)

        self.workbook = None
        self.sheet_map = {}
        self.data = {}

    ###########################################################################
    # Normalize text
    ###########################################################################

    @staticmethod
    def normalize(text):

        normalized = str(text)
        normalized = normalized.replace("_", " ")
        normalized = normalized.replace("-", " ")
        normalized = normalized.replace("\n", " ")
        normalized = normalized.replace("\u00A0", " ")
        normalized = normalized.replace("\xa0", " ")
        normalized = re.sub(r"\s+", " ", normalized)

        return normalized.strip().lower()

    ###########################################################################
    # Column cleaner
    ###########################################################################

    @staticmethod
    def clean_columns(df):

        df.columns = (
            df.columns
            .astype(str)
            .str.strip()
            .str.replace(r"^['\"]+|['\"]+$", "", regex=True)
            .str.replace("\n", " ", regex=False)
            .str.replace(r"\s+", " ", regex=True)
            .str.replace(r"\s*\(in\s*kL\)", "", regex=True, case=False)
            .str.replace(r"jan\s*-?\s*2026", "Jan-26", regex=True, case=False)
            .str.replace(r"jul\s*-?\s*25", "Jul-25", regex=True, case=False)
            .str.replace(r"aug\s*-?\s*25", "Aug-25", regex=True, case=False)
            .str.replace(r"sep\s*-?\s*25", "Sep-25", regex=True, case=False)
            .str.replace(r"oct\s*-?\s*25", "Oct-25", regex=True, case=False)
            .str.replace(r"nov\s*-?\s*25", "Nov-25", regex=True, case=False)
            .str.replace(r"dec\s*-?\s*25", "Dec-25", regex=True, case=False)
            .str.replace(r"lt\s*\(plant to hub\)\s*\(in\s*days\)", "LT (Plant to Hub) (in days)", regex=True, case=False)
            .str.replace(r"lt\s*\(hub to cfa\)\s*\(in\s*days\)", "LT (Hub to CFA ) (in days)", regex=True, case=False)
            .str.replace(r"production lead time\s*\(in\s*days\)", "Production lead time (in days)", regex=True, case=False)
            .str.replace(r"production variability\s*\(in\s*days\)", "Production variability (in days)", regex=True, case=False)
            .str.replace(r"transit lead variability\s*\(in\s*days\)", "Transit lead variability (in days)", regex=True, case=False)
        )

        rename_map = {}
        for original in df.columns:
            normalized = DataPreprocessor.normalize(original)
            if normalized == "product name":
                rename_map[original] = "product"
            elif normalized == "cfa region":
                rename_map[original] = "cfa_region"
            elif normalized == "cfa":
                rename_map[original] = "cfa"
            elif normalized in {"source", "source hub"}:
                rename_map[original] = "source_hub"
        if rename_map:
            df = df.rename(columns=rename_map)

        return df

    ###########################################################################
    # Header detection
    ###########################################################################

    @staticmethod
    def detect_header_row(workbook, sheet_name, max_rows=6):

        df_raw = pd.read_excel(workbook, sheet_name=sheet_name, header=None, nrows=max_rows)

        def score_row(row):
            values = [str(v).strip().lower() for v in row.tolist() if pd.notna(v)]
            score = 0

            if any("product name" == v or v == "product" for v in values):
                score += 10
            if any("pack size" == v for v in values):
                score += 8
            if any("cfa region" == v or v == "cfa" for v in values):
                score += 8
            if any("penalty" in v for v in values):
                score += 5
            if any("lead time" in v or "lt" in v for v in values):
                score += 5
            if any(month in v for v in values for month in ["jul", "aug", "sep", "oct", "nov", "dec", "jan"]):
                score += 3
            if any("service level" in v or "forecast" in v for v in values):
                score += 2

            valid_labels = [v for v in values if v and not v.startswith("unnamed")]
            score += min(len(valid_labels), 4)

            return score

        best_row = 0
        best_score = -1

        for row_index in range(min(max_rows, len(df_raw))):
            row_score = score_row(df_raw.iloc[row_index])
            if row_score > best_score:
                best_score = row_score
                best_row = row_index

        return best_row

    ###########################################################################
    # Load Workbook
    ###########################################################################

    def open_workbook(self):

        logger.info("Opening workbook...")

        self.workbook = pd.ExcelFile(self.excel_path)

        logger.info(
            f"Workbook contains {len(self.workbook.sheet_names)} sheets."
        )

    ###########################################################################
    # Auto detect sheets
    ###########################################################################

    def detect_sheets(self):

        keywords = {
            "sales": ["sales", "actual"],
            "forecast": ["forecast"],
            "opening_inventory": ["opening", "inventory"],
            "jan_forecast": ["jan", "january"],
            "lead_time": ["source", "lt"],
            "sku_master": ["sku", "portfolio"],
            "service_levels": ["tier", "service levels"],
            "plant_data": ["plant", "production"],
            "plant_hub_cost": ["plant", "hub", "transport"],
            "hub_cfa_cost": ["hub", "cfa", "transport"]
        }

        logger.info("Detecting sheets...")

        for logical_name, words in keywords.items():
            found = None

            for sheet in self.workbook.sheet_names:
                sheet_name = self.normalize(sheet)
                if all(word in sheet_name for word in words):
                    found = sheet
                    break

            if found is None:
                for sheet in self.workbook.sheet_names:
                    sheet_name = self.normalize(sheet)
                    if any(word in sheet_name for word in words):
                        found = sheet
                        break

            if found:
                self.sheet_map[logical_name] = found
                logger.info(f"{logical_name:<20} -> {found}")
            else:
                logger.warning(f"Could not detect {logical_name}")

    ###########################################################################
    # Load detected sheets
    ###########################################################################

    def load_sheets(self):

        logger.info("Loading datasets...")

        for logical_name, sheet in self.sheet_map.items():
            header = self.detect_header_row(self.workbook, sheet)
            df = pd.read_excel(self.workbook, sheet_name=sheet, header=header)
            df = self.clean_columns(df)
            df = df.loc[~df.index.duplicated(keep='first')].reset_index(drop=True)
            self.data[logical_name] = df
            logger.info(
                f"{logical_name:<20}{df.shape[0]} rows x {df.shape[1]} columns"
            )
            if logical_name in {"sku_master", "service_levels"}:
                logger.info(f"Columns for {logical_name}: {df.columns.tolist()}")
                if logical_name == "service_levels":
                    try:
                        logger.info("Service Levels sheet sample:\n" + df.head(20).to_string())
                    except Exception:
                        logger.info("Service Levels sheet present (unable to print sample).")

    ###########################################################################
    # Validation
    ###########################################################################

    def validate(self):

        logger.info("Running validation...")

        required = [
            "sales",
            "forecast",
            "lead_time",
            "opening_inventory",
            "jan_forecast"
        ]

        for item in required:
            if item not in self.data:
                raise ValueError(f"Missing required dataset : {item}")

        logger.info("Validation successful.")

    ###########################################################################
    # Pipeline
    ###########################################################################

    def load(self):
        self.open_workbook()
        self.detect_sheets()
        self.load_sheets()
        self.validate()
        return self.data

    ###########################################################################
    # Missing Value Handling
    ###########################################################################

    def clean_missing_values(self):

        logger.info("Cleaning missing values...")

        for name, df in self.data.items():
            numeric_cols = df.select_dtypes(include=np.number).columns
            object_cols = df.select_dtypes(exclude=np.number).columns

            if len(numeric_cols):
                df[numeric_cols] = df[numeric_cols].fillna(0)

            if len(object_cols):
                df[object_cols] = df[object_cols].fillna("Unknown")

            self.data[name] = df

        logger.info("Missing values handled.")

    ###########################################################################
    # Remove Duplicate Records
    ###########################################################################

    def remove_duplicates(self):

        logger.info("Checking duplicates...")

        for name, df in self.data.items():
            before = len(df)
            df = df.drop_duplicates()
            after = len(df)
            self.data[name] = df
            logger.info(f"{name:<20} Removed {before-after} duplicate rows")

    ###########################################################################
    # Detect Common Columns
    ###########################################################################

    def detect_column(self, df, keywords):

        for column in df.columns:
            col = self.normalize(column)
            for keyword in keywords:
                if re.search(rf"\b{re.escape(keyword)}\b", col):
                    return column

        return None

    ###########################################################################
    # Standardize Column Names
    ###########################################################################

    def standardize_columns(self):

        logger.info("Standardizing column names...")

        for name, df in self.data.items():
            rename_dict = {}
            for original in df.columns:
                normalized = self.normalize(original)
                if normalized in {"product", "product name", "sku"}:
                    rename_dict[original] = "product"
                elif normalized == "cfa region":
                    rename_dict[original] = "cfa_region"
                elif normalized == "cfa":
                    rename_dict[original] = "cfa"
                elif normalized == "tier":
                    rename_dict[original] = "tier"
                elif normalized == "penalty" or "penalty cost" in normalized:
                    rename_dict[original] = "penalty"
            self.data[name] = df.rename(columns=rename_dict)

    ###########################################################################
    # Convert Data Types
    ###########################################################################

    def convert_dtypes(self):

        logger.info("Converting numeric columns...")

        for name, df in self.data.items():
            for col in df.columns:
                if any(month in col for month in [
                    "Jul", "Aug", "Sep",
                    "Oct", "Nov", "Dec",
                    "Jan"
                ]):
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
            self.data[name] = df

    ###########################################################################
    # Merge Master Dataset
    ###########################################################################

    def build_master_dataset(self):

        logger.info("Creating master dataset...")

        sales = self.data["sales"].copy()
        product_col = "product"
        cfa_col = "cfa"
        # Merge order per desired relational model
        merge_sequence = [
            "forecast",
            "opening_inventory",
            "jan_forecast",
            "lead_time",
            "sku_master",
            "service_levels",
        ]

        master = sales.copy()
        logger.info(f"Starting master dataset from sales: {master.shape}")

        for dataset in merge_sequence:
            if dataset not in self.data:
                continue

            right = self.data[dataset]
            if dataset == "sku_master":
                merge_keys = [product_col]
                # Keep tier if present in SKU portfolio (SKU->tier mapping)
                keep_cols = merge_keys + ["penalty", "Contractual?"]
                if any(self.normalize(c) == "tier" for c in right.columns):
                    # normalize column name in right to 'tier'
                    detected = self.detect_column(right, ["tier"]) 
                    if detected:
                        right = right.rename(columns={detected: "tier"})
                        keep_cols.append("tier")
            elif dataset == "service_levels":
                # Service levels are defined at tier level
                # Normalize service_levels columns to expected names if possible
                detected_tier = self.detect_column(right, ["tier"]) or self.detect_column(right, ["Tier"]) 
                if detected_tier and detected_tier != "tier":
                    right = right.rename(columns={detected_tier: "tier"})

                detected_sl = self.detect_column(right, ["target fill rate", "target fill rate (%)", "target fill rate (%)"]) or self.detect_column(right, ["service level", "service_level", "servicelevel", "target fill rate"]) or self.detect_column(right, ["target"])
                if detected_sl and detected_sl not in {"ServiceLevel", "service_level"}:
                    right = right.rename(columns={detected_sl: "ServiceLevel"})

                detected_z = self.detect_column(right, ["zscore", "z score", "z_score"]) or self.detect_column(right, ["z"]) or self.detect_column(right, ["z-value"])
                if detected_z and detected_z not in {"ZScore", "z_score"}:
                    right = right.rename(columns={detected_z: "ZScore"})

                merge_keys = ["tier"]
                # Keep tier and any service level columns
                keep_cols = [col for col in ["tier", "ServiceLevel", "ZScore"] if col in right.columns]
                if not keep_cols:
                    keep_cols = list(right.columns)
            else:
                # product + cfa merges
                merge_keys = [product_col, cfa_col]
                if dataset == "opening_inventory":
                    keep_cols = merge_keys + ["Jan-26"]
                elif dataset == "jan_forecast":
                    keep_cols = merge_keys + ["Jan-26"]
                else:
                    keep_cols = list(right.columns)

            # For lead_time, ensure its key columns are normalized to 'product' and 'cfa'
            if dataset == "lead_time":
                detected = None
                if "product" not in right.columns:
                    detected = self.detect_column(right, ["product", "sku"])
                    if detected:
                        right = right.rename(columns={detected: "product"})
                if "cfa" not in right.columns:
                    detected = self.detect_column(right, ["cfa"])
                    if detected:
                        right = right.rename(columns={detected: "cfa"})

            missing = [key for key in merge_keys if key not in right.columns]
            if missing:
                logger.warning(
                    f"Skipping {dataset} because merge keys are missing: {missing}"
                )
                continue

            available_cols = [col for col in keep_cols if col in right.columns]
            right = right.loc[:, available_cols].copy()

            duplicate_count = right.duplicated(subset=merge_keys).sum()
            if duplicate_count:
                logger.warning(
                    f"{dataset} has {duplicate_count} duplicate rows on merge keys {merge_keys}."
                )

            master = master.merge(
                right,
                on=merge_keys,
                how="left",
                suffixes=("", f"_{dataset}")
            )
            logger.info(f"Rows after merge with {dataset}: {master.shape}")

            # After merging sku_master, check for SKU->tier mapping existence
            if dataset == "sku_master":
                if "tier" not in master.columns:
                    # No explicit SKU->tier mapping found; generate from sales using ABC classification
                    logger.info("No SKU->Tier column found in SKU portfolio. Generating SKU tiers from historical sales (Exhibit G ABC classification).")
                    mapping = self.generate_sku_tier_mapping()
                    # merge mapping into master
                    master = master.merge(mapping.loc[:, ["product", "tier"]], on=["product"], how="left")

        self.master = master
        logger.info(f"Master Dataset Shape : {master.shape}")
        return master

    ###########################################################################
    # Month Columns
    ###########################################################################

    def get_month_columns(self):

        months = [
            "Jul-25",
            "Aug-25",
            "Sep-25",
            "Oct-25",
            "Nov-25",
            "Dec-25"
        ]

        return [col for col in months if col in self.master.columns]

    ###########################################################################
    # Demand Statistics
    ###########################################################################

    def calculate_demand_statistics(self):

        logger.info("Calculating demand statistics...")

        months = self.get_month_columns()

        self.master["AverageMonthlyDemand"] = self.master[months].mean(axis=1)
        self.master["AverageDailyDemand"] = self.master["AverageMonthlyDemand"] / WORKING_DAYS
        self.master["DemandSTD"] = self.master[months].std(axis=1)
        self.master["DemandCV"] = self.master["DemandSTD"] / self.master["AverageMonthlyDemand"]
        self.master["DemandCV"] = self.master["DemandCV"].replace(np.inf, 0).fillna(0)

        logger.info("Demand statistics completed.")

    ###########################################################################
    # Forecast Error
    ###########################################################################

    def calculate_forecast_statistics(self):

        logger.info("Calculating forecast statistics...")

        forecast = self.data["forecast"].copy()
        months = self.get_month_columns()

        forecast = forecast.rename(columns={m: f"{m}_Forecast" for m in months})
        merge_cols = ["product", "cfa"]

        self.master = self.master.merge(forecast, on=merge_cols, how="left")

        error_columns = []
        for month in months:
            error = self.master[month] - self.master[f"{month}_Forecast"]
            col = f"{month}_Error"
            self.master[col] = error
            error_columns.append(col)

        self.master["ForecastBias"] = self.master[error_columns].mean(axis=1)
        self.master["ForecastErrorSTD"] = self.master[error_columns].std(axis=1)

        logger.info("Forecast statistics completed.")

    ###########################################################################
    # Lead Time
    ###########################################################################

    def calculate_lead_time(self):

        logger.info("Calculating lead times...")

        # Ensure required lead-time related columns exist in master (they should be merged earlier)
        def ensure_column(name, alt_keywords):
            if name not in self.master.columns:
                detected = self.detect_column(self.master, alt_keywords)
                if detected:
                    self.master.rename(columns={detected: name}, inplace=True)

        ensure_column("Production lead time (in days)", ["production lead time", "production lt"])
        ensure_column("LT (Plant to Hub) (in days)", ["lt (plant to hub)", "plant to hub"])
        ensure_column("LT (Hub to CFA ) (in days)", ["lt (hub to cfa)", "hub to cfa"])
        ensure_column("Production variability (in days)", ["production variability"])
        ensure_column("Transit lead variability (in days)", ["transit lead variability"])

        if "tier" not in self.master.columns:
            detected = self.detect_column(self.master, ["tier"])
            if detected:
                self.master.rename(columns={detected: "tier"}, inplace=True)

        self.master["TotalLeadTime"] = (
            self.master.get("Production lead time (in days)", 0)
            + self.master.get("LT (Plant to Hub) (in days)", 0)
            + self.master.get("LT (Hub to CFA ) (in days)", 0)
        )

        self.master["LeadTimeSTD"] = np.sqrt(
            self.master.get("Production variability (in days)", 0) ** 2
            + self.master.get("Transit lead variability (in days)", 0) ** 2
        )

        logger.info("Lead time completed.")

    ###########################################################################
    # Service Levels
    ###########################################################################

    def calculate_service_levels(self):

        logger.info("Assigning service levels...")
        # Expect that service level information was merged into master on 'tier'
        if "tier" not in self.master.columns:
            raise ValueError("No SKU Tier mapping exists in the provided data, therefore service levels cannot be assigned from the supplied dataset.")

        # If the service level values were merged from the service_levels sheet,
        # they should exist as 'ServiceLevel' (or suffixed). Prefer non-suffixed.
        sl_col = None
        if "ServiceLevel" in self.master.columns:
            sl_col = "ServiceLevel"
        else:
            # find any column that looks like a service level
            for col in self.master.columns:
                n = self.normalize(col)
                if any(k in n for k in ["target", "fill", "service level", "servicelevel"]):
                    sl_col = col
                    break

        # z-score column detection
        z_col = None
        if "ZScore" in self.master.columns:
            z_col = "ZScore"
        else:
            for col in self.master.columns:
                if self.normalize(col) in {"zscore", "z score", "z_score", "z"}:
                    z_col = col
                    break


        # Robustly parse service level values (handle '98%', '0.98', '98', text etc.)
        def parse_fill_rate(x):
            if pd.isna(x):
                return np.nan
            # numeric already
            if isinstance(x, (int, float)):
                try:
                    v = float(x)
                except Exception:
                    return np.nan
                # if >1 assume percent like 98 -> 0.98
                return v / 100.0 if v > 1.0 else v

            s = str(x).strip()
            if s == "":
                return np.nan
            # replace comma decimal separators
            s = s.replace(",", ".")
            # find first numeric token
            m = re.search(r"([0-9]+(?:\.[0-9]+)?)", s)
            if not m:
                return np.nan
            try:
                v = float(m.group(1))
            except Exception:
                return np.nan
            # if percent sign present or value > 1 treat as percent
            if "%" in s or v > 1.0:
                return v / 100.0
            return v

        if sl_col:
            parsed = self.master[sl_col].apply(parse_fill_rate)
            self.master["ServiceLevel"] = parsed
            logger.info(f"Parsed ServiceLevel values: nulls={self.master['ServiceLevel'].isna().sum()} of {len(self.master)}")
        else:
            self.master["ServiceLevel"] = np.nan

        # Additionally, if the original service_levels sheet is available, compute and log the mapping
        if "service_levels" in self.data:
            sl_df = self.data["service_levels"].copy()
            # Some service level sheets have messy layouts. Scan rows to find tier (A-D) and a target fill rate.
            rows = []
            for _, row in sl_df.iterrows():
                # concatenate all cells in the row into one string for flexible parsing
                text = " ".join(["" if pd.isna(v) else str(v) for v in row.values])
                # find tier letter (A-D) as a standalone token
                m_tier = re.search(r"(?<![A-Za-z0-9])([A-D])(?![A-Za-z0-9])", text)
                if not m_tier:
                    continue
                tier_val = m_tier.group(1)
                # find all numeric tokens in the row
                nums = re.findall(r"([0-9]+(?:\.[0-9]+)?)", text.replace(',', '.'))
                nums = [float(n) for n in nums] if nums else []
                target = np.nan
                if nums:
                    # prefer any number >1 (percent), else take last number
                    percent_candidates = [n for n in nums if n > 1.0]
                    if percent_candidates:
                        target = max(percent_candidates) / 100.0
                    else:
                        # fallback: take last numeric token (could be 0.92 etc.)
                        target = nums[-1]
                        if target > 1.0:
                            target = target / 100.0
                rows.append((tier_val, target))

            if rows:
                mapping = pd.DataFrame(rows, columns=["tier", "target_fill_rate"])
                # compute z-scores with clipping
                mapping["z_score"] = mapping["target_fill_rate"].apply(
                    lambda x: float(norm.ppf(np.clip(x, 1e-6, 1 - 1e-6))) if pd.notna(x) else np.nan
                )
                logger.info(f"Parsed Service Levels sheet: nulls={mapping['target_fill_rate'].isna().sum()} of {len(mapping)}")
                logger.info("Service Levels sheet mapping:")
                logger.info("\n" + mapping.to_string(index=False))

        # Compute ZScore from ServiceLevel using inverse normal (no hardcoded Zs)
        # Compute ZScore from ServiceLevel using inverse normal (clip to avoid inf)
        self.master["ZScore"] = self.master["ServiceLevel"].apply(
            lambda x: float(norm.ppf(np.clip(x, 1e-6, 1 - 1e-6))) if pd.notna(x) else np.nan
        )

        # Do not invent hub z-score; set if present in data, else NaN
        self.master["HubZScore"] = np.nan

        logger.info("Service levels assigned.")

    ###########################################################################
    # Final Feature Engineering
    ###########################################################################

    def feature_engineering(self):

        self.calculate_demand_statistics()
        self.calculate_forecast_statistics()
        self.calculate_lead_time()
        self.calculate_service_levels()

        # Inventory calculations
        logger.info("Calculating inventory norms (safety stock, reorder point, days of cover)...")

        # Ensure required columns exist
        req_cols = ["avg_daily_demand", "forecast_error_std", "total_lead_time", "lead_time_std", "ZScore"]
        for col in req_cols:
            if col not in self.master.columns:
                # try alternative names
                alt = col
                if col == "avg_daily_demand" and "AverageDailyDemand" in self.master.columns:
                    self.master["avg_daily_demand"] = self.master["AverageDailyDemand"]
                if col == "forecast_error_std" and "ForecastErrorSTD" in self.master.columns:
                    self.master["forecast_error_std"] = self.master["ForecastErrorSTD"]
                if col == "total_lead_time" and "TotalLeadTime" in self.master.columns:
                    self.master["total_lead_time"] = self.master["TotalLeadTime"]
                if col == "lead_time_std" and "LeadTimeSTD" in self.master.columns:
                    self.master["lead_time_std"] = self.master["LeadTimeSTD"]

        # Use camel-case columns if present
        if "AverageDailyDemand" in self.master.columns:
            self.master["avg_daily_demand"] = self.master["AverageDailyDemand"]
        if "ForecastErrorSTD" in self.master.columns:
            self.master["forecast_error_std"] = self.master["ForecastErrorSTD"]
        if "TotalLeadTime" in self.master.columns:
            self.master["total_lead_time"] = self.master["TotalLeadTime"]
        if "LeadTimeSTD" in self.master.columns:
            self.master["lead_time_std"] = self.master["LeadTimeSTD"]

        # Fill NaNs with zeros where safe to do so for calculation, but keep originals for validation
        self.master["avg_daily_demand"] = pd.to_numeric(self.master.get("avg_daily_demand", 0)).fillna(0)
        self.master["forecast_error_std"] = pd.to_numeric(self.master.get("forecast_error_std", 0)).fillna(0)
        self.master["total_lead_time"] = pd.to_numeric(self.master.get("total_lead_time", 0)).fillna(0)
        self.master["lead_time_std"] = pd.to_numeric(self.master.get("lead_time_std", 0)).fillna(0)
        self.master["ZScore"] = pd.to_numeric(self.master.get("ZScore", np.nan))

        # Safety Stock formula
        def compute_safety_stock(row):
            z = row.get("ZScore", np.nan)
            if pd.isna(z):
                return 0.0
            t = row.get("total_lead_time", 0.0)
            fe = row.get("forecast_error_std", 0.0)
            d = row.get("avg_daily_demand", 0.0)
            lsd = row.get("lead_time_std", 0.0)
            ss = z * np.sqrt((t * (fe ** 2)) + ((d ** 2) * (lsd ** 2)))
            return max(0.0, float(ss))

        self.master["SafetyStock"] = self.master.apply(compute_safety_stock, axis=1)

        # Reorder Point
        self.master["ReorderPoint"] = (self.master["avg_daily_demand"] * self.master["total_lead_time"]) + self.master["SafetyStock"]

        # Days Of Cover (handle zero demand)
        def compute_days_of_cover(row):
            d = row.get("avg_daily_demand", 0.0)
            rp = row.get("ReorderPoint", 0.0)
            if d <= 0:
                return np.nan
            return float(rp / d)

        self.master["DaysOfCover"] = self.master.apply(compute_days_of_cover, axis=1)

        # Validation checks
        #  - Higher service levels produce higher safety stock (spot check pairs where tier differs)
        #  - Safety stock non-negative already enforced

        # Export inventory norms
        norms = self.master.loc[:, [col for col in ["product", "cfa", "tier", "ServiceLevel", "ZScore", "SafetyStock", "ReorderPoint", "DaysOfCover"] if col in self.master.columns]]
        norms = norms.rename(columns={"product": "Product", "cfa": "CFA", "tier": "Tier", "ServiceLevel": "ServiceLevel", "ZScore": "ZScore", "SafetyStock": "SafetyStock", "ReorderPoint": "ReorderPoint", "DaysOfCover": "DaysOfCover"})
        norms.to_csv("inventory_norms.csv", index=False)
        logger.info("Exported inventory_norms.csv")

    def generate_sku_tier_mapping(self):
        """Generate SKU->Tier mapping using ABC classification on last six months sales.

        Returns a dataframe with columns: product, tier, SixMonthDemand, DemandContribution, CumulativeContribution
        """
        logger.info("Generating SKU tier mapping from sales data...")

        if "sales" not in self.data:
            raise ValueError("Sales data not available to generate SKU tiers.")

        sales = self.data["sales"].copy()

        # detect product column
        if "product" not in sales.columns:
            detected = self.detect_column(sales, ["product", "sku"])
            if detected:
                sales = sales.rename(columns={detected: "product"})

        months = [m for m in [
            "Jul-25", "Aug-25", "Sep-25", "Oct-25", "Nov-25", "Dec-25"
        ] if m in sales.columns]

        if not months:
            raise ValueError("No monthly sales columns found to compute six-month demand.")

        # Aggregate total demand for each SKU across all CFAs
        sales["SixMonthDemand"] = sales[months].sum(axis=1)
        agg = sales.groupby("product")["SixMonthDemand"].sum().reset_index()
        total = agg["SixMonthDemand"].sum()
        if total <= 0:
            raise ValueError("Total demand is zero; cannot generate SKU tiers.")

        agg["DemandContribution"] = agg["SixMonthDemand"] / total
        agg = agg.sort_values("SixMonthDemand", ascending=False).reset_index(drop=True)
        agg["CumulativeContribution"] = agg["DemandContribution"].cumsum()

        # Assign tiers per Exhibit G
        def assign_tier(cum):
            if cum <= 0.50:
                return "A"
            if cum <= 0.80:
                return "B"
            if cum <= 0.95:
                return "C"
            return "D"

        agg["tier"] = agg["CumulativeContribution"].apply(assign_tier)

        # Save mapping for auditability
        out = agg.rename(columns={"product": "product", "SixMonthDemand": "SixMonthDemand", "DemandContribution": "DemandContribution", "CumulativeContribution": "CumulativeContribution"})
        out.to_csv("sku_tier_mapping.csv", index=False)
        logger.info("Saved sku_tier_mapping.csv")

        return out

        logger.info("Feature engineering completed.")
        return self.master

    ###########################################################################
    # Canonical Column Names
    ###########################################################################

    def standardize_master_columns(self):

        logger.info("Standardizing master dataset columns...")

        rename_map = {
            "Production lead time (in days)": "production_lt",
            "LT (Plant to Hub) (in days)": "plant_hub_lt",
            "LT (Hub to CFA ) (in days)": "hub_cfa_lt",
            "Production variability (in days)": "production_var",
            "Transit lead variability (in days)": "transit_var",
            "Jan-26": "jan_forecast",
            "AverageMonthlyDemand": "avg_monthly_demand",
            "AverageDailyDemand": "avg_daily_demand",
            "DemandSTD": "demand_std",
            "DemandCV": "demand_cv",
            "ForecastBias": "forecast_bias",
            "ForecastErrorSTD": "forecast_error_std",
            "TotalLeadTime": "total_lead_time",
            "LeadTimeSTD": "lead_time_std",
            "ServiceLevel": "service_level",
            "ZScore": "z_score",
            "HubZScore": "hub_z_score"
        }

        self.master.rename(columns=rename_map, inplace=True)
        logger.info("Column names standardized.")

    def dedupe_duplicate_columns(self):
        """Coalesce duplicate-named columns in self.master.

        For any column name that appears multiple times, create a single column
        whose values are the first non-null value across the duplicates (left->right),
        then remove the duplicate columns and replace with the coalesced column.
        """
        cols = list(self.master.columns)
        dup_mask = pd.Series(cols).duplicated(keep=False)
        dup_names = pd.Series(cols)[dup_mask].unique().tolist()
        if not dup_names:
            return

        logger.info(f"Deduplicating columns: {dup_names}")

        for name in dup_names:
            # select all columns with this exact name (may be duplicates)
            dup_cols = [c for c in self.master.columns if c == name]
            if len(dup_cols) <= 1:
                continue
            df_dup = self.master.loc[:, dup_cols]

            # get first non-null per row across duplicate columns
            def first_nonnull(row):
                for v in row:
                    if pd.notna(v):
                        return v
                return np.nan

            coalesced = df_dup.apply(first_nonnull, axis=1)

            # drop all duplicate-named columns and assign coalesced
            self.master.drop(columns=dup_cols, inplace=True)
            self.master[name] = coalesced

        logger.info("Duplicate columns coalesced.")

    ###########################################################################
    # Final Validation
    ###########################################################################

    def validate_master_dataset(self):

        logger.info("Validating master dataset...")

        required = [
            "product",
            "cfa",
            "avg_daily_demand",
            "forecast_error_std",
            "total_lead_time",
            "lead_time_std",
            "service_level",
            "z_score"
        ]

        missing = [col for col in required if col not in self.master.columns]
        if missing:
            raise ValueError(f"Master dataset missing columns : {missing}")

        logger.info("Master dataset validation successful.")

    ###########################################################################
    # Export Dataset
    ###########################################################################

    def export_master_dataset(self, filename="master_dataset.csv"):

        logger.info("Exporting master dataset...")
        self.master.to_csv(filename, index=False)
        logger.info(f"Saved : {filename}")

    ###########################################################################
    # Summary
    ###########################################################################

    def summary(self):

        logger.info("=" * 60)
        logger.info("MASTER DATASET SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Rows : {self.master.shape[0]}")
        logger.info(f"Columns : {self.master.shape[1]}")
        logger.info(f"Missing Values : {self.master.isna().sum().sum()}")
        logger.info("=" * 60)

    ###########################################################################
    # Complete Pipeline
    ###########################################################################

    def preprocess(self):

        self.load()
        self.remove_duplicates()
        self.clean_missing_values()
        self.standardize_columns()
        self.convert_dtypes()
        self.build_master_dataset()
        self.feature_engineering()
        self.standardize_master_columns()
        # Coalesce any duplicate-named columns that may have arisen during merges
        self.dedupe_duplicate_columns()
        self.validate_master_dataset()
        self.summary()
        return self.master


if __name__ == "__main__":
    DATA_PATH = Path(__file__).parent / "Data.xlsx"
    processor = DataPreprocessor(DATA_PATH)
    master = processor.preprocess()
    processor.export_master_dataset()
    print(master.head())
    print(master.columns.tolist())
