<div align="center">

<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/title_dark.png">
    <img src="assets/title_light.png" alt="DexAgent" height="80">
  </picture>
  <br>
  <sub>An Agentic Human2Sim2Robot Framework for Dexterous Manipulation with Self-Evolving Tool Library</sub>
</h1>

</div>

## Library

DexAgent keeps every skill and verifier it develops in a self-evolving tool library. The **[library visualizer](https://dexagent1.github.io/library/)** shows the library live: how many skills and verifiers there are, each one by category, and its inputs, outputs and source. The tools themselves live in [`tool_library/`](tool_library/); its README explains how to [contribute a tool to the library](tool_library/README.md#contributing-to-the-library) and how to [use an individual tool by hand](tool_library/README.md#using-a-tool-by-hand).

## Quick start

For a unified local view of runs, videos, stage checks, native episode curves and recorded 3D scenes, see **[EmbodiForge Studio](workbench/README.md)** (implemented under the existing `workbench/` path). Start it with `python run_workbench.py` after installing its optional dependencies and building the frontend. It reads existing outputs without starting the generation pipeline.

Maintainers can optionally install the [Feishu CLI](docs/FEISHU_CLI_SETUP.md) to sync project progress to the Feishu knowledge base; it is not required to run DexAgent or EmbodiForge Studio.

### 0. ⚠️ Cost and run time

Each agent stage is a long loop of model turns. Runtime depends on the video and the available tools. This checkout uses Codex through the external file transport and makes no model API calls; API-backed providers are optional alternatives.

- **API-backed providers:** set an API key, `max_rounds`, and `agent.budget_usd` (the run's spending cap) in `dexagent.yaml`; DexAgent stops a stage when either is spent.
- **Codex session:** this checkout uses `agent.provider: external`, so DexAgent does not call a model API. While a run waits, Codex reads the JSON request under `agent.transport_dir` and submits a tool-use or text response with `python -m agent.external respond <request.json> <response.json>`. See [the external transport guide](agent/README.md#codex-file-transport).
- **Run time** depends on the video length, the model, and how much of the task the tool library already covers: when it lacks the tools a task needs, the agent writes and tests new ones, which takes longer.
- **Model:** we recommend Claude Opus 5 or GPT-5.6, but the best choice depends on the task.

### 1. Install

```bash
git clone https://github.com/dexagent1/DexAgent.git && cd DexAgent
conda create -y -n dexagent python=3.12 && conda activate dexagent
pip install -r requirements.txt
```

This installs the harness and CPU tools. The shared environment uses Python 3.12, MuJoCo 3.12 and NumPy 1.x; generation and replay must use the same physics version. SAM3-LiteText runs in this environment with the configured cached weights. Optional components are configured in `dexagent.yaml`:

| Part | Needed for | Install |
|---|---|---|
| SAM 3, SAM 2, HaMeR, Video Depth Anything, GeoCalib, SAM 3D | the perception skills (masks, tracks, depth, gravity, hand meshes, mesh objects); a GPU | `environments:` in `dexagent.yaml` |
| Isaac Sim 5.0 | Stage 2 on deformable cords | `environments.isaac` |
| BODex, DexGraspNet | the optional GPU grasp priors | `models.bodex_dir`, `models.dexgraspnet_dir` |
| VOID / LaMa, Blender | Stage 4; VOID needs about 40 GB GPU memory | [Stage 4 README](stage4_robot_data_generation/README.md); Python 3.12 uses the configured Blender executable |

A tool whose package is missing says so when it is called; the agent then uses another one.

### 2. Setup

Edit [`dexagent.yaml`](dexagent.yaml) with your video, camera calibration, and robot setup. This checkout uses `agent.provider: external`: the pipeline exchanges request/response files with the Codex session and makes no model API calls. The configured Python environments and model cache live under `/data`. To use your own robot or bench, copy `setup/` and edit it as its README describes.

### 3. Run

The RoboSimStudio backend is available for acceptance examples through the same entry point:

```bash
# Once, in the shared Python 3.12 environment:
git clone https://github.com/robosimstudio/robosimstudio.git robosimstudio
git -C robosimstudio checkout 73739abe212f963e8a9f5862b27bca370df091a7
pip install -r requirements-robosimstudio.txt
python run_dexagent.py dexagent.yaml --backend-eval rigid
python run_dexagent.py dexagent.yaml --backend-eval towel
python run_dexagent.py dexagent.yaml --backend-eval sharpa
python run_dexagent.py dexagent.yaml --backend-eval grasp
python run_dexagent.py dexagent.yaml --backend-eval reference --reference-run runs/e2e_top_smoke_20261013
```

These examples record physics, joint commands and cloth particles, then re-render and check the output video.
The Sharpa checks import the full physical bench, preserve its passive/free task objects, and evaluate an actual Sharpa hand grasp of a free cube through lift, carry, place and release. The grasp gate also compares object motion against an independent CPU MuJoCo replay.
The verified cube task does not certify an end-to-end human-video reconstruction or deformable-object grasping; see the [integration report](docs/ROBOSIMSTUDIO_INTEGRATION.md) for measured results and assumptions.
The `reference` mode executes an existing DexAgent episode in RSS, evaluates every original task goal and writes same-camera comparisons. It labels the reused upstream inputs explicitly; this is a backend comparison. See the [fusion review](docs/DEXAGENT_RSS_FUSION_REVIEW.md) for the control contract and remaining integration work.
Python 3.12 uses `models.blender_executable` for the existing Stage 4 Cycles renderer.
See [the integration status and environment instructions](docs/ROBOSIMSTUDIO_INTEGRATION.md).

SimFoundry rigid reconstruction outputs can also be checked in RSS through this entry point:

```bash
python run_dexagent.py dexagent.yaml --backend-eval simfoundry \
  --simfoundry-stage8 /path/to/s8_pose \
  --simfoundry-stage11 /path/to/s11_sim \
  --simfoundry-stage12 /path/to/s12_physics
```

This preserves Stage 11 compound collision proxies, checks passive stability in RSS, and records a native episode that the Studio can replay in 3D. It is an asset import check; it produces no robot actions or training-qualified episodes. See [commands, measured results and limitations](docs/SIMFOUNDRY_RSS_IMPORT_20261020.md).

One command runs the four stages in order and prints a closing table; each stage also runs on its own (its README says what it reads, what it writes and which GPU it needs).

```bash
python run_dexagent.py dexagent.yaml                                  # fresh Stage 1–4 evaluation; uses the configured conda Python
python run_dexagent.py dexagent.yaml --profile smoke                  # smaller end-to-end run; verified examples may be reused
python run_dexagent.py dexagent.yaml --profile dataset_release        # fresh run with dataset timing and visual-review reporting
python run_dexagent.py dexagent.yaml --from stage3 --confirm-stages   # resume from a stage and pause between stages

python stage1_semantic_understanding/run_stage1.py dexagent.yaml      # video + prompt -> scene_semantics.json
python stage2_scene_reconstruction/run_stage2.py dexagent.yaml        # + video -> scene.xml with the verified, placed objects (Isaac Sim for deformables)
python stage3_trajectory_generation/run_stage3.py dexagent.yaml       # + subgoals -> one robot episode verified in physics, packaged for Stage 4
python stage4_robot_data_generation/run_stage4.py runs/<name>/stage3/stage4_task/stage4.json   # -> randomized, retextured robot videos in stage4_task/outputs/
```

Every full run writes `runs/<name>/reports/preflight.json`, a `stage_report.json` per stage, and `run_summary.json`. Stage 4 also writes one JSON report for each of its five steps under `stage4_task/outputs/reports/`; a required failure stops the remaining steps. `pipeline_eval` is the default and disables example reuse. `smoke` enables reuse and reduces Stage 4 work. `dataset_release` checks video-to-trajectory frame mapping and marks visual previews as pending human review; it does not claim that the human comparison video is time synchronized.

## Code

| Part | Folder |
|---|---|
| Agent harness | [`agent/`](agent/) |
| Stage 1: Semantic Understanding | [`stage1_semantic_understanding/`](stage1_semantic_understanding/) |
| Stage 2: Property-Based Simulation Reconstruction | [`stage2_scene_reconstruction/`](stage2_scene_reconstruction/) |
| Stage 3: Robot Trajectory Generation | [`stage3_trajectory_generation/`](stage3_trajectory_generation/) |
| Stage 4: Robot Data Generation | [`stage4_robot_data_generation/`](stage4_robot_data_generation/) |
| Tool library | [`tool_library/`](tool_library/) |
| Default setup | [`setup/`](setup/) |
| Library visualizer | [`library_visualizer/`](library_visualizer/) |

## BibTeX

If you find DexAgent useful in your research, please cite:

```bibtex
@article{wang2026dexagent,
  title   = {DexAgent: An Agentic Human2Sim2Robot Framework for Dexterous
             Manipulation with Self-Evolving Tool Library},
  author  = {Wang, Youhui and Li, Yunzhu and Fei-Fei, Li and Wu, Jiajun and
             Huang, Huang},
  journal = {arXiv preprint arXiv:2609.35318},
  year    = {2026}
}
```
