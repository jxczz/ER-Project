import time
from pathlib import Path
import salabim as sim

# Just using generator-based simulation.
sim.yieldless(False)

# The seed is applied when the environment is created (Salabim reseeds Python's random and numpy
# there), so a run can be repeated by reusing the printed seed.
seed = int(time.time())

# Environment/resources, arrival process, and metrics collection.
from simulation.environment import create_environment
from simulation.arrival_generator import ArrivalGenerator
from simulation.hospital import HospitalCensus
from simulation.esi import ESI
from simulation.data_reader import ERDataReader
from config import SIMULATION_TIME, DATASET_PATH, ARRIVALS_PER_DAY, ANIMATE
print("Seed: ", seed)

# Create the environment and shared resources used by all patients.
env, staff, beds, testing, hospital_beds = create_environment(random_seed=seed)

if ANIMATE:
    from simulation.animation import setup_animation
    setup_animation(env, hospital_beds)

esi_tracker = ESI()

# The main hospital's own patients, so hospital occupancy fluctuates independently of the ED.
HospitalCensus(hospital_beds=hospital_beds, metrics=esi_tracker)

# Optional dataset-driven distributions.
data = None

if DATASET_PATH:
    # Treat relative dataset paths as relative to the project root (this file's directory).
    dataset_path = Path(DATASET_PATH)
    if not dataset_path.is_absolute():
        dataset_path = Path(__file__).resolve().parent / dataset_path

    if dataset_path.exists():
        # Load and preprocess the dataset into distributions used by ArrivalGenerator.
        reader = ERDataReader(dataset_path, arrivals_per_day=ARRIVALS_PER_DAY)
        data = reader.load_and_prepare()
        print(f"Dataset loaded successfully from {dataset_path}.")
    else:
        # Missing dataset, just run the simulation with times from config.
        print(f"Dataset not found at {dataset_path}. \nUsing synthetic generation...")
else:
    print("No dataset path provided. Using synthetic generation.")

# This continuously spawns Patient components over time.
ArrivalGenerator(
    staff=staff,
    beds=beds,
    testing=testing,
    hospital_beds=hospital_beds,
    metrics=esi_tracker,
    data=data
)

env.run(till=SIMULATION_TIME)
print(f"Simulation Time Elapsed: {env.now():.2f} minutes")
esi_tracker.report(hospital_beds, env=env, beds=beds)
