# 基于候选重新规划任务（2026-10-20）

## 使用入口与数据边界

按[工作台启动说明](../workbench/README.md#启动)启动服务后，打开 `http://127.0.0.1:8765/executions`，选择“基于已验收候选重新规划任务”。先读取候选对应的源任务输入，再启动新运行。工作台通过 `GET /api/workbench/v1/executions/tasks/{run_id}` 返回候选、候选验收报告和源任务输入的身份摘要；启动前会再次核对该摘要。读取后任何输入变化都会要求重新读取。

重规划只接受已通过全部必需 RSS 物理检查、且验收报告身份与候选一致的完整候选快照。新运行冻结候选目录、验收报告，以及源任务的视频、语义、Stage 2 场景和重建信息、相机与来源 recipe。它继承原任务意图和观测来源，使用新候选布局作为规划初态。

规划场景必须从原 Stage 2 proposal 构造。流程把候选中的自由物体初态映射到 proposal，再运行原生 `compile_rigid`；编译后完整 `agent.artifacts.model_digest` 必须与已验收 RSS 候选模型相等。不能把 RSS 刚体场景当成 Stage 3 proposal，也不能凭刚体结果反推原机器人结构。物理身份通过后才进入全新的 Stage 3，旧控制量和轨迹不会复制到新任务。原视频和 mask 说明源任务意图，不等于新布局的像素位置；任务结果须等待新规划执行及原生任务门禁，输入预检通过或 Stage 3 已启动都不表示任务成功。

快照清单 `task_inputs.json` 记录输入文件 SHA，清单本身也有固定摘要。首次冻结时拒绝通过符号链接引入的源任务文件，候选验收报告也会单独检查符号链接。重试只从该封存快照复制并逐项核对 SHA，不重新读取可变的源目录；快照中的清单、文件、候选或验收报告被修改或缺失时拒绝重试。封存之后，原目录发生变化不会替换已冻结输入。

新任务的 `external/` transport 独立于源任务与其他运行。执行详情中显示本次请求路径；用现有协议查看请求并提交与该 request/session 匹配的响应：

```bash
python -m agent.external inspect "/path/to/requests/<session-turn>.json"
python -m agent.external respond "/path/to/requests/<session-turn>.json" /path/to/response.json
```

路径以执行详情中显示的本次运行目录为准；响应内容按 `inspect` 输出的请求协议填写。

## 本次真实运行记录

| 项目 | 结果 |
| --- | --- |
| 源任务 | `runs/e2e_reference_layout_20261016` |
| 候选任务 / 运行 | `76e4e7e2dbf147fd8ada2d79` / `studio_candidate_20261020_154930_76e4e7` |
| 候选操作 | `cup_cup` 初始 X 平移 `+0.01 m` |
| 候选 SHA-256 | `73ad4ae90d956686dc9932d42ac9f0299ce72da28f1a0ac73975f4cda31465af` |
| 重规划任务 / 输出运行 | `11f5d9f7f5ce48ce8864bf3f` / `studio_replan_20261020_162456_11f5d9` |
| 工作台运行 ID | `db5d0cc12c33ba764a86c480` |
| 冻结任务输入 SHA-256 | `399eaf67bd97196e73c37c3212eebc7c4c0e75a3e7175513b2c918676e3a6a3f` |
| 快照文件数 | 284 |
| proposal 编译模型 SHA-256 | `2d6432cf9782d49c37cf25c2c40515bea414473603821aaee135f6acb51f1140` |

原生预检 12/12 通过，proposal 编译身份与已验收候选匹配。本次实测 proposal 为 82 维，刚体编译后为 70 维。修正 proposal 来源后进入真实 external Agent；较早把 RSS 刚体场景直接交给 Stage 3 的试跑已停止。

新生成左手夹取姿态，通过 60/100/150 mm 抬升测试（实际 59.7/99.7/149.7 mm）和保持测试（滑移 0.02 mm/s）。完整 waypoint arm screen 误差为 0.01 mm、0°。这些是候选筛选证据，整机执行继续使用原生 MuJoCo 刚体双臂与原始门禁。

第一次整机执行在抬杯时滑落。既有 `search_subgoal_variants` 做 4 次物理试验后，抓起和搬运两个子目标通过；放置前额外抬升导致不可达，随后移除这段多余抬升并从原始初态重放。最终记录为 428 帧、约 14.27 秒，两个子目标通过，第三个放置失败：`release_settle`、`at_target_orientation`、`hand_penetration`、`plan_collision`。实测释放后移动 15.7 mm、目标姿态误差约 83°、最大手部穿透 3.38 mm。失败证据位于该运行 Stage 3 candidate workspace 的 `full_execution/execution_report.json`；未调用成功打包，也没有正式成功轨迹或训练发布资格。诊断完成后经受管停止接口退出，进程状态为 `stopped`，不能把它解释为任务完成。

## 输入封存的进一步核验

审查发现原生 `scene_recon` 允许源目录内绝对路径。现将 frame0、相机、对象 mask、隐藏物体 reveal mask 和 asset XML 重定位到封存的 Stage 2 相对路径，在首次物理编译前生效。目录外活动引用和未封存的外部示例证书明确拒绝交接。原始字节仍留在 `inputs/source_task/`；既有精确帧时间索引保留，并支持相对键，避免相同像素的后续帧冒充第 0 帧。

默认 8765 的真实输入核验任务 `70870270439d4cd3ab26ef21`（`studio_replan_20261020_171320_708702`）已用修正后的封存输入进入原生 Agent，编译物理身份仍与上表一致，帧索引为 `source_frames/frame00000.jpg`。本次仅验证输入与交接，未提交规划响应，随后停止。封存重试任务为 `f023938ad2274af2b5e9798d`（`studio_replan_20261020_171817_f02393`）；两次均完成原生来源重放并进入各自独立的 Agent 请求，输入清单逐字节一致，物理编译摘要不变；输入验证完成后均已受管停止，不作为新的物理任务成功证据。

## 界面与自动验证

后续增加[原生任务规划诊断页](WORKBENCH_PLANNING_DIAGNOSTICS_20261020.md)，可直接检查隐藏候选目录中的失败尝试、子目标与原始测量；部署状态和验证范围以该说明为准。

默认 8765 工作台的桌面（1512×1100）和手机（390 像素）浏览器检查均无 page error、无横向溢出；执行详情存在“查看执行进程”反向链接。本机截图：`.workbench/previews/task-replan-desktop-20261020.png`、`.workbench/previews/task-replan-mobile-20261020.png`。

共享工作树联合检查：`workbench/tests` 与 `agent/tests/test_reference_evidence.py` 合计 **132 passed、14 subtests passed（41.54 秒）**，包含并行背景清理增量。来源检查单独执行为 35 passed；同像素跨时间的绝对与相对帧索引均覆盖第 0 帧接受、第 2 帧拒绝。输入与执行测试覆盖父候选链、来源变化、文件/目录/清单符号链接、畸形 JSON、绝对引用、上游删除后重试及外部证书拒绝。

Ruff 通过，TypeScript/Vite 生产构建通过，OpenAPI 重新生成的前端客户端逐字节一致；既有 SeriesPlot 531 KB 提示保留。`e2e/replanning.spec.ts` 为 1 passed（2.2 秒），覆盖带版本提交与错误输入禁用，使用 API mock，不作为真实物理或任务成功证据。

独立展开本次提交版本后，联合检查 **128 passed、2 skipped、14 subtests passed（26.69 秒）**；两项跳过为隔离目录未复制的可选 Light Editor 环境，已在共享工作树通过。该快照不包含并行背景清理的未提交增量。隔离版本的生产构建和生成客户端逐字节一致性检查也通过。
