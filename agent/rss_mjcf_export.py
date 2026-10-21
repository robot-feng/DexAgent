"""Export a verified, fixed native CPU snapshot using Newton's constructor XML.

This is deliberately not a general Newton exporter. The XML must come from the
same solver construction. Editable Newton force-space limit gains become fixed
native solref values; generated joint-target controls become named MJCF controls.
No physics is stepped and neither the Newton nor native model is modified.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

import mujoco
import newton
import numpy as np

# These are the only runtime differences accepted from the constructor XML.
# All changes are reported. Masses, geometry, drives, ranges and filters cannot
# be repaired here: a mismatch is a failed export, not permission to copy them.
_REPAIR = {
    "jnt_solref": ("joints", "solref_limit"),
    "jnt_solimp": ("joints", "solimp_limit"),
    "body_gravcomp": ("bodies", "gravcomp"),
    "geom_solref": ("geoms", "solref"),
    "geom_solimp": ("geoms", "solimp"),
    "eq_solref": ("equalities", "solref"),
    "eq_solimp": ("equalities", "solimp"),
    "eq_data": ("equalities", "data"),
    "eq_active0": ("equalities", "active"),
}
_FIELDS = """
qpos0 qpos_spring body_parentid body_rootid body_weldid body_mocapid body_jntnum
body_jntadr body_dofnum body_dofadr body_geomnum body_geomadr body_pos body_quat
body_ipos body_mass body_subtreemass body_gravcomp
jnt_type jnt_qposadr jnt_dofadr jnt_bodyid jnt_limited jnt_actfrclimited
jnt_actgravcomp jnt_group jnt_stiffnesspoly jnt_solref jnt_solimp jnt_pos jnt_axis jnt_stiffness jnt_range
jnt_actfrcrange jnt_margin dof_bodyid dof_jntid dof_parentid dof_solref dof_solimp
dof_frictionloss dof_armature dof_damping dof_dampingpoly geom_type geom_contype geom_conaffinity
geom_condim geom_bodyid geom_dataid geom_group geom_priority geom_solmix
geom_solref geom_solimp geom_size geom_pos geom_quat geom_friction geom_margin
geom_gap geom_fluid geom_adhesion geom_surfacevel mesh_vertadr mesh_vertnum mesh_faceadr mesh_facenum mesh_vert
mesh_face mesh_pos mesh_quat mesh_scale pair_dim pair_geom1 pair_geom2 pair_solref
pair_solreffriction pair_solimp pair_margin pair_gap pair_friction exclude_signature
eq_type eq_obj1id eq_obj2id eq_objtype eq_active0 eq_solref eq_solimp eq_data
actuator_trntype actuator_dyntype actuator_gaintype actuator_biastype actuator_trnid
actuator_actadr actuator_actnum actuator_group actuator_ctrllimited
actuator_forcelimited actuator_actlimited actuator_dynprm actuator_gainprm
actuator_biasprm actuator_ctrlrange actuator_forcerange actuator_actrange
actuator_gear actuator_cranklength actuator_lengthrange actuator_acc0
actuator_actearly actuator_damping actuator_dampingpoly actuator_armature
actuator_ctrladr actuator_ctrlnum actuator_ctrlspec actuator_delay actuator_history
actuator_historyadr actuator_outadr actuator_outnum actuator_length0
""".split()
_COUNTS = "nq nv nu na nbody njnt ngeom nmesh nsite neq nexclude npair ntendon nflex nplugin nsensor nhfield".split()
_NAMES = (("body", "nbody"), ("joint", "njnt"), ("geom", "ngeom"),
          ("mesh", "nmesh"), ("site", "nsite"), ("equality", "neq"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fingerprint(native):
    """Provenance of the audited fields, not a byte-identical XML claim."""
    digest = hashlib.sha256()
    for key in _COUNTS + _FIELDS + ["body_iquat", "body_inertia"]:
        if hasattr(native, key):
            value = np.asarray(getattr(native, key))
            digest.update(key.encode())
            digest.update(str((value.shape, value.dtype.str)).encode())
            digest.update(value.tobytes())
    for kind, count in _NAMES + (("actuator", "nu"),):
        for i in range(getattr(native, count)):
            digest.update((kind + "\0" + getattr(native, kind)(i).name + "\0").encode())
    for key in dir(native.opt):
        if not key.startswith("_"):
            value = getattr(native.opt, key)
            if not callable(value):
                digest.update(key.encode())
                digest.update(np.asarray(value).tobytes())
    return digest.hexdigest()


def _massless_frame_mask(expected, actual):
    # Only empty fixed frames have an unobservable inertial origin here.
    # Positive mass, nonzero inertia, geometry or own DOFs are never exempt.
    mask = np.ones(expected.nbody, dtype=bool)
    for native in (expected, actual):
        mask &= ((native.body_mass == 0) & np.all(native.body_inertia == 0, axis=1)
                 & (native.body_jntnum == 0) & (native.body_dofnum == 0)
                 & (native.body_geomnum == 0))
    if expected.nsensor or actual.nsensor:
        mask[:] = False  # A sensor can observe an inertial-frame origin.
    return mask


def _compare(expected, actual, *, omit=()):
    result = {}
    for key in _COUNTS:
        if getattr(expected, key) != getattr(actual, key):
            raise ValueError(f"native snapshot count mismatch: {key}")
    for kind, count in _NAMES:
        left = [getattr(expected, kind)(i).name for i in range(getattr(expected, count))]
        right = [getattr(actual, kind)(i).name for i in range(getattr(actual, count))]
        if left != right:
            raise ValueError(f"native snapshot identity mismatch: {kind} names/order")
    for key in _FIELDS:
        if key in omit or not hasattr(expected, key):
            continue
        a, b = np.asarray(getattr(expected, key)), np.asarray(getattr(actual, key))
        if key == "body_ipos":
            active = ~_massless_frame_mask(expected, actual)
            a, b = a[active], b[active]
        if key in ("body_quat", "geom_quat", "mesh_quat"):
            # Unit quaternions have a meaningful norm, not a per-component
            # relative scale. q and -q represent exactly the same rotation.
            norms_a, norms_b = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
            if (not np.isfinite(a).all() or not np.isfinite(b).all()
                    or not np.allclose(norms_a, 1, rtol=0, atol=1e-6)
                    or not np.allclose(norms_b, 1, rtol=0, atol=1e-6)):
                raise ValueError(f"invalid finite unit quaternion: {key}")
            qa, qb = a / norms_a[:, None], b / norms_b[:, None]
            distances = np.minimum(np.linalg.norm(qa - qb, axis=1), np.linalg.norm(qa + qb, axis=1))
            if np.any(distances > 2e-5):
                raise ValueError(f"native snapshot physical mismatch: {key}")
            result[key] = float(distances.max(initial=0))
            continue
        if key in ("jnt_range", "jnt_actfrcrange", "actuator_ctrlrange", "actuator_forcerange", "actuator_actrange"):
            # Unrestricted/free joints carry unused Newton sentinel ranges;
            # MuJoCo may omit or canonicalize them in XML.
            flag = {"jnt_range": "jnt_limited", "jnt_actfrcrange": "jnt_actfrclimited",
                    "actuator_ctrlrange": "actuator_ctrllimited", "actuator_forcerange": "actuator_forcelimited",
                    "actuator_actrange": "actuator_actlimited"}[key]
            active = np.asarray(getattr(expected, flag), dtype=bool)
            a, b = a[active], b[active]
        if key == "geom_size":
            # Sphere and capsule/cylinder padding is not physical size. Newton
            # fills it, whereas the MJCF compiler canonicalizes unused entries.
            mask = np.ones(a.shape, dtype=bool)
            mask[expected.geom_type == int(mujoco.mjtGeom.mjGEOM_SPHERE), 1:] = False
            for kind in (mujoco.mjtGeom.mjGEOM_CAPSULE, mujoco.mjtGeom.mjGEOM_CYLINDER):
                mask[expected.geom_type == int(kind), 2] = False
            a, b = a[mask], b[mask]
        if not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError(f"non-finite native snapshot field: {key}")
        # XML uses finite decimal precision. Tiny inertias and masses still need
        # relative preservation; the geometry tolerance is not applied to them.
        atol = 1e-12 if ("mass" in key or "inertia" in key) else 1e-6
        exact = a.dtype.kind in "biu"
        ok = a.shape == b.shape and (np.array_equal(a, b) if exact else
                                      np.allclose(a, b, rtol=2e-5, atol=atol))
        if not ok:
            raise ValueError(f"native snapshot physical mismatch: {key}")
        result[key] = float(np.max(np.abs(a.astype(float) - b))) if a.size else 0.0
    # Principal frames are non-unique for repeated eigenvalues. Compare the
    # actual body-frame tensor, not an arbitrary eigensolver orientation.
    tensors = []
    for native in (expected, actual):
        values = []
        for quat, diagonal in zip(native.body_iquat, native.body_inertia):
            if (not np.isfinite(quat).all() or not np.isfinite(diagonal).all()
                    or not np.isclose(np.linalg.norm(quat), 1, rtol=0, atol=1e-6)
                    or np.any(diagonal < 0)):
                raise ValueError("invalid finite unit inertia frame or principal inertia")
            rotation = np.empty(9)
            mujoco.mju_quat2Mat(rotation, quat)
            rotation = rotation.reshape(3, 3)
            values.append((rotation * diagonal) @ rotation.T)
        tensors.append(np.asarray(values))
    errors = np.linalg.norm(tensors[0] - tensors[1], axis=(1, 2))
    if np.any(errors > 2e-5 * np.linalg.norm(tensors[0], axis=(1, 2)) + 1e-12):
        raise ValueError("native snapshot physical mismatch: body inertia tensor")
    result["body_inertia_tensor"] = float(errors.max(initial=0))
    return result


def _actuator_mapping(model, solver):
    m = solver.mj_model
    joints = solver.mjc_jnt_to_newton_jnt.numpy()
    dofs = solver.mjc_jnt_to_newton_dof.numpy()
    if joints.shape != (1, m.njnt) or dofs.shape != joints.shape:
        raise ValueError("export requires one native world and complete joint maps")
    if not m.nu:
        return []
    sources = solver.mjc_actuator_ctrl_source.numpy()
    slots = solver.mjc_actuator_to_newton_idx.numpy()
    target_slots = solver.mjc_actuator_to_newton_target_q_idx.numpy()
    if any(x.shape != (m.nu,) for x in (sources, slots, target_slots)):
        raise ValueError("incomplete native actuator maps")
    labels = list(getattr(model.mujoco, "actuator_label", []))
    joint_labels = list(model.joint_label)
    types = model.joint_type.numpy()
    starts = model.joint_target_q_start.numpy()
    qstarts, dstarts = model.joint_q_start.numpy(), model.joint_qd_start.numpy()
    rows = []
    for a in range(m.nu):
        if m.actuator_trntype[a] != int(mujoco.mjtTrn.mjTRN_JOINT):
            raise ValueError("only mapped scalar joint actuators can be exported")
        j = int(m.actuator_trnid[a, 0])
        nj, dof = int(joints[0, j]), int(dofs[0, j])
        if (nj < 0 or types[nj] not in (newton.JointType.REVOLUTE, newton.JointType.PRISMATIC)
                or m.jnt_type[j] not in (int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE))
                or dof != dstarts[nj] or not joint_labels[nj]
                or joint_labels.count(joint_labels[nj]) != 1):
            raise ValueError("actuator has no unique scalar Newton joint identity")
        source, slot = int(sources[a]), int(slots[a])
        if source == 1:
            if slot < 0 or slot >= len(labels) or not labels[slot] or labels.count(labels[slot]) != 1:
                raise ValueError("direct actuator has no unique full imported label")
            if int(model.mujoco.actuator_trnid.numpy()[slot, 0]) != dof:
                raise ValueError("direct actuator target disagrees with native joint map")
            name, role = labels[slot], "direct"
        elif source == 0:
            position = slot >= 0
            mapped_dof = slot if position else -slot - 2
            if mapped_dof != dof or (position and int(target_slots[a]) != int(starts[nj])):
                raise ValueError("generated actuator target layout disagrees with joint map")
            if (m.actuator_dyntype[a] != int(mujoco.mjtDyn.mjDYN_NONE)
                    or m.actuator_gaintype[a] != int(mujoco.mjtGain.mjGAIN_FIXED)
                    or m.actuator_biastype[a] != int(mujoco.mjtBias.mjBIAS_AFFINE)
                    or not np.array_equal(m.actuator_gear[a], [1, 0, 0, 0, 0, 0])):
                raise ValueError("unsupported generated actuator semantics")
            role = "position" if position else "velocity"
            name = f"{joint_labels[nj]}/__{role}_servo"
        else:
            raise ValueError(f"unsupported actuator control source: {source}")
        old_name = m.actuator(a).name
        if old_name and old_name != name:
            raise ValueError("existing native actuator name disagrees with mapped identity")
        rows.append(dict(native_actuator=a, native_name=old_name, exported_name=name,
                         native_joint=j, native_joint_name=m.joint(j).name,
                         newton_joint=nj, newton_joint_label=joint_labels[nj],
                         newton_dof=dof, q_slot=int(qstarts[nj]), qd_slot=int(dstarts[nj]),
                         target_slot=int(starts[nj]), control_source=source,
                         control_index=slot, role=role))
    names = [row["exported_name"] for row in rows]
    if len(set(names)) != len(names):
        raise ValueError("actuator identity maps are ambiguous")
    return rows


def _expand_solref_shorthand(path, native):
    """Avoid Newton custom-attribute sentinel padding of MuJoCo shorthand.

    MjSpec may write one solref component. MuJoCo supplies the damping ratio,
    but Newton's custom attribute parser pads from its (0, 0) unauthored
    sentinel. Use the just-compiled native value, never a guessed default.
    Only existing attributes are expanded; absent defaults stay absent.
    """
    tree = ET.parse(path)
    expansions = {}
    for tag, attribute, native_kind, field in (
        ("geom", "solref", "geom", "geom_solref"),
        ("joint", "solreflimit", "joint", "jnt_solref"),
    ):
        for node in tree.findall(f".//worldbody//{tag}"):
            before = node.get(attribute)
            if before is None or len(before.split()) >= 2:
                continue
            name = node.get("name")
            if not name:
                raise ValueError(f"unnamed {tag} cannot resolve {attribute} shorthand")
            try:
                index = getattr(native, native_kind)(name).id
            except KeyError as error:
                raise ValueError(f"unmapped {tag} {name} in solver-vector expansion") from error
            vector = getattr(native, field)[index]
            explicit = " ".join(format(float(x), ".17g") for x in vector)
            node.set(attribute, explicit)
            key = (tag, attribute, before, explicit)
            if key not in expansions:
                expansions[key] = dict(element=tag, attribute=attribute, shorthand=before,
                                       explicit=explicit, count=0, name_examples=[])
            row = expansions[key]
            row["count"] += 1
            if len(row["name_examples"]) < 5:
                row["name_examples"].append(name)
    if expansions:
        tree.write(path, encoding="unicode")
    return list(expansions.values())


def export_portable_mjcf(model, solver, constructor_xml: Path, output: Path) -> dict:
    """Save an audited fixed native snapshot; return JSON-ready provenance.

    ``constructor_xml`` must be the XML from this solver's ``save_to_mjcf``.
    Only joint-limit solver parameters, body gravity compensation, equality
    parameters, geom contact solver parameters and current native options may
    differ from that XML. Every difference is reported and compilation must
    reproduce it. In particular unrepresentable CONNECT child-anchor edits fail.
    Unsupported sites, height fields, sensors, tendons, flexes, plugins or
    external assets fail explicitly.
    The destination is published only after reloading and checking the XML.
    """
    constructor_xml, output = Path(constructor_xml).resolve(), Path(output).resolve()
    if output == constructor_xml:
        raise ValueError("preserve the original constructor XML at a separate path")
    if solver.model is not model or str(model.device) != "cpu" or not solver.use_mujoco_cpu:
        raise ValueError("requires the matching native CPU Newton model and solver")
    m = solver.mj_model
    native_sha = _fingerprint(m)
    if m.ntendon or m.nflex or m.nplugin or m.nsensor or m.nsite or m.nhfield:
        raise ValueError("sites, height fields, tendons, flexes, plugins and sensors are outside this exporter scope")
    tree = ET.parse(constructor_xml)
    if any(node.get("file") for node in tree.iter()):
        raise ValueError("portable export currently requires inline assets")
    source_sha = _sha(constructor_xml)
    spec = mujoco.MjSpec.from_file(str(constructor_xml))
    original = spec.compile()
    preflight = _compare(m, original, omit=_REPAIR)
    massless_com_changes = [dict(index=int(i), name=m.body(int(i)).name,
                                 native=m.body_ipos[i].tolist(), constructor=original.body_ipos[i].tolist())
                            for i in np.flatnonzero(_massless_frame_mask(m, original))
                            if not np.array_equal(m.body_ipos[i], original.body_ipos[i])]
    mapping = _actuator_mapping(model, solver)
    for row in mapping:
        a = row["native_actuator"]
        if original.actuator(a).name != m.actuator(a).name:
            raise ValueError("constructor actuator name/order differs from native model")
        spec.actuators[a].name = row["exported_name"]
    changes = []
    for field, (collection, attribute) in _REPAIR.items():
        current, before = getattr(m, field), getattr(original, field)
        for i in range(len(current)):
            if not np.array_equal(current[i], before[i]):
                changes.append(dict(field=field, index=i, constructor=np.asarray(before[i]).tolist(),
                                    native=np.asarray(current[i]).tolist()))
            value = np.array(current[i], copy=True) if np.ndim(current[i]) else current[i].item()
            setattr(getattr(spec, collection)[i], attribute, value)
    # MjSpec's MjOption is a distinct binding from MjModel's MjOption.
    option_fields = [key for key in dir(spec.option) if not key.startswith("_")]
    for key in option_fields:
        value, before = getattr(m.opt, key), getattr(original.opt, key)
        if not np.array_equal(value, before):
            changes.append(dict(field=f"option.{key}", constructor=np.asarray(before).tolist(),
                                native=np.asarray(value).tolist()))
        setattr(spec.option, key, value.copy() if isinstance(value, np.ndarray) else value)
    compiled = spec.compile()
    _compare(m, compiled)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".xml", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        spec.to_file(str(temporary))
        vector_expansions = _expand_solref_shorthand(temporary, compiled)
        exported = mujoco.MjModel.from_xml_path(str(temporary))
        comparisons = _compare(m, exported)
        for key in option_fields:
            if not np.allclose(getattr(m.opt, key), getattr(exported.opt, key), rtol=2e-5, atol=1e-12):
                raise ValueError(f"native snapshot option mismatch: {key}")
        for row in mapping:
            if exported.actuator(row["native_actuator"]).name != row["exported_name"]:
                raise ValueError("exported actuator identity changed")
        if _sha(constructor_xml) != source_sha:
            raise ValueError("constructor XML changed during export")
        if _fingerprint(m) != native_sha:
            raise ValueError("native model changed during export")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(scope="fixed_native_snapshot_export_not_task_acceptance", physics_steps=0,
                mujoco_version=mujoco.__version__, constructor_xml=str(constructor_xml),
                constructor_sha256=source_sha, output=str(output), output_sha256=_sha(output),
                native_audited_fields_sha256=native_sha,
                exported_audited_fields_sha256=_fingerprint(exported),
                massless_fixed_frame_com_canonicalizations=massless_com_changes,
                comparison_semantics=["Quaternion error is minimum unit-quaternion chord distance (q and -q equivalent), threshold 2e-5.",
                                      "COM is unobservable only for empty fixed frames with exactly zero mass/inertia and no sensors.",
                                      "Limited ranges only; unused range sentinels and primitive size padding canonicalize in XML.",
                                      "Principal inertia frames may rotate within a repeated eigenspace; body-frame tensors are compared."],
                counts={key: int(getattr(m, key)) for key in _COUNTS},
                actuator_mapping=mapping, restored_fields=changes,
                explicit_solver_vector_expansions=vector_expansions,
                constructor_max_absolute_differences=preflight,
                exported_max_absolute_differences=comparisons,
                limitations=["Caller must supply the same construction's XML; structural and physical checks verify compatibility.",
                             "Fixed native limit solref replaces editable Newton force-space limit gain semantics.",
                             "MJCF import uses direct actuator controls; generated joint-target channels require the existing RSS position outlet.",
                             "Export equality is not RSS roundtrip qualification or a physical task pass."])
