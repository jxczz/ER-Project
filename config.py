# Staffing: one independent pool per stage (number of staff working that stage).
# No stage draws on another stage's pool.
STAFF = {
    "triage": 2,
    "exam": 2,
    "diagnosis": 2,
    "treatment": 4,
    "extra_treatment": 2,  # patients whose admission was blocked (no hospital bed)
    "disposition": 3,      # discharge/transfer
}
NUM_BEDS = 25

# These are in minutes and can be adjusted at any time if no dataset is provided.
MEAN_ARRIVAL_TIME = 16  # ~90 arrivals/day, matching the NHAMCS average volume
# Triage duration (minutes), lognormal fitted to the median and IQR from "Emergency department
# triage performance timing", a multicenter study of 1,114 triages in 11 Italian EDs
# (Int Emerg Nurs, PMID 26796287).
TRIAGE_TIME_MEDIAN = 2.58
TRIAGE_TIME_IQR = (1.36, 4.35)
MEAN_EXAM_TIME = 15
MEAN_DIAGNOSIS_TIME = 10

# Diagnostic testing. Each test type has its own pool (e.g. lab techs, scanners). Initial tests are
# ordered after examination from INITIAL_TEST_WEIGHTS below. The patient keeps their bed while tests run.
# A type either holds its pool for an exponential time with mean "mean_time", or (CT) holds it for a
# lognormal room time and then waits a lognormal report delay that holds no pool.
TEST_TYPES = {
    "lab": {
        "capacity": 3,
        "mean_time": 45,
        "follow_up_probability": {1: 0.5, 2: 0.5, 3: 0.4, 4: 0.3, 5: 0.3},
    },
    # CT scanner. One scanner: the ED of Stenson et al. 2021 (~53k visits/yr) ran on one dedicated ED
    # CT scanner, and ours sees ~33k/yr; the scanner count is otherwise unmeasured.
    "ct": {
        "capacity": 1,
        # Time in the CT room (holds the scanner), patient in to patient out, observer-timed: median
        # 12.1 min, IQR 9.6-17.7 (Stenson BA et al., "Time in the CT scanner room for emergency
        # department patients", Am J Emerg Med 2021;40:159-161, doi:10.1016/j.ajem.2020.10.012; single
        # ED scanner, Boston, n=102).
        "room_time_median": 12.1,
        "room_time_iqr": (9.6, 17.7),
        # Report delay after the scan (no resource held; results are ready only after it): exam
        # completion to attending final signature, median 44 min, IQR 21-80 (Scheinfeld A et al.,
        # Emerg Radiol 2023;30:153-159, doi:10.1007/s10140-022-02110-9; ED CT read by an attending
        # alone, n=21,874). Caveat: overnight shifts only (4 US hospitals) and time to the final
        # report, not a preliminary read.
        "report_delay_median": 44,
        "report_delay_iqr": (21, 80),
        # CT is never a synthetic follow-up test.
        "follow_up_probability": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
    },
    # X-ray, ultrasound, MRI and other imaging. Capacity, mean_time and follow-up chances are
    # placeholders, not measured.
    "other imaging": {
        "capacity": 2,
        "mean_time": 40,
        "follow_up_probability": {1: 0.7, 2: 0.7, 3: 0.6, 4: 0.5, 5: 0.5},
    },
}

# Initial tests after examination, drawn jointly since lab, CT and other imaging are correlated.
# NHAMCS 2022 PATWT-weighted shares by IMMEDR 1-5 (used as ESI), excluding DIAGSCRN == 2 (item blank),
# rounded to 3 decimals. lab = any blood test (ABG BAC BMP BNP BUNCREAT CARDENZ CBC CMP BLOODCX DDIMER
# ELECTROL GLUCOSE LACTATE LFT PTTINR OTHERBLD); ct = CATSCAN; other imaging = any of XRAY ULTRASND
# MRI OTHIMAGE (together with ct this is exactly ANYIMAGE). Follow-up probabilities above remain synthetic.
INITIAL_TEST_WEIGHTS = {
    1: {"none": 0.110, "lab": 0.107, "ct": 0.018, "other imaging": 0.049,
        "lab+ct": 0.083, "lab+other imaging": 0.235, "ct+other imaging": 0.017, "lab+ct+other imaging": 0.381},
    2: {"none": 0.095, "lab": 0.171, "ct": 0.013, "other imaging": 0.036,
        "lab+ct": 0.106, "lab+other imaging": 0.323, "ct+other imaging": 0.020, "lab+ct+other imaging": 0.236},
    3: {"none": 0.189, "lab": 0.160, "ct": 0.026, "other imaging": 0.117,
        "lab+ct": 0.141, "lab+other imaging": 0.214, "ct+other imaging": 0.024, "lab+ct+other imaging": 0.128},
    4: {"none": 0.568, "lab": 0.045, "ct": 0.029, "other imaging": 0.253,
        "lab+ct": 0.022, "lab+other imaging": 0.046, "ct+other imaging": 0.021, "lab+ct+other imaging": 0.017},
    5: {"none": 0.798, "lab": 0.058, "ct": 0.009, "other imaging": 0.079,
        "lab+ct": 0.012, "lab+other imaging": 0.009, "ct+other imaging": 0.006, "lab+ct+other imaging": 0.028},
}

# A diagnosis can fail, which orders follow-up tests (from "follow_up_probability" above, at least
# one) and another diagnosis. If the last allowed attempt also fails, the patient is admitted.
DIAGNOSIS_FAILURE_PROBABILITY = {1: 0.1, 2: 0.08, 3: 0.06, 4: 0.04, 5: 0.02}
MAX_DIAGNOSIS_ATTEMPTS = 2

# Treatment after diagnosis (treatment staff + bed). Keyed by ESI 1..5.
TREATMENT_PROBABILITY = {1: 1.0, 2: 0.9, 3: 0.7, 4: 0.4, 5: 0.2}
MEAN_TREATMENT_TIME = {1: 90, 2: 60, 3: 40, 4: 20, 5: 10}

# Disposition. Admitted patients board in their ED bed for up to MAX_BOARDING_WAIT minutes waiting
# for a main-hospital bed, then are handed off (transfer). If no bed frees up in time they get extra
# ED treatment (extra treatment staff) and are discharged instead.
ADMIT_PROBABILITY = {1: 0.8, 2: 0.5, 3: 0.2, 4: 0.03, 5: 0.01}
MAX_BOARDING_WAIT = 240
MEAN_EXTENDED_TREATMENT_TIME = {1: 120, 2: 90, 3: 60, 4: 45, 5: 30}
MEAN_DISCHARGE_TIME = 15
MEAN_TRANSFER_TIME = 25

# Main hospital. Its beds are shared by ED admissions and the hospital's own (non-ED) patients,
# so occupancy fluctuates. Non-ED patients who find it full are turned away.
NUM_HOSPITAL_BEDS = 300
HOSPITAL_INITIAL_OCCUPANCY = 0.85  # share of beds already filled at time zero
NON_ED_ADMISSIONS_PER_DAY = 40
# Relative non-ED admissions per hour of day (0..23): mostly daytime, e.g. planned surgeries.
NON_ED_ADMISSION_HOURLY_WEIGHTS = [1, 1, 1, 1, 1, 1, 2, 4, 6, 6, 6, 5, 5, 5, 4, 4, 3, 3, 2, 2, 2, 1, 1, 1]
MEAN_HOSPITAL_STAY = 4.5 * 24 * 60  # minutes
HOSPITAL_DISCHARGE_HOURS = (11, 17)  # hospital patients leave between these hours

# Most patients are ESI 3/4, barely any are ESI 1.
ESI_WEIGHTS_SYNTHETIC = [3, 12, 40, 35, 10]  # ESI 1..5

# Ambulance arrivals skip triage and go straight to the ED bed queue (still ESI priority). With a
# dataset, the ambulance share by hour and the acuity mix of each arrival mode come from NHAMCS ARREMS.
# False makes every patient a walk-in, using the overall acuity mix.
AMBULANCE_ARRIVALS = True
# Synthetic mode only: share of arrivals by ambulance and their acuity (NHAMCS 2022 ambulance arrivals,
# ~17.5%). Walk-ins use ESI_WEIGHTS_SYNTHETIC.
AMBULANCE_SHARE_SYNTHETIC = 0.175
ESI_WEIGHTS_AMBULANCE_SYNTHETIC = [4, 29, 54, 11, 2]  # ESI 1..5
SIMULATION_TIME = 5000

DATASET_PATH = "nhamcs/nhamcs2022.parquet"
# The hour-of-day arrival pattern always comes from NHAMCS; this only sets total ED arrivals per day.
# None uses the NHAMCS national mean ED (~91/day for 2022).
ARRIVALS_PER_DAY = None

# Turn this to True for animation, otherwise keep it False.
ANIMATE = True

# Makes animation speed up.
ANIMATION_SPEED = 5
