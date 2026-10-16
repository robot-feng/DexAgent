"""Reject reference corruption and preserve full free-root coordinates."""
import copy
import unittest
from types import SimpleNamespace

import mujoco
import numpy as np

from agent.rss_episode import feedback_control, source_qpos, validate_reference
from agent.rss_sharpa import SharpaBridge
from tool_library.common.episode import joint_names
from tool_library.common.rigid_robot import compile_rigid
from pathlib import Path
from unittest.mock import patch


class ReferenceContractTests(unittest.TestCase):
    def test_top_level_entry_forwards_the_selected_reference(self):
        from run_dexagent import _main_impl
        config = Path(__file__).resolve().parents[2] / 'dexagent.yaml'
        with patch('run_robosimstudio.main', return_value=0) as backend:
            result = _main_impl([str(config), '--backend-eval', 'reference', '--reference-run', 'runs/standard'])
        self.assertEqual(result, 0)
        self.assertEqual(backend.call_args.args[0], ['reference', '--config', str(config), '--reference-run', 'runs/standard'])

    def setUp(self):
        self.model = m = mujoco.MjModel.from_xml_string('''<mujoco>
          <option timestep="0.002"/><worldbody>
            <body name="arm"><joint name="hinge"/><geom size=".01"/></body>
            <body name="object" pos="0 0 .1"><freejoint name="free"/><geom size=".02"/></body>
          </worldbody><actuator><motor name="motor" joint="hinge" ctrllimited="true" ctrlrange="-1 1"/></actuator>
          <keyframe><key name="home"/></keyframe></mujoco>''')
        self.data = {'qpos': np.tile(m.key_qpos[0], (3, 1)), 'initial_qpos': m.key_qpos[0].copy(),
            'initial_qvel': np.zeros(m.nv), 'substep_ctrl': np.zeros((4, 1)),
            'substep_qpos': np.tile(m.key_qpos[0], (4, 1)), 'substep_qvel': np.zeros((4, m.nv)),
            'substep_time_s': np.arange(1, 5) * .002, 'time_s': np.array([0., .004, .008]),
            'joint_names': np.array(joint_names(m)), 'actuator_names': np.array(['motor']),
            'labels': np.array(['start', 'hold', 'end']), 'subgoal': np.ones(3), 'fps': np.array(250.)}

    def test_valid_contract_includes_each_free_joint_coordinate(self):
        validate_reference(self.model, self.data)

    def test_corrupted_timing_identity_controls_and_initial_state_are_rejected(self):
        changes = [
            ('joint_names', np.array(['hinge', *(['wrong'] * 7)])),
            ('actuator_names', np.array(['another_motor'])),
            ('substep_time_s', self.data['substep_time_s'] + .001),
            ('time_s', np.array([0., .004, .003])),
            ('substep_ctrl', np.array([[0.], [0.], [np.nan], [0.]])),
            ('substep_ctrl', np.full((4, 1), 1.01)),
            ('substep_ctrl', np.zeros((3, 1))),
            ('qpos', np.zeros((3, 7))),
            ('initial_qvel', np.ones(self.model.nv)),
            ('initial_qpos', np.ones(self.model.nq)),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value.tolist()):
                data = copy.deepcopy(self.data)
                data[key] = value
                with self.assertRaises(ValueError):
                    validate_reference(self.model, data)

    def test_missing_substep_commands_cannot_fall_back_to_frame_controls(self):
        del self.data['substep_ctrl']
        self.data['ctrl'] = np.zeros((3, 1))
        with self.assertRaisesRegex(ValueError, 'missing'):
            validate_reference(self.model, self.data)

    def test_frame_states_must_match_the_recorded_physics_substeps(self):
        data = copy.deepcopy(self.data)
        data['qpos'][1, 0] += 0.01
        with self.assertRaisesRegex(ValueError, 'frame states disagree'):
            validate_reference(self.model, data)

        data = copy.deepcopy(self.data)
        data['qpos'][0, 0] += 0.01
        with self.assertRaisesRegex(ValueError, 'frame 0'):
            validate_reference(self.model, data)

    def test_inverse_mapping_resolves_names_and_preserves_free_quaternion(self):
        m = self.model
        # Importer puts the free joint first and uses xyzw; hinge is last.
        values = np.array([.2, .3, .4, 0., 0., np.sqrt(.5), np.sqrt(.5), .6])
        state = SimpleNamespace(joint_q=SimpleNamespace(numpy=lambda: values))
        bridge = SimpleNamespace(source=m, initial_qpos=m.key_qpos[0].copy(),
            state_joint_names=['hinge', 'free'], state_q_starts={'free': 0, 'hinge': 7})
        result = source_qpos(bridge, state)
        np.testing.assert_allclose(result, [.6, .2, .3, .4, np.sqrt(.5), 0, 0, np.sqrt(.5)])


class FeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rig = compile_rigid(Path(__file__).resolve().parents[2] / 'setup/scene.xml')

    def test_nominal_response_is_preserved_and_motor_correction_is_bounded(self):
        bridge = object.__new__(SharpaBridge)
        bridge.configure_source(self.rig)
        home = bridge.initial_qpos.copy()
        nominal = bridge.position_controls(bridge.home, qpos=bridge.home, qvel=np.zeros(56))
        data = {'initial_qpos': home, 'initial_qvel': np.zeros(bridge.source.nv),
                'substep_ctrl': nominal[None]}
        values, velocity = home.copy(), np.zeros_like(home)
        bridge.joint_state = lambda state: (values, velocity)
        np.testing.assert_array_equal(feedback_control(bridge, data, 0, None), nominal)
        arm = np.asarray(['_arm_' in n for n in bridge.joint_names])
        values[arm] += .5
        before = values.copy()
        actual = feedback_control(bridge, data, 0, None, omega=100.)
        limited = bridge.source.actuator_ctrllimited.astype(bool)
        self.assertTrue(np.all(actual[limited] <= bridge.source.actuator_ctrlrange[limited, 1]))
        self.assertTrue(np.all(actual[limited] >= bridge.source.actuator_ctrlrange[limited, 0]))
        fingers = np.asarray([not n.endswith('_motor') for n in bridge.actuator_names])
        np.testing.assert_array_equal(actual[fingers], nominal[fingers])
        np.testing.assert_array_equal(values, before)


if __name__ == '__main__':
    unittest.main()
