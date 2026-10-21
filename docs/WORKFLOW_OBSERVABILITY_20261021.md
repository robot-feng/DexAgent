# EmbodiForge 流程工作区与可替换模块边界（2026-10-21）

本次交付是已有运行的逐环节结果工作区，以及独立模块报告接入。没有启动 FlashCHORD 训练，没有把 HumanEgo 迁移到 Newton，也没有新增三本体任务通过证据。

## 1. 用户可见流程

8765 的运行详情默认打开「完整流程」，按环节查看输入/输出、结果预览、检查证据和原生文件：

输入与校验 → 场景与资产重建 / 任务理解 → 可选轨迹提取 → 重定向与动作生成 → 物理执行与验证 → 数据生成与扩增 → 策略训练 → 闭环评估与真机反馈。

这是观察顺序，不是强制调度依赖：场景采集和操作示范独立；已有资产可复用；指令驱动任务不要求参考轨迹。当前尚未接入的训练执行器和未记录的独立报告明确说明，不显示虚构进度。

### 背景清理的归属

背景与对象分离是**场景重建的内部步骤**，不是与场景重建并列的主流程：

1. 确认扫描中的静态环境与机器人、手、可移动对象。
2. 分割并跟踪需从背景中去除的实体。
3. 利用多帧观测及补全生成干净背景，检查遮挡区域。
4. 静态环境和操作对象分别重建为可复用资产，再进行组合和物理检查。

现有清理工具主要负责 mask、视频补洞与背景构建；其完成不代表对象资产或仿真接触已经通过。当前界面明确这一点，不宣称移除的对象已自动保留为合格资产。

导航改为「场景与资产 → 背景与对象分离」，主地址 `/scenes/separation`。旧 `/background-cleanup` 兼容，任务、模型选择、审核和日志功能保持。

## 2. 实际实现与证据语义

- 复用 `Stage / Report / Artifact / Episode`、`GateReport`、媒体播放器及已有文件预览，没有新增任务注册表或执行调度框架。
- 场景、动作等环节分别查询自己的产物范围。新增 artifacts API 的 `folder` 参数为**运行相对目录**；空值只选根级文件。原 `q` 继续是文本搜索，不用于证明阶段归属。范围过滤在分页前执行。
- 根级后端报告可以映射到「物理执行与验证」，但不会把 Stage3 通过转换为独立执行通过。报告仍显示 `scope` 和 `task_success_evaluated`。
- 预览排除历史尝试、`previous-*`、恢复目录；动作阶段不默认展示下游 Stage4 随机化媒体。文件列表仍可查看原始材料。
- 图片和视频独立查询，避免几百个 JSON/mesh 将视频挤出第一页；每种媒体最多预览前 200 项并提示截断，文件分页可继续访问。
- 查询键绑定运行、逻辑环节、实际目录和报告 SHA，阶段来源切换会重置分页/预览；源报告 running 或有 progress 时轮询，但不把它当成已确认存活进程。
- 阶段目录产物是来源定位，不是逐文件验收身份绑定。当前检查仍以报告的 checks / evidence 为准；没有给目录里的每张图片追认“已通过”。
- 历史四阶段聚合结论保留原义，不因新增训练报告而升级为策略训练或真机闭环完成。

## 3. 模块接入契约：复用现有报告，而非任意 Python 插件

现有索引增加以下**可选**阶段目录：

| 目录 | 职责 | 最低证据要求 |
|---|---|---|
| `reference/` | 人体/手/对象参考轨迹提取 | 输入身份、时间基准、坐标/尺度、配准、有效帧覆盖 |
| `execution/` | 独立物理执行 | 机器人/场景/动作身份、实际后端、物理记录、任务判据 |
| `training/` | 策略训练 | 数据版本、有效配置、算法/模型身份、checkpoint、训练指标 |
| `evaluation/` | 策略闭环评估 | checkpoint、场景种子、目标机器人、实际 Episode、成功/失败及评估范围 |

每个目录使用已有 `stage_report.json`、`progress.json`、`reports/attempts/*/stage_report.json`。原 `stage1`–`stage4` 和根级 `stage_report.json` 保持兼容。

生产者沿用 `agent.run_reports.make_report / finish_report / atomic_json`：

- `inputs`：原视频/资产/任务版本及必要的 hash；
- `config_sha256`、`model_identity`：有效参数与实际实现身份；
- `artifacts`：实际存在的产物引用；
- `checks`：真实测量、阈值与证据；
- `metrics.scope`、`metrics.task_success_evaluated`：训练、跟踪、导入、仿真任务、真机评估不能混写；
- `failure / next_action`：保留失败及可执行诊断方向。

缺少检查、未知 schema 或不一致结论仍由现有解析器判为证据不完整。单独 training 运行也可被发现，但命令退出、checkpoint 存在和任务成功仍是不同概念。

这提供了**结果和验证接入点**。实际执行器替换还需沿既有 execution 子进程/工具接口补薄适配器，并验证动作语义、坐标、依赖环境和恢复协议；本次没有新增一个可以任意启动训练器的选择框。

## 4. V2D / FlashCHORD 与 HumanEgo 审查结论

### V2D

官方 [v0.4.0 发布说明](https://github.com/nvidia-isaac/video_to_data/releases/tag/v0.4.0) 确认 FlashCHORD 使用 Newton / Warp / JAX；原端到端 GR00T 流程仍是 Isaac Lab，不能把两条通道混称为 Newton。SDF 穿透检查值得用于凹形/空心物体，但几何检查并不代替接触任务验收。

[FlashCHORD README](https://github.com/nvidia-isaac/video_to_data/blob/main/robotic_grounding/flash_chord/README.md) 将机器人查看、场景查看、运动学参考重放、动力学重放、训练、评估和策略查看分别提供入口。借鉴重点是每一步有独立可观察结果与验收范围，而非强制所有模块依赖同一个 Python 环境。

[集成说明](https://github.com/nvidia-isaac/video_to_data/blob/main/robotic_grounding/flash_chord/V2D_INTEGRATION.md) 的互通边界是参考运动 Parquet 和场景资产；当前 G1 + Dex3 案例不是本地 G1 Omnipicker。数据格式和本体都需要独立适配。

[`tools/e2e/cli.py`](https://github.com/nvidia-isaac/video_to_data/blob/main/tools/e2e/cli.py) 和 [`runner.py`](https://github.com/nvidia-isaac/video_to_data/blob/main/tools/e2e/runner.py) 展示阶段 commands/inputs/outputs、manifest、尝试、日志与恢复签名。可借鉴“输入或配置变更不能静默复用旧结果”；其目录 fingerprint 不是逐文件内容 hash，命令完成也不是物理任务通过。EmbodiForge 已有执行与报告机制，宜扩展现有证据身份校验，不复制第二套 runner。

### HumanEgo

本地审查 checkout：`239affbc24b8b4a3ab2987cdd76436bea7ca9411`，只读未改动。

- `simulation/humanego_sim/env.py` 使用 `mujoco.mj_step`；`evaluate.py` 的并行是独立进程环境，不能称为 Newton GPU 多世界。
- `runner.py` 的可注入 policy/env、`EpisodeResult` 与独立 `replay.py/evaluate.py` 值得复用为执行适配边界。
- `episode.json`、策略视角和预测轨迹可作为可视化产物；记录回放用 `mj_forward` 渲染，不会重新执行控制。
- 现有快照缺少精确恢复所需的完整速度/控制器/求解器状态，不能直接当成本项目原生可恢复 Episode。
- 默认感知包含仿真真值路径；`dinosam` 分支与真值需要明确区分。
- `inference/interfaces.py` 模板夹爪归一化符号与 `simulation/humanego_sim/driver.py::get_gripper_q()` 的测量符号相反。替换机器人前必须明确动作和测量的单位、开合方向及相机外参，不能只替换网格或机器人名字。

官方入口：[HumanEgo simulation](https://github.com/TX-Leo/HumanEgo/tree/main/simulation)。以上具体实现结论基于本地源码，不推断所有上游版本相同。

## 5. 验证与限制

后端：新可选阶段发现/报告替换/未知 schema 与严格目录范围测试，连同原工作台测试 41 项通过。

前端：生产构建通过；13 项浏览器检查通过（workflow/workspace/input 共 8 项，原工作台清理模型/真实视频与证据/失败后端/手机布局/键盘导航共 5 项）。其中原视频测试的旧“场景重建”按钮定位在新标签下超时，更新为“场景与资产重建”后单项重新通过；没有放宽检查。初次新增测试 fixture 缺少 reports 数组、使用了不存在的报告 CSS 类，也已纠正。新旧报告切换测试实际等待运行轮询，确认预览来源随 backend→execution 改变。

实际浏览器截图：

- [桌面流程工作区](references/workbench/workflow-20261021/scene-desktop.png)
- [窄屏布局](references/workbench/workflow-20261021/scene-mobile.png)
- [场景重建内的背景与对象分离](references/workbench/workflow-20261021/separation.png)

截图直接读取 8765 上的运行；场景预览图片完成加载后截图，无 JavaScript 错误，窄屏无页面横向溢出。服务在确认无活动编辑器、执行任务和子进程后更新，保留待审核 mask 记录，8766 未变更。构建仍提示既有 SeriesPlot 分包超过 500 kB，不影响本次验收。

仍未完成：FlashCHORD 执行器、HumanEgo 策略执行适配、提取过程自动独立报告、跨阶段原始产物的内容 hash 绑定、策略训练与真机评估闭环、同任务三本体物理验收。新增报告可见性不会改变这些状态。

后续实施：[场景重建内部结果与审核时钟](SCENE_RECONSTRUCTION_RESULTS_20261021.md)，包含显式清理关联和真实的未完成审核运行。
