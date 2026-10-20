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
```

列表返回轻量摘要、读取问题和截断标识；选中后读取子目标测量，原始报告继续复用工作台已有预览接口。使用 Pydantic / OpenAPI 生成客户端，前端不直接访问文件系统。

## 实测与部署

真实运行 `studio_replan_20261020_162456_11f5d9`（ID `db5d0cc12c33ba764a86c480`）读取到 6 份报告。最新 `full_execution` 为 428 帧、14.27 秒；抓起和搬运通过，放置失败，原生失败标识为 `release_settle`、`at_target_orientation`、`hand_penetration`、`plan_collision`。页面核对的手部穿透为 3.38 mm，释放后移动为 15.7 mm，目标旋转误差为 83.078°。这是既有物理失败记录的可视化验证，没有再次运行或宣称任务成功。

本轮共享工作树 `workbench/tests` 为 **108 passed（36.47 秒）**。随后加强“物理试验通过也不能提升发布资格”的断言，并把本次全部变更覆盖到独立的已提交基线中重测，结果为 **106 passed、2 skipped（26.46 秒）**；两项跳过来自隔离目录中未复制的可选 Light Editor 环境，已在共享工作树通过。独立版本不含其他对话尚未提交的算法改动。Ruff 通过，OpenAPI 再生成客户端逐字节一致，TypeScript / Vite 构建通过；既有 SeriesPlot 531 KB 提示保留。

前端独立构建通过；Playwright 界面契约测试 1 passed（2.6 秒），覆盖试验切换、dry run 提示、单位、损坏报告、扫描截断和手机宽度。真实 API 使用当前代码的 FastAPI TestClient 读取上述文件，再将响应快照送入浏览器；桌面 1512×1100、手机 390×844 无 page error、无横向溢出。截图为 `.workbench/previews/planning-diagnostics-{desktop,mobile}-20261020.png`。测试与截图使用浏览器路由提供待发布构建，不等同于 8765 已部署新后端。

**部署暂缓：** 核查时，8765 的背景清理任务 `0ff6b4934c4348a2868251aa` 仍有实际存活的 worker，正在等待 external 对象提案。重启会终止它，因此未替换线上前端、未重启服务、未另开端口。待该任务结束后，将前后端一同更新到原 8765，再进行在线检查。待发布前端位于 `.workbench/previews/diagnostics-dist-20261020`。
