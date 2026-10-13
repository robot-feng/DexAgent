# DexAgent 当前复现结果总结

- 更新日期：2026-10-13
- 运行目录：`runs/reproduce_place_cup/`
- 任务：将蓝色杯子放入绿色碗中（`place_cup_in_bowl`）

## 结论

- **有轨迹数据。** 它保存在 MuJoCo 状态格式的 `trajectory.npz` 中，不只是视频。文件按时间保存机器人关节、杯子和碗的位姿、速度、控制输入、动作标签及物理诊断量。见[轨迹文件](runs/reproduce_place_cup/stage3/trajectory.npz)和[阶段标签清单](runs/reproduce_place_cup/stage3/trajectory_manifest.json)。
- **物理仿真用 MuJoCo 3.10.0。** Stage 3 轨迹和 Stage 4 回放记录都报告该版本；Stage 4 随机化结果还逐子步做了动力学回放核验。
- **合成画面用 Blender Cycles。** Blender 负责把 MuJoCo 场景和轨迹渲染成 ego、左右腕部相机视频；它不是这里的物理引擎。
- Stage 4 产物显示它接受了 **2 个随机布局，4 次尝试**，并为两个随机布局和原始布局完成了渲染。需要同时注意：Stage 1–3 复用了仓库自带的 `place_cup_in_bowl` 示例，不能把本次结果描述成从原始视频独立重建并重新规划出的完整新轨迹。
- **运行记录不是一个完整的端到端汇总。** `run_summary.json` 只列了 Stage 1–3；Stage 4 的独立产物在 `stage3/stage4_task/outputs/`。此外根配置设定 3 个随机样本，而当前 Stage 4 任务配置是 2 个样本。因此应分别引用两份记录，不能把根 `run_summary.json` 当作包含 Stage 4 的总报告。

## 1. 本次复现做了什么

输入视频为 `stage1_semantic_understanding/examples/place_cup_in_bowl/video.mp4`：229 帧、30 fps、1280×1024、约 7.63 秒。运行清单记录在[run_summary.json](runs/reproduce_place_cup/run_summary.json)。

| 阶段 | 本次结果 | 解释 |
|---|---|---|
| Stage 1：语义理解 | 通过，0 轮、0 次工具调用 | 视频与仓库示例指纹匹配，直接复用示例语义结果。 |
| Stage 2：场景重建 | 通过，0 轮、0 次工具调用 | 同样复用示例中的场景和资产；因此不是一次全新的场景重建。 |
| Stage 3：动作/轨迹 | 交付成功 | 复用维护者的已验证示例轨迹，并写出本次 Stage 3 工作区、轨迹和 manifest。交付记录注明该示例“not re-verified”。 |
| Stage 4：随机化与导出 | 2/4 随机布局通过，原始布局也通过物理回放 | 两个随机样本均为 `warp` 适配，之后以 MuJoCo 接触动力学回放，再导出多视角渲染。没有重新执行 Stage 3 规划器。 |

运行由当前交互式 Codex agent 驱动；本次记录没有使用单独的 Codex CLI 登录，也没有调用 Anthropic/OpenAI 模型 API。Codex 在这里负责协同本地流水线，并不意味着轨迹是 Codex 直接生成的：轨迹的来源仍是复用的仓库示例。

## 2. 轨迹文件里具体有什么

主要文件：

- Stage 3 交付轨迹：`runs/reproduce_place_cup/stage3/trajectory.npz`
- Stage 3 轨迹说明：`runs/reproduce_place_cup/stage3/trajectory_manifest.json`
- Stage 4 原始布局回放：`runs/reproduce_place_cup/stage3/stage4_task/outputs/samples/original/trajectory.npz`
- Stage 4 随机样本：`runs/reproduce_place_cup/stage3/stage4_task/outputs/samples/0000/trajectory.npz` 和 `0001/trajectory.npz`
- 对应的动力学记录另存为各样本目录下的 `physics_replay.npz`；Stage 4 样本检查和布局信息在 `sample.json`。

`trajectory.npz` 的关键数组结构如下：

| 字段 | 形状 | 含义 |
|---|---:|---|
| `qpos` | `(472, 70)` | 每个采样时刻的广义位置：机器人关节，以及碗和杯子的自由关节位姿。自由关节位置用 3 个平移量和 4 个四元数分量表示。 |
| `qvel` | `(472, 68)` | 对应的广义速度。它比 `qpos` 少 2 列，是因为四元数姿态在速度空间使用 3 个角速度自由度。 |
| `ctrl` | `(472, 56)` | 每帧的 MuJoCo actuator 控制输入。 |
| `reference` | `(472, 70)` | 回放跟踪的参考位置状态。 |
| `time_s`、`fps` | `(472,)`、标量 | 30 fps，时间范围 0 到 15.7 秒。 |
| `labels`、`subgoal` | `(472,)`、`(472,)` | 每帧的人类可读动作标签和子目标编号。 |
| `substep_qpos`、`substep_qvel`、`substep_ctrl` | 7850 个子步 | 更细粒度的动力学状态和控制记录；子步间隔约 2 ms。 |
| `track_pos_m`、`track_rot_deg`、`hand_pen_m`、`object_pen_m`、力/力矩数组 | 逐帧诊断量 | 记录位置/旋转跟踪误差、手/物体穿透以及腕部和手物接触载荷等。 |

70 个 `qpos` 坐标由 56 个机器人关节坐标和两个物体各 7 个自由关节坐标组成。具体关节名称保存在 `joint_names`，执行器名称保存在 `actuator_names`。因此它不是只有末端执行器路点的稀疏计划，而是带机器人和物体状态的逐帧轨迹。

清单把整个轨迹分成三个包含端点的帧区间：

| 子目标 | 帧区间 | 帧数 | 动作 | 结果 |
|---|---:|---:|---|---|
| 1 | 0–177 | 178 | 抓住杯子并抬起 | 通过；杯子上升约 9.83 cm，碗保持不动。 |
| 2 | 178–224 | 47 | 将杯子移到碗上方 | 通过；目标位置误差约 0.2 mm。 |
| 3 | 225–471 | 247 | 把杯子放入碗中、松手并等待稳定 | 通过；杯子最终距目标约 9.7 mm，释放后稳定检查通过。 |

快速读取示例：

```python
import numpy as np

z = np.load("runs/reproduce_place_cup/stage3/trajectory.npz", allow_pickle=False)
qpos = z["qpos"]                 # (472, 70)
joint_names = z["joint_names"]   # qpos 坐标名称/顺序
time_s = z["time_s"]
labels = z["labels"]
```

如果你想要的是**从 RGB 视频估计的人体/手部关键点轨迹、显式末端执行器 XYZ 路点，或 LeRobot 训练集格式**，那不是当前 NPZ 的直接组织形式。当前 NPZ 是 MuJoCo 状态轨迹；可从关节状态和场景模型进一步计算末端执行器笛卡尔位姿，但需要结合 MuJoCo 场景 XML 做正向运动学。

## 3. Stage 4 随机化和物理检查

Stage 4 保留“杯子在碗内”的目标关系。它尝试了 4 个布局，其中 2 个通过，两个失败都发生在第三子目标的放置朝向检查（`at_target_orientation`），不是模拟器崩溃或数值不稳定。原始布局单独回放也通过。

| 样本 | 杯子平移 / yaw | 碗平移 / yaw | 结果 |
|---|---|---|---|
| `0000` | (+1.61 cm, +2.50 cm, +2.03°) | (−1.35 cm, +1.46 cm, +5.85°) | 通过 |
| `0001` | (+1.11 cm, −0.74 cm, −7.25°) | (−2.85 cm, +2.37 cm, −2.87°) | 通过 |

每个样本都回放了 472 帧、7850 个动力学子步。原始轨迹与物理回放的最大 `qpos`、`qvel` 差均为 0；两个随机样本也记录为逐子步精确复现其各自回放轨迹。随机样本的杯子最终位置误差约为 0.3 mm 和 0.1 mm。检查记录中的最大手部穿透约 0.19 mm，最大物体穿透约 0.64 mm；两个接受的样本其 IK、关节速度、物体连续性、碰撞/穿透和 Stage 3 子目标物理检查均通过。

详细记录：[`randomize.json`](runs/reproduce_place_cup/stage3/stage4_task/outputs/randomize.json)、[样本 0000 检查](runs/reproduce_place_cup/stage3/stage4_task/outputs/samples/0000/sample.json)、[样本 0001 检查](runs/reproduce_place_cup/stage3/stage4_task/outputs/samples/0001/sample.json)、[原始布局检查](runs/reproduce_place_cup/stage3/stage4_task/outputs/samples/original/sample.json)。

## 4. 仿真器和渲染器分别是什么

1. **MuJoCo 3.10.0：** 刚体机器人和杯/碗状态、正向运动学、接触/碰撞检查、动力学回放及 Stage 4 物理审计。`randomize.json` 同时记录 Stage 3 与 replay 版本均为 3.10.0。
2. **Blender Cycles（bpy 5.0.1）：** 把场景网格、材质、相机和已导出的状态变换做路径追踪渲染，生成 RGB 视频。Stage 4 配置为每像素 32 个 samples、串行渲染（`parallel=1`）。

所以，问“仿真用什么”时，物理仿真答案是 **MuJoCo**，最终相机视频渲染答案是 **Blender Cycles**。本任务只有刚体杯子和碗，不涉及 Isaac Sim 的可变形物体仿真。

## 5. 生成的视觉结果

Stage 4 对 `0000`、`0001` 和 `original` 三种布局各导出 ego、左腕、右腕三个视角，共 **9 段单视角视频**。配置从 30 fps 轨迹每隔一帧取样，最终视频为 1280×1024、15 fps、236 帧，约 15.73 秒。每个样本还有三视角并排的 `preview.mp4`；原始样本另有真人与合成画面对比的 `compare.mp4`。总计 **13 个 H.264 视频**（9 个单视角、3 个 preview、1 个 compare）。视频元信息已用 `ffprobe` 检查。渲染日志报告 Cycles 使用 `OPTIX`；`parallel=1` 代表一次只运行一个渲染子进程，代码会启用当前 Blender 能发现的全部 OptiX 设备，因此它不等于“只使用一张 GPU”。

文件位于 `runs/reproduce_place_cup/stage3/stage4_task/outputs/samples/`：

- `0000/{ego,wrist_left,wrist_right,preview}.mp4`
- `0001/{ego,wrist_left,wrist_right,preview}.mp4`
- `original/{ego,wrist_left,wrist_right,preview,compare}.mp4`

静态背景板处理使用 SAM3-LiteText 的文本提示分割和 LaMa 补洞：

- gated SAM 3.1 权重未使用；替代模型是 `yonigozlan/sam3-litetext-s0`，本地权重在 `/data/tzq/datasets/starVLA/playground/Pretrained_models/sam3-litetext-s0`。
- VOID/CogVideoX 权重未下载；静态背景板用本地 `big-lama.pt` 完成补洞，权重位于 `/data/tzq/datasets/starVLA/playground/Pretrained_models/lama-big/big-lama.pt`。
- 从原视频抽取 85 帧生成擦除掩码。报告记录平均擦除覆盖率为 6.72%，有 3.36% 的像素在抽样帧中始终未露出，因此由 LaMa 补齐。该做法满足静态背景板需要，但不等价于 VOID/CogVideoX 的时序视频补全。

## 6. 当前工作区代码修改概览

当前工作区里相关修改主要围绕模型替换和本地运行配置：

- **SAM 替换：** 检测、文本提示分割和 Stage 4 擦除掩码改用 `yonigozlan/sam3-litetext-s0`，通过 Transformers 加载，并支持从本地 checkpoint 路径加载；模型路径从 DexAgent 配置传到工具进程。检测证据中记录的模型标识也同步更新。
- **背景板替换路径：** Stage 4 增加 `void` / `lama` 可选后端。LaMa 模式复用已有 PyTorch，跳过 VOID/CogVideoX 的仓库和大权重下载；配置、setup 脚本和 README 已对应更新。
- **依赖和文档：** Stage 4 依赖增加 Transformers/Accelerate，并更新了 SAM3-LiteText 与 LaMa 的安装说明。
- **未提交状态：** 这些代码改动当前仍在工作区，没有提交。本总结和 `REPRODUCTION_NOTES.md` 也都是工作区文档。此处只写总结，没有改动上述运行代码。

## 7. 结果边界和复现注意事项

- **Stage 1–3 的成功状态主要来自示例复用。** Stage 1 和 Stage 2 的 `run.json` 都写明 `reused_example`，调用次数为 0。Stage 3 交付也说明使用维护者示例且没有重新验证。Stage 3 记录中的相机位置拟合没有执行，原因是 `gladLoadGL error`。所以 Stage 3 的 `ok=true` 不能单独证明从原始视频完成了一次全新理解、建模和动作规划。
- **Stage 4 有独立的物理回放证据。** 这里对原始轨迹和两个随机布局实际执行了 MuJoCo 回放和物理审计，因此“Stage 4 随机化样本通过”比“重新独立复现 Stage 1–3”有更直接的验证依据。
- **轨迹是模拟记录，不是实机数据。** 它不能证明真实机械臂传感器、控制器或接触力表现与模拟一致，也不是由真实机器人采集的 demonstration。
- **视频背景板是近似替代。** SAM3-LiteText 与 LaMa 代替了受限 SAM 3.1 权重以及缺失的 VOID/CogVideoX 权重；背景区域始终被遮挡时只能做图像补全，效果不保证与时序视频修复一致。
- **腕部相机背景不是实拍腕部视频。** 代码把 ego 背景板按桌面平面重投影到腕部视角；超出桌面或 ego 画面覆盖范围的像素填成房间中位色。抽查 preview 可见大块灰色/平面区域，因此这些视角当前适合检查机器人运动，不应当作真实腕部摄像头外观的高保真样本。
- **真人视频和仿真轨迹没有逐帧时间对齐。** 输入视频 229 帧、约 7.63 秒；轨迹 472 帧、30 fps、15.7 秒。`compare.mp4` 会把真人视频均匀抽样到合成视频的帧数后并排展示，所以它是阶段对照，不是按真实时间戳同步的动作比较。
- **环境要用复现环境而不是 Conda base。** 依赖和模型权重都在 `/data` 路径下。当前交互 shell 默认落在 `/data/miniconda3` base；该 base 的 MuJoCo 是 3.7.0，且没有 Torch。运行产物中的 Stage 3/4 记录则明确是 MuJoCo 3.10.0。复查时应激活 `/data/miniconda3/envs/dexagent`，以免用错环境得出版本不一致的结论。
- 本文是基于现有记录和产物的整理；检查了轨迹数组结构、manifest、样本审计报告以及 13 个视频的编码/尺寸/帧率/帧数元信息，没有运行测试套件。

## 8. 相关文件索引

- 总运行状态：[`run_summary.json`](runs/reproduce_place_cup/run_summary.json)
- 本次复现简记：[`REPRODUCTION_NOTES.md`](REPRODUCTION_NOTES.md)
- Stage 3 轨迹：[`trajectory.npz`](runs/reproduce_place_cup/stage3/trajectory.npz)
- Stage 3 语义阶段及帧区间：[`trajectory_manifest.json`](runs/reproduce_place_cup/stage3/trajectory_manifest.json)
- Stage 4 配置：[`stage4.json`](runs/reproduce_place_cup/stage3/stage4_task/stage4.json)
- Stage 4 随机化结果：[`randomize.json`](runs/reproduce_place_cup/stage3/stage4_task/outputs/randomize.json)
- Stage 4 背景板统计：[`plate/meta.json`](runs/reproduce_place_cup/stage3/stage4_task/outputs/plate/meta.json)
