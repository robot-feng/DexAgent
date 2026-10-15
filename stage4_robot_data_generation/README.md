# Stage 4: Robot Data Generation

Stage 3's verified robot trajectory and the human video in; randomized, photorealistic robot episodes out, each with its joint trajectory, ready for policy training.

| Step | What it does | Runs on |
|---|---|---|
| 1. `randomize` | Draws new initial object and robot states, adapts the trajectory to each one, and keeps only the episodes that pass the feasibility checks and the physics replay. | CPU |
| 2. `plate` | Removes the demonstrator's hands and the manipulated objects from the human video with SAM3-LiteText and either [VOID](https://github.com/netflix/void-model) or the optional LaMa image inpainter, giving a clean background plate. | GPU |
| 3. `export` | Writes a Blender scene for every episode and camera view: geometry, materials, per-frame poses, cameras. | CPU |
| 4. `render` | Renders the robot and the objects with Blender Cycles, from the calibrated ego camera and from any extra views such as wrist cameras. | GPU |
| 5. `composite` | Pastes the renders onto the clean plate and encodes one video per view. | CPU |

## Requirements

- **OS**: Linux, with an NVIDIA GPU and a recent driver.
- **GPU memory**: **40 GB or more** for the default VOID `plate` backend. VOID loads a 5B-parameter video model and peaked at 38 GB in our runs on an RTX A6000 (48 GB). The optional LaMa image backend uses far less memory. The `render` step needs about 8 GB.
- **Disk**: about 35 GB for the default VOID model weights and 9 GB for the Python environment. The LaMa checkpoint is about 206 MB.
- **Python**: the shared DexAgent/RoboSimStudio environment uses 3.12; the previous 3.11 renderer also remains supported.
- **Blender**: Python 3.12 launches Blender as an executable. Set `models.blender_executable` in `dexagent.yaml`, or `DEXAGENT_BLENDER` for a direct Stage 4/tool invocation. Python 3.11 can use the `bpy` wheel.

## Installation

```bash
conda activate dexagent
export DEXAGENT_BLENDER=/data/tzq/tools/blender-5.0.1-linux-x64/blender
export DEXAGENT_STAGE4_INPAINT_BACKEND=lama
cd stage4_robot_data_generation
bash setup.sh
```

With the default `DEXAGENT_STAGE4_INPAINT_BACKEND=void`, `setup.sh` does three things:

1. clones VOID into `third_party/void-model`,
2. installs the Python packages (VOID's requirements when selected, Transformers, MuJoCo, and `bpy` on Python 3.11),
3. downloads the model weights listed below into the Hugging Face cache.

The repository-level [`dexagent.yaml`](../dexagent.yaml) selects the local LaMa checkpoint for the automated pipeline because VOID and CogVideoX weights are not present on this machine. Stage 4 preflight checks the configured LaMa file and its hash before running. Standalone Stage 4 configs keep `void` as their default and need the weights listed below.

For the LaMa alternative, set `DEXAGENT_STAGE4_INPAINT_BACKEND=lama` before running setup. This skips the VOID checkout and 31 GB of VOID/CogVideoX weights, keeps the existing PyTorch installation, and downloads the 206 MB LaMa checkpoint. Set `DEXAGENT_LAMA_MODEL` to save it at a chosen path; the setup output gives the matching `stage4.json` entry. The LaMa mode expects a Python environment that already has PyTorch installed.

### Models to download in advance

The default `inpaint.backend: "void"` uses the VOID and CogVideoX weights. To avoid those large weights, set `inpaint.backend: "lama"` and provide the LaMa checkpoint path; this uses per-frame image inpainting with the static-camera temporal plate. SAM3-LiteText is used by either backend.

| Model | Hugging Face repo | File | Size | Access |
|---|---|---|---|---|
| VOID pass 1 | [`netflix/void-model`](https://huggingface.co/netflix/void-model) | `void_pass1.safetensors` | 11 GB | open |
| CogVideoX-Fun (VOID's base model) | [`alibaba-pai/CogVideoX-Fun-V1.5-5b-InP`](https://huggingface.co/alibaba-pai/CogVideoX-Fun-V1.5-5b-InP) | whole repo | 20 GB | open |
| SAM3-LiteText-S0 | [`yonigozlan/sam3-litetext-s0`](https://huggingface.co/yonigozlan/sam3-litetext-s0) | model snapshot | about 2.1 GB | open |
| LaMa (alternative to VOID + CogVideoX) | [`simple-lama-inpainting`](https://github.com/enesmsahin/simple-lama-inpainting) release | `big-lama.pt` | 206 MB | open |

To download by hand:

```bash
hf download netflix/void-model void_pass1.safetensors
hf download alibaba-pai/CogVideoX-Fun-V1.5-5b-InP
hf download yonigozlan/sam3-litetext-s0
curl -fL https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt -o big-lama.pt
```

Set `DEXAGENT_SAM3_LITETEXT_PATH` to a local SAM3-LiteText snapshot to reuse it instead of downloading it. VOID and CogVideoX weights go to the Hugging Face cache (`$HF_HOME`, default `~/.cache/huggingface`). If VOID is cloned elsewhere, point `VOID_DIR` at it.

For LaMa, add these entries to `inpaint` in `stage4.json`; `DEXAGENT_LAMA_MODEL` can supply the checkpoint path instead:

```json
{
  "inpaint": {
    "backend": "lama",
    "lama_model": "/path/to/big-lama.pt"
  }
}
```

## Inputs

Stage 3 writes this folder for you (`runs/<name>/stage3/stage4_task/`). To prepare one by hand, put everything for one task in one folder, with a config file next to the data:

```
my_task/
├── scene.xml          simulation scene (MuJoCo MJCF): the robot and the objects
├── assets/            meshes and textures the scene refers to
├── trajectory.npz     the verified Stage 3 trajectory (and the servo commands that produced it)
├── trajectory_manifest.json  the Stage 3 subgoals and what each one must achieve, for the physics replay
├── human_video.mp4    the original human video
├── sim_video.mp4      optional: the Stage 3 simulation video, used only in a comparison video
└── stage4.json        the config: cameras, what to randomize, how many samples
```

| Input | Where it goes | Notes |
|---|---|---|
| Simulation | `scene.xml` | The scene Stage 2 reconstructed, with the robot from Stage 3, as MJCF with its meshes and textures next to it. |
| Robot | inside `scene.xml` | Name the body of each end effector in `robot.end_effectors`. |
| Trajectory | `trajectory.npz` | `qpos` of shape `[T, nq]`, `fps`, and `labels`: one phase name per frame. Optional `reference` `[T, nq]`: the hand servo commands Stage 3 executed (fingers commanded past the object's surface, so they press); the physics replay uses them. |
| Subgoal expectations | `trajectory_manifest.json` | Stage 3's manifest: per subgoal its frame window and `expect` (rise, in hand, at target, released, supported on, untouched ...). The physics replay re-checks them on every new layout. |
| Human video | `human_video.mp4` | The camera must not move during the clip. |
| Ego camera | `cameras.ego` | Position, orientation and vertical field of view of the camera that filmed the human video, in the scene's world frame. |
| New views | `cameras.views` | Extra cameras, each attached to a body: for a wrist camera, its pose relative to the hand and its field of view. |
| Number of samples | `num_samples` | How many randomized episodes to keep. |

## Run

```bash
conda activate dexagent-stage4
cd stage4_robot_data_generation
python run_stage4.py /path/to/my_task/stage4.json
```

That runs all five steps. To run or re-run only some of them:

```bash
python run_stage4.py /path/to/my_task/stage4.json --steps randomize
python run_stage4.py /path/to/my_task/stage4.json --steps export,render,composite
```

## Outputs

Everything is written under `output_dir` (default `outputs/` next to the config; for a run of `run_dexagent.py` that is `runs/<name>/stage3/stage4_task/outputs/`):

```
outputs/
├── randomize.json               every layout that was tried, and why rejected ones failed
├── run_report.json              overall verdict and links to all five step reports
├── reports/                      current step reports, review records, and archived history
│   ├── history/<invocation>/     prior reports saved before each invocation updates them
│   └── reviews/                  explicit plate and render approvals
├── plate/
│   ├── plate.png                the clean background
│   └── preview.mp4              human video with the erase mask | inpainted result
├── samples/
│   ├── original/                the un-randomized trajectory
│   │   ├── ego.mp4, wrist_left.mp4, wrist_right.mp4
│   │   ├── preview.mp4          all views side by side
│   │   ├── compare.mp4          human video | simulation | generated
│   │   ├── trajectory.npz       qpos, joint_names, labels, fps
│   │   └── sample.json          the layout and the check results
│   ├── 0000/                    first randomized episode, the same files (no compare.mp4) plus
│   │   └── physics_replay.npz   what the contact-physics replay of this layout did
│   └── ...
└── render/                      Blender scenes and raw frames (safe to delete afterwards)
```

Each step has a report before work starts, then updates it with checks, metrics, artifacts, input hashes, and elapsed time. A required check failure stops later steps and records them as blocked. The render check decodes every output frame and validates every chunk exit code and log; the composite check validates ffprobe metadata, full video decoding, and the exact trajectory-frame stride. Render process concurrency defaults to one; `chunks` only partitions frames within a view.

### Reusing reports and reviewing release artifacts

`--steps` reuses a prior step only when its effective configuration, input identity, and current artifact hashes still match. Reports without artifact hashes are not trusted. The dependency graph is `randomize + plate → export → render → composite`; `composite` also depends directly on `randomize` and `plate`. Changing configuration, inputs, or a step's artifacts makes the affected steps stale. Rerunning an upstream step invalidates every downstream report, so rerun the affected steps in dependency order. Before a report is replaced, the previous report is archived under `reports/history/<invocation>/`.

For `evaluation_profile: "dataset_release"`, inspect the plate and render contact sheets (`plate/contact_sheet.jpg` and `render/contact_sheet.jpg`) as well as the full outputs: `plate/plate.png`, `plate/preview.mp4`, and every rendered view under `render/<sample>/<view>/frames/`. The reports list the artifacts and their current `artifact_sha256`; use that hash to record an explicit human review:

```bash
python run_stage4.py task/stage4.json --approve-review plate --reviewer NAME --review-artifact-sha256 HASH [--review-note "review note"]
python run_stage4.py task/stage4.json --approve-review render --reviewer NAME --review-artifact-sha256 HASH [--review-note "review note"]
```

There is no automatic approval. Each review is bound to the step attempt, configuration/input identity, and artifact hash; rerunning that step invalidates its approval. `release_ready` becomes true when all five steps pass their required checks and both reviews are explicit. Optional checks may remain as visible warnings. If Stage 4 is run later for review, check the current `runs/<name>/stage3/stage4_task/outputs/run_report.json`: the top-level `run_summary.json` is a snapshot from the earlier pipeline run and does not update after a later review.

## Runtime

Measured on one RTX A6000 for the shipped cup example: a 351-frame trajectory rendered at every second frame (176 frames per episode), three views at 1280×1024, 32 samples per pixel, 4 render processes.

| Step | Time |
|---|---|
| `randomize` | about 1.7 s per draw for the kinematic checks, plus the physics replay |
| physics replay | one replay of the episode for the original, plus one per kinematically accepted layout (10 s for the cup, 23 s for the 1,630-frame computer installation) |
| `plate` | 6 min, once per task (SAM 3 65 s, VOID 190 s); 3 to 7 min on the other tasks |
| `export` | 33 s per episode |
| `render` | 8 min per episode |
| `composite` | 44 s per episode |

The whole example, three randomized episodes plus the original, ran in 43 minutes. Rendering dominates, and it scales with the number of episodes, views and frames. Raise `render.stride` or lower `render.samples` to trade quality for time. Renders are cached: re-running `render` skips frames that are already on disk.

## Third-party components

Stage 4 uses [VOID](https://github.com/netflix/void-model) with [CogVideoX-Fun](https://github.com/aigc-apps/CogVideoX-Fun) for temporal video removal by default, or [LaMa](https://github.com/advimman/lama) for single-frame image inpainting on a static-camera plate. [SAM3-LiteText](https://huggingface.co/yonigozlan/sam3-litetext-s0) generates text-prompted masks, MuJoCo performs kinematics and physics replay, and Blender Cycles renders the output. Each component has its own license and terms; please check them before use.
