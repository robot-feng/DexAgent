"""Small compatibility helpers for MuJoCo's Python bindings."""
from __future__ import annotations

import mujoco
import numpy as np


def full_mass_matrix(model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray:
    """Expand the sparse inertia matrix across supported ``mj_fullM`` signatures."""
    full = np.zeros((model.nv, model.nv))
    try:
        mujoco.mj_fullM(model, data, full)
    except TypeError:
        mujoco.mj_fullM(model, full, data.qM)
    return full
