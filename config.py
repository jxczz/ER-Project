# Staffing. Nurses do triage, treatment, and discharge/transfer; providers do examination and diagnosis.
NUM_NURSES = 6
NUM_PROVIDERS = 3
NUM_BEDS = 25

# These are in minutes and can be adjusted at any time if no dataset is provided.
MEAN_ARRIVAL_TIME = 9
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
    },
    "imaging": {
        "capacity": 2,
        "mean_time": 40,
        "probability": {1: 0.9, 2: 0.75, 3: 0.5, 4: 0.25, 5: 0.05},
    },
}

# Treatment after diagnosis (nurse + bed). Keyed by ESI 1..5.
TREATMENT_PROBABILITY = {1: 1.0, 2: 0.9, 3: 0.7, 4: 0.4, 5: 0.2}
MEAN_TREATMENT_TIME = {1: 90, 2: 60, 3: 40, 4: 20, 5: 10}

# Disposition. Admitted/transferred patients board in their ED bed before a nurse hands them off.
ADMIT_PROBABILITY = {1: 0.8, 2: 0.5, 3: 0.2, 4: 0.03, 5: 0.01}
MEAN_BOARDING_TIME = 120
MEAN_DISCHARGE_TIME = 15
MEAN_TRANSFER_TIME = 25

# Shared-pool task ordering: lower rank is served first, then lower ESI within the same task.
# Finishing patients first frees beds; triage is FIFO since acuity is unknown until it is done.
NURSE_TASK_RANK = {"disposition": 0, "treatment": 1, "triage": 2}
PROVIDER_TASK_RANK = {"diagnosis": 0, "exam": 1}

# Most patients are ESI 3/4, barely any are ESI 1.
ESI_WEIGHTS_SYNTHETIC = [3, 12, 40, 35, 10]  # ESI 1..5
SIMULATION_TIME = 5000

DATASET_PATH = "nhamcs/nhamcs2022.parquet"

# Turn this to True for animation, otherwise keep it False.
ANIMATE = True

# Makes animation speed up.
ANIMATION_SPEED = 5
