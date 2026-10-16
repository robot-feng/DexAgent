"""The physical DexAgent bench in RSS, driven by named MuJoCo actuators.

The complete rigid scene is imported, including free objects and passive
mechanisms. Only the 56 physical robot joints are policy action targets.
"""
from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from tool_library.common.rigid_robot import RigidRobot, compile_rigid, register_model
from agent.run_reports import sha256_file


def named_indices(labels, wanted=None):
    """Resolve importer paths without accepting ambiguous suffixes."""
    result = {}
    for i, label in enumerate(labels):
        name = label.rsplit("/", 1)[-1]
        if wanted is not None and name not in wanted:
            continue
        if name in result:
            raise ValueError(f"ambiguous imported name: {name}")
        result[name] = i
    return result


def inertia_tensors(model):
    tensors = []
    for q, diag in zip(model.body_iquat, model.body_inertia, strict=True):
        r = np.zeros(9)
        mujoco.mju_quat2Mat(r, q)
        r = r.reshape(3, 3)
        tensors.append(r @ np.diag(diag) @ r.T)
    return np.asarray(tensors)


def expand_mjcf_solref_shorthand(xml):
    """Write MuJoCo's implicit damping ratio before RSS parses an MJCF.

    MuJoCo compiles ``solref="0.02"`` as ``(0.02, 1)``. The MJCF importer
    used by this RSS integration currently parses the same shorthand as
    ``(0.02, 0)``. Make the equivalent two-value form explicit so source and
    imported contact behavior agree.
    """
    root = ET.fromstring(xml)
    changed = 0
    for element in root.iter():
        for key in ("solref", "solreffriction", "solreflimit"):
            value = element.get(key)
            if value is None:
                continue
            parts = value.split()
            if len(parts) == 1:
                element.set(key, f"{parts[0]} 1")
                changed += 1
    return ET.tostring(root, encoding="unicode"), changed


class SharpaBridge:
    """Keep the source scene's physics and expose its full actuator vector."""

    def __init__(self, output, *, source=None, device="cuda:0", solver=None, physics_device="gpu"):
        import robosimstudio as rss
        from robosimstudio.scene import register_cell
        from robosimstudio.scene.physics.compose import compose
        from robosimstudio.specs.physics import backend

        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        if physics_device not in {"cpu", "gpu"}:
            raise ValueError("physics_device must be cpu or gpu")
        self.physics_device = physics_device
        use_cpu = physics_device == "cpu"
        self.rig = compile_rigid(source or Path(__file__).resolve().parents[1] / "setup/scene.xml")
        (self.output / "original_source_scene.xml").write_text(self.rig.xml)
        self.source_adaptations = {}
        if (not use_cpu and (self.rig.model.opt.noslip_iterations or int(self.rig.model.opt.jacobian) == int(mujoco.mjtJacobian.mjJAC_AUTO))) or solver:
            # MuJoCo-Warp explicitly refuses the noslip solver. Keep the source
            # artifact and make the runtime difference reviewable and replayable.
            spec = mujoco.MjSpec.from_string(self.rig.xml)
            if solver:
                choices = {"cg": mujoco.mjtSolver.mjSOL_CG, "newton": mujoco.mjtSolver.mjSOL_NEWTON}
                if solver not in choices:
                    raise ValueError(f"unsupported solver: {solver}")
                self.source_adaptations["solver"] = {"source": int(spec.option.solver), "runtime": solver,
                                                     "reason": "explicit rigid-contact solver profile"}
                spec.option.solver = choices[solver]
            if not use_cpu and spec.option.noslip_iterations:
                self.source_adaptations["noslip_iterations"] = {"source": int(spec.option.noslip_iterations),
                                                                "runtime": 0, "reason": "MuJoCo-Warp does not implement noslip"}
                spec.option.noslip_iterations = 0
            if not use_cpu and int(spec.option.jacobian) == int(mujoco.mjtJacobian.mjJAC_AUTO):
                self.source_adaptations["jacobian"] = {"source": "auto", "runtime": "sparse",
                                                       "reason": "explicit sparse layout for mixed articulated/free scenes"}
                spec.option.jacobian = mujoco.mjtJacobian.mjJAC_SPARSE
            model = spec.compile()
            register_model(model, spec)
            self.rig = RigidRobot(self.rig.source_model, model, spec.to_xml())
        self.configure_source(self.rig)
        m = self.source
        runtime = self.output / "runtime_scene.xml"
        runtime_xml, shorthand_count = expand_mjcf_solref_shorthand(self.rig.xml)
        runtime.write_text(runtime_xml)
        if shorthand_count:
            self.source_adaptations["solref_shorthand"] = {
                "expanded_attributes": shorthand_count,
                "reason": "preserve MuJoCo's implicit damping ratio (second value 1) in RSS/Newton import",
            }

        # RSS constructors and readback now share the checked-in rig frame.
        register_cell(rss.CellSpec(name="dexagent_rig", ground_z=-1.0, surface_z=0.0,
                                   provenance="DexAgent setup; identity rig-to-world transform"))
        options = {name: getattr(m.opt, name) for name in (
            "iterations", "ls_iterations", "impratio", "tolerance", "ls_tolerance", "integrator", "cone", "solver", "jacobian")}
        physics = rss.ArrangementSpec(control_hz=1 / (2 * m.opt.timestep), substeps=2,
                                      backends=(("*", backend("newton.solvers.SolverMuJoCo", **options,
                                                            nconmax=2048, njmax=4096, use_mujoco_contacts=True,
                                                            use_mujoco_cpu=use_cpu)),))
        scene = rss.Scene(cell="dexagent_rig", physics=physics, name="dexagent_yam_sharpa")
        from agent.rss_scene import TaskSceneSpec
        scene.add(TaskSceneSpec(path=str(runtime)))

        def preserve_source_inertia(model, parts, embodiment, arrangement, **kwargs):
            # Newton regularizes tiny virtual-link inertias by ~1e-6 kg m².
            # Restore the valid, MuJoCo-compiled source tensors before solvers copy them.
            ids = [m.body(label.rsplit("/", 1)[-1]).id for label in model.body_label]
            tensors = inertia_tensors(m)[ids]
            inverse = np.zeros_like(tensors)
            for i, tensor in enumerate(tensors):
                if np.any(tensor):
                    if np.linalg.eigvalsh(tensor).min() <= 0:
                        raise ValueError(f"invalid source inertia: {model.body_label[i]}")
                    inverse[i] = np.linalg.inv(tensor)
            model.body_inertia.assign(tensors.astype(np.float32))
            model.body_inv_inertia.assign(inverse.astype(np.float32))
            # Compound mesh volumes are integrated differently by Newton and
            # MuJoCo (notably hollow cups/bowls). The compiled source model is
            # the contract for mass and centre of mass as well as inertia.
            masses = m.body_mass[ids]
            model.body_mass.assign(masses.astype(np.float32))
            inv_mass = np.divide(1., masses, out=np.zeros_like(masses), where=masses > 0)
            model.body_inv_mass.assign(inv_mass.astype(np.float32))
            model.body_com.assign(m.body_ipos[ids].astype(np.float32))
            import newton
            # MJCF's inherited classes hide some of this hand's visual geoms.
            # Restore authored render groups 0/1; never change collision flags.
            flags = model.shape_flags.numpy()
            visible = np.isin(model.mujoco.geom_group.numpy(), [0, 1]) & ((flags & int(newton.ShapeFlags.SITE)) == 0)
            flags &= ~int(newton.ShapeFlags.VISIBLE)
            flags[visible] |= int(newton.ShapeFlags.VISIBLE)
            model.shape_flags.assign(flags)
            # finalize() cached only the previously visible shapes in the ray BVH.
            model.bvh_build_shapes(model.state())
            program = compose(model, parts, embodiment, arrangement, **kwargs)
            if use_cpu:
                program.solver.mj_model.opt.noslip_iterations = m.opt.noslip_iterations
            return program

        self.sim = scene.build(device=device, warmup=False, compose=preserve_source_inertia, use_cuda_graph=not use_cpu)
        try:
            if use_cpu:
                from agent.rss_scene import CPUContactProbe
                self.sim.contacts = CPUContactProbe(self.sim.pipeline.solver, self.sim.model, self.sim.pipeline.sim_dt)
            else:
                # RSS's generic Sim probe consults ``program.mujoco_solver``;
                # ScenePipeline owns the active solver for this imported scene.
                # Bind the probe to that concrete solver for contact-based task
                # gates, exactly as the CPU adapter does above.
                from robosimstudio.core.probes import ContactProbe
                self.sim.contacts = ContactProbe(self.sim.pipeline.solver, self.sim.model,
                                                 self.sim.pipeline.sim_dt, world=self.sim.scene.handles.world)
            imported = self.sim.model
            joints = named_indices(imported.joint_label, self.joint_names)
            self.q_indices = np.asarray([imported.joint_q_start.numpy()[joints[n]] for n in self.joint_names])
            self.v_indices = np.asarray([imported.joint_qd_start.numpy()[joints[n]] for n in self.joint_names])
            labels = list(imported.mujoco.actuator_label)
            acts = named_indices(labels)
            self.ctrl_indices = np.asarray([acts[n] for n in self.actuator_names])
            self.body_indices = named_indices(imported.body_label)
            all_joints = named_indices(imported.joint_label, self.state_joint_names)
            self.state_q_starts = {n: int(imported.joint_q_start.numpy()[all_joints[n]]) for n in self.state_joint_names}
            home = self.source_q_to_import(self.initial_qpos)
            self.sim.model.joint_q.assign(home)
            self.sim.pipeline.state_0.joint_q.assign(home)
            self.sim.pipeline.state_1.joint_q.assign(home)
            self.sim.controllers["named_actuator"] = self
            self.sim.controllers["joint_reference"] = JointReferenceControl(self)
            self.sim.control_mode = "named_actuator"
        except BaseException:
            self.sim.close()
            raise

    def configure_source(self, rig):
        """Robot targets remain separate from the task's physical state."""
        self.source = m = rig.model
        robot_joints = [j for j in range(m.njnt) if m.joint(j).name.startswith("robot_")]
        if len(robot_joints) != 56 or m.nu != 56 or any(m.jnt_type[j] != mujoco.mjtJoint.mjJNT_HINGE for j in robot_joints):
            raise ValueError("scene requires 56 physical robot hinges and their 56 actuators")
        allowed = {int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE), int(mujoco.mjtJoint.mjJNT_FREE)}
        if any(int(kind) not in allowed for kind in m.jnt_type):
            raise ValueError("unsupported ball joint; rigid hinge/slide/free scenes only")
        if any(m.body(i).name != "camrig" for i in range(m.nbody) if m.body_mocapid[i] >= 0):
            raise ValueError("task objects cannot be mocap-driven")
        if not m.nkey:
            raise ValueError("task scene requires an initial keyframe")
        self.robot_joints = np.asarray(robot_joints)
        self.source_q_indices = m.jnt_qposadr[self.robot_joints]
        self.source_v_indices = m.jnt_dofadr[self.robot_joints]
        self.joint_names = [m.joint(j).name for j in robot_joints]
        self.state_joint_names = [m.joint(j).name for j in range(m.njnt)]
        if any(not n for n in self.state_joint_names):
            raise ValueError("every task joint must be named for state mapping")
        self.actuator_names = [m.actuator(i).name for i in range(m.nu)]
        lookup = {int(j): i for i, j in enumerate(robot_joints)}
        if set(m.actuator_trnid[:, 0]) != set(robot_joints):
            raise ValueError("every actuator must target one robot joint; objects cannot be policy-controlled")
        self.actuator_joint_indices = np.asarray([lookup[int(j)] for j in m.actuator_trnid[:, 0]])
        self.initial_qpos = m.key_qpos[0].copy()
        self.home = self.initial_qpos[self.source_q_indices].copy()
        self.reference = mujoco.MjData(m)
        self.reference.qpos[:] = self.initial_qpos

    def source_q_to_import(self, qpos):
        """MuJoCo wxyz free roots -> Newton xyzw, resolving each joint by name."""
        values = np.zeros(self.sim.model.joint_coord_count, dtype=np.float32)
        for j, name in enumerate(self.state_joint_names):
            source = int(self.source.jnt_qposadr[j])
            dest = self.state_q_starts[name]
            if self.source.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
                values[dest:dest + 7] = np.asarray(qpos)[source + np.array([0, 1, 2, 4, 5, 6, 3])]
            else:
                values[dest] = qpos[source]
        return values

    def start(self):
        """Warm up only after the caller has accepted the import audit."""
        # Establish the calibrated reset pose before physics warm-up moves it.
        self.sim.warmup = False
        self.sim.reset()
        self.sim.warmup = True
        self.sim.reset()

    @property
    def action_dim(self):
        return self.source.nu

    def _q_to_import(self, q):
        values = self.sim.model.joint_q.numpy().astype(np.float32)
        values[self.q_indices] = q
        return values

    def joint_state(self, state=None):
        state = state or self.sim.state
        return state.joint_q.numpy()[self.q_indices], state.joint_qd.numpy()[self.v_indices]

    def reset(self, state):
        self.apply(self.hold_action(state), self.sim.control, state)

    def hold_action(self, state):
        q, _ = self.joint_state(state)
        return self.position_controls(np.clip(q, *self.source.jnt_range[self.robot_joints].T), state)

    def validate_controls(self, action):
        if isinstance(action, dict):
            if set(action) != set(self.actuator_names):
                raise ValueError("supply every named actuator exactly once")
            action = [action[n] for n in self.actuator_names]
        action = np.asarray(action, dtype=float)
        if action.shape != (self.action_dim,) or not np.isfinite(action).all():
            raise ValueError(f"expected {self.action_dim} finite actuator controls")
        limited = self.source.actuator_ctrllimited.astype(bool)
        lo, hi = self.source.actuator_ctrlrange.T
        if np.any(limited & ((action < lo) | (action > hi))):
            raise ValueError("actuator command exceeds source control range")
        return action

    def apply(self, action, control, state):
        action = self.validate_controls(action)
        raw = np.zeros_like(control.mujoco.ctrl.numpy())
        raw[self.ctrl_indices] = action
        control.mujoco.ctrl.assign(raw)

    def position_controls(self, target, state=None, *, qpos=None, qvel=None):
        """Acceptance controller: bounded computed-torque joint PD.

        This is explicitly distinct from Stage 3's Cartesian impedance controller.
        Finger commands retain the original source position actuators.
        """
        q, v = self.joint_state(state) if qpos is None else (np.asarray(qpos), np.asarray(qvel))
        target = np.asarray(target, dtype=float)
        m, d = self.source, self.reference
        if target.shape != (56,) or not np.isfinite(target).all():
            raise ValueError("invalid joint target")
        ranges = m.jnt_range[self.robot_joints]
        if np.any(m.jnt_limited[self.robot_joints].astype(bool) & ((target < ranges[:, 0]) | (target > ranges[:, 1]))):
            raise ValueError("joint target exceeds source limits")
        if len(q) == m.nq and m.nq != 56:
            q, v = q[self.source_q_indices], v[self.source_v_indices]
        d.qpos[self.source_q_indices] = q
        d.qvel[:] = 0
        d.qvel[self.source_v_indices] = v
        mujoco.mj_forward(m, d)
        mass = np.zeros((m.nv, m.nv))
        mujoco.mj_fullM(m, d, mass)
        # Use acceleration gains, then account for link/rotor inertia. Raw torque
        # damping at 250 Hz destabilizes the low-inertia wrist's sampled loop.
        arm = np.asarray(["_arm_" in name for name in self.joint_names])
        acceleration = np.zeros(m.nv)
        acceleration[self.source_v_indices[arm]] = 12.0**2 * (target[arm] - q[arm]) - 24.0 * v[arm]
        torque = mass @ acceleration + d.qfrc_bias
        controls = np.zeros(m.nu)
        for a, name in enumerate(self.actuator_names):
            j = int(m.actuator_trnid[a, 0])
            r = self.actuator_joint_indices[a]
            if name.endswith("_motor"):
                controls[a] = np.clip(torque[m.jnt_dofadr[j]], *m.actuator_ctrlrange[a])
            else:
                controls[a] = target[r]
        return self.validate_controls(controls)

    def audit(self, check):
        """Independent FK and physical parameter checks before executing actions."""
        import newton
        from scipy.spatial.transform import Rotation

        sim, m = self.sim, self.source
        imported, solver = sim.model, sim.pipeline.solver
        starts = imported.joint_qd_start.numpy()
        joints = named_indices(imported.joint_label, self.joint_names)
        dofs = [starts[joints[n]] for n in self.joint_names]
        checks = [check("physical_joint_identity", len(self.joint_names) == 56 and
                        imported.joint_coord_count == m.nq and imported.joint_dof_count == m.nv,
                        {"robot": 56, "state_q": imported.joint_coord_count, "state_v": imported.joint_dof_count}),
                  check("direct_actuator_control", bool(np.all(imported.mujoco.ctrl_source.numpy()[self.ctrl_indices] == 1)))]
        for field, source in (("joint_armature", m.dof_armature[self.source_v_indices]),
                              ("joint_damping", m.dof_damping[self.source_v_indices]),
                              ("joint_limit_lower", m.jnt_range[self.robot_joints, 0]),
                              ("joint_limit_upper", m.jnt_range[self.robot_joints, 1])):
            values = getattr(imported, field).numpy()[dofs]
            checks.append(check(field, bool(np.allclose(values, source, rtol=2e-6, atol=1e-8)),
                                float(np.max(abs(values - source))), "rtol=2e-6, atol=1e-8"))
        expected = inertia_tensors(m)
        ids = [m.body(n).id for n in self.body_indices]
        checks.append(check("body_mass_and_inertia", bool(
            np.allclose(imported.body_mass.numpy(), m.body_mass[ids], rtol=2e-6, atol=1e-9) and
            np.allclose(imported.body_inertia.numpy(), expected[ids], rtol=2e-5, atol=1e-12) and
            np.allclose(imported.body_com.numpy(), m.body_ipos[ids], rtol=2e-6, atol=1e-9))))
        # Compare the actual solver actuators as well, using its own control-source map.
        order = np.argsort(solver.mjc_actuator_to_newton_idx.numpy())[self.ctrl_indices]
        actual = solver.mj_model
        for field in ("actuator_gainprm", "actuator_biasprm", "actuator_gear", "actuator_ctrlrange", "actuator_forcerange",
                      "actuator_ctrllimited", "actuator_forcelimited"):
            checks.append(check(field, bool(np.allclose(getattr(actual, field)[order], getattr(m, field), rtol=2e-6, atol=1e-7))))
        labels = imported.shape_label
        pairs = {frozenset((labels[int(a)].rsplit("/", 1)[-1], labels[int(b)].rsplit("/", 1)[-1]))
                 for a, b in zip(imported.mujoco.pair_geom1.numpy(), imported.mujoco.pair_geom2.numpy(), strict=True)}
        original_pairs = {frozenset((m.geom(int(a)).name, m.geom(int(b)).name))
                          for a, b in zip(m.pair_geom1, m.pair_geom2, strict=True)}
        checks.append(check("explicit_contact_pairs", pairs == original_pairs and actual.npair == m.npair,
                            {"source": m.npair, "solver": actual.npair}))
        source_solref = np.unique(m.geom_solref, axis=0)
        runtime_solref = np.unique(actual.geom_solref, axis=0)
        well_formed = np.all(((runtime_solref[:, 0] > 0) & (runtime_solref[:, 1] > 0)) |
                             ((runtime_solref[:, 0] < 0) & (runtime_solref[:, 1] < 0)) |
                             np.all(runtime_solref == 0, axis=1))
        checks.append(check("contact_solref_preserved", well_formed and
                            np.allclose(runtime_solref, source_solref, rtol=1e-6, atol=1e-8),
                            {"source": source_solref.tolist(), "solver": runtime_solref.tolist()},
                            "same compiled reference values; no mixed/zero-damping pairs"))
        checks.append(check("solver_options", all(np.isclose(getattr(actual.opt, n), getattr(m.opt, n))
                                                  for n in ("integrator", "cone", "solver", "impratio", "iterations", "noslip_iterations"))))
        if self.source_adaptations:
            physically_changed = bool({"noslip_iterations", "jacobian", "solver"} & set(self.source_adaptations))
            checks.append({"id": "source_solver_adaptations", "status": "warning" if physically_changed else "passed", "required": False,
                           "observed": self.source_adaptations,
                           "evidence": "original_source_scene.xml retained; shorthand expansion preserves MuJoCo's compiled contact values" if not physically_changed else
                                      "original_source_scene.xml retained; runtime solver options are explicitly adapted for backend support"})
        rng = np.random.default_rng(0)
        positions, rotations = [], []
        poses = [self.initial_qpos.copy() for _ in range(5)]
        for pose in poses[1:]:
            pose[self.source_q_indices] = np.clip(self.home + rng.uniform(-0.08, 0.08, 56), *m.jnt_range[self.robot_joints].T)
            for j in range(m.njnt):
                if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
                    qa = int(m.jnt_qposadr[j])
                    pose[qa:qa + 3] += rng.uniform(-0.02, 0.02, 3)
                    xyzw = Rotation.from_rotvec(rng.uniform(-0.4, 0.4, 3)).as_quat()
                    pose[qa + 3:qa + 7] = xyzw[[3, 0, 1, 2]]
        for pose in poses:
            state = imported.state()
            state.joint_q.assign(self.source_q_to_import(pose))
            newton.eval_fk(imported, state.joint_q, state.joint_qd, state)
            bodies = state.body_q.numpy()
            d = mujoco.MjData(m)
            d.qpos[:] = pose
            mujoco.mj_kinematics(m, d)
            for name, i in self.body_indices.items():
                j = m.body(name).id
                positions.append(np.linalg.norm(bodies[i, :3] - d.xpos[j]))
                r = Rotation.from_quat(bodies[i, 3:])
                rotations.append((r.inv() * Rotation.from_quat(d.xquat[j][[1, 2, 3, 0]])).magnitude())
        checks.append(check("fk_five_poses", max(positions) < 2e-6 and max(rotations) < 2e-5,
                            {"max_position_m": float(max(positions)), "max_rotation_rad": float(max(rotations)), "poses": 5},
                            {"position_m": 2e-6, "rotation_rad": 2e-5}))
        return checks

    def camera(self, name="ego"):
        """Source ego pose or a diagnostic view centered on both physical hands."""
        from robosimstudio.core.cameras import CameraSpec

        m = self.source
        d = mujoco.MjData(m)
        d.qpos[:] = self.initial_qpos
        mujoco.mj_forward(m, d)
        if name == "workspace":
            center = np.mean([d.xpos[m.body(f"robot_{s}_{s}_hand_C_MC").id] for s in ("left", "right")], axis=0)
            center += [0, 0.03, 0.08]
            return CameraSpec(name="workspace", width=640, height=480, eye=tuple(center + [0.12, -0.45, 0.5]),
                              look_at=tuple(center), fov_y=58, channels=("rgb", "seg"))
        c = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_CAMERA, name)
        if c < 0:
            raise ValueError(f"unknown source camera: {name}")
        eye = d.cam_xpos[c]
        rotation = d.cam_xmat[c].reshape(3, 3)
        return CameraSpec(name=name, width=640, height=480, eye=tuple(eye),
                          look_at=tuple(eye - rotation[:, 2]), up=tuple(rotation[:, 1]), fov_y=float(m.cam_fovy[c]))

    def manifest(self):
        m = self.source
        assets = {e.get("file") for e in ET.fromstring(self.rig.xml).findall("./asset/*") if e.get("file")}
        return {"schema": "dexagent.rss_sharpa.v1", "source_scene_sha256": self.rig.scene_sha256,
                "asset_sha256": {name: sha256_file(name) for name in sorted(assets)},
                "frame": "dexagent_rig = rss_world", "rig_to_world_xyzw": [0, 0, 0, 0, 0, 0, 1],
                "additional_ground_z_m": -1.0, "joint_names": self.joint_names, "joint_q_indices": self.q_indices.tolist(),
                "joint_qd_indices": self.v_indices.tolist(), "actuator_names": self.actuator_names,
                "actuator_ctrl_indices": self.ctrl_indices.tolist(),
                "actuator_joint_names": [m.joint(int(j)).name for j in m.actuator_trnid[:, 0]],
                "actuator_control_units": ["Nm" if n.endswith("_motor") else "rad" for n in self.actuator_names],
                "control_semantics": "raw source actuator controls; arm torque / individual finger position",
                "recorded_action": "joint_reference: 56 position targets [rad], ordered by joint_names; controls also saved separately",
                "diagnostic_palette": "source MJCF colors; tiled rendering does not implement Stage 4 PBR materials",
                "acceptance_controller": "computed-torque joint PD, omega=12 rad/s, damping=24 /s; not Stage 3 Cartesian control",
                "state_joint_names": self.state_joint_names,
                "state_q_starts": self.state_q_starts,
                "state_quaternion_order": "Newton xyzw; source MJCF free joints wxyz",
                "body_labels": list(self.sim.model.body_label),
                "passive_objects": [m.body(int(m.jnt_bodyid[j])).name for j in range(m.njnt)
                                    if m.joint(j).name not in self.joint_names],
                "task_scene_support": "rigid free bodies and passive hinge/slide mechanisms; no ball or mocap",
                "source_solver_adaptations": self.source_adaptations,
                "physics_device": self.physics_device, "state_and_render_device": str(self.sim.model.device),
                "native_gripper_or_ik_support": False}


class JointReferenceControl:
    """A replayable full-joint action over the same bounded physical actuators."""

    def __init__(self, bridge):
        self.bridge = bridge

    @property
    def action_dim(self):
        return 56

    def reset(self, state):
        self.bridge.reset(state)

    def hold_action(self, state):
        q, _ = self.bridge.joint_state(state)
        return np.clip(q, *self.bridge.source.jnt_range[self.bridge.robot_joints].T)

    def apply(self, action, control, state):
        commands = self.bridge.position_controls(action, state)
        self.bridge.apply(commands, control, state)
        # This field is diagnostic for CTRL_DIRECT actuators; keep capture truthful.
        control.joint_target_q.assign(self.bridge._q_to_import(action))
