# EmbodiForge workspace

Read `工程开发与系统重构准则.md` before implementation. Inspect current source,
runtime evidence and uncommitted changes; preserve other conversations' work.
Reuse existing RoboSimStudio, DexAgent and SimFoundry interfaces.

## Mandatory demo coverage

The user requires **every future demo** to pass on **all three** baseline
embodiments:

1. The existing dexterous-hand setup, called “天玑 + Sharpa” by the user.
   Preserve the actual setup unchanged; `setup/setup.yaml` currently identifies
   it as I2RT YAM + Sharpa Wave. Do not silently substitute or rename its hardware.
2. `GenieSimAssets/robot/G1_omnipicker`.
3. `GenieSimAssets/robot/G2_omnipicker`.

These are minimum coverage requirements, not optional examples or three demos
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

## Shared workbench

Use the existing default port 8765 for user-facing updates. Check actual active
workers and sessions before a restart; do not terminate another conversation's
running work. SimFoundry reproduction and pipeline changes may be in progress
in parallel: scope commits to the files owned by this task.
