# GenieSim 素材与夹爪本体接入审查

用户最终明确区分两类目标：**严格 Real2Sim 复现保留源场景和原本体；Ego 等可迁移任务使用公共桌面，并至少在现有灵巧手、G1 Omnipicker、G2 Omnipicker 三套本体上分别通过**。三本体不是可选实验组，也不是给不同可迁移 demo 分配不同机器人。现有灵巧手基线保持不变，同时复用本机 GenieSim 的对象、背景和材质。本文记录本机源码/资产检查及后续顺序，不把资产存在或 USD 可读取写成机器人执行已通过。

## 按任务目标确定验收范围

| 任务目标 | 场景与本体 | 验收依据 |
| --- | --- | --- |
| 严格 Real2Sim 复现 | 源场景、原本体、原尺度及标定关系；不强制替换公共桌子 | 场景/本体身份、几何与空间关系、动态和原任务结果；缺少源本体时明确未完成 |
| 可迁移任务生成（如 Ego 演示） | 提取意图、对象关系和动作阶段，在公共桌面上分别适配三套基线本体 | 相同任务关系与成功判据；各本体独立规划、执行、记录并全部通过 |

目标在任务建立时明确，并随结果保留；不单凭文件名判断，也不因运行失败自动从严格复现改成可迁移模式。迁移结果不能标成源场景数字孪生。输入是否为 Ego 视频并非硬编码判断条件，仍以用户要求还原还是迁移为准。

## 源码确认可复用的接口与待验证接缝

环境与机器人分别维护；具体运行使用一个已声明的组合。沿用 RSS 的现有类型，不建立第二套环境/本体注册中心：

| 职责 | 现有接口 | 保存内容 |
| --- | --- | --- |
| 环境 | `CellSpec` | 地面、桌板/桌腿、静态背景、支撑面坐标、可用安装点 |
| 机器人 | `EmbodimentSpec`、`ArmSpec`、`GripperSpec` | 本体资产、内部关节/驱动、夹爪联动、工具坐标；机器人相机随对应链接 |
| 任务 | `TaskSpec` 与现有任务对象/子目标记录 | 可操作对象、初始/目标关系、成功判据；可迁移任务按支撑面坐标表达 |
| 组合 | `Binding`；交互构建用 `Scene` / `BuildRequest` | 环境/本体选择、安装变换、主辅机械臂和已验证控制配置 |

`Binding.mount` 使用世界坐标，`mount_point` 引用场景的表面坐标安装点；`scene/frames.py` 负责表面坐标和世界坐标转换。编译器优先使用显式 mount，其次安装点，最后本体默认偏移。轮臂须提供落地站位，不能直接套桌面机械臂的默认安装偏移。仿真后端与步长继续归 `ArrangementSpec`。

“已声明组合”只说明可以尝试构建，不是已经物理或任务通过。RSS `TaskRequirements` 由 bench 的 `matrix.admits()` 做候选筛选，且已声明 Binding 会直接返回允许，不能作为编译器的强制兼容性检查。`TaskSpec.binding_for()` 拒绝未声明组合，`with_pairing()` 可显式声明尝试；`Scene/BuildRequest` 则会合成构建用 Binding，得到的 `Sim` 本身没有任务 evaluator。任务路径须显式接入有效 evaluator，不能靠写入 Binding 获得“已验收”状态。

两类目标可复用组合接口，但目标锁定和验收尚需接入：严格 Real2Sim 固定原环境、原机器人、标定和对象关系；可迁移任务在公共桌面上声明三套机器人组合。每次运行保留所用组合、资产身份、坐标变换与任务目标，避免换机器人后仍使用旧动作维度或回放记录。RSS 原生夹爪控制通常是每手一个开合标量，不能替代 Sharpa 的 44 个独立手指关节控制；共享任务关系和判据，保留本体独立控制。

分离是职责和引用分离，不要求把所有源导出文件立即拆开。RSS 的 `scene_resident` 只跳过机器人重复导入，不能验证原本体身份；`monolithic` 用于候选筛选，不是不可绕过的锁；`static_overrides` 只替换任务 scenery 的具名资产路径，不会自动剥离旧机器人或替换 Cell statics。扫描背景中已烘焙的机器人必须实际清理；仅隐藏独立机器人或再加载新本体会留下残影。

源码审查时 `agent/rss_scene.py::TaskSceneEntity` 完整导入 MJCF，却忽略 `self.frame`；随后已修复整场景平移，并同步自由物体状态、相机和录制回放，见[坐标接入验证](WORKBENCH_RIG_FRAME_ALIGNMENT_20261020.md)。`SharpaBridge` 保留 YAM + Sharpa 的 56 关节控制；目前只是将已有完整场景正确放到 RSS 世界，原桌子仍在其中。可迁移任务还须接入独立环境/任务对象，适配 Stage 2 布局与 Stage 3 抓取/运动学。G1/G2 物理适配未完成前不加入可执行选项；不改写 RSS 编译器、不做泛化自动匹配平台。

环境背景通过实际被编译器消费的 `StaticSpec(asset=...)` 接入；`CellSpec.asset` 当前虽声明却未被该构建路径使用。公共相机也需显式转换：`CameraSpec` 默认世界坐标，并不自动乘桌面 frame。严格复现还需核对编译器新增地面对接触的影响。源码依据与三项目关键调用链见[源码复核](ENVIRONMENT_EMBODIMENT_SOURCE_REVIEW_20261020.md)；新组合尚未端到端运行。

## 可迁移 demo 的最低验收规则

同一个任务在三套本体上分别完成：任务规划、目标运行时物理执行、任务判定、原生数据记录和回读。任务语义、对象关系及成功判据必须一致；各本体的 IK、抓取、动作维度、控制参数和必要的工作空间布置独立适配并记录。禁止以复制关节轨迹或替换渲染模型冒充跨本体验证。

整体完成条件为三套本体全部通过，不是任一通过或平均分达标。未适配、缺依赖、未运行、跳过、阻塞、失败、证据不足都使整个 demo 保持未完成，并分别列明原因。硬件导入、夹爪开合、落稳或已有单本体验收仍可作为阶段证据，但不能提升为三本体 demo 通过。若原任务对夹爪不具可行性，先解决任务适配或向用户明确问题，不自行删除机器人或降低任务要求。

每份 demo 记录至少列出三行本体结果，绑定同一任务版本、实际模型/资产 SHA、规划与物理报告、任务测量及 Episode。历史单本体结果保留原来的验证范围，不能自动升级成满足此规则。目前 G1/G2 均未完成完整任务验收，所以当前没有证据可宣称已有三本体 demo 完整通过。

## 统一桌面与地面

对可迁移任务，用户要求三套本体使用**同一张桌子、同一个桌面任务布局**。统一桌高、桌面尺寸、支撑面、任务对象和地面，分别适配本体安装/站位；不是将三个机器人缩放到相同高度，也不要求三套机器人同时挤在桌边。严格 Real2Sim 任务不应用这个场景替换规则。

- 共用世界地面 `z=0`，桌面高度为唯一的 `H`；桌面厚度、桌腿与支撑碰撞归场景所有，桌腿底面落在地面。保留桌下空间，不用一个实心大箱体代替整张桌子。
- G1/G2 的轮子支撑在同一地面，底盘在桌前；根据真实轮子几何和根坐标计算安装变换，不能把 USD root 的 `z=0` 直接当成轮子接地。
- 原有 YAM + Sharpa 保留双臂与手的装配，通过固定桌边支架定位；安装点相对于桌面定义，不能为它虚构下半身或改变机械臂尺寸。
- 任务对象、目标和公共观察相机绑定桌面坐标系；头部/腕部相机属于机器人。站位、躯干和关节 home 姿态可以按本体适配，但桌面任务布局与成功判据保持一致。须分别验证可达性、与桌面/桌腿/对象的碰撞和任务结果。

现有 RSS `CellSpec` 已有 `ground_z`、`surface_z`、`statics`、`mounts` 与 `surface_frame`，直接复用即可，不再新增场景框架。`scene/builtin/benches.py` 的标准桌是实心箱体，本需求应改用同一接口定义桌板和桌腿。

当前 DexAgent `setup/setup.yaml` 以桌面为 `z=0`，`agent/rss_sharpa.py` 则使用地面 `z=-1`。因此接入地面为零的公共世界时，必须明确保存 `T_world_from_rig`：对于不改变轴向的布置，原任务、机器人安装和固定相机共同平移 `+H`；相机内参及机器人内部关节/手部装配不变。RSS 的表面坐标原点在桌面中心，而当前 rig 原点在两安装位中点，需要同时处理水平原点偏移。规划、物理、回放和记录使用同一变换，不能只抬高可视化模型。

只读源几何检查发现，两款 USD 均朝源 `+X`，对齐当前 rig 的桌内 `+Y` 需整体 yaw `+90°`。G2 四轮最低点已约为源世界 `z=0`；源根的 `+0.04 m` 已包含在顶点世界坐标中，不能重复抬高。G1 底座是合并视觉网格，最低点约 `0.001215 m`，没有独立轮子刚体；这只能用于静态外观对齐，不能冒称轮接触已通过。外部落地平移按“目标地面高度减源支撑最低点”计算，桌前间隔还需结合底盘包络、可达性和碰撞验证。

本节是已确定的场景接入约束；公共场景的几何生成、各本体站位、运行时坐标桥接及三本体任务验收尚未完成。既有单本体记录按原坐标和原验证范围保留。

## 实验本体

| 实验组 | 权威来源 | 本轮状态 |
| --- | --- | --- |
| 现有灵巧手基线 | `setup/setup.yaml`、`setup/robot/` | 配置保持不变。用户称“天玑 + Sharpa”；当前文件实际标为 I2RT YAM 双臂 + Sharpa Wave，每臂 6 轴、每手 22 指关节，刚体运行时共 56 个坐标。名称对应关系待明确，不据此改动模型 |
| G1 Omnipicker | `GenieSimAssets/robot/G1_omnipicker/robot.usda` | 原样导入受缺失装饰相机依赖阻塞；记录匿名 session 修正后，RSS 导入及 CPU finalize 成功，求解器构建失败。未执行任务 |
| G2 Omnipicker | `GenieSimAssets/robot/G2_omnipicker/robot.usda` | 原样 RSS 导入及 CPU finalize 成功，求解器构建失败。未执行任务 |

最初用 `/data/miniconda3/envs/geniesim-data-collection/bin/python` 的 `pxr` 读取组合后的 USD，随后在隔离环境进行了下述实际 RSS 导入检查；未执行物理步进或修改源本体。两个 `.usda` 都是米制、Z-up；G1 根为 `/G01`，G2 根为 `/genie`。各有两条 7 自由度机械臂。

`robot.usd` 与 `robot.usda` **不能互换视为同一入口**。普通遍历（不展开 instance proxy）下，G1 两者分别为 217/397 个 prim、45/46 个关节；`.usda` 额外启用了 world 固定关节。G2 分别为 307/531 个 prim，均为 59 个关节，但 world 固定关节为 inactive，含 8 个轮部铰链。后续记录必须绑定精确入口、依赖、版本与 SHA；若固定 G2 底盘，应明确写成实验适配。

两个本体的 `config.yaml` 是 URDF 转换器配置，包含 `asset_path: null`、`fix_base: false` 和通用 drive 参数，并非当前系统可直接运行的本体配置。

### 用户提供的正常外观参考

2026-10-20 用户补充三栏正常显示图：左为 G1 Omnipicker，中为 G1 120S，右为 G2 Omnipicker；原始静态姿态、完整机器人网格、USD 材质颜色、浅灰背景和地面用于外观核对。中间的 G1 120S 不替换上述必测 Omnipicker 本体。图中标注的 1.30 m / 1.79 m 是参考图信息，尚未作为本系统的实测尺寸验收。

后续预览应保留源视觉网格和材质绑定，核对头部、双臂、夹爪、躯干与底盘的完整性及相对比例。碰撞几何单独验收、单独显示调试；修复碰撞分解时不通过删减视觉网格来改变正常外观。静态源姿态预览与运行时关节姿态、完整材质渲染及物理任务分别记录验证结果。

## 夹爪与传感器

共同的关节和链接名：

- 左臂 `idx21_arm_l_joint1` 至 `idx27_arm_l_joint7`；右臂 `idx61_arm_r_joint1` 至 `idx67_arm_r_joint7`。
- 腕端 `arm_l_end_link`、`arm_r_end_link`；夹爪根 `gripper_l_base_link`、`gripper_r_base_link`。
- 候选工具坐标 `gripper_l_center_link`、`gripper_r_center_link`，须提取相对腕端变换，不能直接把局部平移当作世界位姿。
- 主动夹爪关节 `idx41_gripper_l_outer_joint1`、`idx81_gripper_r_outer_joint1`，范围 0–45°；从动 inner joint1 范围 −45–0°，由 PhysX mimic 指向 outer driver。
- 每手有 8 个转动关节，两手另有共 4 个启用的球关节闭环。因此动作可表达为开合意图，但底层不能简化成两个滑动关节。

USD 中角度使用度；RSS 控制坐标使用弧度。G1 原始夹爪 drive target 左约 87.5°、右约 91.2°，超过相应 45° 上限，导入后不能直接沿用为 reset 目标。

两套 USD 都有头部和腕部 Camera prim；G1 还含底部鱼眼，G2 含多路头部相机。Camera prim 存在不代表成像内参、姿态、输出与 RSS 已正确对接，先验收一个固定视角和一个腕部视角。

## 现有组件能复用到哪里

用户补充的 ARSTAG 与本次 G1 目标直接相关：论文 §IV-A 明确采用 AgiBot G1、双 7 自由度机械臂、双 OmniPicker，头部加两腕相机，10 Hz 数据记录/策略执行，仿真为 Isaac Sim。它可用于核对平台与任务生成思路；论文没有在该实验中验证 G2，也没有给出与本机素材一一对应的模型 SHA，不能据此跳过我们的导入与物理检查。[论文实验配置](https://arxiv.org/html/2609.24563v1#S4.SS1)

接入时重点借鉴“按对象状态计算末端运动与开合 → 抓取/IK 可行性筛选 → 保持任务关系的随机化 → 扰动后重新规划”。保留已确定的 RSS 目标运行时，分别验收三套本体；10 Hz 是论文采集设置，不直接覆盖当前物理子步与原生录制时钟。本次查看的官方项目页仅发现 Website source 入口，尚未取得完整生成/机器人适配源码。[方法](https://arxiv.org/html/2609.24563v1#S3.SS2) · [官方项目页](https://boweili666.github.io/ARSTAG/)

RoboSimStudio 已有 `robots/registry.py`、`EmbodimentSpec`、`ArmSpec`、`GripperSpec` 和 `robots/entity.py` 中的 USD/URDF 导入。后续在这些现有接口上补齐明确的关节、驱动/从动、工具坐标和夹爪行程定义，无需再造本体注册框架。

实际 `/data/miniconda3/envs/dexagent/bin/python` 安装了 Newton 1.6.0/RSS 0.1.0，但没有 `pxr`；本次通过独立的 `.workbench/geniesim-venv` 补充 USD 依赖进行验证，没有改共享环境。安装的 Newton USD importer 含 `PhysxMimicJointAPI`、球关节及闭环处理代码，不能根据 RSS 导入模块的旧注释断言它完全不支持 mimic；结构导入保留了这些约束，动态约束是否正确执行仍待验证。

当前 DexAgent 的 `agent/rss_sharpa.py`、候选验收和相关回放绑定 YAM + Sharpa 的运行时。把新本体名称填进页面不会自动改变规划/控制适配，不复用其 56 坐标数组解释 G1/G2。

`GenieSimAssets/mujoco/robot/G2_place_workpiece.xml` 是另一个任务专用本体：其 hand_pw 中同名夹爪关节被定义为 0–0.024 m 的滑动关节、驱动 inner joint、使用不同网格，部分指部碰撞几何被注释；USD Omnipicker 则驱动 outer 转动关节并带完整联动。它不能冒充 G2 Omnipicker 的等价 MJCF。cuRobo 子目录的 URDF 还引用当前子树中缺失的 `package://genie_robot_description/...` 网格，不作为现成可执行替代。

## 实际 RSS 导入检查与阻塞

以下保留 10 月 20 日基线。后续已定位并修复遗漏 PhysX 属性解析的问题，
G1/G2 完整导入的 CPU 求解器构建均已通过；辅助惯量及动态资格仍未通过。
详见 [PhysX 导入修复与复验](GENIESIM_PHYSX_IMPORT_20261021.md)。

新增 `python -m agent.geniesim_import`，复用 `robosimstudio.scene.build.imports.add_usd_stage` 与既有 `dexagent.step_report.v1` 报告。固定关节不折叠、不改底盘自由度、不简化夹爪机构；绑定入口及已加载 USD layer 的 SHA，并在结束时检查源文件未改动。此命令只做导入检查，不生成成功 Episode。

| 检查 | G1 | G2 |
| --- | --- | --- |
| 原样 USD 导入 | 缺少 Isaac 装饰相机网格，失败 | 成功 |
| 明示的适配 | 仅在匿名 session 清除该相机引用并停用该 prim；原样失败报告保留 | 无 |
| 本体关节、限位、连接、mimic、闭环结构 | 适配后保留 46 个原生具名关节、2 个 mimic、4 个闭环 | 保留 59 个原生具名关节、2 个 mimic、4 个闭环；导入器另建浮动根关节 |
| CPU finalize | 成功，但 3 个零质量辅助坐标体的惯量被修正 | 成功，但 7 个零质量辅助坐标体的惯量被修正 |
| 带碰撞标记的导入 shape（最终报告） | 3,140 | 4,066 |
| MuJoCo CPU 求解器构建 | 栈溢出，接触数量 28,650 | 栈溢出，接触数量 30,441 |
| 物理步进、抓取任务、数据记录 | 未运行 | 未运行 |

G1 原始越限夹爪目标已单独报告；导入器使用了限位内的中间值，这不等于原始目标保真。惯量检查目前覆盖质量及主惯量值，完整惯量方向、关节局部坐标与轴、碰撞形状/过滤、动态约束、相机成像仍作为**必需但未完成的检查**列出。

碰撞数量异常主要集中于手指网格。源资产声明 `convexDecomposition`，原生 importer 转为 CoACD 分解；目前未发现其读取源 PhysX `maxConvexHulls=32` 的代码。这个映射差异是下一步根因调查方向，尚不能断言它是全部接触或栈溢出的唯一原因。源资产部分臂/躯干碰撞本就关闭，也需在任务运行前明确处理。不能靠增加求解器栈或关掉接触直接宣称本体适配完成。

G2 初次与最终日志的总 shape 数分别为 4,178 / 4,170，部分分解数量也发生变化；上表使用最终 JSON 中带碰撞标记的 4,066 个 shape。变化原因与复现稳定性待查，不将中间计数混入最终证据，也不据此直接断言 CoACD 非确定性。

工作台现在透传报告的 `scope` 和 `task_success_evaluated`（兼容既有 `metrics` 存放位置），在检查面板明确展示“本体导入检查”和“任务成功未评估”。不会改变原检查结论，也不会把上述失败升级为 demo 通过。

原始证据目录：`.workbench/previews/geniesim-runtime-20261020/`，包括 `G1_exact.json`、`G1_camera_overlay.json`、`G2_exact.json` 及对应日志。供 Studio 浏览的报告位于 `runs/geniesim_import_G1_20261020/` 与 `runs/geniesim_import_G2_20261020/`。源资产、日志及大体积报告保留本机，不提交整套资产。

隔离环境基于现有 DexAgent Python 的 `--system-site-packages` venv，仅新增 `usd-core==26.8` 与 `newton-usd-schemas==0.6.0`。在仓库根目录复现：

```bash
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=2 .workbench/geniesim-venv/bin/python -m agent.geniesim_import --robot G1_omnipicker --output /tmp/G1_exact.json
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=2 .workbench/geniesim-venv/bin/python -m agent.geniesim_import --robot G1_omnipicker --omit-missing-camera --output /tmp/G1_camera_overlay.json
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=2 .workbench/geniesim-venv/bin/python -m agent.geniesim_import --robot G2_omnipicker --output /tmp/G2_exact.json
.workbench/geniesim-venv/bin/python -m pytest tests/test_geniesim_import.py -q
```

当前三次检查均预期退出 1，原因保留在报告中。隔离环境导入检查回归 **16 项通过**；Workbench 报告/API 回归 **39 项通过**，覆盖范围字段兼容、未知值不强转、原检查结论和源文件不变。TypeScript 与 Vite 构建通过。

Workbench 整套测试 **146 项通过**。默认服务已更新至 **8765**；真实浏览器读取两份报告，桌面 1512 px / 窄屏 390 px 均能看到导入范围、任务未评估及求解器错误，无页面异常或横向溢出，未 mock API/静态资源。入口：[G1](http://127.0.0.1:8765/runs/304ccf455903c9f683d86bda?stage=backend&view=validation) · [G2](http://127.0.0.1:8765/runs/756481d66f6675f34635f531?stage=backend&view=validation)。浏览证据保存在 `.workbench/previews/geniesim-report-browser-20261020.json` 及同目录截图；这些是诊断页面验证，不是机器人视觉或物理验收。

## 素材清点与用途

本机素材树 `VERSION` 为 3.2.0，约 45 GB，在父仓库中整体未跟踪；继续作为外部资产目录使用，不将整库加入代码提交。

| 资源 | 本机检查 | 用途 |
| --- | --- | --- |
| 对象 | 1,245 份 `item.py`：benchmark 1,057、genie 69、sft 61、iros 54、edited 4；对象树有 2,492 个 USD 文件 | 操作对象和干扰物候选 |
| 背景 | 39 个环境目录，44 个 `background.usd[a]` / `background.json` 文件 | 室内场景、布局与视觉环境 |
| 纹理 | 对象/背景的 textures 目录合计 7,172 个文件 | 颜色、法线、金属度、粗糙度等贴图 |
| 环境光 | `background/common/HDR` 有 32 张 HDR；`common/Light` 有 5 个 USDA 预设 | 环境照明与视觉背景变化 |
| 材质变化 | `background/home/home_b`、`home_b_with_table` 的 `material_config.json`；`objects/edited/table_000/material_config.json` | 复用已声明的材质绑定与候选组合 |

这些数字来自本机文件检查，不表示全部素材经过目标运行时验收。对象/背景范围发现 93 个 LFS 占位文件（15 USD、69 JPG、9 PNG），位于 SFT 对象；`objects/omni6DPose` 为断链，声明的可选 `extra` 子模块缺失。

实际 `item.py` 都是裸字典表达式，字段为 `up_axis`、`has_joint`、`has_articulation`、`id`、`size`、`shapes`；使用 AST 字面量读取，不执行这些文件。沿用库内元信息结构；避免直接导入 `GenieSimAssets/__init__.py`，因为导入会全量扫描并打印索引。

视觉材质绑定/纹理、碰撞几何/摩擦/质量、背景的材质替换映射分别保留。不能用颜色材质随机化隐式改变摩擦；`item.py` 的原资产 Y-up 与外层 `.usda` 的 Z-up 也不能重复应用旋转。来源 README/LICENSE 声明 CC BY-NC-SA 4.0；保留原路径、来源和许可证元信息。

## 已执行的最小资产复用验证

使用原生 Light Editor `asset_library.discover_assets` 和 `import_asset` 导入：

`GenieSimAssets/objects/benchmark/apple/benchmark_apple_000/Aligned.usda`

导入到 `.workbench/previews/geniesim-apple-smoke-20261020/objects/apple/benchmark_apple_000/`。整个对象包逐文件 SHA 相同，源目录未变；复制后的 USD 可组合读取，米制、Z-up，存在视觉 Material 与 PhysicsMaterial，贴图随包复制。依赖解析仍报告 `OmniPBR_Opacity.mdl` 未解决，**未验证渲染保真或物理行为**。证据为 `.workbench/previews/geniesim-assets-audit-20261020.json`。

机器人依赖检查中，G1 的六个/G2 的七个 USD layer 可解析；两者的 `OmniPBR.mdl` 未解析，G1 另缺 Isaac 自带的装饰性 camera 模型。此问题与整套机器人几何缺失不同，但均应进入导入报告。

现有 Light Editor 支持额外资产根目录和对象包复制，可以继续复用。但全量接入前须修正资产身份去重：`asset_library.py::_deduplicate` 只比较入口文件字节，会将 `benchmark_beer_bottle_004/Aligned.usda` 与 `benchmark_beer_bottle_009/Aligned.usda` 合并为一个、标为两份拷贝。两份 wrapper 相同而相对引用的 `Aligned.usd` SHA 不同，本轮已调用原生发现函数复现。依赖闭包不同的资产不能因 wrapper 相同被合并；在修正前不将完整库挂成已验证的资产列表。完整背景还引用 sibling/common/HDR 等资源，须保留依赖闭包，不能按单对象目录复制。

## 后续实施顺序

系统级实施顺序以[源码复核的第八节](ENVIRONMENT_EMBODIMENT_SOURCE_REVIEW_20261020.md#八实施顺序与验收)为准，先验证现有灵巧手在公共场景下的坐标与执行等价性。下面列出资产/新本体支线的依赖顺序，可与该场景工作并行；不会单靠新本体导入替代系统闭环。

1. **资产身份与材质样例。** 修正带相对引用的 USD 去重，沿现有编辑器导入完整的少量 benchmark 对象；核对贴图、单位/轴、材质和碰撞，保留源 SHA，不重写素材库。
2. **G1 夹爪基础验收。** 基于已有导入报告解决碰撞分解/过滤与求解器构建问题，明确惯量及初始目标适配；验证关节坐标与动态约束后，再做限位内开合、腕端定位和简单抓取/释放。验证前不加入可执行任务下拉框。
3. **G2 同任务验收。** 与 G1 和现有灵巧手共同构成可迁移 demo 的最低矩阵；严格复现按源本体验收。沿用任务语义和对象关系，单独说明底盘固定或移动配置，重新做本体规划和执行；不直接移植 G1 或 Sharpa 关节轨迹。
4. **小批量随机化。** 先固定动作与场景几何比较材质/HDR，再增加对象布局和干扰物；涉及碰撞、质量、几何、底盘/对象位姿的变化重新做物理与任务验收。保留 seed、资产 SHA、材质/灯光选择、场景和相机参数。
5. **统一记录与展示。** 标明严格复现或可迁移任务；后者同时列出两个夹爪本体和原灵巧手的验收结果，沿用现有失败诊断和 Episode 记录入口；每个本体单独记录动作语义/维度。三者全部通过才显示可迁移 demo 整体完成。严格复现按原本体及源场景证据验收。界面展示真实接受与拒绝样本，不以渲染完成替代任务成功。

截图中的多背景演示是后续展示目标；当前没有宣称已生成 G1/G2 任务数据或完成跨本体训练导出。
