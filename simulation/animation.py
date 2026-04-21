import salabim as sim

from config import ANIMATION_SPEED


def _animate_text(*args, **kwargs):
    """
    Create an AnimateText in a version-tolerant way.
    Some Salabim versions differ in supported kwargs; we try with colors first, then retry without.
    """
    try:
        return sim.AnimateText(*args, **kwargs)
    except TypeError:
        # Retry without optional styling args that may not exist in this Salabim build.
        for k in ["textcolor", "fillcolor", "color", "bgcolor", "backgroundcolor"]:
            kwargs.pop(k, None)
        return sim.AnimateText(*args, **kwargs)


def setup_animation(env):
    """
    Configure Salabim's built-in animation window for this simulation.

    Animate our explicit stage queues (created on env in simulation/environment.py). This ensures
    the animation shows movement for every stage label, because Patient explicitly enters/leaves
    these queues as it progresses.
    """
    # Use a small fixed "world" so coordinates stay stable even on small windows.
    # Different Salabim versions accept different kwargs; try the most specific first.
    try:
        env.animate(True, x0=0, x1=360, y0=0, y1=460, backgroundcolor="#000000")
    except TypeError:
        try:
            env.animate(True, x0=0, x1=360, y0=0, y1=460)
        except TypeError:
            env.animate(True)

    # Best-effort: set animation background after the fact (API differs by version).
    for method_name, kwargs in [
        ("animation_parameters", {"backgroundcolor": "#000000"}),
        ("animation_parameters", {"background_color": "#000000"}),
        ("animation_parameters", {"bgcolor": "#000000"}),
    ]:
        method = getattr(env, method_name, None)
        if method is None:
            continue
        try:
            method(**kwargs)
            break
        except TypeError:
            continue

    # Speed up animation playback. Salabim's API varies by version, so try a few options.
    for method_name, args, kwargs in [
        ("animation_speed", (ANIMATION_SPEED,), {}),
        ("speed", (ANIMATION_SPEED,), {}),
        ("animation_parameters", (), {"speed": ANIMATION_SPEED}),
    ]:
        method = getattr(env, method_name, None)
        if method is None:
            continue
        try:
            method(*args, **kwargs)
            break
        except TypeError:
            continue

    # White text looks best on black. (If unsupported, _animate_text falls back gracefully.)
    white = "#ffffff"

    # "Sim time" pinned to the top-right.
    # With the fixed world coords above, x=350 sits near the right edge.
    _animate_text(lambda: f"Sim time: {env.now():.1f} min", x=350, y=12, fontsize=12, xy_anchor="ne", textcolor=white)

    # Layout: keep all stages visible in a small/narrow window with consistent title->queue spacing.
    x0 = 12
    title_gap = 16
    step = 85
    y0 = 35

    sections = [
        ("Waiting for triage", env.q_wait_triage),
        ("In triage", env.q_in_triage),
        ("Waiting for provider", env.q_wait_provider),
        ("In treatment", env.q_in_treatment),
        ("Waiting for bed", env.q_wait_bed),
        ("In bed", env.q_in_bed),
    ]

    for i, (title, q) in enumerate(sections):
        y_title = y0 + i * step
        y_queue = y_title + title_gap
        _animate_text(title, x=x0, y=y_title, fontsize=10, xy_anchor="w", textcolor=white)
        q.animate(x=x0, y=y_queue, direction="e", title="")
