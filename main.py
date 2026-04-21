import random
import time
from pathlib import Path
import salabim as sim

# Just using generator-based simulation.
sim.yieldless(False)

# Seed both Python's random and Salabim's internal RNG so a single run is repeatable given the seed.
seed = int(time.time())
random.seed(seed)
sim.random_seed(seed)

# Environment/resources, arrival process, and metrics collection.
from simulation.environment import create_environment
from simulation.arrival_generator import ArrivalGenerator
from simulation.esi import ESI
from simulation.data_reader import ERDataReader
from config import SIMULATION_TIME, DATASET_PATH, ANIMATE
print("Seed: ", seed)

# Create the environment and shared resources used by all patients.
env, triage, providers, beds = create_environment()

if ANIMATE:
    from simulation.animation import setup_animation
    setup_animation(env)

esi_tracker = ESI()

# Optional dataset-driven distributions.
data = None

if DATASET_PATH:
    # Treat relative dataset paths as relative to the project root (this file's directory).
    dataset_path = Path(DATASET_PATH)
    if not dataset_path.is_absolute():
        dataset_path = Path(__file__).resolve().parent / dataset_path

    if dataset_path.exists():
        # Load and preprocess the dataset into distributions used by ArrivalGenerator.
        reader = ERDataReader(dataset_path)
        data = reader.load_and_prepare()
        print(f"Dataset loaded successfully from {dataset_path}.")
    else:
        # Missing dataset, just run the simulation with times from config.
        print(f"Dataset not found at {dataset_path}. \nUsing synthetic generation...")
else:
    print("No dataset path provided. Using synthetic generation.")

# This continuously spawns Patient components over time.
ArrivalGenerator(
    triage=triage,
    providers=providers,
    beds=beds,
    metrics=esi_tracker,
    data=data
)

env.run(till=SIMULATION_TIME)
print(f"Simulation Time Elapsed: {env.now():.2f} minutes")
esi_tracker.report()
