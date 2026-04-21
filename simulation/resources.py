import salabim as sim
from config import NUM_TRIAGE_NURSES, NUM_PROVIDERS, NUM_BEDS

def create_resources(env):
    triage_nurse = sim.Resource("triage_nurse", capacity = NUM_TRIAGE_NURSES)
    providers = sim.Resource("providers", capacity = NUM_PROVIDERS)
    beds = sim.Resource("beds", capacity = NUM_BEDS)

    # Return resources so other components can request/release them.
    return triage_nurse, providers, beds
