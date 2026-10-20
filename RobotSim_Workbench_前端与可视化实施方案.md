# EmbodiForge Studio：统一系统与前端实施方案

> 版本：v2.8 · 2026-10-20（Asia/Shanghai）<br>
> 范围：当前 DexAgent 工作区中的 DexAgent + RoboSimStudio + SimFoundry<br>
> 依据：本机源码、已有运行报告与实际文件结构；替代 v1.0 中按公开仓库推测的部分。<br>
> 状态：产品入口命名为 EmbodiForge Studio（现有实现路径仍为 `workbench/`）；已实现真实运行浏览、媒体/报告/工具、两类原生曲线、记录式 3D 回放、SimFoundry 场景副本编辑、RSS live 只读状态反馈和总览续接编辑会话。任务执行页可启动已有 CLI、查看日志、停止、新运行重试并显示 external Agent 等待；MJCF 候选已接入 RSS 物理验收和原生任务重新规划。新布局任务成功仍需完整动作与目标门禁；实时控制、OG/USD 候选物理导入和正式发布仍待接入。当前可运行范围和启动方法见 [workbench/README.md](workbench/README.md)，实际验收见 [可视化验证记录](docs/WORKBENCH_VALIDATION_20261019.md)。
> 当前分工：本工作流完善统一入口、执行管理与可视化；pipeline 算法优化和 SimFoundry 复现分别由并行工作流负责，按产物契约对接。
> **备注：** 当前方案仍可改进；实施前应根据本机源码、运行结果和用户目标持续修订

## 0. 改造结论

**建议将 EmbodiForge Studio 建设为项目驱动的统一工作空间，把输入、生成过程、场景、仿真、验收和数据放在同一个入口中。**

当前主要问题是入口分散、运行状态口径不同、证据和操作脱节。只给现有页面换颜色，或加一个展示多个系统链接的首页，都不足以形成统一系统。

本次确定五项实施决策：

1. **统一产品入口和项目上下文。** 用户围绕一个项目和一次逻辑运行工作，不需要先理解三个仓库；仓库来源、物理后端和实际执行范围在详情中保留。
2. **以运行工作台为核心页面。** 阶段导航、视频/3D 主视图、参数/证据检查器和底部事件台共用同一运行上下文。
3. **复用两种现有 3D 能力。** SimFoundry Light Editor 用于其原生场景的离线布置；RoboSimStudio/Viser 用于目标运行时的交互和仿真。两者通过有版本的资产与场景交接，不能同时编辑同一权威场景。
4. **先把已有真实成果变得可用。** 第一版先浏览当前 `runs/`，展示标准杯子入碗案例、阶段报告、失败证据、视频和原生 episode；之后再开放启动、取消、审核和重试。
5. **界面能力随真实后端开放。** 当前未验证的 SimFoundry 完整生成、自动 Agent 响应、LeRobot/RLDS 导出，不能做成看似可用的一键按钮。

最终体验目标是：**一个入口、统一导航、连续上下文、可追溯状态、可以执行的下一步。** 算法和仿真仍由现有实现负责。

### 0.1 终极目标与当前建设顺序

最终产品是**全自动数据生成器**：用户提供人类操作视频和任务约束，系统选择合适的资产/重建路径，在目标机器人与运行时中生成、验证、修复并记录数据，最后交付可独立读取的训练数据集。静态扫描与操作演示分别路由；已有资产可直接复用。

工作台负责这条流程的连续上下文：输入、场景版本、执行尝试、失败原因、修复预算、质量门禁、episode 与导出都能相互追踪。自动处理条件不足时应显示具体阻塞与所需输入；成功或预算耗尽均有可查询的终态。

| 建设顺序 | 可交付行为 | 完成证据 |
| --- | --- | --- |
| 当前：真实成果与场景版本 | 查看已有运行；保存候选、跨会话继续编辑、保留父版本和原场景摘要 | 实际文件/代理/浏览器操作；源文件不变、重开位姿一致 |
| 本轮：RSS 只读运行反馈 | 通过 RSS Unix socket 显示仿真步数、时间、运行状态和实体位姿；总览页可跳转原生 Viewer | API 只发 `status` 且字段白名单；fixture socket 契约测试通过。实际 RSS live 会话尚未连通验证 |
| 下一步：目标运行时控制与物理门禁 | 当前场景版本绑定 RSS 会话和物理检查，展示具体对象的失败与修复结果 | 固定成功/失败样例、受控命令和生命周期验证；版本绑定 |
| 与 pipeline 工作流对接 | 任务预检查、运行、Agent 等待、取消、有上限的修复/恢复进入同一上下文 | 调用现有入口与报告，不增加平行状态机；至少一个新输入冷启动完成 |
| 最终：自动批量交付 | 验收后的场景/动作变化、数据记录、训练格式转换、独立回读 | 每条 episode 的任务结果/来源/随机种子完整；失败数据隔离；独立 loader 通过 |

本轮继续沿用前端与 pipeline 的既有分工。单次 UI 验证、样例回放、场景候选保存分别记录证据，不据此宣布全自动生成已经实现。融合算法与物理门禁见[总方案](SimFoundry_DexAgent_RoboSimStudio_融合升级实施方案.md)。

**当前实施取舍：优先把现有功能接成完善系统。** 以一个已有刚体任务贯穿输入、场景、执行验收和数据回放；对象/背景先使用现有任务清单和简单设置，运动分析作为辅助。复用现有工具、格式和报告；每轮以一个真实可用流程作为交付，不以增加算法数量作为进度。

## 1. 本机项目核查

### 1.1 已有能力与缺口

| 模块 | 本机证据 | 当前能力 | 前端改造决定 |
| --- | --- | --- | --- |
| DexAgent 主流程 | [run_dexagent.py](run_dexagent.py)、[agent/config.py](agent/config.py) | Stage 1–4，`--from/--to`，profile，阶段确认、恢复与报告 | 包装已有入口；不重写 Agent 主循环 |
| 运行状态与验收 | [agent/console.py](agent/console.py)、[agent/run_reports.py](agent/run_reports.py)、[agent/runner.py](agent/runner.py) | `progress.json`、`trace.jsonl`、阶段报告、失败报告、repair 记录 | 新增只读适配和统一查询；保留原始状态及证据 |
| 模型交互 | [agent/external.py](agent/external.py)、[agent/README.md](agent/README.md) | 当前配置为 external 文件传递，需要外部 Codex 会话响应 | 显示“等待 Agent”；不能把启动进程等同于自动完成任务 |
| RSS 后端集成 | [run_robosimstudio.py](run_robosimstudio.py)、[agent/rss_episode.py](agent/rss_episode.py)、[融合核查报告](docs/DEXAGENT_RSS_FUSION_REVIEW.md) | 已有刚体、Sharpa、毛巾及 reference 验收路径 | 先接已有报告和 episode，再接实时 Studio；后端验收脚本不等于常驻 Studio 服务 |
| RSS 实时界面 | [session.py](robosimstudio/robosimstudio_studio/session.py)、[server.py](robosimstudio/robosimstudio_real2sim_agent/server.py)、[wire.py](robosimstudio/robosimstudio_real2sim_agent/wire.py) | Viser 场景交互、录制、热重载、Unix Socket 控制，命令通过 SimBus 送到仿真主线程 | 复用原生 Viewer；管理服务只桥接固定命令 |
| SimFoundry 场景编辑 | [Light Editor README](SimFoundry/scripts/interactive/light_editor/README.md)、[server.py](SimFoundry/scripts/interactive/light_editor/server.py)、[app.js](SimFoundry/scripts/interactive/light_editor/web/app.js) | 已有 Three.js/GLB 浏览器编辑器，支持对象、相机、关节、场景保存及物理复核入口 | 纳入场景工作区；不另造第三套通用 3D 编辑器 |
| SimFoundry 生成管线 | [orchestrator.py](SimFoundry/simfoundry/pipeline/orchestrator.py)、[reporting.py](SimFoundry/simfoundry/pipeline/reporting.py)、[融合方案](SimFoundry_DexAgent_RoboSimStudio_融合升级实施方案.md) | 已有阶段计划、阶段状态、pipeline manifest 与耗时报告；完整本机运行仍有依赖/API-free 门槛 | 先接产物与能力检测；生成入口按实测状态启用 |
| 工具库界面 | [library_visualizer/index.html](library_visualizer/index.html)、[tool_library/index.json](tool_library/index.json) | 独立静态检索页；页面优先取远端已提交索引，再回退静态快照 | 在统一系统中读取本机索引；保留检索与详情能力 |
| 原生数据记录 | [record/store.py](robosimstudio/robosimstudio/record/store.py) | NPZ/JSONL/MP4 的 EpisodeStore；部分历史记录字段不完整 | 兼容真实历史记录，缺失字段显示未知；训练格式导出另设门禁 |

前期扫描 `runs/` 找到 **53 份 run summary、83 份 stage report、32 份 run meta**；本地工具索引有 **362 个工具条目**。这些是核查时的文件数量，不是成功任务数。核查时尚无统一 React 工程或管理 API；现已新增工作台，并以实际可识别的原生报告索引运行，未将文件数量当成成功数。

锁定的源码基线：DexAgent `cb3d7edb3bf072c0fa603d0d3d8b8f161ac2fa46`，RoboSimStudio `73739abe212f963e8a9f5862b27bca370df091a7`，SimFoundry `9e34ebefcd020583fbb755a8b57268dce78eca26`。DexAgent 另有未提交修改；实施时记录工作树身份，不以 commit 单独代表当前代码。

### 1.2 会直接影响前端的真实情况

- **恢复完成不等于单次全流程完成。** 标准案例当前 [run_summary.json](runs/e2e_reference_layout_20261016/run_summary.json) 为 `scope_completed`，请求范围是 Stage 3–4，`full_pipeline_complete=false`；[pipeline_validation.json](runs/e2e_reference_layout_20261016/pipeline_validation.json) 记录跨调用证据完整。两者应同时展示。
- **阶段通过不等于没有警告。** 标准案例 Stage 2 是 `warning`，不能被汇总界面的绿色成功标签抹掉。
- **机器检查通过不等于数据可发布。** 当前 Stage 4 [run_report.json](runs/e2e_reference_layout_20261016/stage3/stage4_task/outputs/run_report.json) 是 `passed`，但 `release_ready=false`。该 false 本身也不提供具体拒绝理由；UI 必须读取 profile、审核与发布检查，缺少时显示“发布资格未确认”。
- **已有后端记录不一定自带完整数据集元信息。** RSS reference 的 episode 存在 `meta.json`，但 task、embodiment 等字段可能为空，且对应目录未必有 `run_meta.json`。不能要求每个历史目录严格满足理想新格式才能浏览。
- **等待模型响应是真实运行状态。** external 的 `status.json` 有 waiting/executing/finished 等状态；必须核对 session/request 身份，不能把共享 transport 目录里的上一任务状态套到当前 run。
- **视频并排展示不等于时间同步。** 人类视频和仿真视频的播放对齐必须有映射依据；无映射时只能自由播放或按比例比较，并标注口径。

### 1.3 对原方案的调整

| v1.0 的不足 | v2.0 的处理 |
| --- | --- |
| 按公开仓库路径设计，部分路径不对应本机 | 以当前目录中的源码和真实报告为接入依据 |
| 主要围绕运行看板，统一体验定义不足 | 增加统一 Shell、项目上下文和运行工作台 |
| 主要复用 RSS，遗漏 SimFoundry 浏览器编辑器 | 明确离线场景编辑与运行时仿真的分工 |
| 先独立开页，内嵌长期待定 | 独立开页只作接入调试手段；原位工作区是统一版验收项 |
| 一个 status 混合进程、验收和审核 | 分离执行、验证、人工审核和发布资格 |
| 首个 Demo 依赖尚未实现的训练格式导出 | 首版验收使用真实产物浏览和原生 episode；训练导出后置 |

## 2. 统一系统的信息架构

### 2.1 六个业务入口与总览首页

侧栏保留六个业务入口；根路由 `/` 是总览首页，聚合现有 API 的运行数、场景数、最近运行和能力状态，并链接到已有页面。若存在未关闭的场景编辑会话，总览提供直接续接入口。总览不创建第二套任务状态或生成入口。

| 导航 | 用户要完成的事 | 与当前项目的关系 |
| --- | --- | --- |
| 项目 | 导入已有运行，或创建新任务 | 项目是上下文容器，关联输入、逻辑运行和场景版本 |
| 工作台 | 看进度、查结果、调试与处理失败 | 核心工作区；默认恢复最近访问的项目和 run |
| 场景与资产 | 管理场景版本、模型、机器人、相机和物理属性 | 原生格式、来源与验收状态可追溯 |
| 数据与回放 | 浏览 episode、视频、曲线及数据集合 | 区分候选、已验收、已审核、已导出 |
| 工具库 | 搜索技能、验证器和使用记录 | 复用本地工具索引，关联运行中的工具调用 |
| 环境与设置 | 查看 Python/模型/仿真能力和工作区配置 | 启动前说明阻塞原因；高级配置默认折叠 |

不按“DexAgent / SimFoundry / RSS”组织主导航。来源显示在阶段与产物详情中。Dexlab 暂不纳入这条三项目融合主线；后续有明确任务和适配契约时再接入。

### 2.2 共享上下文与路由

统一 Shell 持有 `projectId / workflowRunId / stageAttemptId / sceneRevisionId / studioSessionId`。持久选择由 URL 表达，面板宽度等个人偏好放 localStorage，服务端事实从 API 获取。

建议路由：

~~~text
/projects
/projects/:projectId
/projects/:projectId/runs/:runId?view=overview&stage=stage3&attempt=...
/projects/:projectId/runs/:runId?view=scene&sceneRevision=...
/projects/:projectId/runs/:runId?view=trajectory
/projects/:projectId/runs/:runId?view=validation
/projects/:projectId/runs/:runId?view=artifacts
/scenes
/artifacts
/datasets
/tools
/settings/environment
~~~

全局场景、资产、数据页与工作台使用同一索引和详情组件。跨页后仍可返回原运行、阶段和播放位置。切换项目时清空不匹配的 scene/session 选择；仍在运行的后台任务不会因页面导航而结束。

### 2.3 新任务采用明确模板

第一批提供三个入口，能力由后端判定：

1. **浏览已有运行**：选择已配置工作区中的目录，建立只读关联，立即可用。
2. **人类演示 → 仿真任务**：视频、描述、机器人、已有场景/标定；按当前 DexAgent 能力逐阶段执行。
3. **已有场景 → 目标运行时验收**：使用已支持的 RSS 示例或导入路径。

“场景扫描 → 重建”在 SimFoundry 环境与 API-free 路径实测前显示阻塞说明。不要仅按视频扩展名推断它是扫描视频还是操作演示；输入目的由用户选择，自动识别只作提示。

## 3. 核心界面与视觉规范

### 3.1 工作台布局

~~~text
┌──────────────────────────────────────────────────────────────────────────┐
│ RobotSim   项目 / 杯子入碗   Run 选择   机器人   状态/范围   可执行操作     │
├──────────┬───────────────┬─────────────────────────────┬───────────────────┤
│ 全局导航  │ 阶段与尝试     │ 概览 | 场景 | 轨迹 | 验收 | 产物 │ 检查器          │
│          │               │                             │                   │
│ 项目      │ 预检查 ✓      │ 原始演示 / 3D 工作区 / 回放     │ 目标与输入       │
│ 工作台    │ 理解 ✓        │                             │ 参数与单位       │
│ 场景资产  │ 重建 ⚠        │                             │ 检查与证据       │
│ 数据回放  │ 轨迹 ✓        │                             │ 版本与来源       │
│ 工具库    │ 数据 ✓        │                             │ 下一步操作       │
│ 环境设置  │ 展开历史尝试   │                             │                   │
├──────────┴───────────────┴─────────────────────────────┴───────────────────┤
│ 事件 | 日志 | 工具调用 | 资源     过滤 / 搜索 / 最近更新时间 / 连接状态      │
└──────────────────────────────────────────────────────────────────────────┘
~~~

默认尺寸：全局侧栏 64/200 px 可收起；阶段栏约 224 px；检查器约 320 px；底部事件台约 220 px 可收起，各工作面板可调整宽度。中心区域不足时优先将检查器变为抽屉。

### 3.2 五个内容页签

- **概览**：输入视频、任务目标、运行范围、阶段摘要、待处理事项和关键结果。顶部必须能回答“正在做什么、卡在哪里、下一步是什么”。
- **场景**：按 scene 类型选择离线编辑或 RSS 实时会话；显示当前版本、单位、坐标系、未保存修改和导入/验收状态。没有适配能力时展示模型预览与阻塞原因，不伪装成可编辑仿真。
- **轨迹**：视频、关节/action 曲线、子目标区间和失败时刻；轨迹未生成时显示所需阶段与已有诊断候选。
- **验收**：必需检查优先，随后是警告和人工审核。每项有 observed、threshold、证据链接、来源及适用范围。人工接受不能替代失败的机器门禁。
- **产物**：输入、中间候选、已验收结果和导出产物；图片、视频、JSON、日志、模型按类型预览，使用同一 ArtifactViewer。

Stage 4 的 randomize / plate / export / render / composite 作为数据生成阶段的子步骤展示。SimFoundry 可展开其原生阶段；不把整个业务流程画成所有用户都必须经过的固定四节点。

### 3.3 统一交互规则

- 选中阶段只更新检查器和相关内容，不刷新整页；日志新消息不抢走用户正在阅读的位置。
- 页面刷新恢复查询，不重复启动任务；每个写操作都有 pending、成功/失败反馈和可查询的操作 ID。
- 顶部只展示当前上下文可执行的主操作；“暂停仿真”只影响 RSS 会话，不命名成“暂停整条 pipeline”。
- 重试前展示起始阶段、复用输入、受影响下游和新的尝试；确认后的后端请求保留原结果。
- 场景未保存时切换 run/scene 给出保存、放弃、留在当前页的选择。失去编辑权时保留草稿并停止提交。
- 时间、质量、长度、角度必须带单位；旋转编辑用角度，原始四元数只在高级详情中显示其顺序。
- 搜索、筛选和空态均有真实含义。区分“没有记录”“尚未生成”“解析失败”“后端不可用”和“无权限”。

### 3.4 视觉方向

参考 SimFoundry 的中性深灰和 DexAgent 工具库的清晰分层：背景 `#14161A`、面板 `#1D2026`、分隔 `#343A44`、正文 `#E8EEF7`、次级文字 `#A3AAB6`、主色 `#80ADFF`。颜色集中到 CSS token；成功用绿、运行用蓝、警告/待审核用琥珀、失败用红，文字与图标共同表达状态。

3D 背景按真实来源处理：SimFoundry 保留扫描房间网格与贴图；DexAgent 保留原 MJCF；RSS 复用现有主光/补光能力。背景网格不代表碰撞几何已经验证，不给已有记录替换成无坐标映射依据的其他房间。

使用系统中文字体和等宽数字，不依赖在线字体/CDN。正文 14 px，辅助信息不低于 12 px，间距采用 4/8/12/16/24 px。减少大面积装饰、渐变和统计卡，优先保证视频、场景、表格和报告的阅读空间。

1440 px 及以上使用完整工作台；1024–1439 px 收起辅助栏；更窄屏幕以查看和审核为主，3D 编辑进入专用全屏模式。键盘焦点、表单标签、可关闭抽屉与 reduced-motion 作为基础交互要求。

## 4. 第一条产品闭环

以已有标准任务为第一条可验收纵向切片：

1. 打开统一入口，导入 `runs/e2e_reference_layout_20261016`，保留原目录。
2. 在工作台查看原视频、目标、Stage 1–4 和 Stage 4 子步骤。
3. 页面同时显示“最近调用完成 Stage 3–4”和“跨调用证据覆盖完整流程”，并保留 Stage 2 警告。
4. 查看失败、恢复和 composite 重跑的来源；无法恢复的历史尝试明确标为证据不完整。
5. 打开真人/仿真/合成视频和 contact sheet；标注人类对照为重采样视觉参考。
6. 关联 RSS reference 的独立验收记录，展示 37 项必需检查及原生 episode；标明这是目标后端对照，不把它并入 DexAgent 原始四阶段的执行结果。
7. 再导入一条真实失败记录，验证失败证据、受阻下游和下一步建议的呈现。

这一版无需重新运行昂贵的生成管线，也不依赖 SimFoundry 完整环境或训练格式导出；但必须读取真实报告和媒体，不能只依靠演示数据。

## 5. 技术架构与职责

### 5.1 推荐技术栈

| 层 | 选型 | 约束 |
| --- | --- | --- |
| 前端 | React + TypeScript + Vite | 本地 SPA；构建后由统一入口提供静态文件 |
| 路由与请求 | React Router + TanStack Query | 路由保存上下文，Query 管理服务端状态；不把仿真对象复制进前端 store |
| UI | React 公共组件 + 原生 CSS token + Lucide | 首版直接复用语义控件，不为基础页面额外引入组件框架 |
| 表格与图表 | 原生语义表格 + ECharts | 服务端分页；图表独立延迟加载。出现复杂列需求后再考虑 TanStack Table |
| 管理 API | FastAPI + Pydantic | 独立的轻量服务；请求处理不加载物理引擎和大模型 |
| 元数据 | SQLite WAL + 文件系统 | SQLite 存管理元数据与索引，原生产物保留原格式 |
| 可视化派生任务 | 受控 CPU 子进程 | 仅生成记录式 3D 回放，具备身份检查、幂等缓存、超时和退出清理；不执行 pipeline |
| 状态传递 | HTTP 轮询起步，随后 SSE | 管理事件只读；Viser 的实时 3D 仍使用原生连接 |
| 3D | RSS/Newton/Viser 原生回放 + Light Editor 场景副本编辑 | 同入口内嵌；原格式与上下文保留，物理与发布另设验收 |

本机 Node 为 `v22.23.2`，满足当前 [Vite 文档](https://vite.dev/guide/)列出的 Node 22.12+ 要求。前端依赖与 lockfile 已在独立工程锁定；服务依赖安装于 `.workbench/venv`，不修改已有仿真环境。

采用 TanStack Query 管理读取、缓存、刷新和分页，符合其[官方服务端状态定位](https://tanstack.com/query/latest/docs/framework/react/overview)。前端交互偏好先用 React state/context，出现明确跨组件需求后再考虑额外状态库。

FastAPI 的生命周期用于管理索引任务与连接，参考[官方 lifespan 文档](https://fastapi.tiangolo.com/advanced/events/)；长时间生成任务交给可记录的子进程监督，不依靠请求后的普通 BackgroundTasks 持有整条 pipeline。[官方后台任务说明](https://fastapi.tiangolo.com/tutorial/background-tasks/)也区分了重计算任务与轻量后台工作。这里是本项目的架构取舍，不是已实现能力。

### 5.2 组件关系

~~~mermaid
flowchart TD
    UI["统一 React 工作台"] --> API["FastAPI 管理服务"]
    UI --> VIEW["原生 3D 工作区 / 受控会话入口"]
    API --> INDEX["报告与媒体索引"]
    API --> META["SQLite：项目 / 调用 / 审核 / 操作"]
    API --> SUP["Supervisor：生命周期与资源锁"]
    API --> BRIDGE["Studio Bridge / Editor Adapter"]
    INDEX --> FILES["原生报告、视频、场景、EpisodeStore"]
    SUP --> DA["DexAgent 原有 CLI"]
    SUP --> SF["SimFoundry 原有 CLI"]
    SUP --> RSS["RSS 验收或 Studio 进程"]
    BRIDGE --> RSS
    BRIDGE --> EDITOR["SimFoundry Light Editor"]
    VIEW --> RSS
    VIEW --> EDITOR
    DA --> FILES
    SF --> FILES
    RSS --> FILES
~~~

管理服务负责工作空间和进程控制；各算法负责其真实执行与验收。**一次仿真只能有一个时钟拥有者。** 管理 API 和 React 均不得自行调用第二条 `sim.step()` 循环。

统一系统不要求所有算法合入同一 Python 解释器。沿用当前主环境；仅在实际依赖边界需要时配置专用环境。SimFoundry Light Editor 已明确要求独立环境，不能把它的 standalone `usd-core` 装入 Isaac Sim 环境。

## 6. 统一数据模型与状态

### 6.1 区分逻辑运行和执行调用

~~~text
Project
 ├── InputReference / SceneRevision
 └── WorkflowRun                         一次逻辑任务，可包含恢复与修复
      ├── Invocation                    一次 CLI / 后端调用及其执行范围
      │    └── StageAttempt             一个阶段的某次尝试
      │         ├── GateReport
      │         └── Artifact
      ├── StudioSession                 引用明确的 SceneRevision 和机器人
      └── EpisodeReference → DatasetCollection
~~~

不能只按目录名认定一次运行。现有同一个 `runs/<name>` 可以经历多次 CLI 调用，顶层 summary 是最近一次调用快照，阶段输出还可能后续重跑。

- 新调用记录独立 invocation ID、argv、环境、配置身份、开始/结束时间和请求阶段。
- 原生 attempt ID 优先保留；不存在时建立带文件哈希的导入记录，不编造历史轮次。
- 旧数据默认进入“导入记录/未归档”，项目归属由显式关联确定，不根据 run 名字猜任务。
- 标准案例的跨调用关系来自 `pipeline_validation.json`；没有明确来源的报告不得仅按修改时间拼成完整流程。
- 同一源文件内容变化产生新的报告版本；内容哈希只用于去重和身份核对，不证明成功。
- DatasetCollection 只是对已接受 episode 的索引与审核集合，不另造一种 episode 文件标准。

### 6.2 状态必须分层

建议 API 分开返回：

~~~typescript
type ExecutionState =
  | 'queued' | 'running' | 'waiting_agent' | 'waiting_review'
  | 'cancelling' | 'completed' | 'failed' | 'blocked'
  | 'cancelled' | 'interrupted' | 'skipped' | 'unknown';

type ValidationState =
  | 'not_evaluated' | 'passed' | 'warning' | 'failed' | 'incomplete';

type ReviewState = 'not_required' | 'pending' | 'accepted' | 'rejected';
type ReleaseState = 'not_evaluated' | 'blocked' | 'ready' | 'exported';

interface RunView {
  id: string;
  execution: ExecutionState;
  validation: ValidationState;
  review: ReviewState;
  release: ReleaseState;
  scope: {
    requestedStages: string[];
    scopeComplete: boolean | null;
    singleInvocationFullPipelineComplete: boolean | null;
    evidenceCompleteAcrossInvocations: boolean | null;
  };
  sourceStatus: string | null;
  sourceReportArtifactIds: string[];
  availableActions: Array<{
    action: string;
    enabled: boolean;
    reason?: string;
  }>;
}
~~~

状态展示规则：

| 原始证据 | 统一界面含义 |
| --- | --- |
| `scope_completed`，请求 Stage 3–4 | 本次范围完成；不认定单次全流程完成 |
| `passed_with_warnings` / 阶段 `warning` | 检查通过并保留警告，详情列出来源 |
| `smoke_complete` | 冒烟验证完成，保留复用属性；不代表正式数据验收 |
| `release_review_pending` | 执行完成，发布仍需审核/条件确认 |
| `release_ready=false`，缺少解释 | 发布资格未确认，不自行推断仅差人工点击 |
| 匹配当前 session 的 external waiting | 等待 Agent 响应，显示已等待时长和当前请求 |
| 只有 `progress.status=finished` | Agent 循环结束的提示；最终验收仍以报告为准 |
| 报告缺失、格式未知或解析失败 | 未评估/证据不完整，并展示原因 |
| 进程已确认退出但没有最终报告 | 中断或异常退出；不得延续显示为运行中 |
| 仅报告长时间未更新 | 状态可能过期；不能据此断言任务失败或已退出 |

历史报告没有进程身份时可以显示其原始状态和“当前存活状态未知”。上游 `blocked` 与普通失败也要保留差别。前端使用后端的 actions 判定，后端执行时再次校验当前版本。

### 6.3 权威数据和审核边界

- 原生报告决定算法结果；workbench 不改写这些结果。
- SQLite 的文件索引是可重建投影；项目关联、人工审核、操作和版本发布记录是管理数据，需备份，不能声称都可由 `runs/` 重建。
- 人工审核单独记录对象、报告/场景哈希、结论、原因和时间。产物变化后旧审核不自动沿用。
- 必需机器检查失败时，人工审核不能把它改成 PASS；可以接受候选供继续调试，但不能据此放行正式数据。
- 进度只使用真实指标，如 round/max_rounds、阶段完成数、样本数、render 帧数；不生成虚假的整条 pipeline 精确百分比。
- 原始时区未知的历史时间保留“来源时区未知”；新事件带时区并按用户 Asia/Shanghai 显示。事件顺序不依赖本机无时区字符串。

## 7. 三类适配器与原生文件接入

当前实现复用 DexAgent 与 RSS 的原生产物；SimFoundryAdapter 为后续接入目标。下述 SimFoundry 接口说明不表示本机完整生成路径已经通过。

### 7.1 DexAgentAdapter

读取 `run_summary.json`、`stage_report.json`、`progress.json`、`trace.jsonl`、`failure_report.json` 和可选 `pipeline_validation.json`。Stage 4 还读取当前 `stage3/stage4_task/outputs/run_report.json` 与 `reports/history/`，避免拿顶层旧快照掩盖后续视觉重跑。

execution 更新、最终报告和跨调用汇总分别保留，按证据范围展示。识别 `dexagent.step_report.v1`、`dexagent.stage4_run_report.v1` 等已知 schema；未知 schema 保留原文预览并停止推导验收。

### 7.2 SimFoundryAdapter

优先复用 `pipeline_manifest.json`、`pipeline_run_report.json`，结合各阶段 `stage_info.json`。既展示上游 `success`，也展示实际产物完整性；“文件夹存在”不能变成验收通过。

阶段计划来自原生 StageSpec 和实际参数，不能硬编码所有阶段必经。例如可选 articulation、不同深度路径和缓存跳过要有明确原因。

对每个阶段展示配置的模型后端；运行报告能提供时再展示实际加载的模型 revision/权重 SHA、环境和授权/依赖状态。清楚区分“配置选择”“已加载”“已运行”，不能把模型配置当作推理成功证据。新任务只呈现后端实际声明且已验证的选项。SimFoundry Stage 2 现有 DA3 配置已补 `s2_da.model_name`：两个 Nested 档位满足米制深度与相机位姿契约；DA3-BASE 的 Apache 许可和较小体量不能抵消它当前只输出相对深度这一限制，Workbench 应把它显示为“待尺度标定”，不得作为可直接运行的标准重建选项。真实模型推理和权重 revision 尚未验证前，所有这些选项仍只能显示为“已配置/未验证”。

### 7.3 RSSAdapter

EpisodeStore 支持 `run_meta.json`、`episode_results.jsonl`、episode `meta.json`、`steps.npz`、可选视频和事件/接触文件。字段缺失可浏览但标记不完整；不因历史 `run_meta` 缺失而丢弃所有 episode，也不据目录名补出机器人型号。

实时控制通过现有 `wire.request` 与原生 command handlers；读取实际 ready line / status 中的端口，不能假定请求 8080 就一定监听 8080。后端验收脚本与实时 Studio 是两种独立任务类型，分别监督。

### 7.4 索引与媒体处理

1. 首次显式扫描配置的根目录，后续按文件大小/mtime 发现候选变化，再解析和更新；摘要与列表读取 SQLite，不在每次请求递归扫描全部视频和资产。
2. `progress.json` 当前不是原子写；解析失败时保留上次快照、标记过期并稍后重读。JSONL 末尾半行等待后续补齐；文件轮换/截断时重建游标，不拼接损坏数据。
3. 用 producer、源记录标识与文件版本建立稳定身份。大文件 hash 后台计算，有可信上游 hash 时先核对复用；不得在每次列表请求重算所有视频哈希。
4. 文件统一由 artifact ID 访问；后端解析真实路径并校验配置的根目录、符号链接和文件类型。
5. 视频支持 HTTP Range；图片缩略图延迟生成；JSON/日志按范围预览。NPZ 由服务端按允许字段切片，使用 `allow_pickle=False`，不把整包数组发给浏览器。
6. 前台默认按 2 秒间隔读取活动任务，终态停止高频轮询；日志按 cursor 增量读取。SSE 后续使用事件序号去重和断线续订，不触发写操作。
7. `privileged.npz` 仅作为诊断/重放资源，显式标识仿真真值；默认策略导出字段必须由独立允许列表决定。
8. 模型身份进入运行与阶段 manifest：后端 ID、权重 revision/SHA、推理环境，以及模型 API/授权状态。列表和详情显示这些来源，不在全局界面做一个和 pipeline 配置脱节的“模型市场”。

## 8. 场景编辑与 3D 会话融合

### 8.1 以场景格式和操作目的选择工作区

| 当前对象 / 目的 | 工作区 | 保存与交接 |
| --- | --- | --- |
| OmniGibson scene JSON / USD 的离线布置 | SimFoundry Light Editor | 完整 snapshot 保存新候选，通过原生复核后建立 SceneRevision |
| 已支持的 RSS scene / TaskSpec | RSS/Viser | 原生会话编辑与运行，保存来源、配置、检查及录制引用 |
| DexAgent MJCF 产物 | 已验证的导入/验收路径，或只读产物查看 | 不承诺任意 MJCF 一键进入实时 Studio；需场景构建/机器人映射适配 |
| 仅 GLB 模型 | 资产预览或 Light Editor 支持的导入 | 视觉预览不等于碰撞、质量、关节已验证 |
| SimFoundry → RSS | 转换任务与目标运行时验收 | 生成新的目标场景版本，记录轴向、单位、关节和物理参数映射 |

场景布局编辑与机器人实时操控是不同工作模式。切换时先保存/检查/建立新版本，不直接共享一份未提交的可变状态。

### 8.2 内嵌必须先做接入验证

当前记录式 3D 已通过原生 Viser 的序列化 HTML 内嵌：RSS 使用记录中的 body poses，DexAgent 使用已绑定源场景/资产/时钟的 qpos，经现有正向运动学显示；均不重新执行物理。派生文件在 `.workbench/previews/` 中生成，完成后无须保留实时 Viewer 端口。Light Editor 已添加可选 base path、同源内嵌、实际端口就绪文件和独立缓存目录，由工作台监督并代理。以下 WebSocket 要求仍适用于尚待接入的物理会话。

统一版要求在工作台中心区域使用原生 Viewer，保留统一导航和运行上下文。独立标签页可以用于开发调试或故障降级，但不作为统一工作区已完成的证明。

接入前先验证两个真实约束：

- SimFoundry `app.js` 混用相对资源路径与根路径 `/api/...`，部分位置还使用 `location.reload()`。直接代理到 `/viewer/<session>/` 不会自动让根路径请求进入该会话。
- RSS 底层 Viser 有 WebSocket、动态端口和独立 GUI。官方 [ViserServer 文档](https://viser.studio/main/api/core/server/)说明 server/client 状态与实际端口，但不能据此推断本机 Newton Viewer 已支持某个 base path 或工作台会话协议。

验证顺序：本机启动 → 独立浏览器正常 → 工作台 iframe 正常 → 代理下静态资源/API/WebSocket 正常 → 断线恢复/尺寸调整正常 → 编辑权隔离正常。

优先做小范围、可维护的 base-path/API client 适配和受控代理。若某 Viewer 只支持站点根路径，评估独立受控 viewer origin；这是统一界面内嵌方案的部署分支，不强行声称所有后端都已同源。跨 origin 消息需要校验 origin、session、scene revision，禁止 `postMessage('*')` 传控制信息。

不采用在代理里大范围字符串替换 JS 的长期方案，也不重写 Viser 的前端协议。

### 8.3 会话与命令边界

每个会话绑定 run、SceneRevision、机器人、进程身份、实际访问地址和控制拥有者。页面刷新恢复会话引用；切换页签不重启物理进程。

Web 控制只映射 `status/reload/pause/resume/reset/run/snapshot/check/play` 等明确命令；`run` 有步数上限，`play` 只能选择 status 返回的已注册 routine。路径、相机和输出文件由受控 ID 解析，不接受任意 Python 或 shell；原生 `cmd_eval` 不对 Web 暴露。

命令在仿真主线程执行。检查、重载和播放按会话串行化；`play` 期间原生拒绝外部 run/check 的限制要直接反映到界面。耗时命令返回 operation ID，再查询结果。

**管理层加锁不能自动限制原生 Viewer 内的按钮和拖动。** 第一版只承诺单用户单写者；开放多读者或多人控制前，必须在原生回调/会话入口落实只读和控制权，不能只加一个“只读”徽标。

RSS 当前 Newton Viewer 路径可能监听所有网卡，Light Editor 也没有认证。初期使用本机/SSH 隧道；远程统一入口需同时保护 Viewer 通道与管理 API。单独给 FastAPI 加鉴权不代表原生端口已经受控。

### 8.4 保留 SimFoundry 的真实保存契约

遵循 [SimFoundry/AGENTS.md](SimFoundry/AGENTS.md) 和 [Light Editor README](SimFoundry/scripts/interactive/light_editor/README.md)：

- 保存发送完整 snapshot，并带 scene/session revision；不能只提交 transform 却把物理参数、相机或关节恢复成初始值。
- 继续复用 revision 比较、文件摘要和跨进程写锁；发生冲突后重新获取内容与版本，不能只更新版本号。
- 移动对象要归零相关速度；不为改变布局而直接修改共享 USD 文件。
- 先写新候选，再通过 settle/parity 和目标运行时要求的检查；未经验证不覆盖 latest。
- 质量、关节限位等在 JSON 中记录，不保证所有消费者自动应用；检查器要区分“已记录”和“已在目标运行时应用”。
- 高斯背景是视觉内容，不能作为碰撞地面；地面、质量、摩擦、单位和四元数顺序要随版本保留。

### 8.5 扫描背景与独立实体分离

用户反馈的 `droid_v1` 预览存在机器人外观重叠。已检查官方示例：场景登记了独立 `robot0`、垃圾桶、杯子和 `mesh_background_0`；背景 USD 实际只包含一个 `/droid_v1/base_link/Geom/mesh`，共 101233 顶点。真实浏览器隔离背景后，白色扫描机械臂仍完整可见；隔离机器人与物体后，只剩独立灰色机器人、杯子和垃圾桶，确认背景含原机器人。该网格未声明 CollisionAPI，也不能据此推断 OmniGibson 加载后的最终碰撞行为。

工作台增加“隔离背景 / 隔离机器人与物体 / 显示全部”，沿用原生 `hiddenForView`。这些操作只改变显示，不删除物体、不改变保存内容和碰撞。用于分别检查背景残影与实体完整性；隐藏背景不构成背景清理通过。

统一场景应在原生格式中保持四种职责：

| 内容 | 职责与检查 |
| --- | --- |
| 静态视觉环境 | 房间、墙面等清理后的网格/高斯；排除人、机器人及独立任务实体的残影 |
| 静态支撑与碰撞 | 桌面、地面、墙和固定夹具；有明确尺度、位姿和碰撞代理，与视觉网格分别验证 |
| 操作对象 | 每个实例独立资产、位姿和 body；按任务指定自由体、固定体或关节体，不把所有物体一律设为可动 |
| 机器人 | 权威机器人模型、关节与执行器；独立于扫描环境，底座固定也仍需独立建模 |

有条件时优先使用空场景扫描与独立对象资产；否则确认要作为独立实体的对象，以及需要从背景中清掉的机器人/手。移动相机时不以原始像素变化单独判断对象。SimFoundry 原生默认是 SAM3 关键帧分割 → SAM2 双向跨帧传播 → VOID 两遍视频补洞 → DA3/高斯背景训练和对齐；SAM3 自身也有原生视频跟踪能力。工作台默认使用本机 SAM3 LiteText-S0 文本编码器接入原生 SAM3 双向视频跟踪器：保留原 SAM3 视觉编码器与时序跟踪，只替换文本编码器；指定关键帧没有检测到某个对象时，会在抽样视频帧中继续找播种帧。SimFoundry 的 SAM3+SAM2 组合仍作为可选对照，但它固定要求 Hiera-Large checkpoint 与配置；本机 Hiera-Small 不会被静默替代。LiteText 论文报告在多项图像/视频分割基准上表现可比，但不等于任意机器人场景零精度损失；2026-10-20 经 Workbench API 完成 400 帧 ThrowAwayTrash mask 冒烟并停在人审阶段，只证明原生跟踪和产物链路可运行，不替代有标注集的质量评估。当前人审页同步预览原视频与 mask，任一侧播放、暂停或拖动都会同步到另一侧；补洞复核页同样同步对照输入与清理结果。第一版不增加逐帧标注工具。

SimFoundry 的普通 Stage 2c 直接从视频训练高斯；要去掉机器人/物体并补背景，需要单独运行上述 auto-background 支路。其关键帧自动提案可能漏掉静止机器人或人手，mask 预览要能补充排除对象。Stage 6 的 `s6_upsample.rembg_model` 只给单物体图生成透明 Alpha；默认 `bria-rmbg` 的权重是 CC BY-NC 4.0，`u2net` / `u2netp` 按上游 U-2-Net 仓库记录为 Apache-2.0，其它候选在报告里显示未核验许可，云端 `rembg` 模型会被本地生成流程拒绝。BRIA 与 U2NetP 已在苹果、香蕉、梨固定裁剪上做过同图 CPU 实测；当前无像素级标注，不能当成精度基准。抠图不替代 VOID 视频补洞。Workbench 应分别显示所选模型、权重 SHA 和许可状态。遮挡后补出的背景应标成“模型补全”，不作为实测碰撞表面。

1. 从任务对象清单带入杯子、容器等实体；用户只需在对象列表确认“背景支撑 / 可移动对象 / 机器人或手 / 忽略”，并查看关键帧 mask。运动变化仅作提示，不自动决定角色。
2. 背景清理入口按本机预检启用：Stage 1b 准备统一帧；对象提案 provider 默认走 DexAgent external，可选择 Gemini API/Vertex 或手动对象提示；结构化对象文本提示默认进入 LiteText 文本编码器 + SAM3 原生视频跟踪器，可切换 SimFoundry 的 SAM3 mask + SAM2 传播作对照。系统为每个对象寻找一个可见种子帧，双向跟踪后由人工检查整段 mask，再运行 VOID Pass 1/2 并复核补洞视频。相机扫描再可选运行完整 A 重建与 AutoBG，固定机位操作演示只允许做 mask/补洞冒烟测试。全量 A 重建复用已确认对象类别为 `s5_scene.force_categories`，由 SAM3 分割并跳过重复的 Gemini 对象提案；关键帧使用几何启发式选择。漏检时回到对象列表补充或调整角色后重跑，不把运动检测当成必须的新算法。检查器明确说明隔离显示只用于查看，只有重新生成清理产物才会从背景中移除实体。图像编辑等其它 Gemini 直接调用仍待 provider 适配。
3. 背景、支撑碰撞、操作对象和机器人分别保存在原生场景中，再进入已有编辑、目标运行时检查和数据回放。

界面对应一个连续流程：**视频与任务 → 对象/背景确认 → 场景编辑 → 仿真验收 → 数据回放**。先接已有结果和操作，再逐项开放已验证的自动执行。

后续验收至少包括：单独显示背景时无动态实体残影；移动或移除一个对象后原位置不留下同一对象；实体/支撑碰撞由目标运行时分别核验；相机、单位、坐标与各层来源一致。该要求与候选物理验收一起交接给 pipeline 工作流。

### 8.6 已接入的 MJCF 候选物理验收

2026-10-20 新增完整 DexAgent YAM + Sharpa MJCF 任务场景的布局候选入口。复用既有刚体编译、SharpaBridge 导入审计、EpisodeRecorder 和 Viser；页面只提交自由物体平移，不新建场景 DSL 或物理引擎。将 MJCF 展开并复制全部引用资产，以 SHA 绑定源版本、候选、原生报告和录制；续编记录父候选，重试使用冻结快照。CPU 物理检查初态穿透、3 秒落稳、位移/旋转、末窗速度、采样穿透和机器人保持误差。

默认 8765 已真实跑通创建、验收、同版本重试与 3D 回放；杯子平移 1 厘米的正常布局通过，抬高 15 厘米的负例被漂移/穿透检查拒绝。该结果仅表示布局验收，任务成功未评估，训练发布未放行。OG/USD 编辑候选仍需保真物理导入；不能用当前含 Robotiq 的 OG 机器人或非均匀缩放物体替换成不等价的单刚体代理。证据见[候选场景验证记录](docs/WORKBENCH_SCENE_CANDIDATES_20261020.md)。

### 8.7 已验收候选的原生任务重新规划

候选通过必需物理检查后，可在任务页点击“基于候选重新规划”。沿父候选版本链找到原任务，核对原始场景、视频、语义、相机和来源 recipe 的内容身份；启动前冻结这些输入和候选验收报告。原视频表达任务意图，不作为新布局的像素位置真值；旧 Stage 3 控制与轨迹不进入新规划输入。

复用原 Stage 2 proposal 和既有 `compile_rigid`，只映射候选自由物体初态；编译后完整物理模型摘要必须与 RSS 已验收候选一致。每次运行有独立 external transport，仍由原生 Stage 3 生成新动作并执行完整门禁；重试使用封存的输入快照，缺文件、内容变化或编译环境不一致时拒绝继续。实际预检与规划工具调用已跑通，新布局的完整任务成功尚未确认，详见[重新规划验证记录](docs/WORKBENCH_TASK_REPLANNING_20261020.md)。

## 9. 管理 API 与执行生命周期

### 9.1 首批 API

统一管理命名空间为 `/api/workbench/v1`，避免与原生编辑器 `/api` 混淆。当前实际接口以服务 `/openapi.json` 和 [可运行说明](workbench/README.md) 为准。

| 方法 / 路径 | 内容 | 阶段 |
| --- | --- | --- |
| GET `/capabilities` | UI-1 提供真实能力与阻塞原因；UI-4 扩展已验证的阶段模型选项 | UI-1/UI-4 |
| GET `/executions/catalog`、GET / POST `/executions`、GET `/executions/{id}` | 配置目录、受管启动与进程状态；CLI 原生报告独立验收 | UI-3（已接入） |
| POST `/executions/{id}/stop`、`/executions/{id}/retry`、GET `/executions/{id}/log` | 停止带运行标识的工作进程；以冻结配置创建新运行；有界日志 | UI-3（已接入） |
| GET `/executions/tasks/{run_id}` | 读取已验收候选的源任务和输入版本；提交 `replan` 时核对同一摘要 | UI-3（已接入） |
| POST / GET `/projects` | 创建项目、列出关联项目 | UI-1 |
| POST `/imports` | 按已配置根目录导入历史运行，不修改原文件 | UI-1 |
| GET `/runs`、`/runs/{id}` | 逻辑运行列表、范围、分层状态与阶段尝试 | UI-1 |
| GET `/runs/{id}/logs`、`/artifacts/{id}/text` | 日志列表与有界字节游标读取；阶段/调用历史包含在运行详情中 | UI-1 |
| GET `/runs/{id}/artifacts`、`/artifacts/{id}`、`/artifacts/{id}/content` | 产物列表、元信息、受控媒体/下载 | UI-1 |
| GET `/runs/{id}/episodes`、`/artifacts/{id}/series` | 原生 episode 元信息和允许字段曲线 | UI-1/2 |
| GET `/episode-catalog` | 跨运行只读 EpisodeStore 目录；支持关键词、项目、原始结果、元信息完整度和分页 | UI-2（浏览） |
| POST `/runs/{id}/preview`、GET `/previews/{id}`、`/previews/{id}/viewer` | 核对身份后生成或复用记录式 3D；版本变化提示为快照 | UI-2（记录式） |
| GET `/tools` | 本机工具索引和声明详情，支持搜索与分页 | UI-1 |
| GET `/scenes`；POST / GET `/editors`、GET `/editors/{id}`、POST `/editors/{id}/stop` | 原生场景目录、受管副本编辑会话、真实状态与退出 | UI-2（离线编辑） |
| GET `/scenes/{id}/revisions` | 跨会话候选历史与分页、来源哈希、父版本；POST `/editors` 可指定候选 ID 与期望哈希继续编辑 | UI-2（离线编辑） |
| GET `/editors/{id}/revisions/{revision}`、`/editors/{id}/log`、`/editors/{id}/view/…` | 候选文件下载、进程日志与原生同源代理 | UI-2（离线编辑） |

下列接口仍是后续目标，当前服务未实现：

| 方法 / 路径 | 内容 | 阶段 |
| --- | --- | --- |
| POST / GET `/studio/sessions` | 创建或附着已登记会话，查询真实状态 | UI-2 |
| POST `/studio/sessions/{id}/commands` | 原生命令白名单，返回 operation | UI-2 |
| POST `/scenes/{id}/revisions/{revision}/validate` | 为确定版本创建物理检查；接受/发布状态须绑定检查证据 | UI-2/4 |
| POST `/runs` | 根据模板与通过预检查的配置创建调用 | UI-3 |
| POST `/runs/{id}/cancel`、`/runs/{id}/retry` | 取消受管任务，或建立新的尝试/调用 | UI-3 |
| POST `/reviews` | 针对确定哈希的审核记录 | UI-3 |
| GET `/operations/{id}`、`/runs/{id}/events` | 异步操作结果、SSE 增量事件 | UI-2/3 |
| POST `/datasets/{id}/exports` | 通过导出门禁后创建格式转换任务 | UI-4 |

`capabilities` 区分“源码存在”“环境检查通过”“真实运行验证”“未验证/阻塞”。某个 Python 包可 import 不等于完整功能可用；这些判定由后端产生，前端不硬编码为全部可用。

创建、重试和其他写操作使用幂等键；同一键且同一请求返回原 operation，不同载荷返回冲突。修改带 expected revision，后端事务检查，避免双击和旧页面覆盖。

### 9.2 调度与取消

1. 先持久化调用清单和资源申请，再由唯一 Supervisor 启动已注册命令模板。参数使用 argv，禁止 `shell=True` 与浏览器提交任意命令。
2. 第一版管理服务用单 worker，禁止为提高吞吐而让多个 Web worker 各自创建一套调度循环。
3. 记录 PID、进程启动身份、进程组、后端类型与输出目录。当前上游可能创建独立子进程组，取消必须验证整条受管进程树结束，不能只杀最外层 Python。
4. 取消先进入 cancelling，保留日志，按后端支持的协作停止/终止机制执行；确认退出后才进入 cancelled。已自然完成时返回真实终态。
5. 服务重启先核对进程身份与报告；无法确认的调用记 interrupted/unknown 并提供恢复方案，不凭复用 PID 认领进程，不自动重新运行昂贵任务。
6. GPU、场景和 external transport 分别锁定。首版生成串行；只读浏览不被执行锁阻塞。共享工具库会被 Agent 写入时也限制并发，不能仅隔离 transport 就宣称支持多任务生成。

### 9.3 新调用与恢复不能覆盖历史

现有 `run_dexagent.py` 会更新同名目录的 summary 和阶段报告。新工作台调用应使用独立配置快照和隔离工作目录；不能只增加数据库 attempt 编号，磁盘仍写回旧目录。

恢复需要适配当前 `run_dir = output_dir / run.name / stage` 的真实约定：将明确选择的上游输入以校验副本或受保护引用物化到新调用中，记录来源/哈希并复用已有身份门禁。禁止用可写硬链接把旧的已验收文件暴露给新尝试。此恢复适配通过测试前，先只开放无继承的新任务和已有原生恢复方式，不伪造“一键安全重试”。

网页审核由管理层按 `--from/--to` 边界调度调用；不对 `--confirm-stages` 的 stdin 自动发送 y。重试影响的下游在逻辑运行里失效，但旧结果仍可查看；新版本通过后再更新 accepted 指针。

### 9.4 external Agent 接入

保留当前无模型 API 的模式。每次受管调用使用独立 transport 目录，继续验证 session/request ID 和原有响应 schema。

第一版提供等待状态、请求摘要、超时和操作指引，由现有 Codex 会话完成响应。当前源码没有自动 Codex 子进程，工作台不能宣称具备无人值守 Agent。后续若接入正式会话服务，再独立实现连接状态、授权和恢复；不把普通聊天输入框当成可执行 Agent 接入。

## 10. 仓库落地方式

已在当前仓库新增独立 workbench。首版采用按职责划分的少量文件，没有预先搭建多层 controller/service/plugin 结构：

~~~text
DexAgent/
├── run_workbench.py
├── requirements-workbench.txt
├── workbench/
│   ├── api.py                        # API、生命周期和 SPA 入口
│   ├── models.py                     # Pydantic 契约与 OpenAPI
│   ├── reports.py                    # 复用原生验收口径
│   ├── index.py                      # 文件索引、来源与项目关联
│   ├── previews.py                   # 有界可视化任务与缓存
│   ├── native_preview.py             # 隔离进程生成原生 Viser 回放
│   ├── frontend/
│   │   └── src/
│   │       ├── App.tsx               # 统一 Shell 与路由
│   │       ├── RunWorkbench.tsx      # 阶段、视图和检查器
│   │       ├── pages.tsx             # 项目、工具、场景与数据浏览
│   │       ├── components.tsx        # 公共状态、媒体和验收展示
│   │       ├── NativeScene.tsx       # 原生 3D 内嵌
│   │       ├── SeriesPlot.tsx        # 按需加载的轨迹曲线
│   │       ├── generated/api.ts      # 从 OpenAPI 生成的类型
│   │       └── styles.css            # token、布局和响应式规则
│   └── tests/                        # 契约、故障与进程生命周期测试
└── .workbench/                       # 已加入 gitignore
    ├── metadata.sqlite
    ├── venv/                         # 独立服务依赖
    └── previews/
~~~

开发态用 Vite 代理 API，构建后由同一服务提供 SPA、媒体和原生离线 3D 页面。预览转换在独立 CPU 进程进行；生成后的浏览不需要转发第二个端口。远程 IDE 转发工作台端口即可。

当前启动入口为 `run_workbench.py`，默认 127.0.0.1:8765。运行目录、管理数据目录和额外媒体根目录可通过明确参数配置；启动界面不自动运行 pipeline 或物理。

公共的状态、媒体预览和 GateReport 已复用。组件按实际复用边界拆分，不把每个布局区域单独封装成一层模块。

## 11. 分阶段交付与优先级

以下 UI 编号只表示前端集成里程碑，与融合总方案的算法 P0–P5 分开验收。

| 阶段 | 交付内容 | 必须通过的验收 |
| --- | --- | --- |
| UI-0：基线与契约 | 当前源码审查、代表数据、页面结构、状态模型；冻结已知与待验证能力 | 来源可定位；算法通过、恢复完成与发布资格不混淆。本轮完成规划核查，未执行新仿真验收 |
| UI-1：真实数据工作台（已实现） | FastAPI 只读索引、真实状态总览、项目/归档、运行详情、媒体预览、工具库；统一 Shell | 标准案例和真实失败案例可用；总览状态来自现有 API；刷新不重复执行；警告、执行范围与缺失字段正确 |
| UI-2：原位场景与回放（离线功能已实现） | 两种刚体记录的原生 3D、轨迹曲线和 Light Editor 场景副本编辑；候选历史与续编；物理控制待接入 | 原生记录核对身份、时钟且物理步进为零；编辑候选保存不覆盖源文件，切页/续编检查未保存修改；版本哈希与所属场景核对，父版本可追溯 |
| UI-3：受控任务闭环（执行管理已接入） | 已有配置启动 CLI、日志、external 等待、停止、新运行重试；分阶段审核和局部恢复仍待验收 | 至少一个受管任务有真实执行结果；取消整个受管进程树；重试保留源结果；审核绑定当前版本 |
| UI-4：生成配置与数据发布 | 已验证的 SimFoundry 生成子集、按阶段选择已适配模型、跨后端资产交接、批量数据和格式转换 | 模型身份可追溯；目标运行时门禁通过；独立数据 loader 回读导出；来源与字段映射完整 |

### 11.1 第一轮实际实施顺序

1. **先做报告适配与代表样本。** 以标准任务、RSS reference、真实失败和缺失元信息 episode 建立 fixtures，输出统一 RunView；不先画一套依赖虚构状态的全站页面。
2. **接只读 API 与受控媒体。** 跑通运行列表、详情、报告、视频 Range 和本机工具索引。
3. **搭统一 Shell 与运行工作台。** 优先实现第 4 节闭环；场景页只展示已支持预览和明确的会话能力，不填假 3D 内容。
4. **做 Viewer 接入验证。** 验证 RSS 原生控制、Light Editor 根路径冲突、会话恢复与未保存修改；明确最小适配补丁。
5. **逐项开放写操作。** 先原生会话命令，再任务创建、取消和阶段审核；隔离恢复适配通过后再开放对应重试。
6. **最后增加昂贵生成与训练格式导出。** 按实际能力放行，不以页面完成代替后端验收。

UI-1 及 UI-2 的离线编辑/回放已构成可运行的可视化工作台；“数据与回放”现在也有跨运行只读 Episode 目录，可按项目、原始结果和元信息完整度查找并回到来源运行。该目录不创建数据集、不认证物理验收或发布资格。CLI 启动/日志/停止/新运行重试与 external 等待已接通；默认 8765 实际 RSS 刚体运行 9/9 检查通过，执行管理及回归证据见[验证记录](docs/WORKBENCH_EXECUTIONS_20261020.md)。MJCF 候选的离线 RSS 验收已接通，见第 8.6 节。实时物理控制、OG/USD 候选物理导入与正式发布、自动修复编排、SimFoundry 完整 pipeline 接入、数据集审核和训练导出仍未完成；不把可视化交付宣布成整个融合系统已完成。

### 11.2 首轮工作项

| 优先级 | 工作项 | 主要落点 | 完成依据 |
| --- | --- | --- | --- |
| P0 | 状态/范围/来源 schema，标准报告解析 | backend schemas + adapters | 标准任务同时呈现范围完成、跨调用证据、阶段警告和未发布 |
| P0 | 真实根目录索引与历史导入 | indexer + storage + imports API | 不移动旧目录；报告版本变化可追踪；缺损目录可诊断 |
| P0 | Artifact 元信息、预览、Range、日志 cursor | media + artifacts/logs API | 可播放现有视频，长日志增量读取，拒绝越界文件 |
| P0 | Shell、项目列表、Run 工作台 | frontend app + runs/stages | 可直达链接、刷新恢复上下文、查看检查证据 |
| P1 | 本地工具库与运行工具调用关联 | tools API + frontend tools | 内容来自当前工作树，声明状态不冒充现场实测 |
| P1 | 兼容性 Episode 索引与曲线 | rss adapter + episodes | 无 run_meta 的历史记录可浏览；缺失字段不补造 |
| P1 | 跨运行 Episode 目录 | `/episode-catalog` + `/datasets` | 按项目、原始结果、元信息和关键词分页筛选；回到来源运行；不标记验收或发布 |
| P1 | RSS 与 Light Editor 接入验证 | studio bridge + viewer adapter | 真浏览器完成加载、控制/保存和断线恢复 |
| P2 | 新调用、资源锁、external 等待、取消 | supervisor + operations | 任务和浏览器生命周期独立，操作幂等且终态可信 |

## 12. 验证安排

只对会影响结果可信度、数据完整性和用户关键路径的行为增加测试。文档改写本身不需要运行整套 GPU 回归；实现到对应阶段时再执行相应验证。

- **报告契约（pytest）**：范围完成、跨调用汇总、warning、smoke、缺失 release 信息、未知 schema、部分写入；确保原始结果和解释不丢失。
- **读 API（pytest）**：分页/过滤、导入幂等、文件根目录与符号链接校验、日志截断恢复、视频 Range，以及错误不会变成“没有数据”。
- **执行监督（隔离子进程测试）**：重复启动/取消/重试、服务重启核对、子进程创建新 session、external session 串线、资源互斥；不需要 GPU。
- **前端关键行为（Vitest / React Testing Library）**：分层状态和范围说明、动作 pending/disabled 原因、报告证据跳转、脏场景切换处理。
- **真实数据浏览（Playwright）**：打开标准任务与失败任务，播放现有媒体，切换页签/刷新，检查 URL 上下文和原目录未改写。
- **真实 Viewer 验证**：单独运行支持的 RSS 场景，检验 pause/resume/reset/run/check、动态端口、WebSocket、录制和重连；Light Editor 用真实场景保存完整 snapshot 并核查 revision。
- **恢复验证**：上游输入被继承时仍满足原有来源和配置身份检查；新尝试无法改动旧文件；失败不覆盖已接受产物。
- **导出验证（UI-4）**：独立 loader 回读，核对 frame/action/observation 时序、相机、单位、机器人和公开字段；原生 NPZ 可下载不等于训练格式验收通过。

第一批真实证据入口：

| 用例 | 位置 | 需要验证的产品行为 |
| --- | --- | --- |
| 恢复后完成的标准任务 | [完整管线核查](docs/FULL_PIPELINE_VALIDATION_20261016.md) | 正确表达多次调用、Stage 2 警告和 Stage 4 子步骤 |
| RSS reference 成功 | [stage_report.json](runs/rss_reference_cpu_final_20261016/stage_report.json) | 37/37 必需检查通过，同时保留上游参考复用范围 |
| RSS reference 开环失败 | [stage_report.json](runs/rss_reference_raw_blocking_final_20261016/stage_report.json) | 第一子目标失败，下游受阻，不能显示完整成功 |
| 历史元信息不完整的 episode | [meta.json](runs/rss_reference_cpu_final_20261016/episodes/0000/meta.json) | task/embodiment 为空、缺少 run meta 时仍可浏览且标记未知 |

## 13. 本轮交付与实施边界

已完成源码核查、总览首页、可运行工作台、报告/媒体/工具接入、原生曲线和记录式 3D。派生 3D 使用 CPU 进程复用现有转换与显示，不修改源目录、算法、策略或 pipeline，也不重新执行物理。

当前仓库的其他未提交代码保留。实际功能、启动命令与验证入口见 [工作台说明](workbench/README.md)。后续补齐实时物理会话、候选验收与跨来源交接，并持续按真实操作改进布局、配色和交互；pipeline 优化不由本工作流重复实现。

### 源码定位索引

以下行号对应本轮核查时的工作树，后续代码变更可能移动：

| 核查点 | 本地位置 |
| --- | --- |
| 阶段目录约定 | `agent/config.py:105` |
| 顶层入口与范围参数 | `run_dexagent.py:530` |
| 运行范围与最终状态 | `run_dexagent.py:732` |
| 进度快照及非原子写入 | `agent/console.py:233`、`agent/console.py:240` |
| external 请求与等待 | `agent/external.py:98` |
| 必需检查与 warning 口径 | `agent/run_reports.py:47` |
| 后端验收脚本入口 | `run_robosimstudio.py:404` |
| RSS 主线程命令分发与 status | `robosimstudio/robosimstudio_real2sim_agent/server.py:531`、`:543` |
| RSS 任意 Python 命令 | `robosimstudio/robosimstudio_real2sim_agent/server.py:623` |
| EpisodeStore 历史读取 | `robosimstudio/robosimstudio/record/store.py:144` |
| SimFoundry 原生阶段计划 | `SimFoundry/simfoundry/pipeline/orchestrator.py:26` |
| SimFoundry 阶段状态及汇总输出 | `SimFoundry/simfoundry/pipeline/stage_utils.py:59`、`SimFoundry/simfoundry/pipeline/reporting.py:123` |
| Light Editor API 入口 | `SimFoundry/scripts/interactive/light_editor/server.py:739`、`:2140` |
| Light Editor 根路径请求 | `SimFoundry/scripts/interactive/light_editor/web/app.js:819` |
| 现有工具页远端/快照读取 | `library_visualizer/index.html:168` |
