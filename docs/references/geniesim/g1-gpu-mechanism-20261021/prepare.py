"""Full G1 RSS/Newton GPU construction audit. Construction/audit helper has NO physics-step path."""
from pathlib import Path
import sys, os, json, traceback, argparse, shutil, ast, importlib.metadata as meta
import numpy as np
P=Path(__file__).resolve().parent;SOURCE=P.parent;ROOT=SOURCE.parents[2]
sys.path[:0]=[str(ROOT/'robosimstudio'),str(ROOT)]
from agent.run_reports import atomic_json,sha256_file
def build_context(out, *, keep_open=False):
 out=Path(out);out.mkdir(exist_ok=False)
 report={'scope':'full_G1_RSS_GPU_zero_step_mechanism_preflight','physics_steps':0,'task_success_evaluated':False,
         'source_exact_qualification':'failed_retained','status':'incomplete','blockers':[],
         'versions':{p:meta.version(p) for p in ['newton','mujoco','mujoco-warp','warp-lang']},
         'script_sha256':sha256_file(__file__),'CUDA_VISIBLE_DEVICES':os.environ.get('CUDA_VISIBLE_DEVICES')}
 sim=None
 try:
  import warp as wp,mujoco,newton,robosimstudio as rss
  from robosimstudio.specs import ArrangementSpec
  from robosimstudio.specs.physics import backend
  from agent.rss_scene import TaskSceneSpec
  from agent.rss_mjcf_export import _expand_solref_shorthand,_compare
  old=ROOT/'.workbench/previews/geniesim-gripper-dynamics-20261021'
  sys.path.insert(0,str(old/'discrete-integrator/newton-full/attempt03'))
  from audit_roundtrip import audit
  M=json.loads((old/'report.json').read_text())['mapping']
  export=json.loads((SOURCE/'export-report.json').read_text())
  source_hash=sha256_file(SOURCE/'portable.xml');assert source_hash==export['output_sha256']
  report['source_sha256']=source_hash
  # Materialize only shorthand already present in the XML, using MuJoCo's compiled semantics.
  xml=out/'source-explicit.xml';shutil.copyfile(SOURCE/'portable.xml',xml)
  original=mujoco.MjModel.from_xml_path(str(xml))
  report['solref_expansions']=_expand_solref_shorthand(xml,original)
  explicit=mujoco.MjModel.from_xml_path(str(xml))
  report['explicit_source_native_comparison']=_compare(original,explicit)
  report['explicit_xml_sha256']=sha256_file(xml)
  # Freeze the original CPU mechanism thresholds, now for a distinct GPU qualification.
  cpu_script=SOURCE/'rss_mechanism.py'
  # Read literal thresholds without importing the CPU runner (which changes CUDA env).
  tree=ast.parse(cpu_script.read_text())
  thresholds=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TH' for t in node.targets))
  protocol={'scope':report['scope'],'physics_steps_authorized':0,'future_physical_step_budget':1900,
   'future_control_hz':500,'future_substeps':2,'dt_s':.001,'backend':'Newton1.7 SolverMuJoCo / MuJoCo Warp3.15 / CUDA / discrete(4)',
   'source_sha256':source_hash,'explicit_source_sha256':sha256_file(xml),'thresholds':thresholds,
   'source_solver_options':{k:float(getattr(explicit.opt,k)) for k in ['iterations','ls_iterations','impratio','cone','jacobian']},
   'task_success_evaluated':False,'source_exact_qualification':'failed_retained',
   'environment_addition':'Exactly one RSS void world-fixed ground_plane at z0, +Z normal; all source fields still audited. Ground contact is not silently ignored.',
   'profile':'Source two-local-point connect anchors plus six strict eq solref(.002,1)/solimp(.9999,.9999,.001,.5,2); if used, upload to actual mjw_model and read back. CPU template alone is not GPU state.',
   'prohibited':'No CPU or GPU step/reset/warmup, no asset changes, no source gain/cap/limit/contact changes, no threshold relaxation.'}
  atomic_json(out/'protocol.json',protocol)
  wp.init();assert wp.is_cuda_available();wp.set_device('cuda:0')
  scene=rss.Scene(cell='void',physics=ArrangementSpec(gravity=-9.81,control_hz=500,substeps=2,use_cuda_graph=False,
               backends=(('*',backend('mujoco',integrator=4,iterations=int(explicit.opt.iterations),
                   ls_iterations=int(explicit.opt.ls_iterations),impratio=float(explicit.opt.impratio),
                   cone=int(explicit.opt.cone),jacobian=int(explicit.opt.jacobian))),)))
  scene.add(TaskSceneSpec(path=str(xml)))
  print('BUILDING FULL G1 GPU RSS, zero physics',flush=True)
  sim=scene.build(device='cuda:0',warmup=False,use_cuda_graph=False);sim.warmup=False
  model=sim.model;solver=sim.pipeline.solver;m=solver.mj_model;mw=solver.mjw_model;dw=solver.mjw_data
  mujoco.mj_saveModel(m,str(out/'native-immediately-after-build.mjb'))
  assert not solver.use_mujoco_cpu and str(model.device)=='cuda:0'
  assert solver._step==0 and sim.step_count==0 and sim.pipeline.step_count==0
  assert not sim.pipeline.captured
  report['device']={'device':str(model.device),'name':wp.get_device().name,'use_mujoco_cpu':solver.use_mujoco_cpu,
                    'integrator_gpu':int(mw.opt.integrator),'nworld':int(dw.nworld)}
  assert int(mw.opt.integrator)==4 and dw.nworld==1
  mw.opt.timestep.fill_(.001);m.opt.timestep=.001
  def gpu_value(value):
   a=value.numpy() if hasattr(value,'numpy') else np.asarray(value)
   return a.tolist()
  report['actual_GPU_options']={k:gpu_value(getattr(mw.opt,k)) for k in ['timestep','gravity','iterations','ls_iterations','impratio_invsqrt','cone','solver','integrator']}
  report['actual_GPU_options']['is_sparse']=bool(mw.is_sparse)
  report['actual_GPU_options']['impratio_derived_from_inverse_sqrt']=(1.0/mw.opt.impratio_invsqrt.numpy()**2).tolist()
  assert np.allclose(1.0/mw.opt.impratio_invsqrt.numpy()**2,explicit.opt.impratio,rtol=2e-5,atol=1e-6)
  assert np.allclose(mw.opt.gravity.numpy(),[0,0,-9.81],rtol=0,atol=5e-7)
  mujoco.mj_saveModel(m,str(out/'native-before-profile.mjb'))
  # Keep the raw source-only audit: its count mismatch is real and remains recorded.
  rt=audit(explicit,m);atomic_json(out/'raw-source-roundtrip.json',rt)
  G=rt['mapping']['reference_geom_to_native'];extra=sorted(set(range(m.ngeom))-set(G))
  extras=[]
  for g in extra:
   shape=int(m.geom(g).name.rsplit('_',1)[-1])
   extras.append({'geom':g,'name':m.geom(g).name,'shape':shape,'label':model.shape_label[shape],
     'body':int(m.geom_bodyid[g]),'type':int(m.geom_type[g]),'pos':m.geom_pos[g].tolist(),
     'quat_wxyz':m.geom_quat[g].tolist(),'size':m.geom_size[g].tolist(),
     'contype':int(m.geom_contype[g]),'conaffinity':int(m.geom_conaffinity[g]),
     'solref':m.geom_solref[g].tolist(),'solimp':m.geom_solimp[g].tolist()})
  floor_ok=len(extras)==1
  if floor_ok:
   e=extras[0]
   floor_ok=e['label']=='ground_plane' and e['body']==0 and e['type']==int(mujoco.mjtGeom.mjGEOM_PLANE)
   floor_ok=bool(floor_ok and np.allclose(e['pos'],0,rtol=0,atol=1e-12) and np.allclose(e['quat_wxyz'],[1,0,0,0],rtol=0,atol=1e-12))
  expected_counts=[explicit.nbody,explicit.njnt,explicit.nq,explicit.nv,explicit.nu,explicit.neq,explicit.ngeom+1,explicit.nmesh]
  actual_counts=[m.nbody,m.njnt,m.nq,m.nv,m.nu,m.neq,m.ngeom,m.nmesh]
  source_fail=[c for c in rt['checks'] if not c['passed'] and c['field']!='counts' and not c.get('representation_only') and not c.get('authorized_profile_restores_source_child_anchor')]
  composition={'raw_source_only_counts_passed':False,'expected_composed_counts':expected_counts,'actual_counts':actual_counts,
  'source_fields_failed':source_fail,'explicit_additions':extras,'only_expected_floor':floor_ok,
  'qualified':floor_ok and expected_counts==actual_counts and not source_fail}
  atomic_json(out/'composition-audit.json',composition)
  if not composition['qualified']:report['blockers'].append('source_or_composition_roundtrip_failed')
  # Snapshot actual GPU tensors and compare every common critical source field.
  gpu_fields={};checks=[]
  fields=['qpos0','body_mass','body_inertia','body_ipos','body_iquat','body_gravcomp','body_pos','body_quat',
  'jnt_type','jnt_axis','jnt_pos','jnt_range','jnt_limited','jnt_actfrcrange','jnt_actfrclimited','jnt_solref','jnt_solimp',
  'dof_damping','dof_armature','actuator_trnid','actuator_trntype','actuator_gainprm','actuator_biasprm',
  'actuator_gear','actuator_ctrlrange','actuator_ctrllimited','actuator_forcerange','actuator_forcelimited',
  'actuator_gaintype','actuator_biastype','actuator_dyntype','geom_contype','geom_conaffinity','geom_friction',
  'geom_type','geom_bodyid','geom_pos','geom_quat','geom_solref','geom_solimp','geom_gap','geom_margin','eq_type','eq_obj1id','eq_obj2id','eq_data','eq_solref','eq_solimp']
  for key in fields:
   v=getattr(mw,key,None);native=np.asarray(getattr(m,key))
   if v is None:checks.append({'field':key,'available':False,'passed':False});continue
   gpu=v.numpy() if hasattr(v,'numpy') else np.asarray(v);gpu_fields[key]=gpu
   compare=gpu[0] if gpu.ndim==native.ndim+1 and gpu.shape[0]==1 else gpu
   equal=compare.shape==native.shape and np.allclose(compare,native,rtol=2e-5,atol=1e-6)
   checks.append({'field':key,'available':True,'gpu_shape':list(gpu.shape),'native_shape':list(native.shape),
      'passed':bool(equal),'max_abs_error':float(np.max(abs(compare.astype(float)-native.astype(float)))) if compare.shape==native.shape else None})
  np.savez_compressed(out/'gpu-before-profile.npz',**gpu_fields)
  atomic_json(out/'gpu-native-fields.json',checks)
  report['gpu_native_failed_fields']=[c['field'] for c in checks if not c['passed']]
  if report['gpu_native_failed_fields']:report['blockers'].append('gpu_native_fields_need_diagnosis')
  # Name-based source body/joint identity. Restore dual-anchor profile directly on GPU and mirror it explicitly.
  def unique(names,path):
   ids=[i for i,n in enumerate(names) if n.endswith('/'+path.replace('/','_')) or n==path or n==path.replace('/','_')]
   assert len(ids)==1,(path,ids);return ids[0]
  def inverse(arr,j):
   ids=np.flatnonzero(arr==j);assert len(ids)==1,(j,ids);return int(ids[0])
  bj=[unique(model.body_label,p) for p in M['body_labels']]
  nb=[inverse(solver.mjc_body_to_newton.numpy()[0],j) for j in bj]
  eq_data=m.eq_data.copy();eq_ref=np.tile([.002,1],(m.neq,1));eq_imp=np.tile([.9999,.9999,.001,.5,2],(m.neq,1))
  assert m.neq==6 and np.count_nonzero(m.eq_type==mujoco.mjtEq.mjEQ_CONNECT)==4 and np.count_nonzero(m.eq_type==mujoco.mjtEq.mjEQ_JOINT)==2
  loops=[]
  for slot in range(4):
   parent=nb[M['loop_parent_bodies'][slot]];child=nb[M['loop_child_bodies'][slot]]
   ids=np.flatnonzero((m.eq_type==mujoco.mjtEq.mjEQ_CONNECT)&(m.eq_obj1id==parent)&(m.eq_obj2id==child));assert len(ids)==1
   e=int(ids[0]);eq_data[e,:6]=np.r_[M['loop_parent_frames'][slot][:3],M['loop_child_frames'][slot][:3]]
   loops.append({'native_equality':e,'native_parent':parent,'native_child':child,'source_anchors':eq_data[e,:6].tolist()})
  # No notify/reset after this frozen experimental upload. Those can rebuild connect child anchors.
  profile_readback={}
  for key,value in [('eq_data',eq_data),('eq_solref',eq_ref),('eq_solimp',eq_imp)]:
   array=getattr(mw,key);before=array.numpy();expected=value[None] if before.ndim==value.ndim+1 else value
   assert expected.shape==before.shape,(key,expected.shape,before.shape)
   array.assign(expected.astype(before.dtype));actual=array.numpy()
   assert np.array_equal(actual,expected.astype(before.dtype))
   getattr(m,key)[:]=value
   profile_readback[key]={'gpu_shape':list(actual.shape),'max_error_to_declared':float(np.max(abs(actual-expected))), 'uploaded_and_readback':True}
  report['profile']={'loops':loops,'gpu_verified':profile_readback,'persistence':'Direct frozen experiment upload only; future reset/notify may overwrite and needs a production profile adapter.'}
  # Actual initial state/FK and source robot vs RSS floor geometry, independent of native cached solve state.
  q=dw.qpos.numpy()[0];v=dw.qvel.numpy()[0]
  view=mujoco.MjData(m);view.qpos[:]=q;view.qvel[:]=v;mujoco.mj_fwdPosition(m,view)
  njs=[unique(model.joint_label,M['joint_labels'][i]) for i in M['scalar_joint_indices']]
  js=[inverse(solver.mjc_jnt_to_newton_jnt.numpy()[0],j) for j in njs]
  nq=model.joint_q_start.numpy()[njs];nv=model.joint_qd_start.numpy()[njs]
  state=sim.state;qb=state.body_q.numpy()[bj];native_qb=np.c_[view.xpos[nb],view.xquat[nb][:,[1,2,3,0]]]
  signs=np.where(np.sum(qb[:,3:]*native_qb[:,3:],axis=1)<0,-1,1)[:,None]
  sync={'q':float(np.max(abs(state.joint_q.numpy()[nq]-q[m.jnt_qposadr[js]]))),
        'qd':float(np.max(abs(state.joint_qd.numpy()[nv]-v[m.jnt_dofadr[js]]))),
        'body_position':float(np.max(abs(qb[:,:3]-native_qb[:,:3]))),
        'body_quaternion':float(np.max(abs(qb[:,3:]*signs-native_qb[:,3:])))}
  report['initial_sync']=sync
  if sync['q']>1e-6 or sync['qd']>1e-6 or sync['body_position']>1e-5 or sync['body_quaternion']>1e-5:report['blockers'].append('initial_state_sync')
  robot_min_z=[]
  for g in G:
   mid=m.geom_dataid[g];a=m.mesh_vertadr[mid];count=m.mesh_vertnum[mid]
   vertices=m.mesh_vert[a:a+count]@view.geom_xmat[g].reshape(3,3).T+view.geom_xpos[g]
   robot_min_z.append(float(vertices[:,2].min()))
  floor_pairs=[{'geom1':int(c.geom1),'geom2':int(c.geom2),'distance_m':float(c.dist)} for c in view.contact if c.geom1 in extra or c.geom2 in extra]
  report['initial_floor']={'min_source_mesh_vertex_world_z_m':min(robot_min_z),'closest_source_geom':int(G[int(np.argmin(robot_min_z))]),'geometric_pairs':floor_pairs,'candidate_count':len(floor_pairs)}
  if any(c['distance_m']<0 for c in floor_pairs):report['blockers'].append('initial_robot_floor_overlap')
  # Use the shared production outlet if available; never duplicate its control adapter.
  try:
   from agent.rss_position_targets import PositionTargetOutlet
  except ImportError:
   report['outlet']={'status':'blocked','reason':'shared PositionTargetOutlet GPU implementation not yet available'};report['blockers'].append('GPU_position_outlet_unavailable')
  else:
   try:
    outlet=PositionTargetOutlet(model,solver,list(model.mujoco.actuator_label))
    control=model.control();targets=control.joint_target_q.numpy();commands=control.mujoco.ctrl.numpy()
    # Distinct sub-milliradian probes are only inputs to the control-copy kernel, never physical states.
    expected_ctrl=np.zeros(m.nu)
    for i,row in enumerate(outlet.mapping):
     j=row['native_joint'];test=float(q[m.jnt_qposadr[j]])+(i+1)*1e-5
     if m.actuator_ctrllimited[row['native_actuator']]:test=float(np.clip(test,*m.actuator_ctrlrange[row['native_actuator']]))
     targets[row['target_slot']]=test;expected_ctrl[row['native_actuator']]=np.float32(test)
    control.joint_target_q.assign(targets);outlet.apply(control)
    saved={'ctrl':dw.ctrl.numpy(),'qfrc_applied':dw.qfrc_applied.numpy(),'xfrc_applied':dw.xfrc_applied.numpy()}
    solver._apply_mjc_control(model,state,control,dw)
    actual=dw.ctrl.numpy()[0];assert np.array_equal(actual,expected_ctrl.astype(actual.dtype))
    for key,arr in saved.items():getattr(dw,key).assign(arr)
    report['outlet']={'status':'zero_step_control_copy_passed','mapping':list(outlet.mapping),'actual_gpu_ctrl_max_error':float(np.max(abs(actual-expected_ctrl))),'method':'actual Newton GPU _apply_mjc_control input-copy kernel, no solver step; data inputs restored'}
   except Exception:
    report['outlet']={'status':'blocked','failure':traceback.format_exc()};report['blockers'].append('GPU_position_outlet_qualification')
  mujoco.mj_saveModel(m,str(out/'native-profile-mirror.mjb'))
  np.savez_compressed(out/'gpu-profile.npz',eq_data=mw.eq_data.numpy(),eq_solref=mw.eq_solref.numpy(),eq_solimp=mw.eq_solimp.numpy(),qpos=dw.qpos.numpy(),qvel=dw.qvel.numpy(),eq_active=dw.eq_active.numpy(),newton_q=state.joint_q.numpy(),newton_qd=state.joint_qd.numpy(),newton_body_q=state.body_q.numpy())
  assert solver._step==0 and sim.step_count==0 and np.all(dw.time.numpy()==0)
  assert sha256_file(SOURCE/'portable.xml')==source_hash
  report['source_unchanged']=True
  report['status']='blocked_zero_step' if report['blockers'] else 'zero_step_preflight_passed'
 except Exception:
  report['status']='exception';report['failure']=traceback.format_exc();print(report['failure'],flush=True)
 finally:
  if sim is not None and (not keep_open or report['status']=='exception'):sim.close()
  atomic_json(out/'report.json',report);print('ZERO STEP RESULT',report['status'],report['blockers'],flush=True)
 return locals()

if __name__=="__main__":
 parser=argparse.ArgumentParser();parser.add_argument("--attempt",default="attempt01");args=parser.parse_args()
 build_context(P/args.attempt)
