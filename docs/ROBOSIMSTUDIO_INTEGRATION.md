# DexAgent 与 RoboSimStudio 的结合方案

记录日期：2026-10-15。包含两个仓库的接口评估、本地适配和实际运行验收。

## 当前状态与选择

- 官方仓库已完整克隆到 `/data/tzq/DexAgent/robosimstudio`。
- 上游地址：<https://github.com/robosimstudio/robosimstudio>。
- 当前版本：`73739abe212f963e8a9f5862b27bca370df091a7`，`main`。
- 代码和资源约 142 MB，保留独立 Git 历史。DexAgent 的本地 `.git/info/exclude` 忽略该目录，防止误提交嵌套仓库。
- 按用户选择，以一个统一运行环境、一个主流程为目标。
- 本次启动的独立环境克隆已停止并清理。现有 `/data/miniconda3/envs/dexagent` 已就地升级为 Python 3.12.14，两个项目共用该环境。
- 第一条后端验收链路已经实现：仿真 → 控制和形变记录 → 离线重渲染 → 逐项校验 → DexAgent JSON 报告。
- `--backend-eval` 已支持 DexAgent 的完整 YAM + Sharpa 场景、方块接触抓取和标准杯子入碗 episode 对照。标准 episode 的原任务目标在 RSS 中重新验收；上游视频重建与轨迹提案复用并明确标记。新视频冷启动 Stage 1–4 与 Sharpa 毛巾折叠仍未迁移。

2026-10-16 的逐模块复查、真实迁移问题和融合流程见 [融合复查文档](DEXAGENT_RSS_FUSION_REVIEW.md)。本轮按用户选择先运行已有标准参考。

## 当前可运行入口

```bash
python run_dexagent.py dexagent.yaml --backend-eval rigid
python run_dexagent.py dexagent.yaml --backend-eval towel
python run_dexagent.py dexagent.yaml --backend-eval sharpa
python run_dexagent.py dexagent.yaml --backend-eval reference --reference-run runs/e2e_top_smoke_20261013

# 指定 GPU 和新的输出目录：
python run_robosimstudio.py towel --gpu 2 --output runs/my_towel_check
python run_robosimstudio.py sharpa --gpu 2 --output runs/my_sharpa_check
```

每次运行都先写 `running` 报告。失败写明原因并返回非零；完成后保留关节目标、执行状态、完整布料粒子、拓扑、原生 episode 索引、视频帧到控制帧的映射、证据哈希及代表帧。
毛巾的单向耦合记为 warning，真实材料精度记为未评估；图像来自 tiled 调试渲染器。
报告中的 `full_pipeline_complete: false` 明确表示没有完成从人类视频到数据集的全流程验收。

现有 Stage 4 的 Cycles 渲染已支持调用 `/data/tzq/tools/blender-5.0.1-linux-x64/blender`，入口仍在原流程内。
这是一个渲染程序，共用主 Python 进程发出的任务；未增加第二个 conda 环境。
该二进制压缩包 SHA256：`8019580ee1b7262e505f4196a00237ccf743c88d205b38d34201510676e60b09`，已对照 Blender 官方校验文件。

环境清单、安装日志和测试日志保留在 `runs/robosimstudio_setup/`，升级前清单在其 `baseline/` 子目录。
可复用的依赖约束在 `requirements-robosimstudio.txt`，主环境采用 MuJoCo 3.12.0、Newton 1.6.0、Warp 1.18.0、NumPy 1.26.4、JAX 0.6.2 和 PyTorch 2.5.1。
JAX/JAXLS 使用明确版本，兼容现有 NumPy 1.x 感知链路；SuperDex 1.0.0 也安装在同一环境。

在另一台机器重建时，先在现有 Python 3.12 环境中执行以下命令；本机已有该 checkout，无须重复克隆：

```bash
git clone https://github.com/robosimstudio/robosimstudio.git robosimstudio
git -C robosimstudio checkout 73739abe212f963e8a9f5862b27bca370df091a7
pip install -r requirements-robosimstudio.txt
```

## 本机已验证的结果

### 2026-10-16：标准杯子入碗对照

- [最终报告](../runs/rss_reference_cpu_final_20261016/stage_report.json)：37/37 必需检查通过，三个原始子目标均通过；总状态 `passed_with_warnings`，警告仅表示上游参考复用。
- [同相机初始对照](../runs/rss_reference_cpu_final_20261016/frame0_compare_rss.png)：人类参考、原 MuJoCo、RSS、叠加图。两模拟器的杯子/碗轮廓 IoU 分别为 `0.92562 / 0.96429`。
- [动态对照视频](../runs/rss_reference_cpu_final_20261016/comparison.mp4)：两个模拟器使用相同物理时间；人类视频列仅为重采样视觉参考。[映射](../runs/rss_reference_cpu_final_20261016/comparison_frame_map.json)明确记录这种区别。
- [工作台视频](../runs/rss_reference_cpu_final_20261016/simulation.mp4)和 [实际轨迹](../runs/rss_reference_cpu_final_20261016/trajectory.npz)：7850 个物理子步、15.7 秒，保留 nominal/actual 控制；原轨迹有 472 个检查帧。
- 最大机械臂差 `0.022135 rad`、手指差 `0.017522 rad`；杯子最大位置差 `0.006110 m`、末位置差 `0.005529 m`；碗最大位置差 `0.000146 m`。
- [物理检查](../runs/rss_reference_cpu_final_20261016/physics_report.json)：手与物体最大穿透 `0.17 mm`、物体间最大穿透 `0.48 mm`，末帧没有悬空物体。

导入时发现并修复了复合网格质量/质心变化；直接重放原力矩失败后，加入有力矩上限的机械臂反馈。原始开环失败保留在 `runs/rss_reference_cpu_20261016_v4/`。以上数值描述后端执行差异，不是单目或真机误差；本轮不标为新的完整视频重建通过。

### 较早的环境与后端验收

| 验证 | 当前结果 | 证据 |
| --- | --- | --- |
| 环境与模型预检查 | 通过；使用合成视频检查环境，不宣称任务复现 | `../runs/robosimstudio_setup/preflight.json` |
| 依赖一致性 | `uv pip check` 通过 | 当前共享环境 |
| SAM3 / LaMa | 两者实际前向推理成功；这是兼容性验证 | `../runs/robosimstudio_setup/perception.log` |
| DexAgent 报告和预检查 | 26 项通过 | `../runs/robosimstudio_setup/agent-report-tests.log` |
| 接触默认参数 | 7 项通过 | `../runs/robosimstudio_setup/contact-regression.log` |
| 原 Stage 4 物理回放 | 7 项通过 | `../runs/robosimstudio_setup/runtime-replay-tests.log` |
| Stage 4 报告与恢复 | 16 项通过 | `../runs/robosimstudio_setup/stage4-report-tests.log` |
| Blender 独立程序调用 | 渲染并解码 4 帧 RGBA | `../runs/robosimstudio_setup/blender-smoke.log` |
| RoboSimStudio fast | 1678 项运行，26 项跳过，其余通过 | `../runs/robosimstudio_setup/rss-fast-tests.log` |
| 毛巾接触专项 | 6 项通过；检查抓取保持、抬升、桌面穿透、释放和最终折叠 | `../runs/robosimstudio_setup/towel-contact-tests.log` |
| SuperDex | 60 个节点的软体构建并静置 30 步，数值有限；未验证抓取或真实材料 | `../runs/robosimstudio_setup/soft-probe.json` |

最终验收产物：

- [刚体报告](../runs/rss_rigid_final/stage_report.json)：9/9 项通过。
- [毛巾报告](../runs/rss_towel_verified/stage_report.json)：14 项通过，单向耦合为 warning，真实材料精度为 skipped；步骤状态为 `warning`，[运行汇总](../runs/rss_towel_verified/run_summary.json)状态为 `passed_with_warnings`。
- [毛巾视频](../runs/rss_towel_verified/simulation.mp4)：1103 个控制帧、1922 个布料节点，按模拟时间生成 15 FPS 视频，含逐帧映射。
- 毛巾折叠评分 `0.863589`（要求至少 `0.85`），跨度比 `0.538184`（要求至多 `0.6`），静置速度 `0.005020 m/s`（要求低于 `0.02`）。
- [验证汇总](../runs/robosimstudio_setup/validation_summary.json)：保存测试日志链接与最终产物哈希校验结果。

较早的一次毛巾后端运行评分为 `0.885018`，独立接触专项测试的运行评分为 `0.883586`。重复运行有差异，当前门槛裕量较小；这些分数描述模拟任务完成情况，不是现实形状或物理参数误差。

记录器连续性与来源证据相关的 40 项测试、报告/预检查及后端桥接的 31 项测试已通过；两组包含重复的桥接案例，不能相加当作独立测试总数。
为更新工具目录，还将分割后端注册器移到 `tool_library/common/`：它是内部组件，原来放在 `skills/` 会让严格的工具元数据构建失败。索引构建、对应测试和 SAM3 适配器测试已通过。

适配中发现并修复了两个版本接口问题：旧接触默认参数只允许 MuJoCo 3.10，新版本会直接跳过校准；Newton 1.6 使用 `joint_target_q`，已经移除旧的 `joint_target_pos`。
接触混合、禁用碰撞、显式柔顺参数和穿透测试均保持原门槛通过。
记录接口还增加了控制帧连续性校验，防止热身步骤漏记；原生 EpisodeStore 的结果索引和计数也已接入。

## Sharpa 本体接入与本轮验收

适配实现位于 [`agent/rss_sharpa.py`](../agent/rss_sharpa.py)，验收和录制仍使用 [`run_robosimstudio.py`](../run_robosimstudio.py)。没有增加环境、调度器或模型 API。

### 已实现的接口

- 使用现有 `compile_rigid` 生成物理执行树，双手固定在真实手臂法兰下。保留 12 个臂关节和 44 个手指关节，去掉规划用的虚拟浮动手腕。
- 逐名称映射关节、位置/速度索引和执行器。机械臂使用有上限的力矩命令，手指保留原位置执行器；缺失、未知、非有限数值及越界命令会报错。
- 采用 rig 到 RSS world 的恒等变换，保留桌面与两站安装位置。RSS 补充的地面位于 `z=-1 m`，写入 manifest。
- 保留源模型的 500 Hz 物理步长、求解器参数和 1559 对显式接触。修复 Newton 对 12 个微小虚拟连杆惯性的自动正则化，恢复源模型已通过 MuJoCo 编译的张量。
- 对照五组姿态的全部刚体 FK；检查质量、惯性、关节范围、armature、阻尼、执行器增益、力矩/控制上限及接触对。
- 热身前建立 home 状态，热身后恢复同一关节位置并清零速度，避免把热身产生的偏移当作初始状态。
- 录制的策略动作是按 `joint_names` 排列的 56 维关节位置目标。实际执行器控制另外保存，明确区分手臂 `Nm` 与手指 `rad`；不把 1 个夹爪开度解释成 22 个手指动作。
- 本轮采用有力矩上限的计算力矩控制器：位置误差/速度先转为目标加速度，再经源模型惯性矩阵和偏置力矩得到电机命令。它用于本体验收，尚未替换 Stage 3 的笛卡尔阻抗控制器。
- 恢复源模型的可视几何组并重建渲染 BVH，避免手指在 tiled 预览中消失。颜色为浅灰诊断配色；逐帧检查左右手可见像素，未宣称真实外观标定。

### 当前验收结果

运行产物：[报告](../runs/rss_sharpa_acceptance/stage_report.json)、[视频](../runs/rss_sharpa_acceptance/simulation.mp4)、[代表帧](../runs/rss_sharpa_acceptance/preview_00019.png)、[关节轨迹](../runs/rss_sharpa_acceptance/sharpa_named_trajectory.npz)、[本体与动作 manifest](../runs/rss_sharpa_acceptance/sharpa_manifest.json)。

**32 项必需检查全部通过；总状态为 `passed_with_warnings`。** 另有 1 项开环力矩回放警告和 1 项抓取任务未评估。模拟运行 2.5 秒，625 个控制帧，38 个视频帧，保存帧到轨迹索引的映射。

| 检查 | 实测 | 门槛 |
| --- | --- | --- |
| 五组 FK 的最大位置差 | `4.08e-7 m` | `<2e-6 m` |
| 五组 FK 的最大旋转差 | `5.99e-7 rad` | `<2e-5 rad` |
| 双臂最大目标跟踪误差 | `0.013043 rad` | `<0.05 rad` |
| 手指最大目标跟踪误差 | `0.009855 rad` | `<0.08 rad` |
| 最终回位误差 | `0.000280 rad` | `<0.025 rad` |
| 独立 CPU 反馈回放的最大关节差 | `5.44e-6 rad` | `<0.025 rad` |
| 同状态、同控制量的逐步动力学误差 | `4.35e-7 rad` | `<5e-5 rad` |
| 左/右手逐帧可见像素最小值 | `1653 / 3686` | 各自 `>=100` |

质量门槛位于 `dexagent.yaml` 的 `evaluation.robosimstudio.sharpa`。FK 等极小数值说明两个模型的表示一致，不能解释为单目重建、真实机器人或毛巾物理达到了该精度。

本轮控制接口、相机坐标、错误输入、报告和预检查共 **44 项 unittest 通过**，日志为 `runs/robosimstudio_setup/sharpa-regression-tests.log`。配置检查另外执行，不能与这些 unittest 混算。原刚体样例回归为 **9/9 通过**，产物在 `runs/rss_rigid_regression/`。

另从顶层 `run_dexagent.py --backend-eval sharpa` 新跑了一次，32 项必需检查再次通过。[验证汇总](../runs/robosimstudio_setup/sharpa_validation_summary.json)核对了两次 Sharpa 运行的产物哈希、48 个源资产哈希、记录 action 与关节目标的一致性，以及视频帧索引范围。

### 保留的历史失败证据与限制

初版直接使用原有力矩 PD 增益，在 250 Hz 采样下出现腕关节振荡：最大臂误差约 `0.207 rad`，验收失败，证据在 `runs/rss_sharpa_audit/`。改用考虑惯性的控制后，跟踪通过，但直接复制 GPU 力矩到 CPU 做开环回放仍产生约 `0.210 rad` 的偏差，证据在 `runs/rss_sharpa_control/`。

当前评估保留该开环警告，同时增加两种必需对照：CPU 按相同位置目标和相同控制器独立反馈执行；从相同状态出发使用相同原始控制量做逐步动力学对照。最终录制的策略 action 因此明确为 **关节位置目标**，原始力矩只作为同时保存的执行记录。不能把纯力矩序列跨后端复制当作已验证的确定性回放，也不能把这些产物直接标为可发布训练集。

一次中间预览能解码但不含双手，新增像素检查后正确阻断；定位到可视组与 BVH 缓存后修复。该失败报告在 `runs/rss_sharpa_visual_verified/`，没有覆盖成成功。

上述固定工作台结论是首轮验收的状态，已由下文的完整 MJCF 场景导入实现扩展。Stage 3 控制器迁移、从人类视频自动拟合该场景，以及训练数据发布仍需单独实现和验收。

## 完整任务场景与真实接触抓取

新增 [`agent/rss_scene.py`](../agent/rss_scene.py) 将完整 MJCF 导入 RSS，保留自由物体、被动关节与显式碰撞配对；[`agent/rss_grasp.py`](../agent/rss_grasp.py) 在 Sharpa 全场景上生成一个可重复的方块拾取、搬运、放置和释放任务。机器人使用原有有界电机和 44 个手指位置执行器。已有抓取 JSON 只提供初始手型和逆运动学种子；对象保持自由动力学，不用 weld、mocap、外力或仿真状态写入来伪造抓起。

预运行审计发现 MJCF 的 `solref="0.02"` 简写被 RSS/Newton 解释成 `(0.02, 0)`，而源 MuJoCo 编译值是 `(0.02, 1)`。运行前现将简写展开为语义等价的两值形式，并在门禁中比较源与求解器的接触参数。现有报告也包含整套场景的 63 个位置坐标、62 个速度自由度、56 个受控机器人关节、1609 组显式接触，以及每个任务阶段的单独状态与物理证据。

### 本机实际抓取验收

- [逐阶段最终报告](../runs/rss_grasp_acceptance/stage_report.json)：CPU MuJoCo 物理执行 `settle → approach → close → lift → hold → carry → place → place_settle → release → retreat → final_settle`，11 个阶段全部通过。总报告为 `passed_with_warnings`，仅因本次运行将独立 GPU 专项和负向控制检查记为 `skipped`；这两项分别在下方的单独运行中执行。
- [仿真视频](../runs/rss_grasp_acceptance/simulation.mp4)、[抽帧预览](../runs/rss_grasp_acceptance/preview_00102.png)、[视频帧映射](../runs/rss_grasp_acceptance/video_frame_map.json)：3400 个控制帧、13.596 秒模拟时间，视频通过解码、帧率和双手可见性检查。
- [抓取指标](../runs/rss_grasp_acceptance/grasp_metrics.json)与[逐帧状态/动作/接触轨迹](../runs/rss_grasp_acceptance/grasp_trajectory.npz)：最大物体抬升 `0.1027 m`、最大穿透 `0.000305 m`，独立 CPU MuJoCo 重放的物体位置最大差 `0.00225 m`。
- [GPU 物理验收](../runs/rss_grasp_gpu_acceptance2/stage_report.json)：同一完整任务的 11 个阶段也全部通过，CPU 对照回放物体最大差 `0.00183 m`。GPU 运行关闭源模型的 5 次 no-slip 迭代并将 Jacobian 从 auto 改为 sparse，报告保留这些求解器差异为 warning；CPU 验收保留源求解器设置。
- [负向控制报告](../runs/rss_grasp_cpu_negative_gate/stage_report.json)：命令保持手指张开，夹持阶段因没有对向接触而失败，抬升及其余下游步骤均为 `blocked`；`negative_control_rejected` 检查通过。这证明门禁不会把未抓住的样本记为成功。

这个方块任务验证了**完整 Sharpa 刚体场景中的实际接触抓取**。抓取位姿由已有手型 JSON 作为 IK 种子，物体则始终由自由关节和接触力驱动。它还没有将人类演示视频自动重建成此场景，也没有验证毛巾/衣物的柔性抓取；后续要把视频中的手/物体轨迹配准到本体场景，再为柔性材料补充可测参数和形变评估。

## 两个项目各自承担什么

DexAgent 继续承担视频输入、任务语义、子目标、工具调用、阶段门禁、报告与数据验收。
RoboSimStudio 作为物理仿真与交互调参的候选核心，逐步承担机器人、刚体、绳索、布料和柔体的共同仿真。
Codex 继续通过现有 external 文件请求/响应处理任务。

优先复用 RoboSimStudio 的 Scene / Sim / Ref、布料模型、记录器和渲染器。
现阶段接入核心仿真不要求引入另一个 agent 调度器或 Graph-as-Policy 依赖。

## 已在代码中确认的能力

| 能力 | 证据 | 对当前工程的价值 |
| --- | --- | --- |
| 布料网格与厚度模型 | `../robosimstudio/examples/01_cloth/cloth.py` | 薄膜模型描述面内拉伸与弯曲；slab 模型补充厚度和内部四面体，适合毛巾候选建模 |
| 毛巾折叠示例 | `../robosimstudio/examples/08_demos/fold_the_towel/` | 有抓取、搬运、释放、静置和折叠评分的完整示例 |
| 布料形状状态 | `../robosimstudio/robosimstudio/scene/ref.py` | `particle_q` 给出逐粒子三维位置；布料不需要伪装成单一刚体位姿 |
| 体积柔体 | `../robosimstudio/examples/02_soft/` | 有四面体软体与 SuperDex FEM 海绵路径，物体能够改变形状 |
| 交互调参和参考视频对比 | `../robosimstudio/robosimstudio_real2sim_agent/checks.py` | 可记录场景版本、轮廓/位置比较和运动反馈，供现有评估流程使用 |
| 原生轨迹记录 | `../robosimstudio/robosimstudio/record/store.py` | 保存动作、关节状态、模拟时间、物体与粒子状态、事件和相机视频 |
| 离线重渲染 | `../robosimstudio/robosimstudio/core/render/offline.py` | 回放已记录的刚体和粒子状态，生成与该轨迹对应的视频 |

这些能力意味着布料/柔体表示和调参接口更完整。实际精度、吞吐和稳定性仍需在本机与目标任务上复测。

## 在一个环境中解决依赖冲突

| 项目 | 迁移前 DexAgent | RoboSimStudio 要求/锁定 | 已采用的共同版本 |
| --- | --- | --- | --- |
| Python | 3.11.16 | `requires-python >=3.12`，SuperDex 额外包限定 3.12 | 3.12.14 |
| MuJoCo | `requirements.txt` 固定 3.10.0 | checkout 的 uv override 为 `~=3.12.0` | 3.12.0，原有接触与选定回放测试通过 |
| NumPy | `>=1.26,<2` | 核心依赖未固定上界 | 1.26.4，配合 JAX 0.6.2 |
| 感知模型 | SAM3-LiteText 和已有模型缓存 | 仿真核心不强制安装另一套分割模型 | 复用现有感知与缓存 |
| 渲染 | 当前 Blender 路径 | 默认 uv group 含较大的 RTX 依赖，另有 tiled 后端 | 首次运行显式选择需要的渲染依赖 |

统一版本前保存当前依赖清单和可复现基线。升级后，先验证导入、相机、机器人关节、刚体接触，再验证毛巾和现有小样本流水线。
Python 的本地扩展与解释器版本有关，升级至 3.12 时需要核对 PyTorch、OpenCV 等的对应 wheel；模型权重缓存可继续使用。
一个环境减少维护数量，但版本升级后仍须证明原有任务能够运行。

## 接口冲突的具体处理

### 坐标与相机

DexAgent 的 rig 原点是桌面上两站底座之间；RoboSimStudio 的构造位置使用 cell 工作台表面坐标，读取的状态是 world 坐标。
两者通过一个显式刚体变换统一，不能只复制 xyz。
MuJoCo 常用四元数顺序为 `wxyz`，RoboSimStudio 的姿态为 `xyzw`；交接时写明顺序并检查往返转换。
相机内外参、分辨率和畸变处理必须与源视频保持对应。

### 机器人和动作

双方都有 YAM，但本仓库配置的是 Sharpa Wave 灵巧手，RoboSimStudio 毛巾示例使用平行夹爪。
新项目的夹爪动作不能直接解释成 Sharpa 的 22 个手指关节命令。
先用上游夹爪复测布料能力，再明确导入目标机器人及其关节名、范围、安装位姿、碰撞几何、控制和 IK。
跨本体复用的是任务目标和接触意图，机器人动作要在目标本体上重新生成与验证。

### 布料状态和数据生成

正式交接至少保存：时间戳、关节名、控制命令、执行关节状态、刚体姿态、布料节点位置/速度、拓扑、相机、材料和耦合配置、源文件哈希。
布料重渲染需要完整的节点状态；采样后的少量节点不能默认当作完整网格回放。
当前 Stage 4 Blender 导出主要处理固定几何及逐帧变换，无法直接表达布料的逐顶点形变。
第一版布料视频优先使用 RoboSimStudio 原生离线渲染器，并让输出接受现有的视频和时序检查。
后续需要 Blender 视觉风格时再补充动态网格导出。

`EpisodeStore` 已定义基础记录格式；代码注释提到了 LeRobot 转换，但本次在仓库内未找到独立的 `export_lerobot` 实现。
训练数据导出需要另外核对/实现，不能仅凭注释宣称已经支持完整 LeRobot 数据集交付。
模拟器内部的物体/粒子真值用于评估与渲染；给策略的 observation 字段须由实际可用传感器定义。

### 门禁与精度

现有 Stage 2/3 验证器大多读取 MuJoCo 文件，直接接收 Newton 的数组会缺少正确物理解释。
复用现有报告结构，并为新后端增加必要的读取与独立检查；接入时保留失败、未执行和不支持的反馈。
毛巾至少检查：数值有限、静置、穿透/自接触、抓取与滑脱、释放、折叠覆盖/对齐、时序完整性和可见轮廓。
真实视频中的 SAM3 掩码继续使用来源校验；模拟器反馈不替代真实测量或相机标定。

## 毛巾示例的已知限制

上游 README 和示例代码明确记录了以下条件，接入时必须保留在报告中：

- 使用单向耦合：布料受机械臂作用，机械臂不接收布料的反作用力。
- 成功演示将毛巾内部剪切模量从材料参考值 100 kPa 调至 3 kPa。能折起来不等于材料已经匹配真实毛巾。
- 抓取位置、夹爪开度和搬运速度影响明显；其折叠分数距离通过阈值较近。
- 示例的门槛包括 `fold_extent <=0.6`、`fold_score >=0.85`、静置速度 `<0.02 m/s` 和释放。
  这些是示例任务的完成标准，不是仿真与真实视频的三维误差标准。
- 本机结果见前文报告；上游的材料参考量和任务门槛不能替代真实毛巾的测量。

RoboSimStudio 补充了布料物理模型与调参工具，单目遮挡和深度歧义仍需要标定、已知尺寸或额外观测约束。

## 推荐实施顺序

1. 已完成：统一 Python/依赖，验证上游刚体、毛巾及原有关键回归测试。
2. 已完成：上游布料构建、执行、完整形变记录、离线渲染与报告。
3. 当前已完成 YAM + Sharpa 的本体和自由空间动作验收；下一步补充保留自由物体的完整场景导入，并在该本体上验收刚体抓取，再测试布料接触。
4. 接入真实视频标定和 SAM3 掩码，比较初始状态、关键动作帧和最终状态，并记录材料估计的不确定性。
5. 在上述门禁通过后接入批量随机化、布料动态几何导出及训练数据转换。

每一步先产出可运行的小样本和检查证据，确认结果后再扩大任务范围。

参考：官方[代码仓库](https://github.com/robosimstudio/robosimstudio)与[论文](https://arxiv.org/html/2610.18835v2)。
