import random
import unittest
from collections import Counter
from unittest.mock import patch

import pandas as pd
import salabim as sim

import config as cfg
from simulation.arrival_generator import ArrivalGenerator
from simulation.data_reader import ERDataReader


def sample_frame():
    # Hour 8: ambulance weight 100 of 400 known (the ARREMS -9 visit is ignored) -> share 0.25.
    # Hour 14: ambulance 300 of 300 -> share 1. Hour 20: only an unknown mode -> overall share 400/700.
    return pd.DataFrame({
        "arrtime": ["0830", "0845", "0850", "1415", "2010"],
        "patwt": [100, 300, 50, 300, 100],
        "edwt": [1, 1, 0, 0, 0],
        "arrems": [1, 2, -9, 1, -8],
        "immedr": [1, 3, 5, 2, 4],
        "waittime": [5, 10, 15, 20, 25],
        "lov": [60, 120, 180, 240, 300],
    })


def build_distributions():
    reader = ERDataReader("unused.parquet")
    reader.df = sample_frame()
    reader.build_esi_probabilities()
    reader.build_hourly_arrival_rates()
    reader.prepare_columns()
    reader.clean_esi_and_times()
    return reader.build_distributions()


class AmbulanceDataTests(unittest.TestCase):
    def test_ambulance_share_by_hour(self):
        shares = build_distributions()[5]["ambulance_share_by_hour"]
        self.assertEqual(len(shares), 24)
        self.assertAlmostEqual(shares[8], 0.25)
        self.assertAlmostEqual(shares[14], 1.0)
        for hour in set(range(24)) - {8, 14}:
            self.assertAlmostEqual(shares[hour], 400 / 700, msg=f"hour {hour}")

    def test_acuity_mix_by_arrival_mode(self):
        esi_levels, esi_weights, *_rest, arrival_modes = build_distributions()
        self.assertEqual(esi_levels, [1, 2, 3, 4, 5])
        # Overall mix still includes the visits with an unknown arrival mode.
        self.assertEqual(esi_weights, [100 / 850, 300 / 850, 300 / 850, 100 / 850, 50 / 850])
        self.assertEqual(arrival_modes["esi_weights"]["ambulance"], [0.25, 0.75, 0, 0, 0])
        self.assertEqual(arrival_modes["esi_weights"]["walk_in"], [0, 0, 1, 0, 0])

    def test_dataset_without_arrival_mode_has_no_ambulances(self):
        reader = ERDataReader("unused.parquet")
        reader.df = sample_frame().drop(columns="arrems")
        reader.build_esi_probabilities()
        reader.build_hourly_arrival_rates()
        reader.prepare_columns()
        reader.clean_esi_and_times()
        self.assertIsNone(reader.build_distributions()[5])

    def test_real_dataset_skews_ambulances_to_higher_acuity(self):
        _levels, _weights, _rates, _waits, _visits, arrival_modes = ERDataReader(cfg.DATASET_PATH).load_and_prepare()
        ambulance = arrival_modes["esi_weights"]["ambulance"]
        walk_in = arrival_modes["esi_weights"]["walk_in"]
        self.assertGreater(ambulance[0] + ambulance[1], 2 * (walk_in[0] + walk_in[1]))
        shares = arrival_modes["ambulance_share_by_hour"]
        self.assertTrue(all(0.05 < share < 0.4 for share in shares), shares)


class AmbulanceArrivalTests(unittest.TestCase):
    def setUp(self):
        previous_yieldless = sim.yieldless()
        sim.yieldless(False)
        self.addCleanup(sim.yieldless, previous_yieldless)
        random.seed(2025)

    def run_generator(self, data, days=50):
        env = sim.Environment(trace=False)
        arrivals = []

        def record_patient(**kwargs):
            arrivals.append((int(env.now() // 60) % 24, kwargs["ambulance"]))

        with patch("simulation.arrival_generator.Patient", side_effect=record_patient):
            ArrivalGenerator(
                staff=None, beds=None, testing=None, hospital_beds=None, metrics=None, data=data, env=env,
            )
            env.run(till=days * 24 * 60)
        return arrivals

    def test_dataset_arrivals_follow_hourly_ambulance_share(self):
        rates = [0] * 24
        rates[8] = rates[14] = 20
        shares = [0] * 24
        shares[8], shares[14] = 0.2, 0.8
        data = ([3], [1], rates, {}, {}, {"ambulance_share_by_hour": shares, "esi_weights": {}})
        arrivals = self.run_generator(data)
        totals = Counter(hour for hour, _ambulance in arrivals)
        ambulances = Counter(hour for hour, ambulance in arrivals if ambulance)
        self.assertAlmostEqual(ambulances[8] / totals[8], 0.2, delta=0.04)
        self.assertAlmostEqual(ambulances[14] / totals[14], 0.8, delta=0.04)

    def test_synthetic_arrivals_use_configured_share(self):
        with patch.object(cfg, "AMBULANCE_SHARE_SYNTHETIC", 0.3):
            arrivals = self.run_generator(None, days=30)
        share = sum(ambulance for _hour, ambulance in arrivals) / len(arrivals)
        self.assertAlmostEqual(share, 0.3, delta=0.03)

    def test_turning_ambulances_off_keeps_the_random_stream(self):
        rates = [4] * 24
        data = ([3], [1], rates, {}, {}, {"ambulance_share_by_hour": [0.5] * 24, "esi_weights": {}})
        with patch.object(cfg, "AMBULANCE_ARRIVALS", False):
            arrivals = self.run_generator(data, days=5)
        random.seed(2025)
        without_modes = self.run_generator(([3], [1], rates, {}, {}, None), days=5)
        self.assertFalse(any(ambulance for _hour, ambulance in arrivals))
        self.assertEqual(arrivals, without_modes)


if __name__ == "__main__":
    unittest.main()
