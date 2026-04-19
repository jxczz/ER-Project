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
from simulation.data_reader import ERDataReader
from config import SIMULATION_TIME, DATASET_PATH

print("Seed: ", seed)

env, triage, providers, beds = create_environment()
esi_tracker = ESI()

data = None

if DATASET_PATH:
    reader = ERDataReader(DATASET_PATH)
    data = reader.load_and_prepare()
    print("Dataset loaded successfully.")
else:
    print("No dataset path provided. Using synthetic random generation.")

ArrivalGenerator(
    triage=triage,
    providers=providers,
    beds=beds,
    metrics=esi_tracker,
    data=data
)

env.run(till=SIMULATION_TIME)
esi_tracker.report()
