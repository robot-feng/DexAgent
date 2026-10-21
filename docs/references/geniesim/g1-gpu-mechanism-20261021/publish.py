"""Publish existing, reviewed G1 mechanism evidence; no new physics or task claim."""
from pathlib import Path
import sys,json,shutil
P=Path(__file__).resolve().parent;ROOT=P.parents[4];A=P.parent/'mechanism-attempt01'
sys.path.insert(0,str(ROOT))
from agent.run_reports import make_report,atomic_json,sha256_file,status_for_checks
s=json.loads((A/'compact-summary.json').read_text());protocol=json.loads((A/'protocol.json').read_text());review=json.loads((A/'root-review.json').read_text());replay=json.loads((P/'replay-manifest.json').read_text())
assert s['physical_steps']==1900 and all(s['offline_checks'].values())
for path,h in protocol['inputs_sha256'].items():assert sha256_file(ROOT/path)==h,path
for path,h in s['source_inputs_sha256'].items():assert sha256_file(P.parent/path)==h,path
assert review['samples']==1901 and review['physical_steps_added']==0
assert sha256_file(P/'replay_mechanism.mp4')==replay['output_sha256']
out=ROOT/'runs/g1_rss_gpu_mechanism_20261021';out.mkdir(exist_ok=False)
files={
 'mechanism-summary.json':A/'compact-summary.json','mechanism-raw-report.json':A/'report.json','mechanism-protocol.json':A/'protocol.json',
 'root-review.json':A/'root-review.json','replay-manifest.json':P/'replay-manifest.json','replay_mechanism.mp4':P/'replay_mechanism.mp4',
 'preview_mechanism.png':P/'frame-0700.png','preview_diagnostics.png':A/'mechanism-diagnostics.png',
 'source-import-report.json':P.parents[1]/'source-report.json','trajectory.npz':A/'trajectory.npz','contacts.jsonl':A/'contacts.jsonl'}
for name,path in files.items():shutil.copy2(path,out/name)
checks=[dict(id=k,required=True,status='passed',observed=v,evidence='mechanism-summary.json') for k,v in s['offline_checks'].items()]
checks += [
 dict(id='source_exact_fidelity',required=False,status='failed',observed='failed_retained',evidence='source-import-report.json; exact-source checks remain failed; this run scope is explicitly adapted unloaded mechanism only'),
 dict(id='runtime_profile_persistence',required=False,status='warning',observed='GPU upload/readback in frozen run only',evidence='Reset/notify persistence not qualified; no production control readiness claim.'),
 dict(id='contact_grasp_and_task',required=False,status='skipped',observed=False,evidence='No object/contact in this run. Not a grasp, task episode or three-embodiment demo.'),
 dict(id='wheel_ground_and_full_body_collision',required=False,status='skipped',observed=False,evidence='42 source bodies / 34 coordinates preserved; active collision geoms are source gripper colliders. Fixed base and gravity compensation do not qualify wheel standing or full-body collision.')]
report=make_report(run=out.name,profile='G1 · GPU 空载机构验证（非抓取任务）',stage='backend',status=status_for_checks(checks),checks=checks,
 inputs={'protocol':{'path':str(A/'protocol.json'),'sha256':sha256_file(A/'protocol.json')},'trajectory':{'path':str(A/'trajectory.npz'),'sha256':sha256_file(A/'trajectory.npz')}},
 metrics={**s['maximum_metrics'],'physical_steps':1900,'recorded_samples':1901,'control_frames':950,'adapted_mechanism_qualified':True,'exact_source_qualified':False,'task_success_evaluated':False,'training_eligible':False,'aggregate_demo_complete':False,'backend':s['backend']},
 artifacts=list(files),next_action='Validate contact response with the full source joint tree, actual finger colliders and fixed original gates; then qualify task scene, wheel-ground setup and same task on all three embodiments.')
report.update(scope='G1 GPU 空载机构通过；源保真失败保留；接触与任务未验证',objective=protocol['objective'],task_success_evaluated=False,training_eligible=False,aggregate_demo_complete=False)
atomic_json(out/'stage_report.json',report)
D=ROOT/'docs/references/geniesim/g1-gpu-mechanism-20261021';D.mkdir(parents=True,exist_ok=True)
for name in ['mechanism-summary.json','mechanism-protocol.json','root-review.json','replay-manifest.json','preview_mechanism.png','preview_diagnostics.png','stage_report.json']:shutil.copy2(out/name,D/name)
for path in [P/'render_recording.py',Path(__file__),P.parent/'run_mechanism.py',P.parent/'prepare.py',P.parent/'summarize_mechanism.py']:shutil.copy2(path,D/path.name)
atomic_json(P/'publication.json',{'run':str(out),'status':report['status'],'files_sha256':{k:sha256_file(out/k) for k in files},'physics_steps_added_by_publication':0})
print(json.dumps({'run':str(out),'status':report['status'],'mechanism_qualified':True,'task_success_evaluated':False}))
