# EmbodiForge workspace

Read `工程开发与系统重构准则.md` before implementation. Inspect current source,
runtime evidence and uncommitted changes; preserve other conversations' work.
Reuse existing RoboSimStudio, DexAgent and SimFoundry interfaces.

## Task modes and mandatory demo coverage

The user distinguishes two task objectives:

- **Faithful Real2Sim reconstruction** preserves the source scene, embodiment,
  calibration and spatial relationships. Do not force a common tabletop or
  replacement robots; evaluate fidelity and task behavior on the source robot.
- **Transferable task generation**, especially from ego demonstrations, retains
  task intent/object relationships and may retarget to different robots in the
  shared tabletop scene. Every demo in this mode must pass on **all three**
  baseline embodiments below.

Declare the objective before generation and retain it in the task evidence.
Do not automatically infer it from a video filename, silently change modes to
bypass failures, or treat retargeted data as an exact reconstruction.

1. The existing dexterous-hand setup, called “天玑 + Sharpa” by the user.
   Preserve the actual setup unchanged; `setup/setup.yaml` currently identifies
   it as I2RT YAM + Sharpa Wave. Do not silently substitute or rename its hardware.
2. `GenieSimAssets/robot/G1_omnipicker`.
3. `GenieSimAssets/robot/G2_omnipicker`.

For transferable tasks these are minimum coverage requirements, not optional examples or three demos
distributed across different robots. The same demo task must be planned,
physically executed, checked against its task criteria and recorded separately
for each embodiment. Each robot needs its own matching action/state mapping,
asset identity and evidence. Copying joint trajectories, changing a displayed
mesh, parsing USD, or passing an import/kinematics smoke test does not qualify.

Report both per-robot results and aggregate demo completion. A missing,
unsupported, skipped, blocked, failed or unverified robot prevents aggregate
completion; it does not erase valid partial evidence. Do not waive a robot or
weaken the task to obtain a green result. Resolve capability or task formulation
issues explicitly with the user if necessary.

Keep hardware/import qualification separate from task acceptance. In particular,
`GenieSimAssets/mujoco/robot/G2_place_workpiece.xml` is not an equivalent model of
G2 Omnipicker. See `docs/GENIESIM_ASSET_ADOPTION_20261020.md` for audited assets,
dependencies and current limitations.

## Shared tabletop scene

For transferable tasks, all baseline robots perform each demo on the same table
and task layout. Faithful Real2Sim tasks retain their source environment.
Use a common floor, table dimensions/height, table-relative object poses and
task criteria. Table legs meet the floor; G1/G2 stand on their wheels on that
floor, while the existing YAM + Sharpa rig uses its fixed table-edge mounts.
Adapt robot mounting/standing poses without scaling robots or changing the table
for individual embodiments. Check reach and collisions separately for each.

Reuse RSS `CellSpec` and its surface frame. Preserve existing DexAgent
tabletop-zero coordinates through an explicit rig-to-world transform; do not
silently reinterpret old trajectories or shift only the displayed mesh.
Align wheeled robots using their wheel contact geometry, not an assumed USD
root origin. The user's static appearance reference is not a dynamic task pass.

Keep environment, embodiment and task definitions separate; compose them through
RSS `Binding` / `Scene` / `BuildRequest`. A declared pairing is not a passed
physical task. Preserve monolithic source imports for faithful reconstruction
using the existing scene-resident semantics; never import a duplicate robot.
Do not replace Sharpa's 56-joint mapping with another robot's name or create a
second scene/robot registry. See the adoption review for the source-backed plan.

Keep task intent and acceptance independent of action generation. Ego traces
provide retargeting references; planning, skills, policies and RL are alternative
or complementary action sources. Missing demonstrations do not mandate RL.
Each source must use the selected embodiment's control semantics and the same
physical execution, task verification and episode recording path. Reuse existing
interfaces rather than adding a parallel pipeline framework. See
`docs/ENVIRONMENT_EMBODIMENT_SOURCE_REVIEW_20261020.md` for audited boundaries.

## Ego capture workflow

For new ego collection, scan/model the environment and reusable objects first,
then collect operation demonstrations against that versioned scene. Reuse the
scene across demonstrations; do not reconstruct an unrelated world per video.
Keep scan footage and operation footage explicitly identified and linked.
Support monocular, stereo and RGB-D devices through capability-based input
adapters, with optional poses/SLAM and IMU. Do not require a specific device or
invent missing depth, metric scale, stereo calibration or camera trajectories.
Preserve per-stream calibration, timestamps, coordinate conventions and the
scan-to-demonstration registration. A selected frame's camera pose is not a
moving camera trajectory. Scene geometry/scale and registration must be checked
before robot retargeting; missing alignment does not invalidate the raw capture
but prevents claiming aligned training data.
For transferable tasks, the shared table can come from the scanned environment.
An explicitly chosen standard-table migration keeps a recorded source-to-target
transform and does not silently alter the source scene or per-robot layout.

## Shared workbench

Use the existing default port 8765 for user-facing updates. Check actual active
workers and sessions before a restart; do not terminate another conversation's
running work. SimFoundry reproduction and pipeline changes may be in progress
in parallel: scope commits to the files owned by this task.
