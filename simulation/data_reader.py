import pandas as pd


class ERDataReader:
    def __init__(self, filepath):
        self.filepath = filepath
        self.df = None

    def load_data(self):
        if self.filepath.endswith(".csv"):
            self.df = pd.read_csv(self.filepath)
        elif self.filepath.endswith(".xlsx"):
            self.df = pd.read_excel(self.filepath)
        elif self.filepath.endswith(".dta"):
            self.df = pd.read_stata(self.filepath)
        else:
            raise ValueError("Unsupported file type. Use .csv, .xlsx, or .dta")

        return self.df

    def clean_data(self):
        self.df.columns = self.df.columns.str.strip().str.lower()
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

        rename_map = {}

        if "arrtime" in self.df.columns:
            rename_map["arrtime"] = "arrival_time"
        elif "arrival_time" in self.df.columns:
            rename_map["arrival_time"] = "arrival_time"

        if "immediacy" in self.df.columns:
            rename_map["immediacy"] = "esi"
        elif "immedr" in self.df.columns:
            rename_map["immedr"] = "esi"
        elif "esi" in self.df.columns:
            rename_map["esi"] = "esi"
        elif "triage" in self.df.columns:
            rename_map["triage"] = "esi"

        if "los" in self.df.columns:
            rename_map["los"] = "service_time"
        elif "service_time" in self.df.columns:
            rename_map["service_time"] = "service_time"
        elif "waittime" in self.df.columns:
            rename_map["waittime"] = "service_time"

        self.df = self.df.rename(columns=rename_map)

        required = ["arrival_time", "esi", "service_time"]
        missing = [col for col in required if col not in self.df.columns]
        if missing:
            raise ValueError(f"Missing required columns after renaming: {missing}")

        self.df = self.df[required].copy()
        return self.df

    def convert_arrival_time_to_minutes(self):
        """
        Converts arrival times into minutes after midnight.

        Handles:
        - numeric HHMM values like 930, 1545
        - strings like '09:30'
        """

        def hhmm_to_minutes(value):
            if pd.isna(value):
                return None

            # numeric style: 930, 1545
            if isinstance(value, (int, float)):
                value = int(value)
                hours = value // 100
                minutes = value % 100
                if 0 <= hours <= 23 and 0 <= minutes <= 59:
                    return hours * 60 + minutes
                return None

            # string style: "09:30"
            value = str(value).strip()
            if ":" in value:
                parts = value.split(":")
                if len(parts) >= 2:
                    try:
                        hours = int(parts[0])
                        minutes = int(parts[1])
                        if 0 <= hours <= 23 and 0 <= minutes <= 59:
                            return hours * 60 + minutes
                    except ValueError:
                        return None

            # fallback: maybe string numeric like "930"
            try:
                numeric_value = int(value)
                hours = numeric_value // 100
                minutes = numeric_value % 100
                if 0 <= hours <= 23 and 0 <= minutes <= 59:
                    return hours * 60 + minutes
            except ValueError:
                return None

            return None

        self.df["arrival_minutes"] = self.df["arrival_time"].apply(hhmm_to_minutes)
        self.df = self.df.dropna(subset=["arrival_minutes"])

        return self.df

    def clean_esi_and_service_time(self):
        self.df["esi"] = pd.to_numeric(self.df["esi"], errors="coerce")
        self.df["service_time"] = pd.to_numeric(self.df["service_time"], errors="coerce")

        self.df = self.df.dropna(subset=["esi", "service_time"])
        self.df["esi"] = self.df["esi"].astype(int)

        self.df = self.df[self.df["esi"].isin([1, 2, 3, 4, 5])]
        self.df = self.df[self.df["service_time"] > 0]

        return self.df

    def build_distributions(self):
        """
        Returns:
        - esi_levels: list of ESI levels
        - esi_weights: matching probabilities
        - interarrival_times: list of positive interarrival times in minutes
        - service_times_by_esi: dict mapping ESI -> list of service times
        """

        esi_probs = self.df["esi"].value_counts(normalize=True).sort_index()
        esi_levels = esi_probs.index.tolist()
        esi_weights = esi_probs.values.tolist()

        sorted_arrivals = self.df.sort_values("arrival_minutes")
        interarrival_times = sorted_arrivals["arrival_minutes"].diff().dropna()

        # keep positive gaps only
        interarrival_times = interarrival_times[interarrival_times > 0].tolist()

        service_times_by_esi = {}
        for level in [1, 2, 3, 4, 5]:
            times = self.df.loc[self.df["esi"] == level, "service_time"].tolist()
            if times:
                service_times_by_esi[level] = times

        return esi_levels, esi_weights, interarrival_times, service_times_by_esi

    def load_and_prepare(self):
        self.load_data()
        self.clean_data()
        self.prepare_columns()
        self.convert_arrival_time_to_minutes()
        self.clean_esi_and_service_time()
        return self.build_distributions()
