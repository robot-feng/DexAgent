"""Bounded single-world full-G1 RSS GPU mechanism diagnostic; not a task.

--freeze / --self-check perform no construction or physics. Default constructs
and audits only. --execute needs the frozen protocol SHA explicitly supplied.
The parent must review the protocol before that invocation; no automatic trial.
"""
from pathlib import Path
import argparse, ast, json, sys, traceback, hashlib
import numpy as np
from scipy.spatial.transform import Rotation
P=Path(__file__).resolve().parent;SOURCE=P.parent;ROOT=SOURCE.parents[2]
sys.path[:0]=[str(ROOT/'robosimstudio'),str(ROOT),str(P)]
from agent.run_reports import atomic_json,sha256_file
DT=.001;STEPS=1900
CPU_TREE=ast.parse((SOURCE/'rss_mechanism.py').read_text())
TH=next(ast.literal_eval(n.value) for n in CPU_TREE.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TH' for t in n.targets))
# Retain the reviewed schedule verbatim; importing the CPU module changes CUDA env.
exec(compile(ast.Module(body=[n for n in CPU_TREE.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in ['target_at','StopDiagnostic','self_check']],type_ignores=[]),str(SOURCE/'rss_mechanism.py'),'exec'))

def protocol():
    inputs=[SOURCE/'portable.xml',SOURCE/'source-report.json',SOURCE/'export-report.json',
      ROOT/'.workbench/previews/geniesim-gripper-dynamics-20261021/report.json',
      P/'prepare.py',P/'review_saved.py',P/'run_mechanism.py',
      ROOT/'agent/rss_position_targets.py',ROOT/'agent/rss_mjcf_export.py',
      ROOT/'robosimstudio/robosimstudio/core/schedule.py',
      ROOT/'robosimstudio/robosimstudio/core/probes/contact.py',
      ROOT/'robosimstudio/robosimstudio/scene/compiler.py']
    return {'scope':'full_G1_single_world_RSS_GPU_adapted_mechanism_qualification',
      'objective':'hardware/import/control qualification only; neither faithful task reconstruction nor transferable task generation',
      'source_exact_qualification':'failed_retained','task_success_evaluated':False,
      'physical_step_budget':STEPS,'dt_s':DT,'control_hz':500,'substeps':2,
      'versions':{'newton':'1.7.0','mujoco':'3.15.0','mujoco-warp':'3.15.0','warp-lang':'1.18.0'},
      'backend':'Newton SolverMuJoCo CUDA / MJWarp / discrete(4); source 100 iterations,50 line-search iterations,impratio1,pyramidal cone; AUTO Jacobian compiled sparse at nv34',
      'inputs_sha256':{str(f.relative_to(ROOT)):sha256_file(f) for f in inputs},
      'physical_thresholds_and_historical_representation_thresholds':TH,
      'GPU_representation_checks':{'time':'Actual GPU float32 time vs independent repeated float32 addition of float32(dt), <=1 ULP; ideal double k*dt drift recorded separately. Historical CPU native_clock_s=1e-8 retained above, not applied to float32 accumulation.',
       'dt':'Actual GPU dt equals np.float32(.001) exactly; Python RSS callback dt still satisfies original dt_s=1e-12.',
       'anchors':'Actual GPU eq_data equals source dual anchors cast float32 exactly; source/native double anchors still checked at historical 1e-9m. No physical loop tolerance change.',
       'warnings':'GPU overflow bitmask must remain zero; finite state/force checks and contact/constraint capacities are gated. GPU does not expose CPU mjData.warning counters; no unavailable-field pass.',
       'step_count':'Pre/post hook counts and solver._step independently equal physical step k; final RSS complete frames950.'},
      'initial_state':'Fresh import qpos0, zero qvel; no warmup/reset; all20targets hold initial q except two source drivers.',
      'command_schedule':'1ms samples of:0-.1s hold0;.1-.5 smoothstep0 to.1rad;.5-.7 hold.1;.7-.9 smoothstep.1 to0;.9-1.9 settle0. Both drivers same nominal command, measured separately.',
      'motion_acceptance':'Both hands peak>=.05rad; step700 each |q-.1|<=.03rad; final each |q|<.03rad; all1900steps and every original gate must pass.',
      'runtime_adaptations':'Expand source solref shorthand before import. Exactly one world-fixed RSS void ground plane z0. Source42body gravity compensation. Restore four dual-local-point source connect anchors and six strict eq solref(.002,1),solimp(.9999,.9999,.001,.5,2) on actual GPU, read back and check every step. No reset/notify after profile.',
      'source_preservation':'Full G1 source meshes/inertia/gains/caps/limits/contact parameters preserved. Empty fixed massless inertia frames and physical inertia tensors checked separately from raw quaternion components. No cube/fixture or shared table.',
      'control_path':'Production PositionTargetOutlet all20 unique source actuator labels, per1ms pre_solve, real RSS500Hz/2substeps; graph disabled. Not batched policy rollout.',
      'observation_path':'Actual mjw_data qpos/qvel/ctrl/forces/time; post_solve actual new Newton state. Independent native mj_fwdPosition from GPU q for current FK only. Cached CPU solver state is not execution evidence. RSS ContactProbe actual last-solve contact forces; candidate pairs alone are not physical contact.',
      'hard_stops':['nonfinite','mimic','loop','limit','speed','effort','base','state_mapping','control_mapping','hook_clock','GPU_clock','GPU_overflow','constraints','unexpected_contact','integrator_or_dt','profile_changed','hold_endpoint_tracking'],
      'stop_behavior':'Save failing post-step sample then raise StopDiagnostic; no next substep. Record RSS frame count separately because odd-substep failures leave it at last complete frame. No reset/retry/search.',
      'execution_authorization':'Protocol prepared for parent review; execution requires --execute --protocol-sha with exact frozen protocol hash.'}

def self_check_gpu():
    # Exercise actual RSS ordering with a fake solver; no Newton/MuJoCo integration.
    self_check()
    expected=np.float32(0);times=[]
    for k in range(1,STEPS+1):expected=np.float32(expected+np.float32(DT));times.append(expected)
    reviewed=json.loads((P/'saved-representation-review.json').read_text())
    assert reviewed['frozen_GPU_profile_zero_step_qualified']
    # Verify every dynamic field against the installed MuJoCo Warp dataclass schema.
    from mujoco_warp._src.types import Data,Contact
    fields=['qpos','qvel','ctrl','time','eq_active','qfrc_actuator','actuator_force','qfrc_constraint','qacc','qfrc_bias','qfrc_passive','qfrc_gravcomp','overflow','nacon','nefc']
    assert all(f in Data.__annotations__ for f in fields)
    assert all(f in Contact.__annotations__ for f in ['geom','dist','pos','frame','efc_address','worldid'])
    atomic_json(P/'GPU-runner-selfcheck.json',{'physics_steps':0,'model_builds':0,'passed':True,
       'script_sha256':sha256_file(__file__),'real_RSS_hook_fake_solver_selfcheck':'rss-hook-selfcheck.json',
       'actual_installed_GPU_Data_schema':fields,'float32_final_expected_clock_s':float(expected),
       'float32_final_error_to_ideal_s':float(expected)-STEPS*DT,
       'final_ULP_s':float(np.spacing(expected)),'float32_clock_count':len(times),
       'note':'Schema and saved representation qualification only; no actual G1 GPU motion.'})

def run(args):
    from prepare import build_context
    from review_saved import review
    import mujoco
    from agent.rss_position_targets import PositionTargetOutlet
    from robosimstudio.core.probes.contact import ContactProbe,MIN_IMPULSE
    frozen=P/'mechanism-protocol.json';assert frozen.is_file()
    spec=json.loads(frozen.read_text());assert spec==protocol(),'Frozen inputs/code changed; review and freeze again.'
    if args.execute:assert args.protocol_sha==sha256_file(frozen),'Exact parent-reviewed protocol SHA required.'
    out=P/args.output;out.mkdir(exist_ok=False)
    atomic_json(out/'protocol.json',spec)
    result={'scope':spec['scope'],'task_success_evaluated':False,'source_exact_qualification':'failed_retained',
      'physics_steps':0,'status':'incomplete','protocol_sha256':sha256_file(frozen)}
    ctx=None;rows=[];contact_file=None
    try:
      ctx=build_context(out/'preflight',keep_open=True)
      assert ctx['report']['status']!='exception',ctx['report'].get('failure')
      qual=review(out/'preflight',out/'representation-review.json')
      assert qual['frozen_GPU_profile_zero_step_qualified'],qual
      assert ctx['report']['versions']==spec['versions']
      sim=ctx['sim'];model=ctx['model'];solver=ctx['solver'];m=ctx['m'];mw=ctx['mw'];dw=ctx['dw'];M=ctx['M']
      assert solver._step==0 and sim.pipeline.step_count==0 and sim.step_count==0
      assert sim.pipeline.substeps==2 and abs(sim.pipeline.sim_dt-DT)<TH['dt_s'] and not sim.pipeline.captured
      assert MIN_IMPULSE==TH['unexpected_contact_impulse_Ns'] and m.ngravcomp==42
      assert np.all(m.body_gravcomp[ctx['nb']]==1) and np.all(mw.body_gravcomp.numpy()[0,ctx['nb']]==1)
      unique=ctx['unique'];inverse=ctx['inverse'];nj=ctx['njs'];bj=ctx['bj'];nb=ctx['nb'];js=ctx['js']
      qidx=m.jnt_qposadr[js];vidx=m.jnt_dofadr[js];nqidx=ctx['nq'];nvidx=ctx['nv']
      assert len(js)==34 and len(bj)==42 and m.nq==m.nv==34
      limits=m.jnt_range[js].copy();caps=np.max(abs(m.jnt_actfrcrange[js]),axis=1)
      outlet=PositionTargetOutlet(model,solver,list(model.mujoco.actuator_label));assert len(outlet.mapping)==20
      drivers=[unique(model.joint_label,M['joint_labels'][i]) for i in M['driver_joint_indices']]
      dr=[next(r for r in outlet.mapping if r['newton_joint']==i) for i in drivers]
      dq=[m.jnt_qposadr[r['native_joint']] for r in dr]
      fr=[inverse(solver.mjc_jnt_to_newton_jnt.numpy()[0],unique(model.joint_label,M['joint_labels'][i])) for i in M['follower_joint_indices']]
      fq=m.jnt_qposadr[fr]
      for r,f in zip(dr,fr):
        ids=np.flatnonzero((m.eq_type==mujoco.mjtEq.mjEQ_JOINT)&(((m.eq_obj1id==f)&(m.eq_obj2id==r['native_joint']))|((m.eq_obj2id==f)&(m.eq_obj1id==r['native_joint']))))
        assert len(ids)==1 and np.array_equal(m.eq_data[ids[0],:5],[0,-1,0,0,0])
      source_check=json.loads((SOURCE/'source-report.json').read_text())
      ev=next(c for c in source_check['checks'] if c['id']=='native_ball_loop_anchors_preserved')['evidence']
      rt=ctx['rt']['mapping']['reference_equality_to_native']
      for loop in ctx['loops']:
        matches=[r for r in ev if rt[r['equality_ids'][0]]==loop['native_equality']]
        assert len(matches)==1 and matches[0]['bodies_match']
        assert np.allclose(loop['source_anchors'],matches[0]['expected_anchors'],rtol=0,atol=TH['restored_anchor_m'])
      q0=dw.qpos.numpy()[0];assert np.array_equal(q0,m.qpos0.astype(q0.dtype)) and not np.any(dw.qvel.numpy())
      targets=sim.control.joint_target_q.numpy().copy()
      for r in outlet.mapping:targets[r['target_slot']]=q0[m.jnt_qposadr[r['native_joint']]]
      sim.control.joint_target_q.assign(targets);outlet.apply(sim.control)
      profile={k:getattr(mw,k).numpy().copy() for k in ['eq_data','eq_solref','eq_solimp']}
      fixed={k:getattr(mw,k).numpy().copy() for k in ctx['gpu_fields']}
      fixed.update(profile)
      view=mujoco.MjData(m);view.qpos[:]=q0;view.qvel[:]=0;view.eq_active[:]=dw.eq_active.numpy()[0]
      mujoco.mj_forward(m,view)
      balance=float(np.max(abs(view.qfrc_bias-view.qfrc_passive)))
      assert balance<TH['gravity_balance_N_or_Nm']
      result['initial_native_mirror_gravity_balance']=balance
      result['mapping']={'native_scalar_joints':js,'newton_scalar_joints':nj,'native_bodies':nb,'newton_bodies':bj,
         'driver_q_indices':dq,'follower_q_indices':fq.tolist(),'outlet':list(outlet.mapping)}
      base0=sim.state.body_q.numpy()[bj[M['base_body_index']]].copy()
      probe=ContactProbe(solver,model,DT)
      calls=0;completed=0;pending=None;expected_time=np.float32(0)
      contact_file=open(out/'contacts.jsonl','w')
      def sample(k,state):
        nonlocal expected_time
        q=dw.qpos.numpy()[0];v=dw.qvel.numpy()[0];bodies=state.body_q.numpy();nq=state.joint_q.numpy();nv=state.joint_qd.numpy()
        view.qpos[:]=q;view.qvel[:]=v;view.eq_active[:]=dw.eq_active.numpy()[0];mujoco.mj_fwdPosition(m,view)
        actual_body=np.c_[view.xpos[nb],view.xquat[nb][:,[1,2,3,0]]];bs=bodies[bj]
        bquat=bs[:,3:]*np.where(np.sum(bs[:,3:]*actual_body[:,3:],axis=1)<0,-1,1)[:,None]
        sync=np.array([np.max(abs(nq[nqidx]-q[qidx])),np.max(abs(nv[nvidx]-v[vidx])),np.max(abs(bs[:,:3]-actual_body[:,:3])),np.max(abs(bquat-actual_body[:,3:]))])
        anchors=[]
        for slot in range(4):
          def point(i,frame):
            b=bodies[bj[i]];return b[:3]+Rotation.from_quat(b[3:]).apply(frame[:3])
          anchors.append([point(M['loop_parent_bodies'][slot],M['loop_parent_frames'][slot]),point(M['loop_child_bodies'][slot],M['loop_child_frames'][slot])])
        anchors=np.array(anchors);loops=np.linalg.norm(anchors[:,0]-anchors[:,1],axis=1)
        base=bodies[bj[M['base_body_index']]];base_drift=np.linalg.norm(base[:3]-base0[:3]);base_angle=(Rotation.from_quat(base[3:])*Rotation.from_quat(base0[3:]).inv()).magnitude()
        violation=np.maximum(np.maximum(limits[:,0]-q[qidx],q[qidx]-limits[:,1]),0)
        force=dw.qfrc_actuator.numpy()[0];excess=np.maximum(abs(force[vidx])-caps,0)
        ctrl=dw.ctrl.numpy()[0];expected_ctrl=np.empty(m.nu,dtype=ctrl.dtype)
        for r in outlet.mapping:expected_ctrl[r['native_actuator']]=targets[r['target_slot']]
        actual_time=np.float32(dw.time.numpy()[0]);ulp=float(np.spacing(expected_time));clock_error=abs(float(actual_time)-float(expected_time))
        nacon=int(dw.nacon.numpy()[0]);nefc=int(dw.nefc.numpy()[0]);overflow=dw.overflow.numpy().copy()
        capacities_ok=0<=nacon<=dw.naconmax and 0<=nefc<=dw.njmax
        # Never let ContactProbe's defensive clamp turn an overflow into a pass.
        contacts=[];active_count=0;physical_count=0;max_force=0.
        if k and capacities_ok and not np.any(overflow):
          probe.update(step=k)
          addresses=dw.contact.efc_address.numpy()[:nacon];positions=dw.contact.pos.numpy()[:nacon];frames=dw.contact.frame.numpy()[:nacon]
          assert len(probe.geoms)==nacon
          active=np.any(addresses>=0,axis=1);active_count=int(active.sum())
          for i in range(nacon):
            cf=probe.impulses[i]/DT;mag=float(np.linalg.norm(cf));max_force=max(max_force,mag)
            physical=bool(active[i] and mag*DT>=TH['unexpected_contact_impulse_Ns']);physical_count+=int(physical)
            if physical or not np.isfinite(cf).all():
              contacts.append({'geom_pair':probe.geoms[i].tolist(),'active_constraint':bool(active[i]),'signed_distance_m':float(probe.depths[i]),'point_world_m':positions[i].tolist(),'normal_geom1_to_geom2':frames[i,0].tolist(),'force_world_on_geom2_N':cf.tolist()})
        contact_file.write(json.dumps({'physical_step':k,'geometry_state_index':max(k-1,0),'force_phase':'last_solve' if k else 'no_solve',
          'detected_candidates':nacon,'active_constraint_contacts':active_count,'physical_contacts':physical_count,'max_force_N':max_force,'contacts':contacts})+'\n')
        profile_ok=all(np.array_equal(getattr(mw,key).numpy(),value) for key,value in profile.items())
        row={'physical_step':np.array(k),'GPU_time_s':np.array(actual_time),'expected_float32_time_s':np.array(expected_time),
          'time_ULP_s':np.array(ulp),'time_error_ULP':np.array(clock_error/ulp if ulp else 0.),'time_error_vs_ideal_s':np.array(float(actual_time)-k*DT),
          'native_qpos':q,'native_qvel':v,'native_ctrl':ctrl,'commanded_driver_q':np.array([targets[r['target_slot']] for r in dr]),
          'driver_q':q[dq],'mimic':q[dq]+q[fq],'source_loop_anchor_world':anchors,'loops':loops,'limit':violation,'effort_excess':excess,
          'qfrc_actuator':force,'actuator_force':dw.actuator_force.numpy()[0],'qfrc_constraint':dw.qfrc_constraint.numpy()[0],
          'native_qacc':dw.qacc.numpy()[0],'qfrc_bias':dw.qfrc_bias.numpy()[0],'qfrc_passive':dw.qfrc_passive.numpy()[0],'qfrc_gravcomp':dw.qfrc_gravcomp.numpy()[0],
          'newton_body_q':bodies,'native_post_body_q':actual_body,'newton_q':nq,'newton_qd':nv,'sync_errors':sync,
          'base_drift':np.array(base_drift),'base_angle':np.array(base_angle),'hook_calls':np.array(calls),'solver_steps':np.array(solver._step),
          'eq_active':dw.eq_active.numpy()[0],'overflow':overflow,'contact_count':np.array(physical_count),'active_contact_count':np.array(active_count),
          'candidate_count':np.array(nacon),'constraint_count':np.array(nefc),'max_solved_contact_force_N':np.array(max_force)}
        rows.append(row)
        conditions=[('nonfinite',any(not np.isfinite(x).all() for x in row.values()) or any(not np.isfinite(c['force_world_on_geom2_N']).all() for c in contacts)),
          ('mimic',max(abs(row['mimic']))>TH['mimic_rad']),('loop',max(loops)>TH['loop_m']),('limit',max(violation)>TH['joint_limit_SI']),
          ('speed',max(abs(v[vidx]))>TH['scalar_speed_SI']),('effort',k>0 and np.any(excess>TH['effort_absolute_slack']+TH['effort_relative_slack']*caps)),
          ('base',base_drift>TH['base_m'] or base_angle>TH['base_rad']),
          ('state_mapping',max(sync[:2])>TH['q_qd_sync_SI'] or sync[2]>TH['body_position_sync_m'] or sync[3]>TH['body_quaternion_component_sync']),
          ('control_mapping',k>0 and not np.array_equal(ctrl,expected_ctrl)),('hook_clock',calls!=k or solver._step!=k),
          ('GPU_clock',clock_error>ulp),('GPU_overflow',bool(np.any(overflow)) or not capacities_ok),('constraints',not np.all(row['eq_active'])),
          ('unexpected_contact',physical_count>0),('integrator_or_dt',int(mw.opt.integrator)!=4 or not np.all(mw.opt.timestep.numpy()==np.float32(DT))),
          ('profile_changed',not profile_ok),('hold_endpoint_tracking',k==700 and np.any(abs(q[dq]-.1)>TH['endpoint_hold_tracking_rad_each_hand']))]
        return [name for name,bad in conditions if bad]
      def command(state,dt):
        nonlocal calls,pending
        assert abs(dt-DT)<TH['dt_s'] and completed==calls and pending is None and calls<STEPS
        k=completed+1
        for r in dr:targets[r['target_slot']]=target_at(k)
        sim.control.joint_target_q.assign(targets);outlet.apply(sim.control);calls+=1;pending=k
      def record(state,dt):
        nonlocal completed,pending,expected_time
        assert pending is not None and abs(dt-DT)<TH['dt_s']
        k=pending;completed=k;result['physics_steps']=k;pending=None
        expected_time=np.float32(expected_time+np.float32(DT))
        try:reasons=sample(k,state)
        except Exception:
          # Preserve the real failed post-hook state even if a diagnostic itself
          # rejects nonfinite geometry or a backend read raises; never advance.
          np.savez_compressed(out/'sampling-error-state.npz',physical_step=k,
            GPU_qpos=dw.qpos.numpy(),GPU_qvel=dw.qvel.numpy(),GPU_ctrl=dw.ctrl.numpy(),
            GPU_time=dw.time.numpy(),GPU_qfrc_actuator=dw.qfrc_actuator.numpy(),
            GPU_qfrc_constraint=dw.qfrc_constraint.numpy(),
            newton_q=state.joint_q.numpy(),newton_qd=state.joint_qd.numpy(),newton_body_q=state.body_q.numpy())
          contact_file.flush();raise
        if k%100==0 or reasons:print('GPU G1 STEP',k,'q',rows[-1]['driver_q'],'stop',reasons,flush=True)
        if reasons:contact_file.flush();raise StopDiagnostic(k,reasons)
      sim.pipeline.hooks.add('pre_solve',command,name='GPU_all20_source_position_targets_1ms')
      sim.pipeline.hooks.add('post_solve',record,name='GPU_post_state_record_gate_every_1ms')
      sim.pipeline.invalidate_graph(recapture=False)
      reasons=sample(0,sim.state);assert not reasons,reasons
      result['zero_step_preflight_passed']=True
      atomic_json(out/'ready.json',{'passed':True,'physics_steps':0,'protocol_sha256':sha256_file(frozen),
        'initial_state_sync':rows[0]['sync_errors'].tolist(),'GPU_field_access_and_contact_probe_constructed':True})
      if args.execute:
        try:
          for _ in range(STEPS//2):sim.step_teleop()
          assert completed==calls==solver._step==STEPS and len(rows)==STEPS+1
          assert sim.step_count==sim.pipeline.step_count==STEPS//2
          result['status']='completed'
        except StopDiagnostic as exc:
          result['status']='stopped_on_frozen_criterion';result['stop_reasons']=exc.reasons
          assert completed==calls==solver._step==exc.step
        result['rss_completed_frames']=sim.step_count;result['rss_frame_time_s']=sim.pipeline.sim_time
        q=np.stack([r['driver_q'] for r in rows]);peak=np.max(abs(q),axis=0);hold=abs(q[700]-.1) if len(q)>700 else np.full(2,np.inf)
        ok=(peak>=TH['minimum_peak_rad_each_hand'])&(abs(q[-1])<TH['final_return_rad'])&(hold<=TH['endpoint_hold_tracking_rad_each_hand'])
        result['per_hand']=[{'side':s,'peak_rad':float(peak[i]),'final_rad':float(q[-1,i]),'hold700_error_rad':float(hold[i]) if np.isfinite(hold[i]) else None,'passed':bool(ok[i] and result['status']=='completed')} for i,s in enumerate(['left','right'])]
        result['mechanism_motion_accepted']=bool(result['status']=='completed' and all(ok))
      else:result['status']='zero_step_preflight_passed'
      result['GPU_physical_parameters_unchanged']=all(np.array_equal(getattr(mw,k).numpy(),v) for k,v in fixed.items())
      result['input_files_unchanged']=all(sha256_file(ROOT/f)==h for f,h in spec['inputs_sha256'].items())
      assert result['GPU_physical_parameters_unchanged'] and result['input_files_unchanged']
    except Exception:
      result['status']='exception';result['failure']=traceback.format_exc();print(result['failure'],flush=True)
    finally:
      if contact_file:contact_file.close()
      if rows:np.savez_compressed(out/'trajectory.npz',**{k:np.stack([r[k] for r in rows]) for k in rows[0]})
      if ctx and ctx.get('sim'):ctx['sim'].close()
      atomic_json(out/'report.json',result)
      print('GPU G1 RESULT',result['status'],result['physics_steps'],flush=True)
    return 0 if result['status'] in ['completed','zero_step_preflight_passed','stopped_on_frozen_criterion'] else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--freeze',action='store_true');parser.add_argument('--self-check',action='store_true')
    parser.add_argument('--execute',action='store_true');parser.add_argument('--protocol-sha');parser.add_argument('--output',default='mechanism-preflight')
    args=parser.parse_args()
    if args.self_check:assert not args.execute;self_check_gpu()
    elif args.freeze:
      assert not args.execute;atomic_json(P/'mechanism-protocol.json',protocol());print(sha256_file(P/'mechanism-protocol.json'))
    else:sys.exit(run(args))
