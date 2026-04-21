"""
This module only deals with visualization (layout, colors, labels). It intentionally does not
change the simulation model or its outcomes.
"""

import salabim as sim
from config import ANIMATION_SPEED

def _animate_text(*args, **kwargs):
    try:
        return sim.AnimateText(*args, **kwargs)
    except TypeError:
        for key in ["textcolor", "fillcolor", "color", "bgcolor", "backgroundcolor"]:
            kwargs.pop(key, None)
        return sim.AnimateText(*args, **kwargs)


def _try_animation_kwargs(env):
    """
    Start animation with best-effort kwargs so coordinates and background are stable.
    """
    try:
        env.animate(True, x0=0, x1=420, y0=0, y1=520, backgroundcolor="#000000")
        return
    except TypeError:
        pass

    try:
        env.animate(True, x0=0, x1=420, y0=0, y1=520)
        return
    except TypeError:
        pass

    env.animate(True)


def _try_set_animation_parameters(env):
    # Background
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

    # Speed
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

def setup_animation(env):
    """
    Configure Salabim's built-in animation window for this simulation.

    This animates which Patient components enter/leave as they progress:
    - q_wait_triage, q_in_triage
    - q_wait_provider, q_in_treatment
    - q_wait_bed, q_in_bed
    """
    _try_animation_kwargs(env)
    _try_set_animation_parameters(env)

    white = "#ffffff"
    _animate_text(
        lambda: f"Sim time: {env.now():.1f} min",
        x=350,
        y=12,
        fontsize=12,
        xy_anchor="ne",
        textcolor=white,
    )

    # Layout: keep all 6 stages visible.
    x0 = 12
    title_gap = 14
    step = 72
    y0 = 28

    sections = [
        ("Waiting for triage", env.q_wait_triage),
        ("In triage", env.q_in_triage),
        ("Waiting for provider", env.q_wait_provider),
        ("In treatment", env.q_in_treatment),
        ("Waiting for bed", env.q_wait_bed),
        ("In bed", env.q_in_bed),
    ]

    for i, (title, queue) in enumerate(sections):
        y_title = y0 + i * step
        y_queue = y_title + title_gap

        # Show live counts so we can verify patients are entering later queues.
        _animate_text(
            lambda qq=queue, t=title: f"{t} ({len(qq)})",
            x=x0,
            y=y_title,
            fontsize=10,
            xy_anchor="w",
            textcolor=white,
        )
        queue.animate(x=x0, y=y_queue, direction="e", title="")
