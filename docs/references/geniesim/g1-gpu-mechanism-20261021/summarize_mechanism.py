"""Read-only saved G1 GPU trajectory review, scientific figure and compact evidence."""
from pathlib import Path
import hashlib,json,numpy as np,mujoco
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
P=Path(__file__).resolve().parent;A=P/'mechanism-attempt01'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
r=json.loads((A/'report.json').read_text());protocol=json.loads((A/'protocol.json').read_text());th=protocol['physical_thresholds_and_historical_representation_thresholds']
a=np.load(A/'trajectory.npz');m=mujoco.MjModel.from_binary_path(str(A/'preflight/native-profile-mirror.mjb'));d=mujoco.MjData(m)
inputs=[A/'trajectory.npz',A/'report.json',A/'protocol.json',A/'contacts.jsonl',A/'preflight/native-profile-mirror.mjb']
before={str(p.relative_to(P)):sha(p) for p in inputs}
steps=a['physical_step'];assert np.array_equal(steps,np.arange(1901))
expected=[];x=np.float32(0)
for k in steps:
 if k:x=np.float32(x+np.float32(.001))
 expected.append(x)
clock=np.asarray(expected);assert np.array_equal(clock,a['GPU_time_s'])
assert np.array_equal(a['hook_calls'],steps) and np.array_equal(a['solver_steps'],steps)
loop_ids=np.flatnonzero(m.eq_type==mujoco.mjtEq.mjEQ_CONNECT);independent=[]
for q in a['native_qpos']:
 d.qpos[:]=q;mujoco.mj_fwdPosition(m,d)
 errors=[]
 for e in loop_ids:
  pa,cb=m.eq_obj1id[e],m.eq_obj2id[e]
  x=d.xpos[pa]+d.xmat[pa].reshape(3,3)@m.eq_data[e,:3]
  y=d.xpos[cb]+d.xmat[cb].reshape(3,3)@m.eq_data[e,3:6]
  errors.append(np.linalg.norm(x-y))
 independent.append(errors)
independent=np.asarray(independent)
qidx=m.jnt_qposadr[r['mapping']['native_scalar_joints']];vidx=m.jnt_dofadr[r['mapping']['native_scalar_joints']]
limits=m.jnt_range[r['mapping']['native_scalar_joints']]
violation=np.maximum(np.maximum(limits[:,0]-a['native_qpos'][:,qidx],a['native_qpos'][:,qidx]-limits[:,1]),0)
assert np.array_equal(violation,a['limit'])
caps=np.max(abs(m.jnt_actfrcrange[r['mapping']['native_scalar_joints']]),axis=1)
excess=np.maximum(abs(a['qfrc_actuator'][:,vidx])-caps,0);assert np.array_equal(excess,a['effort_excess'])
contact_records=[json.loads(line) for line in (A/'contacts.jsonl').read_text().splitlines()]
assert len(contact_records)==1901 and all(c['physical_step']==k for k,c in enumerate(contact_records))
checks={'all1900_physical_steps':True,'independent_hook_and_solver_counts':True,'GPU_clock_equals_independent_float32_recurrence':True,
 'all_source_loops_independent_postq_FK_below_1mm':bool(np.max(independent)<=th['loop_m']),
 'recorded_Newton_loops_below_original_limit':bool(np.max(a['loops'])<=th['loop_m']),
 'mimic':bool(np.max(abs(a['mimic']))<=th['mimic_rad']),
 'joint_limits':bool(np.max(violation)<=th['joint_limit_SI']),
 'scalar_velocity':bool(np.max(abs(a['native_qvel'][:,vidx]))<=th['scalar_speed_SI']),
 'effort_caps':bool(np.all(excess[1:]<=th['effort_absolute_slack']+th['effort_relative_slack']*caps)),
 'base_position':bool(np.max(a['base_drift'])<=th['base_m']),'base_orientation':bool(np.max(a['base_angle'])<=th['base_rad']),
 'state_sync':bool(np.max(a['sync_errors'][:,:2])<=th['q_qd_sync_SI'] and np.max(a['sync_errors'][:,2])<=th['body_position_sync_m'] and np.max(a['sync_errors'][:,3])<=th['body_quaternion_component_sync']),
 'all_finite':all(np.isfinite(a[k]).all() for k in a.files),'all_constraints_active':bool(np.all(a['eq_active'])),
 'no_GPU_overflow':bool(not np.any(a['overflow'])),
 'no_unexpected_physical_contacts':bool(not np.any(a['contact_count']) and all(c['physical_contacts']==0 for c in contact_records)),
 'both_hands_motion':bool(all(h['passed'] for h in r['per_hand'])),
 'runtime_GPU_parameters_unchanged':r['GPU_physical_parameters_unchanged'],'source_and_protocol_inputs_unchanged':r['input_files_unchanged']}
assert all(checks.values()),checks
summary={'scope':r['scope'],'status':'PASS_bounded_adapted_mechanism_only','physical_steps':1900,'saved_samples_including_initial':1901,
 'RSS_control_frames':950,'integration_dt_s':.001,'backend':'Newton1.7.0/MuJoCoWarp3.15.0/Warp1.18.0 CUDA discrete singleworld',
 'protocol_sha256':sha(A/'protocol.json'),'runner_sha256':protocol['inputs_sha256'][str((P/'run_mechanism.py').relative_to(P.parents[3]))],
 'per_hand':r['per_hand'],'maximum_metrics':{'source_loop_Newton_state_m':float(np.max(a['loops'])),
 'source_loop_independent_GPU_q_native_FK_m':float(np.max(independent)),'mimic_rad':float(np.max(abs(a['mimic']))),
 'joint_limit_violation_SI':float(np.max(violation)),'scalar_velocity_SI_per_s':float(np.max(abs(a['native_qvel'][:,vidx]))),
 'effort_excess_N_or_Nm':float(np.max(excess)),'base_drift_m':float(np.max(a['base_drift'])),'base_drift_rad':float(np.max(a['base_angle'])),
 'state_sync_q_qd_position_quaternion':np.max(a['sync_errors'],axis=0).tolist(),'contact_force_N':float(np.max(a['max_solved_contact_force_N'])),
 'contact_candidates':int(np.max(a['candidate_count'])),'GPU_clock_error_ULP':float(np.max(a['time_error_ULP']))},
 'time':{'ideal_duration_s':1.9,'GPU_float32_s':float(clock[-1]),'RSS_frame_clock_s':r['rss_frame_time_s'],
 'GPU_error_from_ideal_s':float(clock[-1])-1.9,'comparison':'exact repeated float32 addition; original physical step budget/count unchanged'},
 'offline_checks':checks,'new_physics_steps_for_this_review':0,'offline_forward_positions':1901,
 'task_success_evaluated':False,'exact_source_qualification':'failed_retained','contact_grasp_lift_acceptance_evaluated':False,
 'limitations':['Singleworld host-synchronized mechanism trial, not GPU parallel G1 task evidence.',
 'No object or task; no contacts occurred, so grasp/contact response remains unqualified.',
 'Exact source fidelity remains failed; strict six-equality profile is an explicit runtime adaptation.',
 'Profile currently requires explicit GPU upload; reset/notify persistence is not production qualified.',
 'This does not complete G1/G2/Sharpa common-demo coverage.'],
 'source_inputs_sha256':before}
(A/'compact-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
np.savez_compressed(A/'offline-loop-check.npz',physical_step=steps,independent_source_loop_m=independent)
fig,axes=plt.subplots(3,2,figsize=(12,10),constrained_layout=True);t=steps*.001
ax=axes[0,0];ax.plot(t,a['commanded_driver_q'][:,0],'k--',label='command');ax.plot(t,a['driver_q'][:,0],label='left');ax.plot(t,a['driver_q'][:,1],':',label='right');ax.set(ylabel='Driver joint (rad)',title='Both source hands: open / hold / return');ax.legend()
ax=axes[0,1];ax.plot(t,np.max(a['loops'],axis=1)*1000,label='Newton body state');ax.plot(t,np.max(independent,axis=1)*1000,':',label='Independent post-q FK');ax.axhline(1,color='r',ls='--',label='original 1 mm gate');ax.set(ylabel='Maximum loop error (mm)');ax.legend()
ax=axes[1,0];ax.plot(t,np.max(abs(a['native_qvel'][:,vidx]),axis=1),label='all 34 scalar joints');ax.axhline(10,color='r',ls='--',label='original gate');ax.set(ylabel='Maximum speed (SI / s)');ax.legend()
ax=axes[1,1];ax.plot(t,a['max_solved_contact_force_N'],label='actual contact force');ax.plot(t,np.max(excess,axis=1),'--',label='source cap excess');ax.set(ylabel='Force / excess (N or Nm)',title='No contacts; no contact-task acceptance');ax.legend()
ax=axes[2,0];ax.plot(t,a['sync_errors'][:,2]*1e6,label='body position (µm)');ax.plot(t,a['sync_errors'][:,3]*1e6,label='quaternion component (×1e-6)');ax.axhline(10,color='r',ls='--',label='original limits');ax.set(ylabel='State synchronization error');ax.legend()
ax=axes[2,1];ax.plot(t,a['time_error_vs_ideal_s']*1e6,label='GPU float32 minus ideal (µs)');ax.plot(t,a['time_error_ULP'],label='GPU vs float32 recurrence (ULP)');ax.set(ylabel='Clock representation diagnostics');ax.legend()
for ax in axes.flat:ax.set_xlabel('Physical time from step count (s)');ax.grid(alpha=.25)
fig.suptitle('G1 + RSS + Newton GPU: 1900 / 1900 mechanism steps PASS\nSingle world, source drives/caps/meshes preserved; task and contact acceptance NOT evaluated',fontsize=13)
fig.savefig(A/'mechanism-diagnostics.png',dpi=170);plt.close(fig)
assert all(sha(P/f)==h for f,h in before.items())
artifacts=inputs+[A/'compact-summary.json',A/'offline-loop-check.npz',A/'mechanism-diagnostics.png',Path(__file__)]
(A/'manifest.json').write_text(json.dumps({'physics_steps_added_by_review':0,'files':{str(p.relative_to(P)):sha(p) for p in artifacts}},indent=2)+'\n')
print(json.dumps({'all_checks_passed':all(checks.values()),'maximum_metrics':summary['maximum_metrics'],'physics_steps':1900},indent=2))
