import salabim as sim
from config import NUM_PROVIDERS, NUM_BEDS

def create_resources(env):
    triage_nurse = sim.Resource("triage_nurse", capacity = 1)
    providers = sim.Resource("providers", capacity = NUM_PROVIDERS)
    beds = sim.Resource("beds", capacity = NUM_BEDS)

    return triage_nurse, providers, beds