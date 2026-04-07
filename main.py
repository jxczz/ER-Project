import random
import time
import salabim as sim
sim.yieldless(False)

seed = int(time.time())
random.seed(seed)
sim.random_seed(seed)

from simulation.environment import create_environment
from simulation.arrival_generator import ArrivalGenerator
from simulation.esi import ESI
from config import SIMULATION_TIME

print("Seed: ", seed)

env, triage, providers, beds = create_environment() # creates the simulation environment and resources
esi_tracker = ESI() # creates instance of ESI tracker to show patient distribution across ESI levels
ArrivalGenerator(
    triage = triage,
    providers = providers,
    beds = beds,
    metrics = esi_tracker
)

env.run(till = SIMULATION_TIME) # runs the simulation based on the specified simulation time from config.py
esi_tracker.report() # prints the report of patient distribution across ESI levels and average wait times