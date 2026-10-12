import math
from pathlib import Path

import pandas as pd


class ERDataReader:
    """
    A class to read, clean, and process emergency room (ER) patient data from various file formats.
    It standardizes the data and extracts statistical distributions for use in ER simulation models.
    """

    def __init__(self, filepath, arrivals_per_day=None):
        raw_path = Path(filepath)
        if raw_path.is_absolute():
            self.filepath = raw_path
        else:
            project_root = Path(__file__).resolve().parent.parent
            self.filepath = project_root / raw_path
        if arrivals_per_day is not None and not (
            isinstance(arrivals_per_day, (int, float))
            and math.isfinite(arrivals_per_day)
            and arrivals_per_day > 0
        ):
            raise ValueError(f"arrivals_per_day must be a positive finite number or None, got {arrivals_per_day!r}")
        # Total ED arrivals per day; None uses the NHAMCS national mean ED.
        self.arrivals_per_day = arrivals_per_day
        self.df = None

    def load_data(self):
        """
        Loads data from the specified file based on its extension.
        Supports CSV, Excel (.xlsx), Stata (.dta), and Parquet formats.
        """
        if not self.filepath.exists():
            raise FileNotFoundError(f"Dataset file not found: {self.filepath}")

        filepath_str = str(self.filepath)

        if filepath_str.endswith(".csv"):
            self.df = pd.read_csv(self.filepath)
        elif filepath_str.endswith(".xlsx"):
            self.df = pd.read_excel(self.filepath)
        elif filepath_str.endswith(".dta"):
            self.df = pd.read_stata(self.filepath)
        elif filepath_str.endswith(".parquet"):
            self.df = pd.read_parquet(self.filepath)
        else:
            raise ValueError("Unsupported file type. Use .csv, .xlsx, .dta, or .parquet")

        return self.df

    def clean_data(self):
        # Standardize column names: strip whitespace and convert to lowercase
        self.df.columns = self.df.columns.str.strip().str.lower()
        # Remove rows where all values are missing
        self.df = self.df.dropna(how="all")
        return self.df

    def prepare_columns(self):
        """
        Adjust this mapping to match your dataset.

        Example NHAMCS-like columns:
        arrtime   -> arrival time
        immedr    -> triage / acuity / ESI-like field
        los       -> length of stay or service proxy
        """
        # Initialize a dictionary to map original column names to standardized names
        rename_map = {}

        # Map arrival time columns
        if "arrtime" in self.df.columns:
            rename_map["arrtime"] = "arrival_time"
        elif "arrival_time" in self.df.columns:
            rename_map["arrival_time"] = "arrival_time"

        # Map ESI/triage columns
        if "immediacy" in self.df.columns:
            rename_map["immediacy"] = "esi"
        elif "immedr" in self.df.columns:
            rename_map["immedr"] = "esi"
        elif "esi" in self.df.columns:
            rename_map["esi"] = "esi"
        elif "triage" in self.df.columns:
            rename_map["triage"] = "esi"

        # Map NHAMCS time fields using their actual meanings
        if "waittime" in self.df.columns:
            rename_map["waittime"] = "wait_time"

        if "lov" in self.df.columns:
            rename_map["lov"] = "length_of_visit"

        # Apply the renaming
        self.df = self.df.rename(columns=rename_map)

        # Check for required columns and raise error if missing
        required = ["arrival_time", "esi", "wait_time", "length_of_visit"]
        missing = [col for col in required if col not in self.df.columns]
        if missing:
            raise ValueError(f"Missing required columns after renaming: {missing}")

        # Keep only the required columns
        self.df = self.df[required].copy()
        return self.df

    def build_esi_probabilities(self):
        # NHAMCS IMMEDR probabilities are PATWT-weighted, conditional on recorded levels 1-5.
        levels = pd.to_numeric(self.df["immedr"], errors="coerce")
        weights = pd.to_numeric(self.df["patwt"], errors="coerce")
        valid = levels.isin([1, 2, 3, 4, 5]) & weights.gt(0) & weights.lt(float("inf"))
        totals = weights[valid].groupby(levels[valid]).sum().reindex(range(1, 6), fill_value=0)
        if totals.sum() <= 0:
            raise ValueError("NHAMCS acuity probabilities require positive weights for IMMEDR 1-5")
        self.esi_probs = totals / totals.sum()

    def build_hourly_arrival_rates(self):
        """Estimate a national mean ED profile from the full NHAMCS sample."""
        weights = pd.to_numeric(self.df["patwt"], errors="coerce")
        ed_weights = pd.to_numeric(self.df["edwt"], errors="coerce")
        times = self.df["arrtime"].astype("string")
        hhmm = pd.to_numeric(times, errors="coerce")
        valid = (
            times.str.fullmatch(r"\d{4}", na=False)
            & hhmm.floordiv(100).between(0, 23)
            & hhmm.mod(100).between(0, 59)
        )
        hourly_weights = weights[valid].groupby(
            hhmm[valid].floordiv(100).astype(int)
        ).sum().reindex(range(24), fill_value=0)
        if ed_weights[ed_weights > 0].sum() <= 0 or hourly_weights.sum() <= 0:
            raise ValueError("NHAMCS arrival rates require positive ED and valid-time visit weights")
        daily_mean = weights.sum() / ed_weights[ed_weights > 0].sum() / 365
        # Total daily volume: the NHAMCS national mean ED unless ARRIVALS_PER_DAY sets it.
        daily_volume = daily_mean if self.arrivals_per_day is None else self.arrivals_per_day
        # Spread the daily volume over the hours using the known-time NHAMCS profile
        # (this also redistributes unknown-time visits).
        self.hourly_arrival_rates = (daily_volume * hourly_weights / hourly_weights.sum()).tolist()
        # Preserve the existing valid-arrival-time subset for clinical distributions.
        self.df = self.df.loc[valid].copy()

    def clean_esi_and_times(self):
        # Convert ESI and observed time fields to numeric, coercing errors to NaN
        self.df["esi"] = pd.to_numeric(self.df["esi"], errors="coerce")
        self.df["wait_time"] = pd.to_numeric(self.df["wait_time"], errors="coerce")
        self.df["length_of_visit"] = pd.to_numeric(self.df["length_of_visit"], errors="coerce")

        # Drop rows with missing ESI or observed time fields
        self.df = self.df.dropna(subset=["esi", "wait_time", "length_of_visit"])
        # Ensure ESI is integer
        self.df["esi"] = self.df["esi"].astype(int)

        # Keep only valid ESI levels (1-5)
        self.df = self.df[self.df["esi"].isin([1, 2, 3, 4, 5])]
        # Keep valid wait times and visit lengths
        self.df = self.df[
            (self.df["wait_time"] >= 0) &
            (self.df["length_of_visit"] > 0)
        ]

        return self.df

    def build_distributions(self):
        """
        Returns:
        - esi_levels: list of ESI levels
        - esi_weights: matching probabilities
        - hourly_arrival_rates: expected arrivals per hour, indexed 0-23
        - wait_times_by_esi: dict mapping ESI -> observed wait times
        - lengths_of_visit_by_esi: dict mapping ESI -> observed total visit lengths
        """
        esi_levels = self.esi_probs.index.tolist()
        esi_weights = self.esi_probs.values.tolist()

        # Group observed wait times and total visit lengths by ESI level
        wait_times_by_esi = {}
        lengths_of_visit_by_esi = {}

        for level in [1, 2, 3, 4, 5]:
            wait_times = self.df.loc[self.df["esi"] == level, "wait_time"].tolist()
            visit_lengths = self.df.loc[self.df["esi"] == level, "length_of_visit"].tolist()

            if wait_times:
                wait_times_by_esi[level] = wait_times
            if visit_lengths:
                lengths_of_visit_by_esi[level] = visit_lengths

        return (
            esi_levels,
            esi_weights,
            self.hourly_arrival_rates,
            wait_times_by_esi,
            lengths_of_visit_by_esi,
        )

    def load_and_prepare(self):
        """
        Runs the full data processing pipeline: load, clean, estimate arrivals, and build clinical distributions.
        Returns the computed distributions for simulation.
        """
        self.load_data()
        self.clean_data()
        self.build_esi_probabilities()
        self.build_hourly_arrival_rates()
        self.prepare_columns()
        self.clean_esi_and_times()
        return self.build_distributions()
