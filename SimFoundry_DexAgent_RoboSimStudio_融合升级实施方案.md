# EmbodiForge：SimFoundry、DexAgent 与 RoboSimStudio 融合升级实施方案

> **文档类型：** 源码审查后的工程规划与实施验收方案\
> **规划日期：** 2026-10-19\
> **状态：** 实施计划，尚不代表跨项目集成已经完成或测试已经通过。
> **备注：** 当前方案仍可改进；实施前应根据本机源码、运行结果和用户目标持续修订。

## 0. 本机核查结论与已有基线

本方案按本机 checkout 的源码核对，不把上游 README 的能力描述直接当作本地验收结论。核查时的版本为：DexAgent `cb3d7edb3bf072c0fa603d0d3d8b8f161ac2fa46`（工作区另有未提交修改）、RoboSimStudio `73739abe212f963e8a9f5862b27bca370df091a7`、SimFoundry `9e34ebefcd020583fbb755a8b57268dce78eca26`。

- DexAgent 已有 `run_dexagent.py`、阶段报告/交付校验和 `agent/rss_episode.py`；RoboSimStudio 已通过 `run_robosimstudio.py` 接入标准刚体 episode、Sharpa 和毛巾原型。标准杯子入碗迁移记录为 37/37 必需检查通过。因此 P1 不应从零重做，P2 中 DexAgent MJCF 路径已有证据。
- 尚未通过的关键门槛是 SimFoundry 资产到 RoboSimStudio 的实际导入与物理语义对照、无模型 API 的 SimFoundry 重建路径、以及人类操作视频到目标机器人任务的动态映射。它们不能由现有标准 episode 重放代替。
- SimFoundry 当前 A 重建流程针对静止桌面物体和移动相机；第 3–14 阶段使用一张 canonical frame。深度可走 DA3 单目或 FoundationStereo 双目路径，估计深度不能直接视为实测真值，绝对尺度仍需已知尺寸/标定约束。C 流程采集/标注的是仿真机器人演示，并从 HDF5 提取 object-centric waypoints。它不是 RGB 人类演示的手部/物体三维轨迹恢复系统。
- SimFoundry 文档明确在下游使用前由 scene editor 人工修正位姿、尺度和相机。因此“无人工标注”不等于所有场景无需复核；融合门禁应在低置信度对象、相机或物理属性处提供可追溯的人工确认点。
- SimFoundry Stage 5 当前实例化的是 `SAM3(video=False)`，在 canonical frame 上做图像分割；DexAgent Stage 4 的 `sam3-litetext-s0` 是另一套模型权重/接口。需要通过同一固定帧集评估并实现后端适配，不能仅因名称都含 SAM3 就认为可直接互换或具备视频掩码传播。
- SimFoundry 当前 README 将发布范围写为刚体与关节物体生成，并把机器人数据生成列为后续能力；其部分 A/B 阶段直接请求 Gemini/Vertex 和图像生成服务。正式集成前必须先确定 API-free 的可运行子集。
- 本机没有 `mamba`，且不存在 `/data/miniconda3/envs/simfoundry`；默认 shell 使用 Python 3.13，也没有 SimFoundry 的 `omegaconf`。显式将 DexAgent Python 3.12 环境放到 `PATH` 后，A 的 `1b,2`、B 的 `1`、C 的 `smoke-random` 三个 `--dry-run --no-env-switch` 均成功生成阶段命令；其默认输入 `Data/home_coffee_4/.../scene.mp4` 不存在。改用仓库自带 `Fruits.mp4` 并在 `.workbench/venvs/simfoundry-preprocess` 隔离环境补齐 Open3D 0.19.0、Supervision 后，Stage 1b 实际成功，产出 452 全帧和 15 抽样帧；Stage 2 配置为 DA3，但 DA3 包和模型缓存均不存在，尚未运行深度推理。SimFoundry 四组轻量测试为 20 passed、1 skipped。DexAgent 环境缺少 OmniGibson 和 `rembg`，所以场景重建/仿真及 API-free 路径仍未验证。后续只补阶段确实需要的依赖，先核对版本冲突和许可，再隔离不兼容的具体阶段。
- SimFoundry Stage 11 的刚体质量/摩擦来自 VLM 估计；Stage 12 使用 PyBullet 稳定物体时每步将物体线速度和角速度重置为零。因此这两步提供资产初始化/布局稳定化，不构成物理参数实测或动态物理验收。
- SimFoundry 场景约定为 Z-up、四元数 XYZW；RoboSimStudio/MuJoCo 路径存在 WXYZ 约定，USD 导入也有专门变换规则。跨项目适配必须用已知平移/旋转 fixture 验证轴向、四元数顺序和单位，不能只看导入后的预览图。
- RoboSimStudio 毛巾示例证明了指定配置下的布料仿真和接触流程，不证明人类视频可自动重建出毛巾材料，也不证明另一只手或另一种耦合设置可复现同样结果。
- RoboSimStudio `EpisodeStore` 的权威格式是 NPZ/JSONL/MP4；源码说明 LeRobot/RLDS 应作为其上的转换器，但当前 checkout 未找到可运行的导出实现或端到端验证记录。P5 的“训练数据导出”仍是待实施门槛，需要独立 loader 回读检查，不能把已有 episode 记录等同于可直接训练的数据集。

## 1. 项目目标

**终极目标是全自动数据生成器。** 用户提交输入和约束后，现有主流程完成输入路由、资产准备、任务规划、目标运行时执行、质量检查、有限次修复、数据记录与导出。先完成一个刚体任务的新输入闭环，再增加场景变化、批量规模与其他本体。

输入可以是人类操作演示视频、机器人操作演示视频、场景扫描视频、自然语言任务，或已有三维资产/机器人模型。最终希望建立一条可复现的流程：

1.  理解输入，提取场景、对象、任务与约束；
2.  按需重建或导入场景资产；
3.  将任务转换为可执行、可验证的仿真任务；
4.  在目标机器人和目标物理运行时中执行、检查与调试；
5.  生成经过验收的轨迹与训练数据；
6.  保存来源、版本、配置、日志和验证结果，支持复现与断点续跑。

**第一版范围：** 以人类操作视频为演示输入，不要求机器人演示；先在已有/参数化场景中完成刚体任务，再接入 SimFoundry 的静态场景扫描资产。自然语言直达任务和其他演示来源作为后续可选入口。

自动化完成的验收使用以下条件：

- 新输入可冷启动；已有缓存只作为显式复用来源，输入/配置/资产身份匹配。
- Agent 请求能够被持续处理并校验响应；当前 external 文件通道中尚需外部会话处理的等待必须真实呈现。
- 每次修复绑定失败检查、输入场景与新尝试，设定次数/时间/资源预算，终态可恢复查询。
- 正式数据绑定目标运行时的任务与数据完整性检查，失败数据保留诊断且不进入已验收集合。
- 导出在干净路径由独立 loader 回读；观测、动作、时间戳、相机、机器人与来源保持一致。

统一入口由 [EmbodiForge Studio 实施方案](RobotSim_Workbench_前端与可视化实施方案.md)实施。总览首页汇总现有运行数、场景数、最近运行和能力状态，并链接到项目/运行、场景、数据回放与工具库。当前已验证的范围是报告/媒体/轨迹、记录式 3D 与 SimFoundry 离线场景副本；候选版本支持来源追踪与继续编辑。实时物理、跨后端资产验收及自动执行/导出仍按 P2–P5 单独验收，具体证据见[可视化验证记录](docs/WORKBENCH_VALIDATION_20261019.md)。

### 1.1 最高原则

**正确性 \> 简单性 \> 可维护性 \> 可扩展性 \> 性能优化。**

-   复用优先：先查现有代码、API、工具和测试，再决定是否新增实现。
-   单一职责：权威场景状态、任务判定和最终数据记录不应维护多份。
-   算法可选、验收统一：不同算法可以并存，但输出必须通过目标运行时验收。
-   明确边界：格式可以转换，不代表坐标、关节、语义和物理行为完全兼容。
-   可追踪：每个产物都能追溯来源、版本、配置、运行命令和验证结果。
-   渐进集成：先跑通最小闭环，再接入昂贵的视频重建与批量生成。
-   失败关闭：未通过验收或条件不足时，禁止把产物标记为成功。
-   不提前抽象：第一版不构建复杂插件框架、微服务或通用仿真后端。

### 1.2 明确不做

-   不将 RoboVerse/MetaSim 作为额外的并行仿真层引入。
-   不重新实现物理引擎、通用场景编辑器、第二套任务
    DSL、第二套成功判定器或第二套 Episode 记录系统。
-   不把三个仓库直接复制成一个巨型代码库。
-   不强行把所有依赖安装进同一个 Python 环境。
-   不把某个引擎中的轨迹回放成功视为目标引擎中的物理验证成功。
-   不把仿真特权状态未经筛选地作为策略输入。

## 2. 三个项目的定位

### 2.1 RoboSimStudio：统一仿真、执行、验证与记录核心

优先复用：

-   场景加载、场景编译和资产导入；
-   `AssetSpec`、`ObjectSpec`、任务定义和机器人规格；
-   Real2Sim Agent 的场景构建、状态检查、图像对齐和控制入口；
-   现有 IK、运动工具、动作图/`SolutionGraph` 与执行机制；
-   任务定义和任务相关的 goal predicates/evaluators（按具体 TaskSpec 或任务实现提供）；
-   `EpisodeRecorder`、`EpisodeStore` 和基准测试工具；
-   现有物理组合与目标运行时。

源码入口：

-   [RoboSimStudio](https://github.com/robosimstudio/robosimstudio)
-   [Real2Sim
    Agent](https://github.com/robosimstudio/robosimstudio/tree/main/robosimstudio_real2sim_agent)
-   [Scene
    模块](https://github.com/robosimstudio/robosimstudio/tree/main/robosimstudio/scene)
-   [Record
    模块](https://github.com/robosimstudio/robosimstudio/tree/main/robosimstudio/record)
-   [测试目录](https://github.com/robosimstudio/robosimstudio/tree/main/tests)

**决策：** RoboSimStudio
是最终场景执行、目标运行时物理验证、任务成功判定和原始 episode
记录的权威入口。Real2Sim Agent
应尽早进入验证流程，而不是等其他项目接好后再加入。

### 2.2 DexAgent：演示理解、任务语义与验证反馈

-   **Stage 1：** 从演示提取操作对象、属性、子目标与任务终态。
-   **Stage 2：** 构建任务相关对象、资产或 MJCF 场景。
-   **Stage 3：** 规划并生成机器人操作轨迹。
-   **Stage 4：** 轨迹/场景随机化、多视角数据或视频合成。
-   **Agent Harness：**
    工具注册、预算限制、执行记录、验证反馈、阶段修复和产物管理。

源码入口：

-   [DexAgent](https://github.com/dexagent1/DexAgent)
-   [Agent
    Harness](https://github.com/dexagent1/DexAgent/tree/main/agent)
-   [Stage
    1](https://github.com/dexagent1/DexAgent/tree/main/stage1_semantic_understanding)
-   [Stage
    2](https://github.com/dexagent1/DexAgent/tree/main/stage2_scene_reconstruction)
-   [Stage
    3](https://github.com/dexagent1/DexAgent/tree/main/stage3_trajectory_generation)
-   [Stage
    4](https://github.com/dexagent1/DexAgent/tree/main/stage4_robot_data_generation)
-   [Tool
    Library](https://github.com/dexagent1/DexAgent/tree/main/tool_library)

**决策：**
优先复用不依赖特定物理后端的语义理解、工具编排和修复机制。依赖 MuJoCo
的轨迹、状态和物理检查必须经过适配，不能直接视为 RoboSimStudio
原生输入。

### 2.3 SimFoundry：场景重建、资产生成与场景变化

-   **A：**
    视频处理、深度估计、场景/对象分解、网格生成、位姿估计、物理稳定化和场景导出。
-   **B：** Digital Cousins、场景/资产变化和任务提案。
-   **C：** 策略评估、演示生成、轨迹回放等应用流程。

源码入口：

-   [SimFoundry](https://github.com/NVlabs/SimFoundry)
-   [Pipeline
    说明](https://github.com/NVlabs/SimFoundry/blob/main/scripts/pipeline/README.md)
-   [A：场景重建](https://github.com/NVlabs/SimFoundry/tree/main/scripts/pipeline/A_reconstruction)
-   [B：场景增强](https://github.com/NVlabs/SimFoundry/tree/main/scripts/pipeline/B_augmentation)
-   [C：应用流程](https://github.com/NVlabs/SimFoundry/tree/main/scripts/pipeline/C_application)

**决策：** 保留独特的重建、网格生成和 Digital Cousins 算法。涉及
OmniGibson、PyBullet
等特定运行时的阶段可用于预处理或候选结果生成，但最终场景仍须在
RoboSimStudio 的目标运行时重新检查与执行。

**适用边界：** SimFoundry A 的“输入视频”是场景扫描视频：相机移动、场景物体基本静止。它会选择一张 canonical frame 做对象分割、网格生成和位姿拟合；不能把这条链路用于直接恢复正在操作的毛巾或人手轨迹。当前 checkout 的场景识别/图像补全还包含 Gemini/Vertex 或在线图像模型调用。API-free 模式必须显式替换这些调用或把它们改成 Codex external 请求/响应，不能只设置缓存后就宣称完成了无 API 冷启动。

### 2.4 论文与本机代码对照

| 项目 | 论文要解决的问题 | 本机代码里最值得复用的流程 | 集成边界 |
| --- | --- | --- | --- |
| [SimFoundry 论文](https://arxiv.org/abs/2606.28276) | 从场景视频构建可交互数字孪生，再生成对象/场景/任务 cousins，用于策略评估与训练 | A 重建、B 变化生成、C 应用都已有独立入口；Stage 5 对对象逐个分割、抠图并移除，Stage 7 生成独立对象网格，Stage 8 对位姿，后续整理物理与场景 | 标准 A 的物体几何主要来自一张 canonical frame；自动背景重建是可选的视频支路，不是 Stage 2c 普通高斯训练的一部分 |
| [DexAgent 论文](https://arxiv.org/abs/2609.35318) | 将人类演示变成带物理验证的机器人任务和数据 | Stage 1 语义与子目标，Stage 2 对象/场景重建，Stage 3 轨迹优化和失败诊断，Stage 4 状态随机化、重放、渲染与视频合成；工具库和 verifier 允许按任务修复 | 当前 Stage 2/3 的 MJCF/MuJoCo 物理结果不能直接当作 RoboSimStudio 目标运行时的验收结果；人类视频里的 2D 动作也不能直接当作机器人关节标签 |
| [RoboSimStudio 论文](https://arxiv.org/abs/2610.18835) | 让人和编码 Agent 能检查、调节并迭代修正接触丰富的 Real2Sim | Newton 场景、solver/contact/material 调节、状态/力反馈、失败后改场景再重跑、Episode 记录 | 它是目标仿真与物理验收核心，不替代视频分割、网格生成或视频补洞 |

SimFoundry 自动背景支路在本机的实际默认顺序为：从关键帧提出需要移除的前景对象，SAM3 生成关键帧 mask，SAM2 将 mask 向前/向后传播，VOID 两遍修复视频，再对干净帧跑 DA3、训练高斯背景并与对象场景对齐。SAM3 本身也能按文本 prompt 处理视频并跨帧传播；工作台新增可选 SAM3 单模型模式，以关键帧文本 prompt 做双向视频分割。两种方式共享预览和人工复核，不预设 SAM3 单模型质量优于原组合。AutoBG 支路承担“对象移走后补出后方视觉背景”；背景 mesh/高斯仍是视觉层，碰撞支撑需另外建模。它默认对象提案可能漏掉拍摄时静止的机器人，因此机器人/人手要进入明确的移除对象提示词并检查掩码预览。

### 2.5 模型职责与可替换边界

“模型可以替换”按**能力接口**落实，不要求每个模型共用一个万能接口，也不先造模型市场。先沿用各 pipeline 已有配置，缺少配置的阶段再补一个明确选项；每次运行把选中模型、权重 revision/SHA、运行环境和授权状态记进 manifest。适配失败或权重缺失时明确阻塞，不静默换模型。

| 能力 | 本机实现与作用 | 替换判断 |
| --- | --- | --- |
| 单帧文本分割 | SimFoundry Stage 5 使用 SAM3；DexAgent Stage 4 已通过 `segment(image, prompt) -> masks/boxes/scores` 适配 SAM3-LiteText（[论文](https://arxiv.org/abs/2602.12173)） | 可先让 LiteText 接管文本提示分割；它替换的是文本编码器，SAM3 图像编码器仍保留，因此不是完整轻量视觉模型。SimFoundry 的点提示回退仍需保留或单独适配，也不能把它当作视频跟踪器 |
| 视频 mask 传播 | SimFoundry auto-bg 默认使用 SAM3 关键帧 mask + SAM2 双向传播；SAM3 视频 predictor 也支持文字 prompt 后的视频传播 | Workbench 提供 `sam3` 与 `sam2` 两个传播选项。SAM3 使用关键帧文字提示并输出逐帧实例 mask；SAM2 接收 SAM3 图像 mask。需用同一输入比对 mask 质量、实例身份、耗时和显存，并在真实权重推理通过后确定生产默认。[SAM 3](https://github.com/facebookresearch/sam3) · [SAM 2](https://github.com/facebookresearch/sam2) |
| 视频补洞 | SimFoundry auto-bg 使用 VOID 两遍视频修复；DexAgent Stage 4 可选 VOID 或静态镜头 LaMa | 统一“输入帧+mask → 清理帧/plate”产物契约，保留时序一致性与单帧方案的差异 |
| 单图前景 alpha | SimFoundry Stage 6 的 `remove_background()` 通过 `rembg` 默认使用 `bria-rmbg` 生成透明 PNG；配置已接入 `s6_upsample.rembg_model`，并将模型身份、来源和许可状态写入阶段报告。Pixal3D 自身的 BRIA loader 则被本机替换成拒绝加载的 stub（[RMBG-2.0 模型卡](https://huggingface.co/briaai/RMBG-2.0)） | 这是对象图抠图，不负责视频补洞。允许本地通用 `rembg` 模型按同一 RGBA 输出契约切换；其它模型的权重许可标作未核验，`withoutbg` / `atlascloud` 云端模型名会被拒绝。替换后仍需用固定对象样本核对 alpha 边缘与物体完整性 |
| 图像特征/3D 条件 | DINOv3 是 Pixal3D 的图像特征提取器，为像素对齐的 3D 回投提供密集 patch 特征；它不是分割或抠图模型。Pixal3D 是 Stage 7 可选 mesh backend（[Pixal3D 论文](https://arxiv.org/abs/2605.10922)），不是 SimFoundry 重建效果的唯一来源 | `timm` 可作为模型加载/特征提取实现候选，但不是换成了另一种开源权重：[timm ViT-L/16 模型卡](https://huggingface.co/timm/vit_large_patch16_dinov3.lvd1689m)同样标注 DINOv3 License；Meta 模型卡说明 ViT-L/16 是从 ViT-7B 蒸馏的，且输出 CLS、register、patch tokens（[DINOv3 模型卡](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m)）。使用 timm 不会免除权重条款。Pixal3D 当前特征提取器直接访问 Transformers 的 patch embeddings、RoPE 和各层输出；改用 timm 仍需适配同一预处理、token 切分与投影特征契约，再做固定样例特征和 mesh 回归。详见 [DINOv3 论文](https://arxiv.org/abs/2508.10104) |
| 场景语义/图像编辑 | Stage 5 的对象类别/移除提示及 Stage 6 的补全/放大可配置 VLM、Gemini 或图像编辑后端；这些调用可能走云 API | 保留类别 JSON、对象 crop 和编辑图像的输入/输出契约；对云端、本地或 external 响应分别记录来源，不能静默切换 |
| 深度、网格、位姿 | DA3/DepthPro/FoundationStereo、Hunyuan3D/TRELLIS.2/Pixal3D、FoundationPose 等已有后端 | 先复用 stage 选择项，并按模型能力校验输出 contract。Stage 2 已支持用 `s2_da.model_name` 在两个 DA3 Nested 档位间切换，单帧与分块推理共用该选项，Stage 2 报告记录 repo ID、许可及深度/位姿能力。DA3-BASE 虽为 Apache 2.0 且支持位姿，但只输出相对深度；后续消费者将其当米制深度使用，所以代码会在推理前拒绝它，直到完成并验证尺度标定。 |

DA3 默认 Nested 权重标注 CC BY-NC 4.0；许可证允许与否必须作为部署条件核验，不能只因为模型可加载就视作可商用。[DA3 Nested 模型卡](https://huggingface.co/depth-anything/DA3NESTED-GIANT-LARGE-1.1) 标出米制深度、位姿与非商业许可；[DA3-BASE 模型卡](https://huggingface.co/depth-anything/DA3-BASE) 标出 Apache 2.0、相对深度和位姿。初步实现已把模型档案和 Stage 2 尺度契约接入配置/阶段报告，模型权重加载、质量回归和真实推理尚未验证。

因此，“BRIA 是否去背景”的准确答案是：**当前默认 Stage 6 通过 `rembg` 的 `bria-rmbg` 调用 RMBG-2.0，从单个物体图生成 alpha；现在可在 `s6_upsample.rembg_model` 切换受支持的本地通用权重，所选模型、软件版本和实际权重 SHA-256 会写入 `stage_info.json`。它不负责视频补洞；自动背景支路用 SAM3/SAM2 产生视频 mask，再由 VOID 补出视频中被遮挡的背景。** RMBG-2.0 的 Hugging Face 权重使用 CC BY-NC 4.0，商业部署需另行取得许可；`rembg` 本身的软件许可不覆盖其下载的模型权重。[RMBG-2.0 模型卡](https://huggingface.co/briaai/RMBG-2.0)、[rembg 官方模型说明](https://github.com/danielgatis/rembg#models)。本机以隔离虚拟环境完成了 BRIA 和 U2NetP 的固定图像实测，数据与限制记录在 `docs/baseline_report_20261019.json`；这只验证单图 alpha，不等同于 Stage 6 全流程验收。`timm` 是 DINOv3 的另一种加载/特征提取实现；其 ViT-L 模型卡仍标注 DINOv3 License，换库不会改变权重条款。Pixal3D 当前特征提取器直接访问 Transformers 的 patch embeddings、RoPE 和各层输出；适配同一 token/特征契约并通过固定样例回归后才可替换。

### 2.6 复现 SimFoundry 展示效果时必须串起的两条场景分支

SimFoundry 论文和当前代码把场景构建拆成“可交互前景实体”和“静态背景”两条分支。标准 A reconstruction 会从代表帧提取对象 RGB-D、生成对象网格并估计位姿/物理属性；若还要得到干净的房间/桌面背景，需额外运行 [auto-background 支路](SimFoundry/scripts/pipeline/A_reconstruction/stages/auto_bg_reconstruction/README.md)。只运行标准重建或普通 Stage 2c 高斯训练，不等价于完成对象去除、视频补洞和背景对齐。

代码中的自动背景顺序是：同一源视频关键帧由可替换的对象提案 provider 列出前景类别 → SAM3 生成各类 mask（可选利用 provider 返回的框做点提示）→ SAM3 单模型传播或 SAM2 双向传播 → VOID 分两遍、分块修复 → DA3 在修复帧估深度，并复用原视频 DA3 相机轨迹 → 深度监督 Gaussian Splat 训练 → 将背景刚性桥接到重建场景坐标 → 与场景资产合并。SimFoundry AutoBG 原生配置默认用 Gemini；Workbench 已把提案后端接成 Codex external（默认）、Gemini 或手动对象提示，并将外部结果传入既有 SAM 阶段。这样修复帧提供“对象后面可能是什么”的视觉推断，而实体几何/碰撞仍单独从前景分支生成；补全纹理不应充当已实测碰撞表面。论文报告自动补洞分支单 GPU 约 90 分钟/场景，适合与对象生成并行。

DINOv3 不参与上述背景分支。它只在可选 Pixal3D 单图网格生成后端里提取 global 与 patch 特征，并把 patch 特征按相机参数回投到 3D 网格条件中；BRIA 则只负责 Stage 6 物体图的前景 alpha。复现完整自动数据生成器还需接上 SimFoundry B/C（对象、布局、任务变体与运行时采集/评估）、DexAgent 的演示语义和阶段修复、RoboSimStudio 的目标运行时物理验收及权威 episode 记录；EmbodiForge Studio 提供统一 run/场景/证据入口，不再建立第二套权威数据格式。

## 3. 输入路由：不同视频不能走同一条强制流程

### 3.1 场景扫描视频

相机围绕桌面或环境移动，物体大体静止，主要依赖视差恢复几何和空间布局。

`场景扫描视频 → SimFoundry 重建 → 资产与坐标检查 → RoboSimStudio 导入 → 目标运行时验证`

### 3.2 人类或机器人操作演示

相机可能固定，手/机器人与物体随时间运动，主要目标是提取操作意图、对象状态变化和操作阶段。

`操作演示 → DexAgent Stage 1 → 任务相关资产重建/导入 → 任务定义 → 动作重定向与执行 → 验证`

当前目标以**人类操作视频**为输入，不要求机器人演示。第一步应提取任务语义、操作阶段、手/物体的 2D 观测和事件时间；在相机标定、已知尺度或多视角约束不足时，输出只能作为带置信度的相对运动/关键帧，不能当作准确的 3D 机器人动作。后续再将对象相关目标重定向为目标本体的可执行动作，并在 RoboSimStudio 中重新规划和验收。机器人状态日志作为未来可选输入，不作为本项目的前置条件。

### 3.3 自然语言与已有资产

`自然语言/资产/机器人模型 → RoboSimStudio 场景与任务定义 → 原生执行与验证`

仅当资产缺失、场景信息不足或需要生成变体时，才调用场景重建或资产生成流程。

### 3.4 同时提供场景扫描与操作演示时

两条路径可并行，但仿真前必须完成对象对应、相机内外参与坐标系对齐、单位和尺度对齐、物体初始状态对齐，以及机器人本体和关节映射检查。不能假设操作视频一定适合完整的场景扫描重建流程。

## 4. 功能重叠与融合决策

  ------------------------------------------------------------------------------------------------------------
  能力                    主要来源                    决策
  ----------------------- --------------------------- --------------------------------------------------------
  场景几何重建            SimFoundry A、DexAgent      保留不同算法路径；按输入和质量选择，不重复重建同一对象
                          Stage 2

  演示理解与子目标        DexAgent Stage              区分演示语义和场景任务提案，统一转换为正式任务定义
                          1、SimFoundry 任务提案

  场景编辑与视觉对齐      RoboSimStudio Real2Sim      以 RoboSimStudio
                          Agent、SimFoundry 工具      为主要交互/验收入口，保留必要的专用编辑功能

  资产生成与变化          SimFoundry Digital          复用生成算法；统一资产来源、版本和验收记录
                          Cousins、DexAgent Stage 2/4

  动作规划与执行          RoboSimStudio               先用目标运行时原生执行；其他算法按需适配
                          原生工具、SimFoundry
                          waypoint/CuRobo、DexAgent
                          Stage 3

  成功判定                RoboSimStudio 任务          统一报告和工作流入口；保留语义不同的底层检查
                          goal predicates、DexAgent
                          Verifier

  物理稳定性              SimFoundry 预处理、DexAgent 预处理不替代目标运行时验收
                          MuJoCo 回放、RoboSimStudio
                          运行时

  轨迹与视频生成          三个项目各自的流程          物理轨迹以目标运行时验证为准；视频合成作为可选后处理

  数据记录                各项目产物、RoboSimStudio   以原生 episode 为权威执行记录，再转换导出
                          EpisodeStore
  ------------------------------------------------------------------------------------------------------------

不同验证器不一定应该合并成一个算法。例如轨迹压力测试、静置漂移、图像对齐和任务终态判定测量的是不同问题。应统一报告与调度方式，而不是删除语义不同的检查。

## 5. 目标架构

``` text
静态扫描 ──> SimFoundry A ──> 对象/支撑/场景资产 ──┐
             └─ auto-bg：mask → VOID → 背景 splat ─┤
人类演示 ──> DexAgent Stage 1–3 ─> 语义/动作意图 ──┼─> 轻量资产适配 ─> RoboSimStudio
已有资产/任务 ────────────────────────────────────┘                  │
                                      场景执行/物理 gate/失败修正 <───┘
                                                  │
                           SimFoundry B cousins / DexAgent 数据变化
                                                  │
                 SimFoundry C 应用能力 + 目标运行时 episode 生成/回放
                                                  │
                                     验收记录与训练格式导出
```

完整目标覆盖 SimFoundry A/B/C：A 建重建场景，B 生成对象与场景变化，C 提供评估、遥操作/演示、waypoint 和重放能力。先完成 A 的一个真实输入和 RSS 目标运行时验收，再逐段开放 B/C；C 在 OmniGibson 里的通过记录不能替代 RSS 的目标运行时检查。DexAgent Stage 4 的遮罩、背景 plate 和渲染可复用，正式数据的物理轨迹仍由 RSS 重新执行与验收。

### 5.1 复用现有主流程，只补必要适配器

DexAgent 已有统一入口 `run_dexagent.py`、阶段报告、交付校验、工具库和 Codex external 文件交互；RoboSimStudio 已有 `run_robosimstudio.py` 与 episode 适配器。继续沿用它们作为单一工作流入口和目标运行时，不再新增一个平行的 `robot-sim-pipeline` 包或第二套状态机。

确有格式转换需求时，按实际缺口在现有 `agent/` 或 `tool_library/` 下新增一个小适配器，例如 `simfoundry_asset_adapter.py`。适配器只处理已确定的文件格式、坐标/单位和物理字段映射；报告沿用 DexAgent 的报告结构，仿真记录沿用 RoboSimStudio `EpisodeStore`。先完成一个资产、一个任务的闭环，再决定是否需要抽出通用工作流代码。

### 5.2 不新增第二套通用场景格式

不创建囊括所有几何、关节、材质、动作和任务语义的庞大
`UniversalScene.json`。

-   场景执行：使用 RoboSimStudio 原生场景、资产规格和场景文件。
-   任务定义：以 DexAgent 的任务契约和已验证的任务 gate 为准；将 RoboSimStudio 的场景测量/谓词作为执行证据。当前 checkout 没有找到一个覆盖通用任务的 `GoalEvaluator` 统一 API，因此不可把它当作现成组件依赖。
-   来源产物：保留 SimFoundry 与 DexAgent 原始输出。
-   跨项目元数据：只增加轻量 `manifest.json`。
-   训练数据：以原生 EpisodeStore 为标准记录，再按需转换。

### 5.3 环境策略：默认沿用当前主环境

用户已选择尽量保持一个环境和一个主流程。本机 DexAgent 与 RoboSimStudio 已共用 `/data/miniconda3/envs/dexagent`。因此先在该环境核对 SimFoundry 所需模块和小型 smoke；不要预先规划四套项目/集成环境。

-   优先复用主环境中已存在的依赖和 `/data` 上的模型缓存。
-   只有记录到明确的版本/ABI 冲突，并且无法用延迟导入或子进程规避时，才为冲突阶段使用现有的专用环境；不复制整套集成环境。
-   SimFoundry 的 Isaac Sim/OmniGibson 与图像生成阶段可能有额外依赖；逐阶段检查后再决定，不以“依赖隔离”为由增加暂时用不到的环境。

第一版不需要微服务、消息总线或分布式调度器。

### 5.4 无模型 API 约束

DexAgent 不通过模型 API 调用 Codex。需要 Codex 判断的步骤使用现有 external 文件请求/响应流程：程序写出带输入哈希和 schema 版本的请求文件，Codex 读取并生成响应文件，程序校验响应后继续。SimFoundry 中的 Gemini/Vertex、GPT 图像或其它在线调用必须关闭、替换为本地模型，或改成上述文件交互；不能在正式流程中静默回退到在线服务。离线缓存只用于复现已有请求，不代表新输入可无 API 处理。

**Gemini 是当前上游代码的一种实现，不是 EmbodiForge 的系统要求。** 每个需要语义判断/提案的阶段都应调用统一的 provider 能力契约，可按阶段选择 Codex external（复用 `agent/external.py` 的请求/响应协议）、本地模型、显式配置的远程 API，或人工提供结构化结果。阶段只检查本次所选 provider 和其输入/输出契约；不能把 `GCLOUD_PROJECT` 当成全流程能力门槛。当前某些 SimFoundry 阶段仍直接实例化 Gemini，因此升级期间可报告该上游兼容缺口并阻止无效的长任务；移除检查前必须先接好替代 provider，不能把失败推迟到昂贵阶段中。

**已落地的 provider 替换：** Workbench 背景清理中的 AutoBG 对象提案默认调用 DexAgent external，发出带关键帧图像的隔离请求，要求 Agent 通过 `propose_objects` 返回对象文本提示，再交给 SimFoundry 既有 SAM mask 阶段；Gemini 与手动提示仍可选择。进入全量 A 构建时，已确认的对象类别会复用为 `s5_scene.force_categories`，Stage 5 使用 SAM3 分割而跳过重复的 Gemini 对象提案；关键帧选择也固定使用现成的几何/清晰度启发式。Gemini 仍用于独立的图像编辑及其它尚未接入 provider 的上游调用，因此当前全量构建预检继续检查 Gemini 能力，不能据此声称整个 A 流程已 API-free。

### 5.5 静态环境与操作实体必须分离

扫描时静止的机器人与物体，在后续任务中仍可能运动，不能靠运动检测决定哪些内容进入静态背景。场景需要分别保存：清理后的静态视觉环境、静态支撑/碰撞、独立对象实例和权威机器人。对象是否固定由任务定义；机器人底座固定不意味着可把整台机器人烘焙进环境。

**第一版以现有系统接通为优先。** 用户输入需移除对象（尤其机器人和手），检查关键帧/跨帧 mask 后才能运行 VOID；补洞结果再次人工复核。移动相机下不能用原始像素差直接决定对象身份。当前默认仍复现 SAM3 关键帧 mask → SAM2 双向传播 → VOID → DA3/高斯对齐；WorkBench 已加 SAM3 视频单模型选项，使用关键帧文本 prompt 生成逐帧 mask，不再硬编码第 0 帧与单向跟踪。两种方式共用 mask 结果契约和预览界面，但真实推理质量尚未在本机权重环境验收。用同一操作演示比较漏检、漂移、遮挡恢复、帧数/分辨率、显存和耗时后再选择生产默认。仍未被观测到的区域明确标成模型补全，不将其当成实测碰撞表面。一个固定扫描场景、一个刚体任务通过后再扩大输入范围。

本机源码可复用的部分：

- SimFoundry：Stage 5 的逐对象分割、图像/深度去除和 Stage 7 独立网格；可选 auto-background 的 SAM3 → SAM2 → VOID → DA3/高斯背景。默认提案可能漏掉机器人/人手；普通 Stage 2c 直接训练 splat 不会先清除前景。
- DexAgent：Stage 2 的逐对象/手部掩码、独立资产与场景装配；可复用来源校验、对象对应和视觉重合检查。已有参数化场景的正确分离不能替代任意扫描网格的清理。
- RoboSimStudio：静态实体、独立刚体/关节体、机器人及视觉/碰撞代理分别构建；导入 arena 时可移除已标注的自由体。该机制依赖源文件已有实体语义，无法从一个合并的扫描 mesh 自动恢复其中的机器人。

首版设置只保留对象名/角色确认和 mask 预览，不增加复杂的手工逐帧标注器。后台记录原始帧、对象列表、mask、清理视频和模型身份；模型身份至少包括图像分割器、视频传播器、权重/revision、阈值和推理状态。界面明确显示“已观测背景”与“VOID 补全区域”，并按同一时间轴预览原视频、mask 叠加和清理结果。背景、支撑碰撞、操作对象和机器人仍分别保存，再进入场景编辑和 RSS 验收。清理失败时保留诊断，不把遮挡补全出的外观当成已测量的碰撞结构。

新增门禁：背景单独渲染无机器人/任务对象残影；对象移开后旧位置不残留同一实例；机器人运动无第二套静态机器人；视觉环境与支撑碰撞分别检查。具体 UI 与当前官方示例问题见[前端方案第 8.5 节](RobotSim_Workbench_前端与可视化实施方案.md#85-扫描背景与独立实体分离)。

采集可控时优先空场景扫描；否则使用明确的实体排除集合与多视角一致性检查。残留机器人/物体可能在训练图像中成为未标注实例或产生错误遮挡；如果残留几何被加入碰撞，还可能形成幽灵障碍。因此背景清理结果也应参与 P5 的图像/实例标签/深度一致性门禁，不能只检查 JSON 中对象是否独立。

## 6. 实施阶段与验收门禁

### P0：冻结基线并复现原项目能力

**任务：** 固定三个仓库 commit；记录
Python、CUDA、驱动、物理后端和关键包版本；运行现有轻量测试与 smoke
test；区分通过、失败和环境阻塞；维护 `baseline_report.json`。本轮的[部分基线报告](docs/baseline_report_20261019.json)已记录 commit、工作区状态、依赖、Stage 1b 真实运行及已完成检查；完整报告还需补模型权重身份、深度/重建阶段和目标运行时验证。同时记录主环境中 SimFoundry A 各阶段的 API/模型依赖，产出“可在无 API 模式运行的阶段列表”；按核心能力登记当前后端、权重来源/revision、安装/加载/执行状态与授权要求，作为后续替换模型的基线。

**通过条件：** - 每项检查都有明确状态； - 失败可复现且有日志； -
缺少模型、权重或硬件条件单独记录； - 不修改上游代码来掩盖基线问题。

**已有结果：** RoboSimStudio 标准刚体参考迁移及 DexAgent 四阶段流程已有各自报告，不应混为一次“从人类视频到 RSS”的集成验收。复用它们作为基线，并把 SimFoundry 基线单独补齐。

**本轮增量核查（2026-10-19）：** SimFoundry A/B/C 的 dry-run 成功生成命令，默认视频 `SimFoundry/Data/home_coffee_4/s1_video/video/scene.mp4` 不存在；因此改用仓库样例 `Fruits.mp4`，在隔离的轻量预处理环境实际运行 A Stage 1b，`stage_info.success=true`，耗时 18.58 秒，得到 452 张 1280×720 全帧与 15 张抽样帧。产物在 `.workbench/baseline/simfoundry_20261019/`，不是模型推理或场景重建结果。默认 Stage 2 后端 DA3 的包装代码在本机，但 `depth_anything_3` 依赖和模型缓存未找到，故深度推理仍阻塞。`SimFoundry/tests/test_subpipeline_layout.py`、`test_resource_scheduler.py`、`test_pipeline_reporting.py`、`test_pipeline_orchestrator.py` 合计 20 passed、1 skipped。DexAgent 的 `agent/tests` 是脚本式测试目录，直接交给 pytest 会在收集 `test_config.py` 时触发 `SystemExit`，不是功能用例失败；改用 `tool_library/tests/run_all.py` 隔离执行的 Episode、报告、输入身份三组各 1 passed。RoboSimStudio 对应五个测试文件通过：81 passed、80 subtests passed；测试耗时 261 秒，记录 46 个依赖弃用警告。单元测试只验证代码局部行为，不代表跨项目资产导入通过。

### P1：跑通 RoboSimStudio 原生最小闭环

**任务：** 选择已有刚体抓取放置示例；使用 Real2Sim Agent
检查场景、图像对齐、静置漂移和动作执行；使用原生目标判定器判定成功/失败；录制成功和失败执行并验证回放。

**通过条件：** - 固定成功样例判定成功，固定失败样例判定失败； -
轨迹可回放； - 失败执行不会被记为成功； - 验收结果、日志和 episode
可以互相追踪。

**已有状态：** 本机标准杯子入碗 episode 在 RoboSimStudio 中已有 37/37 必需检查通过的记录；另有 Sharpa 接触与 YAM 毛巾原型。复用已有验收，不重复搭建。当前缺口是将这些任务 gate 与新视频生成轨迹闭环连接。

### P2：验证跨项目资产导入

**任务：** 选择一个 SimFoundry 静态刚体资产，经过显式转换/导入后进入 RoboSimStudio；复用已有 DexAgent MJCF 导入结果作为对照。检查几何、尺度、位姿、碰撞、质量、质心/惯量、摩擦、关节与渲染；运行目标引擎中的静置和物理步进。不得把 OmniGibson scene JSON、USD 可打开或 PyBullet 稳定化直接当作 RoboSimStudio 验收通过。

**通过条件：** - 无缺失网格、纹理或外部引用； -
坐标轴、四元数顺序、单位和缩放符合已知合成测试值； - 关节、碰撞和关键物理属性明确； -
场景可以构建并执行物理步进。

**停止条件：**
若资产特性无法正确转换，先记录兼容性边界并修复适配器，不继续堆叠上层自动化。

### P3：接入场景扫描、视频语义与任务定义

**任务：** 将“场景扫描视频”和“人类操作演示”分开路由。场景扫描只有在无 API 路径通过 P0 后才调用 SimFoundry A；人类操作演示走 DexAgent Stage 1 的 Codex external 流程。把任务对象映射到具体场景实体，将子目标转换为有测量定义的任务契约，并记录尺度/位姿是实测、估计、先验还是未知。

**通过条件：** - 每个任务对象映射到具体场景实体； -
每个子目标有可测量的成功条件； - 坐标与尺度对齐有记录； -
未知物理属性不会被静默填入未经验证的值。

### P3b：建立人类演示到机器人动作的显式映射

**任务：** 从 RGB 视频生成带时间戳和置信度的人手/物体观测、接触/释放事件及对象相对关键帧；必要时加入人工确认的相机、尺度或对象对应约束。先把这些作为动作意图/目标，不直接复制成机器人关节轨迹。由目标本体求 IK/规划并执行，再根据状态反馈修正。

**通过条件：** - 每个关键帧可回指源视频帧； - 遮挡、尺度歧义和低置信度显式保留； - 任务不依赖未观测的绝对深度； - 目标机器人动作在 RSS 目标运行时可执行并通过任务 gate。

### P4：动作执行与失败修正

**任务：** 优先使用 RoboSimStudio 原生执行；按需适配 DexAgent 规划或
SimFoundry
waypoint/CuRobo；将跨引擎轨迹转换为具名关节、末端目标或对象相对运动；在目标运行时重新执行；根据失败类型决定修复环节。

**通过条件：** - 轨迹在目标仿真中可执行； - 关节命令满足限制，状态无
NaN/Inf； - 目标条件成立才判定成功； - 失败可定位到具体阶段； -
重试次数有上限。

### P5：规模化数据生成与导出

**任务：** 复用 SimFoundry 场景变化；选择性复用 DexAgent
轨迹随机化和视频合成；所有正式轨迹在目标运行时重新验证；用 EpisodeStore
保存原始记录；按需导出 LeRobot/RLDS。

**通过条件：** - 每条保留 episode 有来源、版本、任务结果和验证记录； -
观测、动作、时间戳、视频帧数与 episode 长度一致； -
导出后可由独立程序读取； - 场景引用的网格、纹理和 include 文件全部随包提供并在包内相对解析，不依赖本机绝对路径； - 特权仿真真值与策略输入隔离； -
数据生成配置与随机种子可追踪。

**柔体限制：** 先把刚体 episode 导出闭环做完整。毛巾不能沿用“固定网格 + 每帧一个刚体变换”的 Stage 4 表达；柔体验收需要记录粒子/顶点状态和拓扑、厚度、质量面密度、拉伸/弯曲/接触参数、抓取方式与求解器耦合。当前 RSS 毛巾示例的单向耦合和调过的材料参数必须随报告记录；换手爪、材料或耦合后重新验收。未经实测标定的结果只能称为参数化仿真原型。

## 7. 基线检查命令

> 以下为建议核对的基线命令，不代表已经在统一环境中全部执行成功。运行前必须以实际
> checkout 的 commit、README 和 CLI 帮助为准。

### 7.1 RoboSimStudio

``` bash
PYTHONPATH="$PWD/robosimstudio${PYTHONPATH:+:$PYTHONPATH}" python -m pytest -q \\
  robosimstudio/tests/test_agent.py \\
  robosimstudio/tests/test_agent_checks.py \\
  robosimstudio/tests/test_agent_server.py \\
  robosimstudio/tests/test_asset_mjcf.py \\
  robosimstudio/tests/test_bench_documents_and_runs.py
```

从 DexAgent 仓库根目录执行，并传入有效场景的绝对路径：

``` bash
export PYTHONPATH="$PWD/robosimstudio${PYTHONPATH:+:$PYTHONPATH}"
python -m robosimstudio_real2sim_agent up --scene /path/to/scene.py
python -m robosimstudio_real2sim_agent status
python -m robosimstudio_real2sim_agent check \\
  --steps 60 \\
  --settle-mm 2 \\
  --penetration-mm 1
python -m robosimstudio_real2sim_agent quit
```

执行前先在同一已激活的主环境中用 `export PYTHONPATH="$PWD/robosimstudio${PYTHONPATH:+:$PYTHONPATH}"`、再执行 `python -m robosimstudio_real2sim_agent --help` 和子命令
`--help` 核对参数。`/path/to/scene.py`
必须替换为实际有效场景；示例阈值不是适用于所有任务的通用标准。不要因为这里的候选测试命令另建 `uv` 环境；仅在主环境确认有依赖冲突时才隔离具体阶段。

### 7.2 SimFoundry

先核对当前 README 与脚本参数，再尝试 dry-run：

``` bash
bash scripts/pipeline/A_reconstruction/run.sh \\
  --dry-run --no-env-switch --include 1b,2

bash scripts/pipeline/B_augmentation/run.sh \\
  --dry-run --no-env-switch --include 1

bash scripts/pipeline/C_application/run.sh \\
  --dry-run --no-env-switch --mode smoke-random
```

以上命令从 `SimFoundry/` 仓库根目录执行。当前 shell 没有 `mamba`；省略
`--no-env-switch` 时包装脚本会在干运行前因 `mamba: not found` 退出。轻量测试候选：

``` bash
pytest \\
  tests/test_subpipeline_layout.py \\
  tests/test_resource_scheduler.py \\
  tests/test_pipeline_reporting.py \\
  tests/test_pipeline_orchestrator.py
```

测试路径和参数可能随 commit 变化。Dry-run
只能证明流程规划/命令生成可执行，不能证明视频重建或物理结果正确。

### 7.3 DexAgent

核对实际脚本路径与依赖后再运行：

``` bash
python tool_library/tests/run_all.py rss_episode
python tool_library/tests/run_all.py run_reports
python tool_library/tests/run_all.py input_identity
python tool_library/tests/run_all.py stage3
```

如果路径或参数不存在，先检查当前仓库实际入口，不要为了让命令通过而凭空创建脚本。`agent/tests` 多数为脚本式用例，不可直接用 pytest 收集；用 `tool_library/tests/run_all.py <唯一文件名片段>` 将所选脚本隔离运行并单独记录结果。`tool_library/build_index.py` 会重写 `tool_library/index.json`；索引存在未提交修改时先核对并保留变更，不要直接重建。先使用当前 DexAgent Python 3.12 环境验证；只有确认依赖冲突后，才将具体冲突阶段交给已有专用环境。

## 8. 集成层必须具备的测试

  ------------------------------------------------------------------------------------------------------
  编号                    测试                    验收标准
  ----------------------- ----------------------- ------------------------------------------------------
  T01                     来源与资产完整性        文件存在、哈希匹配；场景依赖资源随包提供并在限定根目录内解析

  T02                     坐标与单位转换          合成测试中的位置、尺度、旋转和变换矩阵符合已知值

  T03                     机器人映射              按具名关节映射；维度、限制、控制模式和末端定义正确

  T04                     物理稳定性              状态有限；漂移、穿透和接触行为通过任务相关检查

  T05                     任务判定                固定成功/失败样例被正确区分

  T06                     轨迹重放                目标运行时可执行，末态满足目标条件

  T07                     数据完整性              观测、动作、时间戳、视频和元数据一致

  T08                     断点恢复                恢复后不会错误复用过期产物或覆盖已验收结果

  T09                     端到端闭环              从输入到场景、任务、执行、验证、记录与导出成功

  T10                     特权信息隔离            策略观测不意外包含 privileged state 或仿真真值

  T11                     失败关闭                强制门禁失败时，产物不得进入正式训练集

  T12                     可复现性                相同版本、配置、输入和随机种子可复现关键元数据与结果
  ------------------------------------------------------------------------------------------------------

### 8.1 数值门槛

不要为所有任务套用同一个容差。

-   格式转换：用人工指定的单位立方体、平移与旋转测试。
-   视觉重建：用代表性验证集校准
    IoU、位置偏差和尺度误差；校准前保留人工检查。
-   物理稳定性：复用目标运行时检查，按物体尺寸和任务设阈值。
-   动作执行：检查关节限制、碰撞、末端轨迹和目标终态。
-   数据导出：导出后重新读取，核对 episode
    长度、动作/观测时序和视频帧数。

## 9. 工作流状态与产物追踪

逐步骤状态沿用 DexAgent 现有报告的规范化小写枚举，避免出现两套近义状态：

-   `running`：阶段正在执行。
-   `passed`：必需检查通过且无警告。
-   `warning`：必需检查可继续，但存在已记录的非阻断警告。
-   `failed`：必需检查失败并阻止下游。
-   `blocked`：缺少依赖、权重、硬件、输入或外部响应；补齐后可恢复。
-   `reused`：明确复用了已有产物，列出其来源及本次未重跑的检查。
-   `skipped`：由输入路由规则跳过，不代表成功或已验证。
-   `interrupted`：用户或系统中断，不能标记为全流程通过。

运行汇总可将已完成的警告运行记为 `passed_with_warnings`；用户指定只跑部分阶段时记为 `scope_completed` 或 `scope_completed_with_warnings`。部分完成不能伪装成全流程 `passed`。

每个阶段至少记录来源哈希、代码版本、配置哈希、随机种子、执行命令、退出码、产物列表、验证结果和日志路径。例如：

``` json
{
  "stage": "asset_import",
  "status": "blocked",
  "input_hashes": {},
  "output_files": [],
  "repository_commits": {},
  "config_hash": null,
  "seed": null,
  "command": null,
  "exit_code": null,
  "validation": [],
  "log_path": null,
  "reason": "待填入实际阻塞原因"
}
```

此 JSON 仅为元数据示意，不是实际运行报告。实现时应使用严格 schema。

发布规则：

1.  每阶段输出到独立临时目录；
2.  完成文件完整性和验收检查；
3.  通过后再发布为正式产物；
4.  失败产物保留在诊断目录，不覆盖上次通过验收的产物；
5.  缓存键包含输入哈希、代码版本和影响结果的配置；
6.  输入、配置或上游代码变化后，使相关下游缓存失效。

## 10. 主要风险与处理方案

### 坐标系、尺度与相机不一致

记录坐标变换、单位、相机内外参和变换方向；用合成场景测试；禁止靠目测对齐。

### 无 API 模式与外部模型调用

SimFoundry 当前若干对象识别、补全和 Cousins 阶段依赖 Gemini/Vertex 或图像生成服务。每个阶段需明确标注 `local`、`codex_external`、`cached` 或 `unavailable` 执行方式；无 API 正式运行禁止偷偷发起在线请求，缓存命中也不得伪装成新输入的冷启动结果。

### 跨物理引擎轨迹不等价

源引擎轨迹先转换为具名关节、末端目标或对象相对运动，再在目标运行时重新求解和验证。

### 资产导入但语义丢失

USD/MJCF
导入成功不代表关节限制、耦合关系、碰撞网格、摩擦、质量和材质完整保留。建立资产能力矩阵与回归样例，明确不支持项。

### 依赖冲突

优先在当前主环境执行。若有明确的版本或 ABI 冲突，再用 CLI/子进程隔离单个冲突阶段；不预建多套环境，也不为统一 `import` 强行合并不兼容依赖。

### 特权状态泄漏

明确区分 policy observation、action、task label 和 privileged
state；导出后检查字段白名单。

### Agent 无限修复或错误重试

设置阶段预算、最大重试次数、超时、缓存键和失败类型；每次修复说明变更并重新运行相关门禁。

## 11. 最终决策与执行顺序

### 架构决策

1.  **RoboSimStudio：**
    统一场景执行、目标运行时物理验收、任务成功判定与原始 episode 记录。这里指机器人数字本体上的仿真，不等于真实硬件执行或真机安全验收。
2.  **SimFoundry：** 提供场景重建、资产生成、Digital Cousins
    和专用算法。
3.  **DexAgent：**
    提供演示语义、任务分解、选择性规划工具和验证反馈/修复机制。
4.  **集成层：** 只负责路由、调度、格式转换、门禁、元数据和恢复。
5.  **RoboVerse/MetaSim：** 不作为额外的并行仿真层引入。
6.  **正式训练数据：**
    必须经过目标运行时执行验证，并保留可追踪的原始记录。

**EmbodiForge Studio 当前接入边界：** 总览页可通过 RoboSimStudio live Unix socket 只读展示仿真步数、时钟、运行/暂停状态和实体位姿，并跳转 RSS 原生 Viewer。该桥只发送 `status` 请求，不驱动仿真、不判断物理成功，也不替代 RSS 的任务 gate 和 EpisodeStore。独立 RoboSimStudio live 服务已用真实 Newton 刚体场景验证 API 连通；实际使用需在 Studio 启动时配置 `WORKBENCH_RSS_RUN_DIR`。现有实现路径暂时保留为 `workbench/`。详见 [Studio 配置与边界](workbench/README.md)。

### 第一轮实际工作

1.  **P0 收尾：** 固定三个项目的干净 commit/差异、依赖、模型来源和 API-free 能力；补 SimFoundry baseline。现有工作区有未提交变更，不能只记录 HEAD SHA。
2.  **P1 复用：** 复用已通过的 RoboSimStudio 刚体 episode、Sharpa 接触和毛巾原型报告，不重复实现运行器。
3.  **P2 聚焦：** 首先把一个 SimFoundry 静态刚体资产导入 RoboSimStudio，并对照物理字段；DexAgent MJCF 路径已部分验证。
4.  **P3 刚体闭环：** 使用一段人类操作视频完成语义、对象对应、对象相对关键帧/事件、目标本体规划执行、RSS 门禁和 episode 记录；新跑和复用分别报告。
5.  **P4 柔体原型：** 从有已知展开尺寸的毛巾开始，先验证观测到参数化布料模型的映射，再接入人类视频驱动操作和动态几何数据导出。

正式训练数据来自目标运行时执行产生的机器人观测/动作 episode；人类 RGB 视频本身没有机器人动作标签，不能直接当作已完成的机器人监督数据。仿真通过后仍需独立规划真机验证。

进入下一阶段前，必须能够回答：三个项目的 commit
与依赖是否固定？目标环境能否运行最小场景？选定资产是否有可复现的预期结果？执行、判定与录制是否闭环？失败是否能定位到环境、资产、任务、机器人映射、规划、物理或导出阶段？

只要关键问题尚未回答，就不应标记"集成完成"。

## 12. 参考仓库

-   [RoboSimStudio](https://github.com/robosimstudio/robosimstudio)
-   [RoboSimStudio Real2Sim
    Agent](https://github.com/robosimstudio/robosimstudio/tree/main/robosimstudio_real2sim_agent)
-   [DexAgent](https://github.com/dexagent1/DexAgent)
-   [DexAgent Agent
    Harness](https://github.com/dexagent1/DexAgent/tree/main/agent)
-   [DexAgent Tool
    Library](https://github.com/dexagent1/DexAgent/tree/main/tool_library)
-   [SimFoundry](https://github.com/NVlabs/SimFoundry)
-   [SimFoundry Pipeline
    Reference](https://github.com/NVlabs/SimFoundry/blob/main/scripts/pipeline/README.md)

> **版本提醒：** `main` 分支会变化。实施前应记录实际 checkout 的 commit
> SHA，并以该版本源码、README、CLI
> 帮助和测试结果为准。本文列出的命令是基线检查候选，执行前需核对目标
> commit 中的路径和参数。
