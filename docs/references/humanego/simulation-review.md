# HumanEgo simulation source review for EmbodiForge

Source: `/data/tzq/DexAgent/HumanEgo`, tracked source unchanged, HEAD `239affbc24b8b4a3ab2987cdd76436bea7ca9411` (`sim: release checked against the real code; wrap-up timing follows it`). Review is read-only; no dependencies installed, heavy checkpoints downloaded, server started, or tracked source modified.

## What is actually implemented

* `simulation/humanego_sim/runner.py:78` loads the **released ICTPolicy checkpoint**, reuses `inference/policy.py`; `runner.py:95` explicitly only supports **camera_frame + absolute** checkpoints. This is a real learned-policy closed loop, not a scripted animation/oracle trajectory. At ~0.3 s policy updates it generates 50-step hand poses + grip probabilities; controller 10 Hz, physics 2 ms. Bimanual mode reuses a single right-hand model by horizontally mirroring the left camera/poses, starts left 6 s later (`runner.py:262`, `:397`).
* It is specifically **serve_bread**: procedurally modelled croissant, danish, plate, table + calibrated arm bases, not a general scanned-scene reconstructer. `scene.py:51` derives world placements from real hand-eye `T_base_in_cam`; `scene.py:179` builds MJCF. Current cam height is **0.366 m** (`configs/serve_bread.yaml:25`), while README prose still has old0.436. Tabletop is world z=0 and floor z=-0.76; distinct visual/robot/object/collision geom groups. Table legs/furniture visual design does not prove collisions.
* Robots: Trossen WidowX AI (6 joints, shipped), Franka Panda (7), UR5e + Robotiq2F85 (6) (`robots.py:31`, `:89`, `:182`). Mapping includes exact joint/actuator names, open/close units, corrected TCP frame (+Z approach/+X spread), force limits and grip sensing, mount offset and IK matched initial hand pose. Franka/UR assets fetched separately. These are **not** EmbodiForge's YAM+Sharpa/G1/G2; performance does not transfer by mesh swap.
* Camera registry simulates D405/D435/ZED2i/ZEDMini with nominal cropped640x480K, metric RGB-D (`cameras.py:20`, `:51`); sim perception uses rendered metric depth. **Not evidence that generic monocular RGB supplies metric scale.** Human training metrics from Aria-MPS SLAM are another capability contract.
* `perception.py:160` defaults GT masks; `env.py:107`, `:260` render segmentation and hide robot for perfect clean background. Real DINO-SAM optional (`perception.py:230`; pipe-separated subprocess `dinosam_client.py:23` / `scripts/dinosam_server.py:41`) uses Grounding-DINO+SAM2. Clean image remains rendered robot-free, not LaMa restoration in sim. Accordingly cannot reuse this as proof of removing a robot baked into scanned mesh.
* It reuses upstream KptsSelector20 contour points → CamRS multi-frame depth lift → MAD+DBSCAN → PCA object frames → VisualKpts. 5-frame mask majority fusion by default (same underlying GT frame with stochastic errors; real DINO-SAM branch adds image noise); mirror left object+camera transform. At init object center/axis/extents are perceived rather than grabbing truth directly for the controller. Bread center height heuristic half of top surface assumes tabletop zero (`runner.py:146-180`).
* **Perception latching** (`runner.py:273-294`) moves believed object pose along current hand when grasp flag activates. It does **not weld/teleport the physical bread** in the physics model. Ground-truth bread remains free soft body and may slip while the belief stays latched; optional re-perception before retry exists.
* `driver.py:154` has scratch-data DLS IK (no mutation of physics state during IK), Cartesian quintic interpolation continuous from current interpolator position/velocity/acceleration, timed grip commands, corrected EE/TCP frames and joint position servos. `move_p` rejects position residual>1cm; rotation convergence not independently part of final rejection. Driver is an emulator, not actual hardware fidelity proof.
* `controller.py:27` retains temporal ensemble, safety cage, EMA/Slerp, lookahead. Tuned profile additionally enables **grasp assist** (`:202-381`): refine policy pinch relative to perceived bread, wait/timeout arrival, hold while closing, use opening to detect empty grasp, retry lower/central, optional sense/reperceive. Not pure raw policy zero-shot performance. Optional `touch` uses simulated contact sensors absent on real listed grippers; shipped defaults use q/grip gap.
* `scene.py:97` applies gravity compensation, modifies Trossen servo gains/force limits; per-robot contact skins differ. `softbody.py:66`: MuJoCo flex trilinear4x2x2 node grid, E20kPa, nu.25, damping.03, textured skin; Kabsch rigid fit for display/evaluation (`:133`). UR uses volumetricdim3; Franka fine skin+2mm radius to prevent tunnelling. Rigid fallback uses **adhesion actuators** (`scene.py:390`), defaultsoft holds by friction. Plastic rest-layout modification is optional, Python-only/off.

## Task acceptance and evidence limits

`runner.py:617-636` exact native success:
```
dxy = ||bread_center.xy - plate_center.xy||
on_plate = dxy < plate_radius and lowest_bread_z > .003 and bread_center_z < .12
released = not arm.gripper_cmd_closed()
success = on_plate and released
```
Whole episode = all sides successful. `success_strict` separate strictercenter-radius variant, not headline. This is a **final geometric proxy**: no actual plate contact requirement, no velocity/resting dwell check, no measured opening / absence of hand support, no requirement ever grasped or moved (grasped only reported). At end normally wrap+home+0.6stail helps, but condition itself permits transient aboveplate placement or already satisfied initial states. Do not weaken EmbodiForge physical task gates to match it. Counterfactual tests should include no-grip, wrong target, transient overgoal, initial-satisfied and collision failure.

`evaluate.py:149` rough failure taxonomy (`never_closed`, `grasp_missed`, `dropped_in_transit`, `timeout`, `rim_or_edge`). Rich audit `scripts/grasp_audit.py:34` reconstructs qfull and classifies closed_on_air/slipped_on_lift/dropped_lift/dropped_transit/stuck/released_off; source truth vs perceived pose errors measured separately in `scripts/robust_audit.py:28`. `scripts/real/sim_phases.py:86` also measures phase durations, release height, grasp offset, path/speed/jerk.

`stats.py` has Wilson CI, scene-bootstrap correlated arm handling, paired McNemar/common scenes; reserved9001–9099 final seeds checked byCLI. `sampling.py:111` creates independent RNG stream per variation kind so same scene remains paired. `robustness.py` models missing/biased masks, depth noise/holes, calibration, latency, lighting, size/layout/tableheight; distractors are **visual-only**, not collision objects.

Upstream final100scene report (`docs/upgrade/report.md:12`, tunedJSON) says Trossen80.5%perarm/64%both, Franka75.5%/57%, UR76%/53%. Those are upstream reported, **not rerun here**; raw evaluation logs and pretrainedweights absent locally. Real95%singlearm and sim67.5% are not equivalent to bimanualheadline. `docs/real_vs_sim.md` explicitly records medium-confidencevideo-derived tableheight, real-vs-sim release/jerk/grasp differences and lack of actual matching real logs/checkpoint series. Documentation citing `/home/leo/.../AriaMimic` is prior author evidence not independently available proof in this clone.

## Exports / live versus replay / sim-real bridge

* `_save`, `runner.py:638` creates `episode.json`: meta robot/camera/K/frames/objectposes/perception,30Hzqfull rounded1e-5 + perarmqpos/gripcommand + policyworldtrajectory/grasp/done + optional exact policyinputJPGs. This is primarily replay logging: no regular complete joint velocities/torques/controls/contact report/state-action map or complete config/weights hashes. Need minimal mapping to existing EmbodiForgeEpisode with provenance; do not silently call replaylog qualified trainingdataset.
* `web_export.py:89`, `:230` takes geometry from compiledMuJoCo (matching meshframes), modelkinematic tree + rollout qpos + predictedtrajectories + policyinputvideos. `web_pack.py` integer/delta encodes rounded tracks; bitexact means against roundedexportJSON, **not originalfullphysicsstate**. Softgeometry retains floatpositions. `replay.py:19` reassignsqpos + forwardforvideo, not reexecution.
* Live uses MuJoCoWASM + ONNXRuntimeWeb. `web/js/live/policy.js:8` WebGPU20stepfp32, fallbackWASM10stepfp16weights (**different inference precision/steps**, mustlabel). `export_onnx.py:28` exports wholeEulerrollout withcachedcontextcrossKV; `parity_test.py` distinguishes sync(controlportedlogic), free(physicsdrift), full(actualperception+ONNX) and comparesfirstdivergence. Good learning: replay, controlparity and independentlyexecutedphysics must remain different claims.
* `scripts/real/replay_real.py:79` loads versioned `humanego-real-log/1`: metaK/crop/extrinsics, `init_*/objects.json`, `state.npz`, `steps/commands/events.jsonl`, outcome. Four diagnostic modes at`:287,:542,:634,:683`: scenereconstruction fromperceivedcontours+depthtableplane; recorded-jointdriverphysicalreplay; closedlooppolicy rerun; offlinepolicy comparison ofloggedRGB vs simRGB. This offers a useful minimal adapter/diagnosis pattern, not generic completeReal2Sim. `sim_as_real.py` creates these logs fromsimulation, **not independentrealvalidation**.

## Verified issues not to copy

1. `autotune/loop.py:203` validation creates `np.random.default_rng(10000+it)` inside listcomprehension for eachscene; independent reproduction with32 scenes gave **1unique scene**. This affects online validate-and-stop branch; separate `autotune_validate.py` samples withpersistentRNG, so do not assert all published finalresultsinvalid.
2. `evaluate.py:172` skips errored episodes when computing `per_side_success_rate`, yet `stats.py:53` counts errors asfailures. Independent probe one successfulbimanual+oneruntimeerror yielded **100%per_side_success_rate versus50%CIrate and50%episode success**. Tuner scoreconsumesoptimistic former (`autotune/loop.py:50`). Must count missing/errorincommon denominator.
3. `scripts/export_onnx.py:158-163` trailing code references undefined `p`, `s`, `feeds`; sourceinspectionshows CLIcanfail afterwritingpartialexports. Notrun (no checkpoint/onnxdepsdownload).
4. sourceclaimsof'resting' exceed actualsuccesspredicate; docversion/tableheight/oldnumbers mustnot be copied withoutconfigsourcecheck.
5. Single-arm error results default two arms in stats; scene_bootstrap may fail raggedshape on mix1armsuccess+runtimeerror. Sourceinference, not yet runtimeprobe.

## Actual local tests (no installs)

Python `/data/miniconda3/envs/dexagent/bin/python`, MuJoCo **3.12.0** versus required **3.16.0**.

1. `python -m pytest simulation/tests -m 'not sim' -q -p no:cacheprovider`: **collection error** missing `box` (`python-box`),6simdeselected. No claim suitepass.
2. `python -m pytest simulation/tests/test_grasp_skill.py simulation/tests/test_web_build.py simulation/tests/test_config_profiles.py simulation/tests/test_driver_ik.py -q -p no:cacheprovider`: **37passed,4failed,8skipped**,6.80s. 3Trossenphysicaltests failmissing`mjtIntegrator.mjINT_DISCRETE`;1CLIhelpfailmissingbox.6Franka/UR assettests skipped+2webbuildartifacttestsskipped. No dynamicpolicyepisodecompleted.
3. Independent purePython probes confirmed issue1/2 above. No fallbackchangingphysics toforcegreen.

## Recommended reuse in EmbodiForge (no new framework/no engine migration)

1. Treat this repo first as **reusable object-centric humanpolicy/demo representation and closed-loop control reference**; sceneconstruction remains SimFoundry/DexAgent/RSS boundaries, HumanEgo isn't a fourth registry.
2. Feed realHumanEgo datasetcapture+objects+hand poses through current Demo/inputadapter withactualmetricpose/calibrationprovenance. AriaSLAM metricavailable differs arbitrarymonocular estimatedscale. Scanner/task/demo remainsindependent. Preserve robot-visible RGB and semanticcleanpolicyrepresentation separately; do not destrip source imagery.
3. One optional ICTPolicy actionsource → targetrobotTCP/grippermapping → existing execution/gates/episode. Existing3robots allrequired for transferable tasks; HumanEgo externalrobots are referencebackend baselinesonly.
4. Add observationtruth/perceived/reference/cmd/realized channels to existingevidence, plus phase-local grasp/releaseerrors. Control-specific retry should retain originaltaskacceptance and record eventreason; never fakephysicalattachment.
5. Reuse **paired scene seeds, perrobot/sides+aggregate, failure-complete denominators, modelcalibration provenance, physicsbench separate from controlleroptimization**. Keep sourcephysics fixed absentmeasurement; avoid success-drivenfrictiontuning.
6. Keep MuJoCo3.16 HumanEgo in isolatedenv/process when reproductionauthorized; don'tupgradeworkingdexagentenv. Training/simresults from this reference still need actualbaselinephysicalacceptance. Targetfirst simplemonocularsourceasset+taskintegration before addingsoftbodyorbrowserliveclones.

## Coverage inventory (explicit evidence boundary)

180 tracked files under simulation. Full file/size/line/symbol/status inventory: [simulation-coverage.json](simulation-coverage.json). {'inventory_only': 93, 'content_review_partial_output_truncated': 3, 'implementation_review': 60, 'content_review': 8, 'api_and_docstring_review': 16}

Core execution,physics,controller,perception,evaluation and task criteria were implementation-reviewed; supportingPython scripts indexed by every function and docstring. All real/replay_real.py, real/sim_as_real.py and web_export.py bodies subsequently read in full. Some large docs had truncation, recordedaspartial; frontendviewer/liveports exceptpolicy.js and binarymeshes notline-by-lineaudited. Do not translate this into “all source fully read” or a simulation reproduction claim. Source-reference findings above are constrained to actually inspected bodies.

## Additional full reads and findings

* Controller/physics/engineering experiment logs show why the **experimental method** matters more than copying every tweak: promising single-seed/oldphysics slow approaches later harmed UR5e by ~20 points on paired two-seed evaluation; re-perception, yaw, touch, slow-approach options stayed off in final defaults (`docs/upgrade/controller.md:151`). Physics corrections can lower task success; authors retain better-sourced parameters and tune controllers separately (`docs/upgrade/physics.md:75`). These are upstream reported studies, not local repetitions.
* Engineering notes disclose non-bit-reproducible native episodes even single-threaded CPU+deterministic torch (`docs/upgrade/eng.md:52`), so paired scene seeds are necessary but not a guarantee of identical continuous rollouts.
* `scripts/parity_test.py:106` compares commands/poses/state and first divergence; sync/free do not independently infer policies. Full mode adds image PSNR and sameinputONNX/PyTorch differences. Its CLI reports discrepancies without a strict failing acceptance exit; don't use process exit alone as paritypass.
* `humanego_sim/web_export.py:273-297`: `P` is assigned only if a policy_view directory exists, then read whenever policy logs are nonempty. Episodes with logged policy outputs but no images can raise UnboundLocalError. Source-inspected, not executed. Legacy no-qfull reconstruction at 251 hardcodes six joints; seven-joint Franka requires qfull.
* `scripts/robust_report.py:53-60` overwrites run meta, then reuses existing condition JSON without comparing its old resolved config/commit/hash. Resume with changed parameters can misattribute old results; record immutable confighash and reject mismatches in our existing job/evidence pipeline.
* `scripts/real/sim_as_real.py:109-111` still synthesizes wrap events at halt+0.8 and +3.9 s, whereas current runner's bimanual default opening is 1.0 s (`runner.py:524-535`). The synthetic-real fixture does not exactly follow latest timing despite its docstring; actuallogged event export is preferable.

No full reproduction, new training, dependency installation, or backend migration was performed. Local lightweight test failures are environment/asset qualification evidence, not a claim the upstream full setup fails.
