"""The SimFoundry rigid file contract maps into the existing RSS object API."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.simfoundry_assets import simfoundry_rigid_specs


class SimFoundryRigidAdapterTests(unittest.TestCase):
    def setUp(self):
        import trimesh

        self.tmp = tempfile.TemporaryDirectory(prefix="simfoundry-rss-adapter-")
        root = Path(self.tmp.name)
        self.root = root
        self.mesh_dir = root / "s8/canonical_mesh"
        self.pose_dir = root / "s8/info"
        self.stage11_dir = root / "s11"
        self.stage12_dir = root / "s12"
        self.mesh_dir.mkdir(parents=True)
        self.pose_dir.mkdir(parents=True)
        (self.stage11_dir / "objects" / "cup" / "abcdef" / "urdf").mkdir(parents=True)
        self.stage12_dir.mkdir(parents=True)
        trimesh.creation.box(extents=(0.1, 0.2, 0.3)).export(
            self.mesh_dir / "iter_0.glb"
        )
        (self.pose_dir / "iter_0.json").write_text(
            json.dumps({"z_up": {"scale": 1.75}})
        )
        (self.stage11_dir / "scene_objects_info.json").write_text(
            json.dumps(
                {
                    "0": {
                        "name": "iter_0",
                        "category": "cup",
                        "model": "abcdef",
                        "friction": 0.42,
                    },
                }
            )
        )
        collision_dir = self.stage11_dir / "objects/cup/abcdef/urdf/meshes/collision"
        collision_dir.mkdir(parents=True)
        trimesh.creation.box(extents=(1, 1, 1)).export(collision_dir / "hull_a.obj")
        trimesh.creation.box(extents=(1, 1, 1)).export(collision_dir / "hull_b.obj")
        (self.stage11_dir / "objects/cup/abcdef/urdf/abcdef.urdf").write_text(
            '<robot name="cup"><link name="base_link">'
            '<inertial><mass value="0.31"/><inertia ixx="1" ixy="0" ixz="0" '
            'iyy="1" iyz="0" izz="1"/></inertial>'
            '<collision><origin xyz="0.01 0 0" rpy="0 0 1.5707963267948966"/>'
            '<geometry><mesh filename="meshes/collision/hull_a.obj" scale="0.12 0.06 0.03"/>'
            "</geometry></collision>"
            '<collision><geometry><mesh filename="meshes/collision/hull_b.obj" '
            'scale="0.04 0.04 0.04"/></geometry></collision>'
            "</link></robot>"
        )
        (self.stage12_dir / "pb_scene_poses.json").write_text(
            json.dumps(
                {
                    "iter_0": [
                        [0.25, -0.4, 0.8],
                        [0.0, 0.0, 0.7071067811865476, 0.7071067811865476],
                    ],
                }
            )
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _load(self, **overrides):
        arguments = {
            "canonical_mesh_dir": self.mesh_dir,
            "pose_info_dir": self.pose_dir,
            "stage11_dir": self.stage11_dir,
            "stage12_dir": self.stage12_dir,
            "collision": "convex_hull",
        }
        arguments.update(overrides)
        return simfoundry_rigid_specs(**arguments)

    def test_maps_canonical_mesh_scale_world_pose_mass_and_friction(self):
        from robosimstudio.specs import AssetSpec

        [spec] = self._load()
        self.assertEqual("iter_0", spec.name)
        self.assertEqual("cup", spec.category)
        self.assertEqual((0.25, -0.4, 0.8), spec.pose)
        self.assertAlmostEqual(1.0, sum(value * value for value in spec.quat))
        self.assertEqual(
            AssetSpec(path=str((self.mesh_dir / "iter_0.glb").resolve()), scale=1.75),
            spec.asset,
        )
        self.assertEqual(0.31, spec.mass)
        self.assertEqual(0.42, spec.friction)
        self.assertEqual("convex_hull", spec.collider)

    def test_preserves_stage11_compound_collision_meshes_and_transforms(self):
        import numpy as np
        import trimesh

        [spec] = self._load(
            collision="stage11", derived_asset_dir=self.root / "rss-assets"
        )
        self.assertEqual("", spec.collider)
        self.assertEqual(2, len(spec.asset.collision_paths))

        first = trimesh.load(spec.asset.collision_paths[0], force="mesh", process=False)
        np.testing.assert_allclose(
            first.extents * spec.asset.scale, (0.06, 0.12, 0.03), atol=1e-6
        )
        np.testing.assert_allclose(
            first.centroid * spec.asset.scale, (0.01, 0.0, 0.0), atol=1e-6
        )
        second = trimesh.load(
            spec.asset.collision_paths[1], force="mesh", process=False
        )
        np.testing.assert_allclose(
            second.extents * spec.asset.scale, (0.04, 0.04, 0.04), atol=1e-6
        )

    def test_stage11_collision_requires_a_derived_asset_directory(self):
        with self.assertRaisesRegex(ValueError, "derived_asset_dir is required"):
            self._load(collision="stage11")

    def test_rejects_stage11_collision_paths_outside_the_model_directory(self):
        urdf_path = self.stage11_dir / "objects/cup/abcdef/urdf/abcdef.urdf"
        urdf_path.write_text(
            '<robot name="cup"><link name="base_link">'
            '<inertial><mass value="0.31"/></inertial>'
            '<collision><geometry><mesh filename="../../../../outside.obj"/>'
            "</geometry></collision></link></robot>"
        )
        with self.assertRaisesRegex(
            ValueError, "escapes its SimFoundry asset directory"
        ):
            self._load(collision="stage11", derived_asset_dir=self.root / "rss-assets")

    def test_rejects_anisotropic_scale(self):
        (self.pose_dir / "iter_0.json").write_text(
            json.dumps({"z_up": {"scale": [1.0, 1.0, 1.2]}})
        )
        with self.assertRaisesRegex(ValueError, "non-uniform"):
            self._load()

    def test_rejects_metadata_names_that_escape_the_asset_root(self):
        (self.stage11_dir / "scene_objects_info.json").write_text(
            json.dumps(
                {
                    "0": {
                        "name": "iter_0",
                        "category": "../cup",
                        "model": "abcdef",
                        "friction": 0.42,
                    },
                }
            )
        )
        with self.assertRaisesRegex(ValueError, "not a path component"):
            self._load()

    def test_rejects_a_multilink_rigid_urdf_until_flattened(self):
        path = self.stage11_dir / "objects/cup/abcdef/urdf/abcdef.urdf"
        path.write_text('<robot name="cup"><link name="a"/><link name="b"/></robot>')
        with self.assertRaisesRegex(ValueError, "expected one rigid link"):
            self._load()


if __name__ == "__main__":
    unittest.main()
