# HumanEgo：数据、策略仿真与 Real2Sim 源码审阅

日期：2026-10-20。结论：**HumanEgo 最值得接入的是人类示范的关系表示、可替换策略的闭环执行，以及真实与仿真之间的诊断方法。沿用 EmbodiForge 的 Scene / Demo / Task / Robot / Episode 边界即可。**

这是源码、公开元数据和轻量契约检查的审阅报告，未完成模型推理或物理策略复现。没有安装依赖、下载大数据/权重、操作真机或修改 HumanEgo 源码。

## 1. 先分清三个证据来源

| 来源 | 实际解决的问题 | 在 EmbodiForge 中的位置 |
| --- | --- | --- |
| HumanEgo 论文与训练/预处理代码 | 从人类示范提取交互状态、学习末端动作策略 | Demo 数据处理、可选的策略动作源 |
| [Policy Sim](https://humanego-ai.github.io/sim/) 与本地 `simulation/` | 加载公开策略，替换传感器和驱动，在 MuJoCo 中闭环运行 | 策略评估、控制诊断、物理参数核对 |
| [Real2Sim](https://humanego-ai.github.io/real2sim/) | 通过 DexAgent 从示范构建任务场景、求动作并扩增数据 | 现有 DexAgent 四阶段的具体应用参考 |

不能把三个来源的任务数量、机器人、成功率或实现混为一谈。Policy Sim 的当前任务是 serve_bread，机器人为 Trossen WidowX AI、Franka Panda、UR5e + Robotiq；Real2Sim 页面描述 Tianji + Sharpa 和两个任务。

论文的方法、实验及限制已核对：它使用 Aria 的公制定位与手部观测；机器人端感知使用深度；对象遮挡时依赖抓取锁定假设。论文也明确讨论单目替代手跟踪的性能下降、滑移和手内操作的局限。因此我们继续单目优先时，要把尺度、相机运动和手部轨迹质量作为前置验证，不能将该论文视为普通 RGB 已解决这些问题的证据。[论文及附录](https://arxiv.org/html/2605.24934)

## 2. 来源、覆盖范围与复查方式

- 本地源码：`HumanEgo/`，提交 `239affbc24b8b4a3ab2987cdd76436bea7ca9411`。
- 逐模块审阅了采集、预处理、ICT、全部训练与推理实现、配置及下载脚本。
- 仿真目录 180 个跟踪文件已登记：60 个实现文件全文审阅、8 个文档全文、16 个 Python 文件 API/docstring 审阅、3 个文档部分审阅、93 个文件仅清单（包括静态资源及部分网页实现）。核心执行、控制、物理、感知、验收、导出及真实日志回放已阅读。
- 网页核对包括 HTML、Real2Sim 的应用 JS、两页实际加载的 manifest/summary。读取了公开 checkpoint 配置、统计量及示例 040 的相机和对象初始化 JSON。
- 没有逐行审阅第三方依赖、所有网页移植代码、二进制资产；没有把网页回放当作本机物理执行。

分模块细节与准确源码锚点：

- [采集与预处理](references/humanego/data-review.md)
- [训练、策略与部署模板](references/humanego/policy-review.md)
- [仿真、验收、导出与真实日志诊断](references/humanego/simulation-review.md)
- [仿真文件覆盖清单](references/humanego/simulation-coverage.json)
- [契约探针结果](references/humanego/policy-probes.json)

源码引用均针对上述提交；并非宣称最新上游永远具有相同实现。

## 3. 数据路径：先得到可核验的示范，再决定如何执行

本地代码的数据流：

```text
Aria VRS + MPS 相机轨迹 + MPS 手关键点
    → RGB 去畸变、逐帧标定与世界坐标手姿态
    → 前置静态观察段、对象分割与二维点跟踪
    → 已知相机轨迹的多视角三角化、初始对象参考系
    → 手臂补绘、虚拟夹爪与对象关键点图像
    → 逐帧 training_data.json
    → 图像 + ICT → Flow Matching → 末端位姿与抓放信号
```

### 应直接学习的设计

1. **原始与派生数据并存。** 保留原始图像、去臂图像、标定、当前相机位姿、手与对象数据；训练图像只是原始观测的一种派生表示。
2. **固定参考系与移动相机分开。** `metadata.c2w` 是当前相机；`world_transforms.cam0` / `virtual_static_anchor` 是固定参考。训练配置中的 `camera_frame` 并非每帧重新变化的相机系。
3. **稳定的虚拟夹爪定义。** 用拇指与食指尖中点定义操作位置，用 MCP 基部构建朝向，避免闭合时两指尖重合造成轴退化。它仍需映射到目标机器人的真实 TCP。
4. **保留对象相对手的关系。** ICT 同时描述实体位姿与手在该实体局部坐标中的位姿，适合作为策略输入；Task 的目标、接触约束和成功谓词继续由现有任务系统表达。

### 不能直接当作场景或物理真值的输出

- `Lama.py` 实际去除 `mask_arm`，得到二维 `rgb_WoArm`；没有清除 USDZ 中的机器人几何，也没有更新背景深度和碰撞。
- 三角化产生稀疏点和初始对象框架，不是完整可交互网格或环境扫描建模。
- 对象后续运动主要依赖抓取时锁定到手的启发式估计；滑移、掉落、手内旋转需要另外观测。
- `is_finished` 是演示阶段标签，`is_dynamic` 表示当前锁定状态，都不是物理任务通过证明。
- 预处理默认依赖 Aria MPS；提供 HaMeR 等分支不等于普通 MP4 已拥有公制相机轨迹。

### 公开数据与模型核验

官方数据卡列出 serve_bread、water_flowers 各 61 段，共 122 段、约 238 GB；提供原始 VRS/MPS 和预处理结果。本次通过 HF 文件索引复核了 122 个会话目录，没有下载全量数据。[数据卡](https://huggingface.co/datasets/Leo-TX/HumanEgo)

远端数据 revision：`f0aa87fade6a41316d65322512f56f066dec62e3`。示例 040 相机配置为 544 帧、30 fps、640×480；对象 JSON 含 `cam0_c2w` 与两个对象的稀疏点/初始化框架。这些元数据尚不能证明完整会话的同步和几何精度。

模型 revision：`9d14cb0f7dfbfdba47e83925c9db3ab9b37cca2b`，当前索引只有 serve_bread 配置、统计量及两份权重文件。实际配置为右手单手、固定 camera frame、absolute action、50 步预测；三个辅助任务开关均为 false，训练/验证会话列表分别 60/1。因此“论文包含某机制”与“发布 checkpoint 启用该机制”要分别记录。[实际模型配置](https://huggingface.co/Leo-TX/HumanEgo/blob/9d14cb0f7dfbfdba47e83925c9db3ab9b37cca2b/serve_bread/config.json)

代码 LICENSE 为 PolyForm Noncommercial 1.0.0；数据卡为 CC BY-NC 4.0。接入时保留各自产物的来源与许可信息，不把可公开下载理解为用途无限制。

## 4. Policy Sim：真正补上的闭环是什么

`simulation/humanego_sim/runner.py` 加载原 `inference/policy.py:ICTPolicy`，持续执行：

```text
仿真状态 → 相机观测 → 感知估计 / ICT
    → 已训练策略 → 末端轨迹与抓放信号
    → 控制器修正 / IK / 时间插值
    → 执行器 → 接触物理 → 新观测
```

这是真实的学习策略闭环。策略输出末端目标，机器人适配器负责关节与夹爪控制；不是把同一套关节轨迹硬套在不同机器人上。

### 决定效果的实现条件

- **场景：** 参数化生成桌面、面包、盘子，并使用真实手眼标定布置相机与机器人；不是任意视频自动重建。
- **观测：** 渲染 RGB-D；默认使用仿真分割，隐藏机器人得到干净图像。也提供 DINO-SAM 感知分支，但不能据默认结果宣称真实视觉误差已经覆盖。
- **控制：** `production` 与 `tuned` 分开。后者含目标修正、到达等待、闭合保持、空抓判断及重试等 grasp assist；网络参数未改，但执行系统增加了控制能力。
- **双臂：** 当前 serve_bread 使用单手模型与左侧镜像观测，按时序错开；这不证明任意协同双臂任务均能直接部署。
- **物理：** 默认面包使用 MuJoCo flex，通过摩擦夹持；刚体回退另有 adhesion 机制。两者应在结果中标明。
- **感知锁定与物理独立：** 感知可以认为物体跟手移动，物理物体仍可能滑落。这种估计与实际状态的差异正是诊断信号。

### 上游报告的结果应怎样阅读

下表来自本地 `simulation/configs/tuned/*.json` 的 reserved test 结果，每本体 100 场景；是上游提供的统计，**本机未复现**。

| 本体 | tuned 每臂成功率 | tuned 双臂同时成功率 | production 每臂成功率 |
| --- | ---: | ---: | ---: |
| Trossen WidowX AI | 80.5% | 64% | 60.5% |
| Franka Panda | 75.5% | 57% | 46% |
| UR5e + Robotiq | 76% | 53% | 57.5% |

网页展示集与保留测试集的样本数不同，不能混用。比较应固定场景种子、策略、感知条件与本体，并分别报告每臂及整任务成功率。

### 验收要继续用我们自己的独立任务判据

当前 `runner.py:evaluate` 检查末态面包中心在盘子范围、底部高于桌面阈值、中心高度低于阈值，以及已发出打开夹爪命令。它没有直接要求稳定停留、实际支撑接触、已脱离手支撑，也不以 `has_ever_grasped` 作为必过条件。

因此 EmbodiForge 可学习其执行循环，但仍应以任务相关的接触、操作顺序、释放和稳定终态验收；策略的 done 与控制器发出的 open 都只作为事件。

## 5. Real2Sim 页面：与我们已有 DexAgent 的关系

页面将 Aria 示范转换为 EgoDex 输入，再走 DexAgent 的语义、场景、动作和数据扩增阶段；描述了 Tianji + Sharpa 的 58 关节适配。它对现有 6-DOF YAM 到 7-DOF Tianji 的迁移有直接参考意义，但该页面链接的是 DexAgent 仓库入口，不能据此认定所有演示专用改动已在本地源码中。[Real2Sim 页面](https://humanego-ai.github.io/real2sim/)

实际公开 [summary.json](https://humanego-ai.github.io/real2sim/data/summary.json) 在本次读取时记录 1,403 个渲染 Episode、609 个物理样本。**同一物理样本的多种外观不是独立动作样本**；应保留 physics sample ID，训练划分也应按源样本隔离。这里是公开清单核验，没有下载并重新执行全部数据。

该页的面包柔软效果采用顺应接触与渲染形变；Policy Sim 使用 flex。视觉相似不意味着物理模型相同。

本地 `setup/setup.yaml` 的 YAM + Sharpa 及 56 关节映射继续保留。Tianji 58 关节若后续引入，应作为有独立 URDF、控制、标定和验证证据的本体，不能仅重命名或替换 mesh。

## 6. 可视化最值得吸收的内容

现有 Workbench 与 RSS viewer 已有运行、场景与回放入口，优先补证据展示，不复制另一套网页仿真内核。

| HumanEgo 的有用呈现 | 我们的最小落点 |
| --- | --- |
| 同屏显示机器人、策略输入图、预测轨迹 | 现有 Episode 时间轴对齐观测、参考/预测末端轨迹及实际运动 |
| 初始布局平面图，成功与失败可选 | 执行记录按同一任务/本体/布局筛选，保留失败样本 |
| Replay 与 Live 明确分开 | 页面标明“记录回放 / 新执行”，拖动物体后重新运行才产生新结果 |
| 感知关键点与对象实际状态可比较 | 显示观测、估计、控制目标和仿真实际状态，保留来源 |
| 同场景 production / tuned 对照 | 同一配置快照与种子下比较控制改动，展示差异与失败原因 |

上游浏览器 Live 还涉及 ONNX 和 MuJoCo WASM；CPU 回退改变精度与 ODE 步数。本轮不把这套运行栈搬进平台，以免同时维护 Python、浏览器两种控制实现。

## 7. 已验证的边界与实现风险

这些问题用于指导接入验证，不能据此推断论文所有实验无效。

| 证据等级 | 位置 | 发现与接入要求 |
| --- | --- | --- |
| 原源码契约探针 | `inference/policy.py` + `run_inference.py` | anchor_frame 按当前物体编码、按初始 anchor 解码，构造平移 0.3 m 后回读误差约 0.3 m。固定参考必须独立于物体状态；当前 sim 仅支持 camera_frame，不能把此问题泛化到其全部结果。 |
| 原源码契约探针 | `run_inference.py:latch_objects` | 模板释放后物体估计回到初始姿态；应保留最后可信状态或重新感知。 |
| 原源码契约探针 | `inference/controller.py:_smooth` | 首目标绕过步长限幅。此为模板控制契约检查，未驱动硬件。 |
| 确定性小探针 | `simulation/autotune/loop.py:203` | 验证列表内每次重建同种子 RNG，32 次采样只有 1 个独立场景；不等于另外的 final evaluation 也有此问题。 |
| 原函数统计探针 | `simulation/humanego_sim/evaluate.py:summarize` | 1 次成功 + 1 次运行异常得到 100% 每臂成功率，但 Episode/CI 为 50%；错误样本必须留在共同分母。 |
| 静态源码 | `preprocess/Preprocess.py`、`AriaHands.py` | 手/相机采用长度尾部配对，应按真实时间戳验证。 |
| 静态源码 | `CamTriangulator.py` 与 batch 编排 | 跨会话缓存缺少对应 reset，需验证批处理隔离。 |
| 静态源码 | `DatasetGen.py` 与任务配置 | obj1 始终作为固定锚点，不应将其输出当作独立实测的移动对象轨迹。 |
| 静态源码 | `training/FlowMatchingDataloader.py` | 图像路径可能失效补黑，未来标签缺失没有完整 mask；导入后先查图像和目标有效性。 |
| 静态源码 | `inference/run_inference.py` | 发布的真机入口是模板，物体感知仍抛 NotImplementedError；仿真有自己的完整实现。 |

详细报告另列出导出、标签、可选 delta 模式和统计缓存问题，均未擅自修改上游。

本机使用现有 dexagent 环境运行：

- 完整 `pytest simulation/tests -m 'not sim'`：因缺少 `python-box` 在收集期失败。
- 四文件兼容性检查：**37 passed / 4 failed / 8 skipped**。3 个失败来自当前 MuJoCo 3.12 不支持所需的 `mjINT_DISCRETE`，1 个来自缺少 `box`；跳过项涉及未下载资产或未构建网页产物。
- 未完成任何公开策略的完整物理 Episode，不把上述通过项当作任务成功。
- 如后续复现，应独立使用其要求的 MuJoCo 3.16 环境，通过进程/文件适配；不升级共享 dexagent 环境来凑兼容。

## 8. 接入顺序：一个数据入口，一个可选策略源

1. **先接一条可核验 Demo。** 从公开 serve_bread 会话导入源视频、时间戳、逐帧相机、人手与对象初始化；复用 `rigcam.py` 的移动相机契约和已有示范记录，保留 Aria 坐标约定及场景配准，不盲套 EgoDex 的重力/桌面假设。
2. **同一任务支持两种使用方式。** 示范可经 DexAgent 求解成物理验证数据，也可训练/运行 HumanEgo 风格的策略；它们共用 Scene、Task、Robot、执行与验收。
3. **再接策略动作源。** 输入图像与关系状态，输出带时间语义的末端目标/抓放信号，由目标本体控制桥执行。夹爪标量不能替代 Sharpa 全手动作；原始手骨架继续保留。
4. **在现有 viewer 上补诊断。** 先做到观测、参考、预测、命令、实际状态和任务门禁可对照，再扩展美观程度。
5. **最后做随机化与训练。** 物理样本与渲染变体分别计数；重新布局后重新求解、执行、验收；按源场景/示范/物理样本划分训练测试。

可迁移任务仍须同任务覆盖既有 YAM + Sharpa、G1 Omnipicker、G2 Omnipicker；上游三个机械臂的结果不替代我们的三本体验收。严格 Real2Sim 保留来源场景和本体。

本次审阅交付是来源核验、实现审阅、轻量检查及接入设计。后续已完成
[HumanEgo 示范输入适配与完整 040 会话导入](HUMANEGO_IMPORT_20261021.md)，
保留实测时钟、原始骨架与未配准状态；完整策略复现和机器人任务验收仍未完成。
