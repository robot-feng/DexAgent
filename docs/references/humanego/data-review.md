# HumanEgo 数据采集、预处理与统一框架接入审读

审读对象：`/data/tzq/DexAgent/HumanEgo`，本地 HEAD `239affbc24b8b4a3ab2987cdd76436bea7ca9411`。这是静态源码审读，不是复现通过报告；没有下载数据/权重、安装依赖、训练、仿真执行或修改项目源码。论文、远端数据集及仿真训练由主审其他分工覆盖。

## 1. 最重要的结论

HumanEgo 对我们的最大价值是“移动相机的人手示范 → 稳定世界坐标手轨迹 → 对象关系表示 → 同类机器人动作学习”的完整数据组织思路。它的本地预处理并不是普通单目 MP4 自动生成可交互场景：默认入口需要 Aria VRS、MPS 相机轨迹和 MPS 手部追踪；它也没有从这里输出环境网格、对象完整碰撞网格或清除机器人后的三维背景。

最值得借鉴的实现包括：逐帧保存标定与相机运动；用手掌 MCP 基部定义稳定虚拟夹爪朝向；以对象及对象相对手的变换构建 ICT；同时保留原始图像、去臂图像、可视化和结构化位姿；对不同人手估计器采用相同中间结构。不要直接继承其静态锚点、抓取锁定、自动结束标记和部分宽松成功统计作为我们的物理真值或任务通过证据。

## 2. 覆盖范围

已阅读以下全部文件的可执行实现、数据结构、调用关系及相应配置；长文件以保留原始行号、去除重复 docstring/注释的视图阅读，并补读关键原文与图形输出辅助代码。未声称逐字阅读每段重复注释或第三方模型内部实现。

- 文档：根 README、datacollection/README、preprocess/README；LICENSE 的许可证名称、声明及核心许可段落。
- 采集：`datacollection/AriaMPS.py`。
- 编排：`preprocess/Preprocess.py`、`RobotPreprocess.py`、空的 `__init__.py`。
- Aria 相机/SLAM：`AriaCam.py`、`AriaCamTypes.py`、`AriaSlam.py`、`AriaSlamOps.py`、`AriaSlamTypes.py`。
- 手部：`AriaHands.py`、`AriaHandsTypes.py`、`AriaHandsOps.py`、`AriaHandsOptimizer.py`、`MediaPipeHands.py`、`WiLoRHands.py`、`HaMeRHands.py`、`HandTrackingComparison.py`。
- 阶段：`AriaPhases.py`、`AriaPhasesOps.py`、`AriaPhasesTypes.py`。
- 视觉/对象：`DINOSAM.py`、`DINOSAMOps.py`、`KptsSelector.py`、`CoTrackerOffline.py`、`CamTriangulator.py`、`OrientAnything.py`、`DepthLifter.py`、`Lama.py`、`VisualKpts.py`。
- 输出：`DatasetGen.py`、`RobotDatasetGen.py`。
- 公共组件：`utils/ict.py`、`utils_io.py`、`utils_math.py`、`utils_media.py`、`utils_vis.py`。
- 配置：`cfg/preprocess/base` 全部 15 个 YAML 与 `tasks/{serve_bread,water_flowers}.yaml` 全部有效配置。
- 对接参照：当前项目 `tool_library/skills/read_egodex_episode.py`、`place_egodex_camera_for_reach.py` 的移动相机和重定位实现；`tool_library/common/rigcam.py` 轨迹入口。

## 3. 实际输入和数据流

### 3.1 Aria 路径

1. `AriaMPS.py:73` 构造 `aria_mps single` 云处理命令；`113` 后整理产物并移动原始 VRS 到样本目录。`mps_check:160` 检查必需路径是否存在，不校验轨迹质量。
2. `Preprocess.py:129` 创建 VRS、MPS 与 hand_tracking CSV 读取器。`139` 用 `len(rgb_tss)-len(hand_results)` 假定手结果覆盖 RGB 尾部；随后 `AriaHands.py:124` 按序 zip。这是长度对齐，不是手/相机时间戳容差匹配。
3. `AriaCam.py:154` 建线性相机标定并顺时针转 90°；图像、K 与相机坐标旋转需要整体保留。`261` 取 RGB corrected timestamp，`350` 用 `T_world_camera = T_world_device @ T_device_camera`。输出 `idx, ts(ns), K, d, c2w, c2d, d2w`，并重编号帧 idx；均匀 fps 由平均时间间隔取整获得（`414`）。
4. `AriaHands` 将设备手关键点变到相机及世界，保留 21 关键点、置信度及人手关节角。手指角为度，不能直接当 robot joint action。
5. `AriaPhases` 根据运动、手部等信号切阶段；`Preprocess.py:240` 提取操作段及前面的对象观测段。对象观测不足时回借早期操作帧，可能违背静态三角化假设；不是独立完整的扫描建模管线。
6. GroundingDINO + SAM2 图像分割 → 轮廓采样点 → CoTracker 跨帧二维点 → 已知 MPS 相机位姿的多视角三角化 → 初始对象参考框架。
7. LaMa 对每帧臂掩码做二维补绘；虚拟夹爪与对象关键点叠加仅用于策略图像表示和可视化。
8. DatasetGen 输出每帧 training_data.json；对象后续姿态来自静态初始化加启发式抓取锁定，不是重新估计的全视频真实 6DoF 轨迹。

### 3.2 手参考系与平滑

`AriaHandsTypes.py:82` 的虚拟夹爪原点是拇指/食指尖中点。朝向由拇指 MCP、食指 MCP 与腕部计算稳定正交基，不用闭合时退化的两指尖连线当主轴。这个设计值得保留，但其原点不是机器人腕部/TCP；必须有显式手参考框架到目标夹爪工具框架的映射。

处理包括置信度过滤、短段删除、短缺口插值、Savitzky–Golay/EMA 平滑。配置中的速度/角速度限值主要参与显示诊断；`AriaHandsOptimizer.py` 并没有执行机器人关节限速、碰撞或动力学约束。`AriaHandsOps.py:902` 的 GOOD/EXCELLENT 主要是检测覆盖率，不能代表位姿精度。

替代方法接口存在，但 `Preprocess.py:625` 后主流程中替代手追踪和比较调用默认注释掉了。MediaPipe 以手掌尺寸先验估绝对深度；WiLoR/HaMeR 使用学习到的手模型相机平移并可回退手掌估深。它们仍复用 Aria 标定/相机轨迹文件，没有补齐普通 RGB 的公制 SLAM。HaMeR 可回退 MediaPipe-only，应记录真正执行的方法而不只看输出文件名。`HandTrackingComparison.py:270` 以 MPS 为参照而非真值，jerk 单位是 mm/frame³，不是 SI 的 m/s³。

### 3.3 对象生成和追踪边界

`DINOSAM.py:75` 使用 GroundingDINO 和 **SAM2ImagePredictor**，并非 SAM 视频传播，也不是 DINOv3。`180` 后按文本提示合并所有命中的 mask；同名多个物体可能成为一个对象掩码。KptsSelector 按最大外轮廓等弧长采样，默认 20 点，不提供语义一致关键点。

CoTracker 使用 cotracker3 offline 模型，缩放/letterbox 到 640，100 帧片段、99 步长衔接；输出各对象 tracks/visibility，但结果数组本身不含独立帧时间戳映射，依赖编排时的同序列表。

`CamTriangulator.py:64` 做归一化 DLT；`105` 对点做固定相机的 Huber BA，不共同优化相机/尺度。假定同一物体关键点在所选观测段静止。没有强制的基线/视差、重投影残差、几何退化验收门限。`275` 后由点云中心和 PCA 或 OrientAnything 生成一个初始对象位姿。输出稀疏点、坐标轴以及初始 `object_to_cam0_matrix`；PLY 的球/箭头是可视化，不是可交互资产几何。

`OrientAnything.py:400` 的异常回退可返回完整单位 4×4 矩阵，连已知平移也丢失。该回退必须显式失败/未知，不能在接入时作为合格对象定位。

### 3.4 背景去除到底做了什么

`Lama.py:210` 实际读取 **mask_arm**，输出 **rgb_WoArm**。它没有在该函数使用对象掩码去掉场景所有操作对象；有些上层注释描述“去除物体”与实际不同。实现是 ONNX LaMa、512×512 输入、mask 膨胀、单帧补绘后模糊边缘融合。

这能改善图像中的人臂外观，但不保证跨帧纹理一致，也没有更新深度/mesh/碰撞。它不能直接修复用户此前看到的已融合在 USDZ 背景中的机器人，更不能作为静态背景的三维清理完成证据。

### 3.5 DatasetGen 的抓取锁定与锚点

`DatasetGen.py:98` 选排序首个对象为固定虚拟锚点；`109` 将对象从 cam0 变回 MPS world。输出实体位置仍在 MPS 世界坐标，不是全部已转换到对象中心世界；对象中心视角主要发生在可视化或 ICT 编码中。

`439–493` 实现的对象运动是：手先出现过松开状态 → 抓取上升沿 → 找 0.20m 内最近非锚点对象 → 记录 hand→object 固定变换 → 抓住期间对象跟随手 → 松开后固定在最后姿态。没有滑移、掉落、双手所有权协调或逐帧观测校正。

首对象永不锁定。`tasks/serve_bread.yaml:6` 把 bread 设为 obj1，所以输出中它一直作为静态参考，即使实体面包实际可能移动。应把“初始虚拟锚点”与“同名物体当前物理姿态”分为两个概念；不要误把这个设计直接解释为真实对象追踪。`water_flowers.yaml:19` 禁用两手锁定，所有对象标签保持静态。

### 3.6 输出 schema / ICT

每帧 `training_data.json`（`DatasetGen.py:566`）含：
- metadata: idx、ns timestamp、w/h、fps、K、当前 c2w、anchor_key、is_finished、world_transforms。
- world_transforms: 固定 cam0 与固定 anchor；当前 moving camera 是 metadata.c2w，不能混淆。
- obs: RGB、mask_arm、mask_arm_and_obj（字段名 mask_obj_path）、去臂/夹爪叠加图像的本机路径。
- entities.hands: 每手 `T_hand_to_world` + grasp；objects: `T_obj_to_world` + is_dynamic；可选替代手方法。

`is_dynamic` 实际表示当前是否被锁定，并不表示资产是否能动或真实运动。`is_finished` 是阶段/尾部自动标记，不是任务成功判定。绝对路径需重定位；路径存在也不证明其内容有效。

`utils/ict.py:30` 单手 token 是 20 维：type(1) + reference-relative pose(9) + hand-in-entity relative pose(9) + grasp/flag(1)，默认最多 8 token。9D=position3+rotation6，rotation6 是 `R[:,:2].reshape(-1)` 行交错展平约定，应原样保留。`camera_frame` 选的是 **固定 cam0**，不是当前逐帧相机；`anchor_frame` 选固定初始 anchor。对象 token 类型只有 anchor/other，不自动携带类别语义或任务成功谓词；超过容量截断，不是通用 TaskSpec。

### 3.7 Robot 分支

RobotPreprocess 是已有 robot session + RGB/深度/EE状态路径，默认相机固定。`DepthLifter.py:88` 把 uint16 深度当 mm 除以 1000 转米，要求深度已与 RGB 对齐；不是单目估深模型。默认以首批有效观测中位数初始化对象。RobotDatasetGen 用相机框架当世界，读 `T_ee_in_cam`，gripper 做 `1-q`/阈值归一，保留可选关节状态；不是可直接用于我们三本体的控制执行适配。

`RobotDatasetGen.py:619` 腕相机 transform 为简化直接写 cam0，不能当真实腕相机外参；尾部若干帧自动 finished 也不是物理通过。原始动作命令/控制器语义不由这套标签自动恢复。

## 4. 必须在接入前处理的源级风险

以下为静态证据；未运行受影响样本，所以区分可见实现事实与待验证影响。

|优先级|源码位置|发现与可能影响|
|---|---|---|
|P0|Preprocess.py:139；AriaHands.py:124|相机/手按长度尾部+zip对齐，需用采集时间戳核验，不能默认同步。|
|P0|AriaCam.py:350–392|MPS 位姿缺失可回退单位阵；精确单位 d2w 又被视为缺失。有效初始单位位姿与失败混淆。|
|P0|CamTriangulator.py:144 起及 :474；Preprocess.py:674|全局 manager 缓存 objects_3d；batch 只 reset CoTracker/VisualKpts，不 reset 三角化。第二 session 可能沿用旧对象/缺少新输出。|
|P0|DatasetGen.py:439–493|锁定标签不是独立对象观测，固定锚点不等于真实物体不动；需要来源/不确定性标注。|
|P0|AriaPhasesOps.py:447–456|stage_window_check 不管 require_order/allow_missing，始终 passed=True。|
|P0|DatasetGen.py:549/591|无手帧增加 discarded_no_hand 仍写文件并 valid++；计数不能作有效样本验收。|
|P1|Preprocess.py:240–296|回借操作帧后拼接可重复；raw_manip 为空时 split_idx 可能未赋值，警告后继续。|
|P1|Preprocess.py:object_ref_img_path；CoTrackerOffline.py:ref_idx|ref_idx=-1 对象参考图按前置观测子列表取最后，tracker 按拼接全列表取最后；默认0不触发，非默认会错配。|
|P1|AriaHands.py:434–459|移动相机下在相机坐标直接插值；新插值手未显式计算所有 raw-world 字段，后续优化读默认值的影响需定向测试。|
|P1|CamTriangulator.py:64–126|DLT 条件数未用于门控，BA 无完整几何验收；低视差结果可被输出。|
|P1|OrientAnything.py:400–406|模型异常返回单位姿态，已知平移丢失。|
|P1|DINOSAM.py:180–208|文本同类对象 mask union，不具备稳健实例身份。GroundingDINO 显式 BGR→RGB，SAM set_image 直接 BGR，需结合依赖 API 核验颜色约定。|
|P1|RobotDatasetGen.py:619–626|腕相机外参占位为 cam0，需避免当标定真值。|
|P2|VisualKpts.py:260|可选 object trails 期待顶层 tracks/visibility，实际 CoTracker 是按obj嵌套；默认trail0掩盖。|
|P2|Lama.py:267|子进程 READY 等待对 EOF 未明确退出，异常启动可能挂起。|
|P2|Preprocess.py:691|逐样本异常捕获继续并输出 Done；命令退出成功不能证明所有 session 成功。|

## 5. 对 EmbodiForge 最小接入建议

1. **先读产物，不移植整条新管线。** 给现有 Demo 输入边界添加 HumanEgo 来源适配，支持已处理 session 的原始 RGB、AriaCam、AriaHands、training_data。记录模型/配置及观测、插值、锁定、补绘的来源。默认不复制 HumanEgo 的全局 worker 架构。
2. **复用当前移动相机契约。** `tool_library/common/rigcam.py:71` 已支持 camera.json + trajectory_npz 的 `camera_world, R_world_rig, origin_world, frame_idx`；`read_egodex_episode.py:160–181` 已输出类似 archive。HumanEgo 可把每帧 c2w 和测得 ns 时间戳映射为该契约，增加或保留真实 timestamps_s，不能再用 idx/fps 覆盖。当前 EgoDex 接口假设 ARKit Y-up 和指尖百分位桌面，因此不能仅改文件扩展名套进去；HumanEgo 的重力方向、桌面和 scan registration 必须来自它的真实标定/场景对齐。
3. **Scene 独立。** HumanEgo 初始稀疏对象点作为定位/关联参考；完整场景 mesh、材质、碰撞、对象资产仍走现有 SimFoundry/RSS 能力。环境扫描不是必须重复一次，人演示与已有 Scene 应通过显式 registration 绑定。
4. **Task 保留本体无关关系。** 借鉴 ICT 的 hand/object relative transform 和抓放事件形成参考约束，接入现有 semantic task 与成功谓词；不新建第二个 TaskSpec，不用 is_finished 代替任务成功。
5. **Robot 分别适配。** 人手中点/掌轴 → 目标 TCP 的变换单独标定；保留 21 点用于 YAM+Sharpa，不把全部手示范提前压缩成二指开合。G1/G2 可使用夹爪参考，但必须分别规划、真实后端物理执行、验收和录 Episode。
6. **质量先于扩增。** 首个样本核验时间/帧索引、相机与手重投影、尺度和静态点视差、锚点/对象区分，再比较原始与补绘图。UI 展示“测得/推断/补绘/未验证”，避免把漂亮叠加图当成重建或控制通过。
7. **保持任务模式。** Faithful 模式保留来源本体/场景；transferable 模式才使用统一桌面并覆盖既有 YAM+Sharpa、G1 Omnipicker、G2 Omnipicker 的同一任务。HumanEgo 参考不能自动豁免其中任一本体。

建议第一步交付是“一条 HumanEgo Demo 可核验导入 + 逐帧相机/手/对象证据浏览”，随后才进行本体执行。无需为了借鉴这篇工作先另建一套场景、机器人注册表或复刻其训练栈。

## 6. 依赖与许可事实

本地 LICENSE 标明 PolyForm Noncommercial 1.0.0；不能因为代码能克隆就称为无用途限制许可。这里只记录仓库声明，不作法律结论。Aria MPS、MANO、HaMeR/WiLoR、SAM、GroundingDINO、CoTracker、LaMa、OrientAnything 的代码/权重/服务各有独立来源和条件，仓库根许可不自动覆盖它们。数据集许可与公开文件范围由主审另行核验。
