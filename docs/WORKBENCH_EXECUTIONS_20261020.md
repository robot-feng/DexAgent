# EmbodiForge Studio 任务执行接入与验证

日期：2026-10-20（Asia/Shanghai）。本轮负责统一执行入口及可视化；SimFoundry 复现由另一个工作流负责，本轮不安装或修改其模型/环境。

## 实现

新增 `/executions`，从现有 YAML 启动 DexAgent 视频流程或 RSS 验收。视频流程可选 profile、终止阶段、输入视频及任务描述；RSS 复用 rigid/towel/sharpa/grasp/reference 和已有 SimFoundry 资产验收 CLI。界面不接受任意 shell 命令，也不新增任务执行器、物理引擎或 Agent 调度框架。

每次启动保存独立配置快照、源配置 SHA、快照 SHA、入口源码 SHA、实际 argv、PID/出生时间和退出码，记录位于 `.workbench/executions/<id>/`，原生产物仍写入 `runs/studio_<mode>_<time>_<id>/`。目录权限 0700，配置/启动记录 0600；公开 API 不返回配置中的凭据或完整启动元数据。源码之后发生变化时，重试使用当前入口源码并记录新的 SHA；配置则沿用原快照，输出创建新目录，不覆盖旧证据。

单个服务状态目录同时管理一个执行任务；日志保留在本地，API 返回末尾最多 64 KiB。原生报告独立决定物理/任务验收，进程退出码 0 只表示 CLI 已结束。运行开始产生报告后即可跳到已有工作台查看阶段、日志和验收。

external 模式为每条运行设置独立 `external/` transport，继续使用 `agent.external`。页面显示“等待 Agent”和当前请求路径；收到响应后提示消失，不自动替用户填写模型结果。状态与请求 ID/session/turn/路径对应，并检查响应文件是否已经存在；损坏状态或另一目录的请求不会被展示成当前请求。

停止先中断，再升级为 TERM/KILL。CLI 环境携带随机运行标识，子进程继承该标识；检查时核对运行标识与进程出生时间，覆盖 Stage 4/渲染器创建的独立会话。异常父进程退出后清理残留工作进程，并标记执行失败。恢复服务后保留存活进程身份；取不到真实退出码时明确标为 interrupted，不推断成功。正常关闭服务会停止其管理的运行。

## 验证

```bash
.workbench/venv/bin/python -m pytest workbench/tests -q
# 当前工作区 68 passed, 42.39 s（包括并行工作流的背景清理增量）

.workbench/venv/bin/python -m pytest workbench/tests/test_executions.py -q
# 9 项执行管理测试，已包含在上面的全套结果中

cd workbench/frontend
npm run build
WORKBENCH_NATIVE_EXECUTION_TEST=1 npm exec playwright test -- e2e/executions.spec.ts
# 3 passed, 23.1 s
```

原生执行浏览器测试需要显式设置 `WORKBENCH_NATIVE_EXECUTION_TEST=1`，并要求 RSS 环境和可用 GPU；默认测试仅检查 Agent 等待提示等 UI 契约。模拟 CLI 的进程测试和 UI 响应 fixture 均不作为物理验收证据。

提交前将暂存版本独立展开验证：**61 passed、2 skipped（22.11 秒）**；两项跳过均因隔离目录没有复制可选的 Light Editor 虚拟环境，这两项已在共享工作区的 68 项结果中通过。隔离版本 TypeScript/Vite 构建通过，重新生成的 OpenAPI TypeScript 与暂存客户端逐字节一致；并行背景清理代码与类型改动未打包进本次提交。

执行管理测试覆盖：配置与凭据隔离；退出码 0 但源报告失败；快照重试和旧结果保留；配置快照修改拒绝；停止与信号升级；恢复运行身份；无关 PID 不被停止；独立会话残留清理；真实 external 请求/响应协议；API 409、停止、重试及服务关闭清理。

首次全套回归因整理测试时误删 fixture 脚本内部的 `Path` 导入，产生 6 项测试失败；已修正 fixture 并完整重跑得到上述 68 passed，未跳过或放宽功能检查。生产构建通过；既有 SeriesPlot 大于 500 kB 的 chunk 提示仍保留。

## 默认 8765 的实际运行

通过真实页面选 `RSS 刚体基础验收` 并启动，产物：

`runs/studio_rigid_20261020_103558_1842be`

| 项目 | 实测结果 |
| --- | --- |
| 执行记录 | `1842be9a72114937aa0404ff`，completed，退出码 0 |
| 原生验收 | 9/9 必需检查 passed |
| 控制/捕获 | 150 控制帧、151 capture 帧 |
| 立方体支撑高度 | 0.7749953 m，符合原生验收条件 |
| 原生视频 | 解码 45 帧，15 FPS，tiled 诊断渲染 |
| 范围 | `backend_acceptance_example`，Panda 基础刚体例子 |
| 发布 | `full_pipeline_complete=false`，发布资格未评估 |

页面显示来自原生报告的“必需检查 9/9”和“发布资格未确认”；进程状态独立显示。桌面及 390 px 移动端无横向溢出，未发现页面脚本错误。

页面链接：`http://127.0.0.1:8765/executions?job=1842be9a72114937aa0404ff`。浏览器实际从页面打开原生运行/验收详情。另一次真实 grasp CLI 启动由页面停止；重试创建新运行后再次停止，分别保留 `2af83731be9649248ab7a860` 与 `26476ae1c4a84be79fb4c135` 的启动/日志证据。这两条停止记录不视为抓取任务通过。

截图：`.workbench/previews/executions-native-rss-20261020.png`，移动端截图 `executions-mobile-20261020.png`。

## 后续边界

通用 CLI 启动、停止、新运行重试和等待提示已接入；尚未提供阶段候选审核、任意断点恢复、场景 revision 到物理验收的自动绑定、模型自动响应服务或训练格式导出。当前新运行重试从 CLI 默认起点开始。成功的 RSS 基础例子不代表人类视频已完整转成训练数据，也不代表 SimFoundry 重建已经复现。
