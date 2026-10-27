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
TEST_TYPES = {
    "lab": {
        "capacity": 3,
        "mean_time": 45,
        "follow_up_probability": {1: 0.5, 2: 0.5, 3: 0.4, 4: 0.3, 5: 0.3},
    },
    "imaging": {
        "capacity": 2,
        "mean_time": 40,
        "follow_up_probability": {1: 0.7, 2: 0.7, 3: 0.6, 4: 0.5, 5: 0.5},
    },
}

# Initial tests after examination, drawn jointly since lab and imaging are correlated. NHAMCS 2022
# PATWT-weighted shares by IMMEDR 1-5 (used as ESI), excluding DIAGSCRN == 2 (item blank). lab = any
# blood test (ABG BAC BMP BNP BUNCREAT CARDENZ CBC CMP BLOODCX DDIMER ELECTROL GLUCOSE LACTATE LFT
# PTTINR OTHERBLD); imaging = ANYIMAGE. Follow-up probabilities above remain synthetic.
INITIAL_TEST_WEIGHTS = {
    1: {"none": 0.110, "lab": 0.107, "imaging": 0.084, "both": 0.699},
    2: {"none": 0.095, "lab": 0.171, "imaging": 0.069, "both": 0.665},
    3: {"none": 0.189, "lab": 0.160, "imaging": 0.168, "both": 0.483},
    4: {"none": 0.568, "lab": 0.045, "imaging": 0.304, "both": 0.084},
    5: {"none": 0.798, "lab": 0.058, "imaging": 0.095, "both": 0.049},
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
