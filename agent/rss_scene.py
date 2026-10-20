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
        import warp as wp

        raw_frame = np.asarray(self.frame, dtype=float)
        if (raw_frame.shape != (7,) or not np.isfinite(raw_frame).all()
                or not np.allclose(raw_frame[3:6], 0, rtol=0, atol=1e-12)
                or not np.isclose(abs(raw_frame[6]), 1, rtol=0, atol=1e-12)):
            raise ValueError("whole MJCF placement currently supports translation only")
        frame = np.asarray(self.placed((0, 0, 0, 0, 0, 0, 1)), dtype=float)
        if np.any(frame[:3] != 0):
            source = mujoco.MjModel.from_xml_path(self.spec.path)
            # The importer leaves world-relative equality anchors/relposes
            # unchanged. Refuse these instead of placing constrained bodies at
            # the old world origin. Body-to-body constraints remain local.
            body_equalities = np.isin(source.eq_type, [mujoco.mjtEq.mjEQ_CONNECT, mujoco.mjtEq.mjEQ_WELD])
            # Site IDs are not body IDs. The native site's equality conversion
            # has not been qualified here; keep it out of translated imports.
            if np.any(body_equalities & (source.eq_objtype == mujoco.mjtObj.mjOBJ_SITE)):
                raise ValueError("translated MJCF with a site-based equality is not supported")
            if np.any(body_equalities & ((source.eq_obj1id <= 0) | (source.eq_obj2id <= 0))):
                raise ValueError("translated MJCF with a world-connected equality is not supported")
        # None honors each root's own freejoint/fixed declaration. False would
        # anchor objects; the robot importer additionally strips them entirely.
        builder.add_mjcf(self.spec.path, xform=wp.transform(tuple(frame[:3]), tuple(frame[3:])),
                         floating=None, collapse_fixed_joints=False,
                         enable_self_collisions=True, ctrl_direct=True, verbose=False)

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
