import salabim as sim
from config import STAFF, NUM_BEDS, TEST_TYPES, NUM_HOSPITAL_BEDS

# Animation queues in patient-flow order: (attribute name, label).
STAGE_QUEUES = [
    ("q_wait_triage", "Waiting for triage"),
    ("q_in_triage", "In triage"),
    ("q_wait_bed", "Waiting for ED bed"),
    ("q_wait_exam", "Waiting for exam (bed held)"),
    ("q_in_exam", "Examination (bed held)"),
    ("q_testing", "Awaiting test results (bed held)"),
    ("q_wait_diagnosis", "Waiting for diagnosis (bed held)"),
    ("q_in_diagnosis", "Diagnosis (bed held)"),
    ("q_wait_treatment", "Waiting for treatment (bed held)"),
    ("q_in_treatment", "Treatment (bed held)"),
    ("q_boarding", "Boarding: waiting for hospital bed (bed held)"),
    ("q_wait_extended", "Waiting for extra treatment (bed held)"),
    ("q_in_extended", "Extra treatment, no hospital bed (bed held)"),
    ("q_wait_disposition", "Waiting for discharge/transfer (bed held)"),
    ("q_in_disposition", "Discharge/transfer (bed held)"),
]


def create_environment():
    env = sim.Environment(trace = False) # just creating a new simulated environment, trace -> False to avoid printing every event to console

    # Queues used for animation/visualization. Patients will enter/leave these during their journey.
    for name, _label in STAGE_QUEUES:
        setattr(env, name, sim.Queue(name))

    # One staff pool per stage, e.g. {"triage": Resource, "exam": Resource, ...}.
    staff = {
        stage: sim.Resource(f"{stage}_staff", capacity = capacity)
        for stage, capacity in STAFF.items()
    }
    beds = sim.Resource("beds", capacity = NUM_BEDS) # creating resource for beds, capacity based on config.py
    # One pool per test type, e.g. {"lab": Resource, "imaging": Resource}.
    testing = {
        name: sim.Resource(name, capacity = spec["capacity"])
        for name, spec in TEST_TYPES.items()
    }

    # Main-hospital beds, shared with the hospital's own (non-ED) patients.
    hospital_beds = sim.Resource("hospital_beds", capacity = NUM_HOSPITAL_BEDS)

    return env, staff, beds, testing, hospital_beds # returning the environment and resources to be used in the simulation
