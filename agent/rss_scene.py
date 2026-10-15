"""Import an entire rigid MJCF into RSS without dropping task mechanisms."""
from dataclasses import dataclass

import mujoco
import numpy as np

import robosimstudio as rss
from robosimstudio.scene.handles import EntityHandle
from robosimstudio.core.probes.contact import ContactQueries


@dataclass(frozen=True, kw_only=True)
class TaskSceneSpec:
    path: str
    name: str = "task_scene"
    entity: str = "dexagent_mjcf_scene"
    stage: str = "scenery"


@rss.register_entity("dexagent_mjcf_scene")
class TaskSceneEntity(rss.Entity):
    domain = "articulated"

    def build(self, builder, episode):
        # None honors each root's own freejoint/fixed declaration. False would
        # anchor objects; the robot importer additionally strips them entirely.
        start = builder.shape_count
        builder.add_mjcf(self.spec.path, floating=None, collapse_fixed_joints=False,
                         enable_self_collisions=True, ctrl_direct=True, verbose=False)
        # Preview colors do not alter contact properties or shape collision bits.
        for i in range(start, builder.shape_count):
            body = builder.shape_body[int(i)]
            name = builder.body_label[body].rsplit("/", 1)[-1] if body >= 0 else ""
            if name.startswith("robot_"):
                builder.shape_color[int(i)] = (0.62, 0.66, 0.71)

    def handle(self, model):
        bodies = tuple(int(i) for i in self.range.body_indices)
        return EntityHandle(name=self.name, body=bodies[0], bodies=bodies,
                            shapes=self.range.shape_indices) if bodies else None


class CPUContactProbe(ContactQueries):
    """The same force/depth queries over native MuJoCo CPU contacts."""

    def __init__(self, solver, model, dt):
        self.solver, self.dt = solver, float(dt)
        # SolverMuJoCo generates each geom name as <shape label>_<shape index>.
        self.geom_to_shape = np.asarray([int(solver.mj_model.geom(i).name.rsplit("_", 1)[-1])
                                         for i in range(solver.mj_model.ngeom)])
        if np.any(self.geom_to_shape < 0) or np.any(self.geom_to_shape >= model.shape_count):
            raise ValueError("CPU geom-to-shape mapping is invalid")
        self.step = None
        self.geoms = np.zeros((0, 2), dtype=int)
        self.impulses = np.zeros((0, 3))
        self.depths = np.zeros(0)

    def update(self, step=None):
        if step is not None and step == self.step:
            return
        self.step = step
        m, d = self.solver.mj_model, self.solver.mj_data
        self.geoms = np.asarray([[c.geom1, c.geom2] for c in d.contact], dtype=int).reshape(-1, 2)
        self.depths = np.asarray([c.dist for c in d.contact])
        self.impulses = np.zeros((d.ncon, 3))
        for i, contact in enumerate(d.contact):
            force = np.zeros(6)
            mujoco.mj_contactForce(m, d, i, force)
            self.impulses[i] = contact.frame.reshape(3, 3).T @ force[:3] * self.dt
