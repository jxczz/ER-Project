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
MEAN_TRIAGE_TIME = 5
MEAN_EXAM_TIME = 15
MEAN_DIAGNOSIS_TIME = 10

# Diagnostic testing. Each test type has its own pool (e.g. lab techs, scanners) and is ordered
# after examination with a per-ESI probability. The patient keeps their bed while tests run.
TEST_TYPES = {
    "lab": {
        "capacity": 3,
        "mean_time": 45,
        "probability": {1: 0.95, 2: 0.9, 3: 0.75, 4: 0.3, 5: 0.1},
        "follow_up_probability": {1: 0.5, 2: 0.5, 3: 0.4, 4: 0.3, 5: 0.3},
    },
    "imaging": {
        "capacity": 2,
        "mean_time": 40,
        "probability": {1: 0.9, 2: 0.75, 3: 0.5, 4: 0.25, 5: 0.05},
        "follow_up_probability": {1: 0.7, 2: 0.7, 3: 0.6, 4: 0.5, 5: 0.5},
    },
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
SIMULATION_TIME = 5000

DATASET_PATH = "nhamcs/nhamcs2022.parquet"
# The hour-of-day arrival pattern always comes from NHAMCS; this only sets total ED arrivals per day.
# None uses the NHAMCS national mean ED (~91/day for 2022).
ARRIVALS_PER_DAY = None

# Turn this to True for animation, otherwise keep it False.
ANIMATE = True

# Makes animation speed up.
ANIMATION_SPEED = 5
