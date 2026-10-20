import math
import random
import statistics
import unittest
from unittest.mock import patch

import salabim as sim

import config as cfg
import simulation.environment as environment
from simulation.esi import ESI
from simulation.patient import Z75, Patient, lognormal_params_from_median_iqr

STUDY_MEDIAN = 2.58
STUDY_IQR = (1.36, 4.35)


class LognormalFitTests(unittest.TestCase):
    def test_fit_matches_study_median_and_iqr_width(self):
        mu, sigma = lognormal_params_from_median_iqr(STUDY_MEDIAN, *STUDY_IQR)
        self.assertAlmostEqual(mu, math.log(2.58), places=12)
        self.assertAlmostEqual(sigma, 0.862, places=3)
        self.assertAlmostEqual(math.exp(mu), 2.58, places=12)
        self.assertAlmostEqual(2 * Z75 * sigma, math.log(4.35 / 1.36), places=12)

    def test_invalid_inputs_are_rejected(self):
        cases = [
            (1.36, 1.36, 4.35),  # q1 == median
            (1.0, 1.36, 4.35),  # q1 > median
            (4.35, 1.36, 4.35),  # median == q3
            (5.0, 1.36, 4.35),  # median > q3
            (2.58, 0, 4.35),
            (2.58, -1.0, 4.35),
            (0, -1.0, 4.35),
            (-2.0, -3.0, -1.0),
            (math.nan, 1.36, 4.35),
            (2.58, math.nan, 4.35),
            (2.58, 1.36, math.nan),
            (2.58, 1.36, math.inf),
            (math.inf, 1.36, math.inf),
        ]
        for median, q1, q3 in cases:
            with self.subTest(median=median, q1=q1, q3=q3):
                with self.assertRaises(ValueError):
                    lognormal_params_from_median_iqr(median, q1, q3)

    def test_samples_reproduce_median_and_quartiles(self):
        mu, sigma = lognormal_params_from_median_iqr(cfg.TRIAGE_TIME_MEDIAN, *cfg.TRIAGE_TIME_IQR)
        random.seed(2016)
        draws = [random.lognormvariate(mu, sigma) for _ in range(200_000)]

        self.assertTrue(all(draw > 0 for draw in draws))
        q1, median, q3 = statistics.quantiles(draws, n=4)
        self.assertAlmostEqual(median / cfg.TRIAGE_TIME_MEDIAN, 1, delta=0.02)
        self.assertAlmostEqual(q1 / math.exp(mu - Z75 * sigma), 1, delta=0.05)
        self.assertAlmostEqual(q3 / math.exp(mu + Z75 * sigma), 1, delta=0.05)


class TriageHoldTests(unittest.TestCase):
    def setUp(self):
        previous_yieldless = sim.yieldless()
        sim.yieldless(False)
        self.addCleanup(sim.yieldless, previous_yieldless)
        animate = patch.object(cfg, "ANIMATE", False)
        animate.start()
        self.addCleanup(animate.stop)

        staff = dict.fromkeys(("triage", "exam", "diagnosis", "treatment", "extra_treatment", "disposition"), 1)
        with patch.multiple(environment, STAFF=staff, NUM_BEDS=1, NUM_HOSPITAL_BEDS=1):
            self.env, self.staff, self.beds, self.testing, self.hospital_beds = environment.create_environment()

    def test_triage_hold_is_drawn_from_configured_lognormal(self):
        duration = 3.25
        expected = lognormal_params_from_median_iqr(cfg.TRIAGE_TIME_MEDIAN, *cfg.TRIAGE_TIME_IQR)
        with patch("simulation.patient.random.lognormvariate", return_value=duration) as draw:
            metrics = ESI()
            patient = Patient(
                staff=self.staff, beds=self.beds, testing=self.testing, hospital_beds=self.hospital_beds,
                metrics=metrics, esi=3, env=self.env,
            )
            self.env.run(till=duration - 0.01)
            self.assertIn(patient, self.staff["triage"].claimers())
            self.env.run(till=duration + 0.01)

        draw.assert_called_once_with(*expected)
        self.assertNotIn(patient, self.staff["triage"].claimers())
        # With no queues, the exam (door-to-provider) starts the moment triage ends.
        self.assertEqual(metrics.wait_times[3], [duration])


if __name__ == "__main__":
    unittest.main()
