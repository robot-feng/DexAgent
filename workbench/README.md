# EmbodiForge Studio

EmbodiForge Studio 统一管理 DexAgent 与 RoboSimStudio 的运行、场景、轨迹和验收证据，并通过原生编辑器布置场景副本。“任务执行”页在用户启动后调用已有 CLI；“背景清理”页独立管理帧准备、mask、VOID 补洞和可选场景重建。项目归属、执行记录、清理任务与可视化缓存单独保存。

当前可用：

- 总览页读取真实运行数、场景数、最近运行和可用能力；存在未关闭的场景编辑时可从总览继续进入。
- 项目归档、运行搜索、状态筛选和可分享的本机页面路径。
- 任务执行：选择配置、输入视频/任务与执行范围，启动既有 DexAgent/RSS CLI，查看日志、停止并以原配置快照创建新运行重试。external Agent 请求按运行隔离，等待响应时显示当前请求路径。
- 阶段与历史报告、Stage 4 子步骤、必需检查、阈值、警告和失败证据。
- 原视频、仿真/合成视频、图片、日志及原生文件预览；视频支持 HTTP Range。
- 原生 EpisodeStore 元信息、关节/action 曲线，以及 DexAgent 的 qpos/qvel/ctrl 曲线；保留各自记录时钟、坐标名称和缺失字段提示。
- “数据与回放”提供跨项目原生 Episode 目录，可按关键词、项目、记录结果和元信息完整度筛选、分页，并跳回来源运行；仍以原始 EpisodeStore 记录为准。
- RSS 刚体记录与 DexAgent rigid_robot 轨迹的原生 Viser 3D 回放。生成过程使用现有 SharpaBridge / StateReplay，核对场景、资产、坐标身份和记录时钟，不执行物理。
- RoboSimStudio live 会话只读状态卡片：显示真实仿真步数、时钟、暂停/运行、数值状态和实体位姿，并可跳转原生 Viewer；不启动、暂停或驱动仿真。
- SimFoundry scene JSON 的原位布局编辑：保留原生房间网格、贴图与机器人；支持选中、变换、保存候选、跨会话历史与继续编辑，离开或续编时检查未保存修改。
- 背景清理：浏览 SimFoundry 示例与本地视频；对象提案可选 DexAgent/Codex external、Gemini API/Vertex 或手动提示词；SAM3 单模型视频跟踪与原流程 SAM3 mask + SAM2 传播共用同一 mask 契约。确认关键帧/跨帧 mask 后再运行 VOID，并复核补洞视频。相机扫描可继续进入原生 A 重建与 AutoBG 对齐；操作演示仅用于清理冒烟测试。
- 当前工作树中的工具库检索及输入输出声明；不会执行浏览器提交的 Python。

## 启动

先激活已有 DexAgent Python 3.12 环境。独立虚拟环境只安装可视化服务依赖，避免改动仿真环境：

```bash
python -m venv --system-site-packages .workbench/venv
.workbench/venv/bin/python -m pip install -r requirements-workbench.txt
cd workbench/frontend
npm ci
npm run build
cd ../..
.workbench/venv/bin/python run_workbench.py
```

打开 `http://127.0.0.1:8765`。远程 IDE 使用端口转发或 SSH 隧道转发 8765；普通浏览、曲线和生成后的 3D 回放都通过这一入口访问。若同时查看 RSS 原生 Viewer，还需转发 RSS 自己监听的端口（默认 8080）。

要接入一个正在运行的 RoboSimStudio live 页，在启动 Workbench 时设置它的 run 目录。RSS live 服务必须已经运行，且其 `ctl.sock` 对 Workbench 进程可访问：

```bash
WORKBENCH_RSS_RUN_DIR=/path/to/rss/run \
  .workbench/venv/bin/python run_workbench.py
```

EmbodiForge Studio 每 2 秒通过 RSS Unix socket 请求一次 `status`，只读返回的运行信息；连接失败会显示具体状态，不会自动启动 RSS。页面上的“打开 RSS 原生 Viewer”链接指向 RSS 自己的 Viser 页面。实时控制和物理验收仍由 RSS 原生工具负责。

在另一台电脑上直接访问服务器时，`127.0.0.1` 指向那台电脑，需要先转发端口。同一内网也可显式启动 IPv4 网络监听，再访问服务器的内网 IP：

```bash
.workbench/venv/bin/python run_workbench.py --host 0.0.0.0 --port 8765
# 浏览器打开 http://<服务器内网IP>:8765
```

Node 环境已在 22.23.2 验证。服务不自动安装或启动 RSS；3D 生成还需要现有 `requirements-robosimstudio.txt` 对应环境。纯报告、媒体与工具浏览不加载物理引擎。

```bash
# 可替换运行目录和管理数据目录；不会移动原产物
.workbench/venv/bin/python run_workbench.py --runs-root /path/to/runs --state-dir /path/to/workbench-state

# 显式允许额外输入媒体目录，浏览器仍通过 artifact ID 访问文件
.workbench/venv/bin/python run_workbench.py --artifact-root /path/to/input-videos

# 让“背景清理”页浏览自定义视频目录
.workbench/venv/bin/python run_workbench.py --cleanup-video-root /path/to/input-videos
```

不要对外网直接开放未配置认证的本机服务。默认仅监听 127.0.0.1。

## 使用

### 启动与管理任务

进入 `/executions`，选择项目根目录中的 YAML 配置。视频流程可覆盖视频、任务描述、评估 profile 和终止阶段；RSS 验收可选择既有示例或参考运行。启动前会保存独立配置快照，原配置和已有运行不会被覆盖。服务中的执行槽一次只允许一个运行，不限制用户在其它终端独立启动的任务。

外部 Agent 后端不自动调用模型 API；当前请求在运行自己的 `external/` 中。页面出现“等待 Agent”后，可复制请求路径交给 Agent，通过现有 `agent.external inspect/respond` 协议继续。提交响应后该提示自动消失。

停止会先尝试中断，再升级信号，并核对带本次运行标识的子进程；包含 Stage 4/渲染器创建的独立会话。服务正常关闭也会停止其管理的任务。重试复制原配置快照并创建新目录；源 YAML 后续修改不会影响这次重试。进程退出码和原生验收结果分开显示，退出码 0 不代表任务成功或训练数据已发布。详见[执行管理验证](../docs/WORKBENCH_EXECUTIONS_20261020.md)。

### 浏览已有产物

1. 在“项目”页搜索已有运行；自动索引保留源目录，创建项目后可在运行标题栏调整归属。
2. 打开工作台，左侧选择阶段，在“验收”页展开对应检查。右侧原始报告可直接预览或下载。
3. “概览”选择原视频、原始布局、随机布局或 contact sheet。播放位置在当前浏览器会话中恢复。
4. “场景”中，具备受支持记录的运行可生成 3D 回放；首次生成可能需要编译 CPU 可视化内核。完成后直接在页面中旋转、缩放、播放和拖动原生时间轴。源文件版本变化时保留快照并提示确认当前版本；确认后按完整内容摘要加载对应回放。
5. “轨迹与回放”中选择原生记录。RSS 支持 `joint_q / joint_qd / action / reward`；DexAgent 支持 `qpos / qvel / ctrl`，按 `time_s` 绘制，并在记录提供时显示坐标/执行器名称。未记录单位映射时明确提示，不推断所有通道均为角度或力矩。
6. 底部日志支持查看开头/末尾、暂停跟随和完整下载；向上阅读时暂停自动跟随。

总览只汇集已有 API 状态，不启动新的 DexAgent/RSS 运行。执行范围、机器验收和发布资格分别显示。`scope_completed` 不等于单次 Stage 1–4 完成；缺失报告、未知 schema、损坏 JSON、未绑定的跨调用汇总均不能产生绿色通过。视频背景清理是独立入口，运行前展示当前 SimFoundry/VOID 环境状态。

### 背景清理 Agent 提案

默认对象提案走 DexAgent 已有的 `external` 请求/响应协议，不调用 Gemini API。任务抽帧后，详情页会显示隔离的 transport 目录；在 DexAgent Python 环境中读取请求：

```bash
python -m agent.external wait "/path/from/workbench" --timeout 60
python -m agent.external inspect "/path/to/requests/<session-turn>.json"
```

按请求中的 `propose_objects` schema 写响应 JSON（图像文件路径位于请求中），再提交：

```json
[{"type":"tool_use","id":"proposal-1","name":"propose_objects","input":{"objects":["robot arm","gripper"],"evidence":"visible foreground robot"}}]
```

```bash
python -m agent.external respond "/path/to/requests/<session-turn>.json" /path/to/response.json
```

响应需要和本任务 request/session 匹配；每个任务有独立 transport，结果与图像留在该任务目录供审计。全量构建会复用已确认的对象类别作为 SimFoundry Stage 5 `force_categories`，跳过重复的 Gemini 对象提案，并使用启发式关键帧选择。Gemini API/Vertex 仍可作为提案 provider 选项；图像补全等其它上游模型调用尚未全部接入 provider 协议，因此构建预检仍会报告这些兼容要求。预检遵循 `SIMFOUNDRY_GEMINI_BACKEND`：Vertex 路由要求项目 ID 和带有已识别类型/必填字段的 ADC JSON；API key 路由要求 key；`auto` 则优先 key，再检查 Vertex。预检只检查本地凭据配置，不验证模型权限或配额。

### 场景布局编辑

编辑器必须使用独立环境，避免 standalone `usd-core` 与 Isaac Sim 的 `pxr` 混用：

```bash
python -m venv .workbench/editor-venv
.workbench/editor-venv/bin/python -m pip install -r SimFoundry/scripts/interactive/light_editor/requirements.txt

# 默认解释器为 .workbench/editor-venv/bin/python；可显式指向其他独立环境
.workbench/venv/bin/python run_workbench.py \
  --scene-root /path/to/simfoundry-scenes \
  --editor-python /path/to/editor-venv/bin/python \
  --robot-assets /path/to/omnigibson-robot-assets
```

在“场景与资产”选择可编辑的原生场景，打开编辑器。场景绑定 run 之外的独立 scene ID；不把官方示例误认成当前 DexAgent 运行的重建结果。所有静态资源和 API 经工作台同一入口访问，用户只需一个转发端口。

受管会话以 `.workbench/editors/<session>/` 中的场景副本为编译来源，把相对 USD 引用按原文件目录解析。`metadata.workbench_source` 保存来源路径和摘要；原 JSON 与原 USD 不变。保存后可下载时间戳候选，物体移动会沿用原生逻辑清零速度。候选仍引用本机资产，下载 JSON 不等于获得独立可移植资产包。

“已保存的候选版本”按场景汇总历史会话并分页。点击“继续编辑”后核对候选所属场景与当前内容 SHA-256，再创建新副本；当前会话有未保存修改时，先由原生 Save / Discard / Stay 处理。新会话及后续候选记录父会话、候选 ID 和内容摘要。原场景后续变化会提示版本差异，旧候选仍按其记录的来源继续编辑；这不代表通过了物理验收。

当前受管编辑聚焦场景候选：不覆盖 `_latest`，不写共享任务 YAML、相机 YAML、房间 sidecar 或发布包。这些按钮隐藏或明确拒绝；原生独立编辑器仍保留原能力。场景/任务生成与物理验收由各原生流程处理。

刷新会附着既有会话；切换工作台页面不重启进程。未保存修改通过原生 Save / Discard / Stay 对话框处理；临时失联保留 iframe，停止/超时/服务退出会清理受管进程。当前仅承诺单用户单写者。

本机已验证的官方示例为 `droid_desk_throw_away_trash`，文件位于 `.workbench/resources/simfoundry/`。环境和资产不提交。需要重建示例时，可使用 Hugging Face CLI 按确定版本下载：

```bash
hf download nadunRanawaka1/simfoundry-assets \
  --revision beac75a98d99a881eacff25dd53a3ec9ce8efcfc \
  --include 'assets/scenes/DROID/droid_desk_throw_away_trash/**' 'assets/backgrounds/mesh_backgrounds/droid_v1*' \
  --local-dir .workbench/resources/simfoundry
hf download behavior-1k/omnigibson-robot-assets \
  --revision c325bd3b6d1560d3145f3c9c0e885e79e47f180c \
  --include 'models/franka/franka_robotiq/usd/franka_robotiq.usda' \
  --local-dir .workbench/resources/simfoundry/robot-assets
```

### 页面与 3D 背景

页面参考 Light Editor 的中性深灰：底色 `#14161a`，面板 `#1d2026`，分隔 `#343a44`；统一选中态与状态色，减少灰蓝色常量分散。3D 使用源场景的环境几何和材质，不给 DexAgent 回放套用其他任务的房间。RSS 回放复用 `LightingRig` 的主光/补光且关闭外部 HDRI，保留源模型和记录时钟。

编辑器上方“分层检查”可隔离背景或机器人与物体，并恢复全部显示。沿用原生显示开关，隐藏不会删除资产或修改候选。扫描背景可能仍含原机器人/物体残影；实体分开登记和纹理可加载不等于背景已清理。当前官方 `droid_v1` 示例的背景清理与物理碰撞仍未验收，详见[场景分离要求](../RobotSim_Workbench_前端与可视化实施方案.md#85-扫描背景与独立实体分离)。

### 3D 支持范围

- RSS：需要 `original_source_scene.xml`、`sharpa_manifest.json` 和原生 episode 的 `steps.npz / privileged.npz`。模型、资产哈希及 body label 顺序必须与记录一致。
- DexAgent：需要 `stage3/stage4_task` 中的 `scene.xml / trajectory.npz` 及 Stage 4 输入身份报告。场景哈希、每列坐标身份、资产摘要、qpos 与物理子步时间绑定必须匹配。通过已有转换器显示记录姿态；不会重新执行动作或重新评定任务成功。
- 当前导入路径面向现有 56 关节 Sharpa 刚体工作站；不将它泛化为所有机器人或柔性场景均已兼容。缺少身份或状态证据时拒绝生成并显示原因。
- 默认均匀选取最多 301 帧作交互式预览；完整原始轨迹与原验收报告保留。该预览不是新的仿真验收或训练数据。

## 开发与验证

后端入口为 `workbench.api.create_app`，管理 API 前缀是 `/api/workbench/v1`。文件索引、项目归属和缓存位于 `.workbench/`，不提交环境、产物和浏览器截图。

```bash
# 在已激活的开发环境中提供 pytest/httpx
.workbench/venv/bin/python -m pytest -q workbench/tests

# 修改 Pydantic 契约后重新生成前端类型
.workbench/venv/bin/python run_workbench.py --openapi .workbench/openapi.json
cd workbench/frontend
npm run generate:api
npm run build

# 前端开发：API 服务仍在 8765，Vite 代理 /api
npm run dev

# 服务启动且存在文末的真实验收记录时运行
npx playwright install chromium
npm run test:e2e
```

TypeScript 类型由服务端 OpenAPI 生成。TypeScript 固定 5.9.3 以匹配当前类型生成器；Playwright 固定 1.62.1 以兼容本机 Ubuntu 20.04。图表单独延迟加载，不影响报告与视频页面的首包。

验证新前端时可使用独立构建与管理目录，保留原入口中的编辑会话：

```bash
npm --prefix workbench/frontend run build -- --outDir ../../.workbench/staging-dist --emptyOutDir
.workbench/venv/bin/python run_workbench.py \
  --port 8766 --state-dir .workbench/staging --frontend-dist .workbench/staging-dist \
  --scene-root .workbench/resources/simfoundry/assets/scenes \
  --editor-python .workbench/editor-venv/bin/python \
  --robot-assets .workbench/resources/simfoundry/robot-assets
WORKBENCH_URL=http://127.0.0.1:8766 npm --prefix workbench/frontend run test:e2e
```

候选与会话保存到各自管理目录；原始运行仍从同一只读根目录索引。原生编辑测试会创建并停止其测试会话，应使用上面的独立入口执行。

浏览器验收读取本机这些真实记录，不用模拟响应替代：

- `e2e_reference_layout_20261016`：跨调用完成、Stage 2 警告、真实视频和 DexAgent 3D。
- `rss_reference_cpu_final_20261016`：37 项必需检查、原生 episode、真实曲线和 RSS 3D。
- `rss_reference_raw_blocking_final_20261016`：子目标失败及下游受阻。
- `droid_desk_throw_away_trash_scene_state_latest`：官方 SimFoundry 扫描背景、纹理对象与原生编辑保存。未安装这个示例时，该浏览器用例明确跳过，不能据此宣称编辑渲染已通过。

单元/契约测试使用临时目录，覆盖身份损坏、缺失报告、路径越界、文件替换、日志轮换、可视化任务幂等、超时和进程释放。真实 3D 由独立浏览器验收证明，进程生命周期 stub 不用来宣称真实渲染通过。

损坏或类型错误的 episode 元信息保留在列表并显示原因，原文件可直接查看；元信息异常不当成“无记录”，也不认证为成功。

跨运行目录是只读浏览视图，不创建或改写数据集，也不把原始 `success` 标记解释为物理验收、审核通过或训练发布资格。字段完整度仅按当前可读取的原生元信息判定；数据集收集、质量门禁和训练格式导出仍未接入。

本次实际验证结果与限制见 [可视化验证记录](../docs/WORKBENCH_VALIDATION_20261019.md)。

## 尚未接入

实时物理仿真控制、场景候选的物理验收/发布、pipeline 启动与重试、训练格式导出仍需独立集成。当前离线布局编辑和记录回放不代表这些能力已经实现。pipeline 优化由另一个工作流负责，本目录只推进可视化功能和使用体验。
