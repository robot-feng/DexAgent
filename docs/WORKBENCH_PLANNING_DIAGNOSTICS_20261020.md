# 原生任务规划诊断

运行详情增加“规划诊断”页，入口为 `/runs/<run_id>?view=planning`。页面读取原生 `execute_subgoal_plan` 的 `execution_report.json`，包含 Stage 3 的隐藏 `.candidates` 尝试目录；不修改任务、机器人、执行器或门禁。

此前普通产物扫描跳过隐藏目录，导致整机规划失败后只能从本机文件查找证据。现在可在同一运行中选择各次尝试，查看子目标结果、失败标识、释放后静置问题、原始测量和报告。URL 的 `trial` 参数保存选择，列表和详情每 5 秒刷新；详情按报告 SHA 切换，避免更新时混用不同版本的摘要与测量。

## 结果与范围

- `dry_run=true` 明确显示“仅可达性预检 / 未运行物理”。任何试验报告都不会改变运行验收、发布资格或生成 Episode。
- 原生报告的 `ok=true` 只有在完整记录全部子目标、顺序一致且没有失败标识或错误时才显示试验通过；矛盾、缺失记录显示不完整。此判断仅服务诊断展示，不替代原生验收。
- `step.frames` 为整段轨迹帧范围；`measured` 中的释放和碰撞帧号是子目标内帧号，不自动混用。
- 手部穿透原生单位为毫米；释放后移动的原生单位为米，显示时乘以 1000；旋转误差为度。页面保留字段名和原始 JSON。
- 只读取当前运行 Stage 3 中的报告，不跟随报告 `files` 字段的任意路径；拒绝符号链接及跨运行报告请求。损坏、结构异常、非有限数值报告独立显示读取问题。
- 扫描最多 10,000 个目录和 200 份报告，触及上限时明确提示结果不完整。单份报告沿用现有 32 MiB 读取限制。截断时列表仅在已扫描范围内按修改时间排序。

## 接口

```text
GET /api/workbench/v1/runs/{run_id}/planning-trials
GET /api/workbench/v1/runs/{run_id}/planning-trials/{report_id}
POST /api/workbench/v1/runs/{run_id}/preview
  {"trial_report_id": "<report_id>"}
```

列表返回轻量摘要、读取问题和截断标识；选中后读取子目标测量，原始报告继续复用工作台已有预览接口。使用 Pydantic / OpenAPI 生成客户端，前端不直接访问文件系统。

## 失败试验三维回放

选择有实际运动记录的物理试验，点击“生成试验记录回放”，即可在诊断页旋转、缩放和播放。复用原生 SharpaBridge、RoboSimStudio StateReplay 和 Viser；派生 HTML 保存在 `.workbench/previews/native`。不调用规划器，不运行物理，不生成正式 Episode。

读取每个子目标的 `rollout_runtime.npz`，而不是动作提案 `trajectory_proposal.npz` 或规划中间 `rollout.npz`。按实际时间戳连接各段，并去除后一段重复的起始帧。检查内容包括原始 `scene_physics.xml` SHA、坐标顺序、原生 `input_contract.json` 中的资产 SHA、重建后的完整模型身份、子目标帧范围、积分采样时钟和段间完整状态连续性。生成前后再次核对输入 SHA；生成中输入变化会失败。

当前支持完整记录的刚体试验。缺少运行记录、输入契约或资产绑定时显示原因；不补造轨迹。柔性对象、等式机构、参与任务的 mocap 和未绑定的 cubemap 分面暂不支持。数据解压总量限制为 128 MiB。

`Preview.trial_report_id` 绑定所选报告；URL 使用独立的 `trial_preview` 参数。切换试验会清除旧回放入口，属于其他运行或试验的回放会阻止展示。原有正式轨迹与 RSS Episode 回放入口继续保留。

## 实测与部署

真实运行 `studio_replan_20261020_162456_11f5d9`（ID `db5d0cc12c33ba764a86c480`）读取到 6 份报告。最新 `full_execution` 为 428 帧、14.27 秒；抓起和搬运通过，放置失败，原生失败标识为 `release_settle`、`at_target_orientation`、`hand_penetration`、`plan_collision`。页面核对的手部穿透为 3.38 mm，释放后移动为 15.7 mm，目标旋转误差为 83.078°。这是既有物理失败记录的可视化验证，没有再次运行或宣称任务成功。

失败试验回放实测：报告 ID `cad922e62dc9020e065454d1`，派生预览 ID `b8139012dee037e8a2a795ca`。三个子目标的实际记录分别为 189、61、180 帧，连接去重后共 428 帧，预览抽样为 301 帧；模型身份核对通过，物理步数为 0。报告的 14.27 秒是按帧率估算值，积分记录实际为 **14.232 秒**，回放采用后者。生成前后检查 609 个源文件 SHA 全部不变，未生成 `trajectory.npz` 或正式 Episode。默认服务可直接读取已生成的独立回放：`http://127.0.0.1:8765/api/workbench/v1/previews/b8139012dee037e8a2a795ca/viewer`；这不表示新版诊断入口已经部署。

本次共享工作树 `workbench/tests` 为 **122 passed（36.63 秒）**；把本次改动覆盖到独立已提交基线后，最终为 **120 passed、2 skipped（26.98 秒）**。两项跳过来自隔离目录中未复制的可选 Light Editor 环境，已在共享工作树通过。独立版本不含其他对话尚未提交的算法改动。Ruff、TypeScript / Vite 构建通过，独立后端 OpenAPI 再生成客户端逐字节一致；既有 SeriesPlot 531 KB 提示保留。

前端 Playwright 界面契约测试 **1 passed（2.7 秒）**，覆盖试验切换、dry run 提示、错误预览阻止、请求与报告绑定、清除旧预览、单位和手机宽度。真实数据检查使用当前代码的 FastAPI TestClient 读取报告及生成回放，再由浏览器加载待发布界面和 API 响应；三维 iframe 读取默认 8765 实际提供的派生 HTML。实际检查旋转、缩放、暂停、跳转至 13.8 秒和恢复播放后时钟前进；桌面 1512×1100、手机 390×844 无 page error、无横向溢出。截图为 `.workbench/previews/planning-replay-{desktop,mobile}-20261020.png`。自动化浏览器使用软件 WebGL，三维交互检查明显较慢；未测量硬件加速环境的性能。浏览器路由测试不等同于新版前后端已在线部署。

**部署更新（2026-10-20）：** 初次验证时背景清理任务 `0ff6b4934c4348a2868251aa` 仍在运行，曾暂缓部署。该任务随后在分割阶段失败并结束，日志和中间产物保留。确认没有活动执行、编辑会话和子进程后，随[原生记录包](WORKBENCH_EPISODE_BUNDLES_20261020.md)一同更新了默认 8765 的前后端，未另开端口。上文的浏览器路由验证仍是当时的历史证据；现在可从 `/runs/db5d0cc12c33ba764a86c480?view=planning&trial=cad922e62dc9020e065454d1` 进入在线诊断页。
