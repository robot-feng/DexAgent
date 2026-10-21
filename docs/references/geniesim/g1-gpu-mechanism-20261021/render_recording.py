"""Render saved GPU joint states with forward kinematics only; never integrate."""
import os
os.environ.setdefault('MUJOCO_GL','egl')
os.environ.setdefault('MUJOCO_EGL_DEVICE_ID','1')
from pathlib import Path
import json,hashlib
import numpy as np,mujoco,imageio.v2 as imageio
from PIL import Image,ImageDraw,ImageFont
P=Path(__file__).resolve().parent;A=P.parent/'mechanism-attempt01'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
inputs=[A/'trajectory.npz',A/'preflight/native-profile-mirror.mjb'];before={str(p):sha(p) for p in inputs}
m=mujoco.MjModel.from_binary_path(str(inputs[1]));d=mujoco.MjData(m);z=np.load(inputs[0])
# These are display-only cameras/lights. Source physical arrays and files are untouched.
m.vis.global_.offwidth=640;m.vis.global_.offheight=480
m.vis.map.znear=.001
m.vis.headlight.ambient[:]=.5;m.vis.headlight.diffuse[:]=.7
renderer=mujoco.Renderer(m,height=480,width=640)
cam=mujoco.MjvCamera();cam.distance=.25;cam.elevation=-75;cam.azimuth=90
font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',20)
small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',15)
steps=list(range(0,1901,10));frames=[]
writer=imageio.get_writer(str(P/'replay_mechanism.mp4'),fps=25,codec='libx264',quality=8,macro_block_size=16)
try:
 for k in steps:
  d.qpos[:]=z['native_qpos'][k];mujoco.mj_kinematics(m,d);mujoco.mj_comPos(m,d)
  canvas=Image.new('RGB',(1280,624),'#11171f');draw=ImageDraw.Draw(canvas)
  draw.text((20,12),'G1 GPU recorded-state replay | Collision geometry | 0.25x playback',font=font,fill='white')
  for side,y,xoffset,col in [('Left',.985,0,0),('Right',-.985,640,1)]:
   cam.lookat[:]=[.131,y,.9535]
   renderer.update_scene(d,camera=cam)
   canvas.paste(Image.fromarray(renderer.render()),(xoffset,64))
   draw.text((20+xoffset,42),f'{side} gripper: measured driver {z["driver_q"][k,col]:.5f} rad',font=small,fill='#9cbdff')
  draw.text((20,555),f'Recorded physical step {k}/1900  |  t = {k*.001:.3f} s  |  No new physics executed for this replay',font=small,fill='white')
  draw.text((20,582),'Unloaded mechanism only. No contact, grasp, wheel-standing, task or exact source-fidelity qualification.',font=small,fill='#ffbe80')
  writer.append_data(np.asarray(canvas))
  if k in [0,500,700,1900]:canvas.save(P/f'frame-{k:04d}.png')
finally:
 writer.close();renderer.close()
assert all(sha(Path(p))==h for p,h in before.items())
geom_bodies=sorted(set(int(v) for v in m.geom_bodyid if v))
manifest={'scope':'recorded_state_visualization_only','physics_steps_added':0,'physics_frames_source':1901,'video_frames':len(steps),'fps':25,'sampled_physical_steps':steps,'playback_speed':.25,'render_engine':'MuJoCo EGL forward kinematics; dynamics source Newton MuJoCo Warp GPU','source_input_sha256':before,'geometry':'Only source-enabled collision shapes from native model; not full-body USD visual mesh or USD material appearance','collision_body_names':[m.body(i).name for i in geom_bodies],'output_sha256':sha(P/'replay_mechanism.mp4'),'script_sha256':sha(Path(__file__))}
(P/'replay-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'frames':len(steps),'physical_steps_added':0,'video_sha256':manifest['output_sha256']}))
