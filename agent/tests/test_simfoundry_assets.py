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
        (self.stage11_dir / "objects/cup/abcdef/urdf/abcdef.urdf").write_text(
            '<robot name="cup"><link name="base_link">'
            '<inertial><mass value="0.31"/><inertia ixx="1" ixy="0" ixz="0" '
            'iyy="1" iyz="0" izz="1"/></inertial>'
            '<collision><geometry><mesh filename="../meshes/cup.obj"/></geometry></collision>'
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
