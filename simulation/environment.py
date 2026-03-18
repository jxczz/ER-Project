import salabim as sim
from config import NUM_PROVIDERS, NUM_BEDS

def create_environment():
    env = sim.Environment(trace = False) # just creating a new simulated environment, trace -> False to avoid printing every event to console

    triage = sim.Resource("triage_nurse", capacity = 1) # creating resource for triage nurse, set to 1 to have only one triage nurse available
    providers = sim.Resource("providers", capacity = NUM_PROVIDERS) # creating resource for providers, capacity based on config.py
    beds = sim.Resource("beds", capacity = NUM_BEDS) # creating resource for beds, capacity based on config.py

    return env, triage, providers, beds # returning the environment and resources to be used in the simulation