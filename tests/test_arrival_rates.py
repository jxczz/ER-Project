import math
import random
import unittest
from collections import Counter
from unittest.mock import patch

import pandas as pd
import salabim as sim

from simulation.arrival_generator import ArrivalGenerator
from simulation.data_reader import ERDataReader

# Tiny NHAMCS-like sample. Two records carry a positive ED weight (0.5 + 1.5 = 2 EDs).
# Known-time visit weights: hour 8 -> 100 + 200 = 300, hour 14 -> 600. The "-9" visit (weight 100)
# has an unknown arrival time, so it counts toward volume but not toward the hourly shape.
TOTAL_PATWT = 1000
TOTAL_EDWT = 2.0
DEFAULT_DAILY_MEAN = TOTAL_PATWT / TOTAL_EDWT / 365
EXPECTED_SHARES = {8: 1 / 3, 14: 2 / 3}  # every other hour has share 0


def sample_frame():
    return pd.DataFrame({
        "arrtime": ["0830", "0845", "1415", "-9"],
        "patwt": [100, 200, 600, 100],
        "edwt": [0.5, 1.5, 0, 0],
    })


def build_rates(arrivals_per_day=None):
    reader = ERDataReader("unused.parquet", arrivals_per_day=arrivals_per_day)
    reader.df = sample_frame()
    reader.build_hourly_arrival_rates()
    return reader.hourly_arrival_rates


class HourlyArrivalRateTests(unittest.TestCase):
    def assert_shape(self, rates):
        self.assertEqual(len(rates), 24)
        total = sum(rates)
        for hour, rate in enumerate(rates):
            self.assertAlmostEqual(rate / total, EXPECTED_SHARES.get(hour, 0), places=12, msg=f"hour {hour}")

    def test_default_volume_is_nhamcs_national_mean(self):
        rates = build_rates()
        self.assertAlmostEqual(sum(rates), DEFAULT_DAILY_MEAN, places=12)

    def test_configured_volume_sets_daily_total(self):
        rates = build_rates(arrivals_per_day=48)
        self.assertAlmostEqual(sum(rates), 48, places=12)

    def test_configured_volume_preserves_hourly_shape(self):
        default_rates = build_rates()
        configured_rates = build_rates(arrivals_per_day=150)
        self.assert_shape(default_rates)
        self.assert_shape(configured_rates)
        default_total = sum(default_rates)
        configured_total = sum(configured_rates)
        for hour in range(24):
            self.assertAlmostEqual(
                configured_rates[hour] / configured_total, default_rates[hour] / default_total, places=12
            )
        self.assertAlmostEqual(configured_rates[8], 50, places=12)
        self.assertAlmostEqual(configured_rates[14], 100, places=12)

    def test_invalid_volume_is_rejected(self):
        for value in (0, -10, math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ERDataReader("unused.parquet", arrivals_per_day=value)


class ConfiguredArrivalVolumeTests(unittest.TestCase):
    def setUp(self):
        previous_yieldless = sim.yieldless()
        sim.yieldless(False)
        self.addCleanup(sim.yieldless, previous_yieldless)
        random.seed(2022)

    def test_generator_produces_configured_volume_and_shape(self):
        days = 200
        rates = build_rates(arrivals_per_day=48)
        env = sim.Environment(trace=False)
        arrival_hours = []

        def record_patient(**_kwargs):
            arrival_hours.append(int(env.now() // 60) % 24)

        with patch("simulation.arrival_generator.Patient", side_effect=record_patient):
            ArrivalGenerator(
                staff=None, beds=None, testing=None, hospital_beds=None, metrics=None,
                data=([1, 2, 3, 4, 5], [0.2] * 5, rates, {}, {}, None), env=env,
            )
            env.run(till=days * 24 * 60)

        self.assertAlmostEqual(len(arrival_hours) / days, 48, delta=2)
        counts = Counter(arrival_hours)
        self.assertEqual(set(counts), set(EXPECTED_SHARES))
        for hour, share in EXPECTED_SHARES.items():
            self.assertAlmostEqual(counts[hour] / len(arrival_hours), share, delta=0.02, msg=f"hour {hour}")


if __name__ == "__main__":
    unittest.main()
