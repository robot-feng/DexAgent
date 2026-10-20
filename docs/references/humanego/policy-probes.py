"""Read-only contract probes: no model load, no hardware, no training."""
import json, sys
from pathlib import Path
import numpy as np
repo = Path(__file__).resolve().parents[3] / 'HumanEgo'
sys.path[:0] = [str(repo), str(repo / 'inference')]
from policy import ICTPolicy
from interfaces import ObjectState
from run_inference import latch_objects
from controller import TrajectoryController

def T(x):
 t=np.eye(4,dtype=np.float32);t[0,3]=x;return t
p=object.__new__(ICTPolicy)
p.device='cpu';p.pos_mean=np.zeros(3,dtype=np.float32);p.pos_std=np.ones(3,dtype=np.float32)
p.single_hand=True;p.single_hand_side='right';p.sides=['right'];p.num_hands=1
p.ict_dim=20;p.max_ict=8;p.use_object_tokens=True;p.frame_mode='anchor_frame'
static={'obj1':ObjectState(T(1.))}
latches={'right':None}
latch_objects(static,{'right':T(1.)},{'right':1.},latches,'obj1')
dynamic=latch_objects(static,{'right':T(1.3)},{'right':1.},latches,'obj1')
x,mask=p.build_ict({'right':T(1.4)},{'right':1.},dynamic,'obj1')
encoded=x[0,0].numpy()
# Feed current pose back as a predicted pose, solely to test coordinate contracts.
decoded=p.decode_ee_in_cam(encoded[1:4],encoded[4:10],static['obj1'],np.eye(4))
released=latch_objects(static,{'right':T(1.3)},{'right':0.},latches,'obj1')
c=TrajectoryController({'right':object()}, {'max_pos_step':0.05})
first=c._smooth('right',T(1.));second=c._smooth('right',T(2.))
results={
 'scope':'Exact source methods; manually configured objects; no weights, camera, robot or physics. Contract probes only.',
 'anchor_frame':{'initial_anchor_x':1.,'latched_anchor_x':float(dynamic['obj1'].T_in_cam[0,3]),'hand_x':1.4,'encoded_hand_x':float(encoded[1]),'decode_against_initial_anchor_x':float(decoded[0,3]),'coordinate_roundtrip_error_m':abs(float(decoded[0,3])-1.4)},
 'released_object_x':float(released['obj1'].T_in_cam[0,3]),
 'controller_first_target_x':float(first[0,3]),'controller_second_increment_m':float(second[0,3]-first[0,3]),
 'grasp_flow_target_vs_sigmoid':[{'target':x,'sigmoid':float(1/(1+np.exp(-x))),'strict_threshold_0_5':bool(1/(1+np.exp(-x))>0.5)} for x in [0.,0.01,0.49,1.]],
}
print(json.dumps(results,indent=2))
