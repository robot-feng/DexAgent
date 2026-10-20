"""Whole-scene placement preserves source geometry and free-root semantics."""
from pathlib import Path
import tempfile
import unittest

import mujoco
import newton
import numpy as np
import warp as wp

from agent.rss_scene import TaskSceneEntity, TaskSceneSpec
from robosimstudio.specs import CellSpec


SCENE = """<mujoco model="frame_fixture">
  <compiler angle="radian"/>
  <worldbody>
    <geom name="world_geom" type="box" pos="-.7 .4 -.08"
          quat=".9238795325 0 0 .3826834324" size=".2 .3 .02"/>
    <body name="fixed_root" pos=".3 -.2 .1" quat=".9238795325 0 0 .3826834324">
      <geom name="root_geom" type="box" pos=".03 .02 .01" size=".03 .02 .01"/>
      <body name="fixed_child" pos=".11 .07 .2">
        <geom name="child_geom" type="sphere" size=".015"/>
      </body>
    </body>
    <body name="free_root" pos="-.15 .65 .25" quat=".9659258263 .2588190451 0 0">
      <freejoint name="object_free"/>
      <geom name="object_geom" type="box" pos=".02 -.01 .03" size=".02 .03 .04"/>
    </body>
  </worldbody>
</mujoco>"""


class TaskSceneFrameTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "scene.xml"
        self.path.write_text(SCENE)
        self.source = mujoco.MjModel.from_xml_path(str(self.path))

    def build(self, frame):
        entity = TaskSceneEntity(TaskSceneSpec(path=str(self.path)))
        entity.frame = frame
        builder = newton.ModelBuilder()
        entity.build(builder, None)
        model = builder.finalize(device="cpu")
        state = model.state()
        newton.eval_fk(model, state.joint_q, state.joint_qd, state)
        return model, state

    @staticmethod
    def named(labels):
        return {label.rsplit("/", 1)[-1]: i for i, label in enumerate(labels)}

    def assert_source_geometry(self, model, state, translation, qpos=None):
        data = mujoco.MjData(self.source)
        if qpos is not None:
            data.qpos[:] = qpos
        mujoco.mj_forward(self.source, data)
        poses = state.body_q.numpy()
        bodies = self.named(model.body_label)
        self.assertEqual(set(bodies), {"fixed_root", "fixed_child", "free_root"})
        for name, index in bodies.items():
            source_id = self.source.body(name).id
            with self.subTest(body=name):
                np.testing.assert_allclose(
                    poses[index, :3], data.xpos[source_id] + translation, atol=2e-6, rtol=0)
                expected = data.xquat[source_id][[1, 2, 3, 0]]
                self.assertAlmostEqual(abs(float(np.dot(poses[index, 3:], expected))), 1., places=6)
        shape_poses = model.shape_transform.numpy()
        shape_bodies = model.shape_body.numpy()
        for name, index in self.named(model.shape_label).items():
            body = int(shape_bodies[index])
            transform = wp.transform(*shape_poses[index])
            if body >= 0:
                transform = wp.transform(*poses[body]) * transform
            actual = np.asarray(transform)
            geom = self.source.geom(name).id
            with self.subTest(geom=name):
                np.testing.assert_allclose(
                    actual[:3], data.geom_xpos[geom] + translation, atol=2e-6, rtol=0)
                rotation = np.asarray(wp.quat_to_matrix(wp.quat(*actual[3:]))).reshape(3, 3)
                np.testing.assert_allclose(rotation, data.geom_xmat[geom].reshape(3, 3), atol=2e-6, rtol=0)
        world_shape = self.named(model.shape_label)["world_geom"]
        self.assertEqual(int(shape_bodies[world_shape]), -1)

    def test_identity_keeps_source_roots_and_world_geometry(self):
        model, state = self.build((0., 0., 0., 0., 0., 0., 1.))
        self.assert_source_geometry(model, state, np.zeros(3))

    def test_cell_surface_translation_moves_every_root_and_world_geometry(self):
        cell = CellSpec(name="frame_test", ground_z=0., surface_z=.78, center=(.4, -.3, 99.))
        model, state = self.build(cell.surface_frame)
        translation = np.array([.4, -.3, .78])
        self.assert_source_geometry(model, state, translation)
        bodies = self.named(model.body_label)
        poses = state.body_q.numpy()
        np.testing.assert_allclose(
            poses[bodies["free_root"], :3] - poses[bodies["fixed_root"], :3],
            [-.45, .85, .15], atol=2e-6, rtol=0)
        self.assertEqual(model.joint_coord_count, self.source.nq)
        self.assertEqual(model.joint_dof_count, self.source.nv)

    def test_translated_free_joint_moves_without_moving_fixed_roots(self):
        translation = np.array([.4, -.3, .78])
        model, state = self.build((*translation, 0., 0., 0., 1.))
        joints = self.named(model.joint_label)
        joint = joints["object_free"]
        self.assertEqual(int(model.joint_type.numpy()[joint]), int(newton.JointType.FREE))
        start = int(model.joint_q_start.numpy()[joint])
        q = state.joint_q.numpy()
        displacement = np.array([.12, -.06, .08])
        q[start:start + 3] += displacement
        state.joint_q.assign(q)
        newton.eval_fk(model, state.joint_q, state.joint_qd, state)
        source_q = self.source.qpos0.copy()
        source_q[:3] += displacement
        self.assert_source_geometry(model, state, translation, source_q)

    def test_rotation_is_rejected_before_import(self):
        frame = (.4, -.3, .78, 0., 0., np.sqrt(.5), np.sqrt(.5))
        with self.assertRaisesRegex(ValueError, "translation only"):
            self.build(frame)

    def test_invalid_raw_frames_are_rejected_before_pose_normalization(self):
        for frame in (
            (0., 0., 0.),
            ((0., 0., 0., 0., 0., 0., 1.),),
            (np.nan, 0., 0., 0., 0., 0., 1.),
            (0., 0., np.inf, 0., 0., 0., 1.),
            (0., 0., 0., 0., 0., 0., 0.),
            (0., 0., 0., 0., 0., 0., 2.),
        ):
            with self.subTest(frame=frame), self.assertRaisesRegex(ValueError, "translation only"):
                self.build(frame)

    def test_world_connected_equalities_are_rejected_only_for_translation(self):
        for equality in (
            '<weld name="world_lock" body1="free_root" relpose="-.15 .65 .25 1 0 0 0"/>',
            '<connect name="world_lock" body1="free_root" anchor="0 0 0"/>',
        ):
            with self.subTest(equality=equality):
                self.path.write_text(SCENE.replace("</mujoco>", f"<equality>{equality}</equality></mujoco>"))
                with self.assertRaisesRegex(ValueError, "world-connected equality"):
                    self.build((.4, -.3, .78, 0., 0., 0., 1.))
                model, state = self.build((0., 0., 0., 0., 0., 0., 1.))
                self.assert_source_geometry(model, state, np.zeros(3))

    def test_body_to_body_equality_does_not_block_whole_scene_translation(self):
        equality = '<weld name="local_lock" body1="fixed_root" body2="free_root"/>'
        self.path.write_text(SCENE.replace("</mujoco>", f"<equality>{equality}</equality></mujoco>"))
        translation = np.array([.4, -.3, .78])
        model, state = self.build((*translation, 0., 0., 0., 1.))
        self.assert_source_geometry(model, state, translation)
        self.assertEqual(len(model.mujoco.equality_constraint_type.numpy()), 1)

    def test_site_equalities_are_not_misidentified_as_world_body_constraints(self):
        scene = SCENE.replace(
            '<geom name="root_geom"', '<site name="s0"/><geom name="root_geom"'
        ).replace('<freejoint name="object_free"/>', '<site name="s1"/><freejoint name="object_free"/>')
        for kind in ("connect", "weld"):
            with self.subTest(kind=kind):
                equality = f'<equality><{kind} site1="s0" site2="s1"/></equality>'
                self.path.write_text(scene.replace("</mujoco>", equality + "</mujoco>"))
                source = mujoco.MjModel.from_xml_path(str(self.path))
                self.assertEqual(int(source.eq_obj1id[0]), 0)
                self.assertGreater(int(source.site_bodyid[0]), 0)
                with self.assertRaisesRegex(ValueError, "site-based equality"):
                    self.build((.4, -.3, .78, 0., 0., 0., 1.))


if __name__ == "__main__":
    unittest.main()
