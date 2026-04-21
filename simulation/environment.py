import salabim as sim
from config import NUM_TRIAGE_NURSES, NUM_PROVIDERS, NUM_BEDS

def create_environment():
    env = sim.Environment(trace = False) # just creating a new simulated environment, trace -> False to avoid printing every event to console

    # Queues used for animation/visualization. Patients will enter/leave these during their journey.
    env.q_wait_triage = sim.Queue("q_wait_triage")
    env.q_in_triage = sim.Queue("q_in_triage")
    env.q_wait_provider = sim.Queue("q_wait_provider")
    env.q_in_treatment = sim.Queue("q_in_treatment")
    env.q_wait_bed = sim.Queue("q_wait_bed")
    env.q_in_bed = sim.Queue("q_in_bed")

    # Triage staffing (number of nurses doing triage in parallel).
    triage = sim.Resource("triage_nurse", capacity = NUM_TRIAGE_NURSES)
    providers = sim.Resource("providers", capacity = NUM_PROVIDERS) # creating resource for providers, capacity based on config.py
    beds = sim.Resource("beds", capacity = NUM_BEDS) # creating resource for beds, capacity based on config.py

    return env, triage, providers, beds # returning the environment and resources to be used in the simulation
