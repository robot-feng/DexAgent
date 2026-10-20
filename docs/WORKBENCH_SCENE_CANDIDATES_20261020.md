# MJCF 场景候选与 RSS 验收（2026-10-20）

## 已接通的操作

默认服务仍为 `http://127.0.0.1:8765`。从完整 DexAgent 运行页点击“调整物体布局”，或进入 `/executions` 选择“任务场景候选 · RSS 落稳验收”。本轮聚焦已有 YAM + Sharpa 完整任务场景；SimFoundry 复现与背景清理由并行工作流推进。

1. 在配置中的原生 Python 环境读取任务 MJCF，显示初始自由物体与场景/资产的内容身份。
2. 以世界坐标、米、Z 向上调整物体位置。只修改自由物体初始平移，每轴 ±1 米；机器人、方向、尺度、材质和物理参数不作为页面编辑项。
3. 展开原生 MJCF，复制所有编译后引用资产到新运行的 `candidate/`，保存相对路径和 SHA 清单。使用既有 DexAgent 刚体编译、RSS `SharpaBridge`、导入审计和原生记录器。
4. 默认 CPU 物理运行 3 秒，机器人通过现有保持控制器停在初态；报告初始接触、位移、旋转、末 0.5 秒速度、采样穿透和机器人保持误差。
5. 在原生运行页查看具体阈值、视频、Episode 和 Viser 三维记录。回放核对场景、资产与 body 顺序，执行物理步数为零。
6. “从此候选继续调整”生成具有父候选身份的新版本。“以新运行重试”使用同一完整快照，不再读取可变的源布局。文件损坏、身份不符或未形成快照时拒绝重试。

原生 MJCF 仍是场景事实来源，`candidate/manifest.json` 只记录来源、位移、资产和身份，不引入第二套场景 DSL。新运行不覆盖源场景、旧候选或 `_latest`。

## 关键边界

- 场景通过不等于任务完成。报告与清单明确包含 `task_success_evaluated=false`、`training_eligible=false` 和 `policy_dataset_ready=false`；Episode 的任务成功状态在 Studio 中显示未评估，原生记录文件保持不变。
- 复用 DexAgent 既有刚体执行编译，包括其执行接触配置；导入审计比较该编译模型与 RSS。原输入 SHA、编译模型 SHA、全部引用资产 SHA 及运行代码来源分别保存。
- CPU 物理默认保留已有无滑移设置。显式选择 GPU 时，现有桥接器会记录其求解器适配与警告，不能将两种配置混同。
- 动态指标按控制间隔采样（本例 4 毫秒），不声称连续时间内无穿透；另检查编译源模型初始接触。
- 当前只编辑自由物体平移。关节、方向、尺度、支撑关系、批量布局、任务重新规划和正式发布仍待后续接入。
- Light Editor 的 OG/USD JSON 候选尚未接入此 RSS 路径。当前示例包含 Robotiq 机器人、非均匀缩放物体和含原机械臂残影的背景，不能替换成不等价的 Panda/单刚体碰撞代理来宣称复现完成。
- 布局改变后不能把旧轨迹重放成功当作新任务成功；后续必须重新规划并通过独立任务门禁。

## 原生验证证据

源场景：`runs/e2e_top_smoke_20261013/stage3/stage4_task/scene.xml`；初态含 56 个机器人关节和两个自由物体。初始场景/资产复合身份：`eb3a372b1bf0de486225a993c574191c626eb52407fe6d75ffc3fd054e2bf1b9`。

默认 8765 的真实浏览器流程完成后得到：

| 项目 | 结果 |
| --- | --- |
| 创建候选 | `studio_candidate_20261020_151602_cfb243`，任务 `cfb2439d1d8840c5aeef0b65` |
| 候选操作 | `cup_cup` 的 X 平移 +0.01 米，其余初态不变 |
| 原生检查 | 39/39 通过，其中必需检查 38/38；3 秒物理执行、750 控制记录、45 帧视频 |
| 最大漂移 | 杯子约 0.093 毫米，碗约 0.093 毫米；详见原生 `stage_report.json` |
| 候选身份 | `3b8a4d6002bab1e9edf94b8387e08fe7efe2c362e4ff7a1a63c92ab948a6dea6` |
| 按快照重试 | 任务 `c2f2c693c6a9431980d374b0`；新输出目录，相同候选身份，原生检查再次通过 |
| 回放 | 原生 RSS body 位姿，750 源帧、最多 301 预览帧，约 2.996 秒记录时长；模型身份通过，物理步数 0 |
| 任务/发布 | 未评估；`full_pipeline_complete=false`，Studio 发布资格 `not_evaluated` |

负例 `runs/rss_candidate_negative_final_20261020` 将杯子抬高 0.15 米。RSS 检测到约 0.1555 米最大漂移及 0.00548 米采样穿透，超过默认 0.01 米、0.004 米阈值，任务退出码为 1，原生报告失败，记录保留。该录制不是任务失败示范样本，而是失败布局的诊断证据。

## 自动检查与复现

```bash
.workbench/venv/bin/python -m pytest workbench/tests -q
cd workbench/frontend
npm run build
WORKBENCH_NATIVE_EXECUTION_TEST=1 npm exec playwright test -- e2e/candidates.spec.ts
```

候选契约测试覆盖位移与四元数、物理参数保留、资产独立复制、源文件不变、父版本续编、源/资产变化拒绝、快照篡改、非有限数/错误维数，以及场景录制的任务结果未评估。执行管理测试还覆盖候选来源与原生报告绑定、同快照重试和损坏拒绝。

浏览器测试 **2 passed（1.8 分钟）**：表单绑定读取版本、非法来源禁用启动；真实创建候选、物理执行、冻结版本重试及 Viser 回放。测试使用真实原生执行器，界面契约 mock 不作为物理证据。

共享工作树 **79 passed（32.07 秒）**。在并行 SAM3 LiteText 提交合入后，重新展开完整暂存代码验证 **77 passed、2 skipped（22.92 秒）**；跳过项是隔离目录未复制的可选 Light Editor 环境，已在共享工作树通过。两处旧环境预检测试现在显式选择其验证的 SAM3 后端，继续实际检查缺依赖拒绝启动和启动前重新探测，不依赖新的默认模型。

主目录与隔离暂存版本的 TypeScript/Vite 构建通过；隔离版本重生成的 TypeScript 客户端与暂存文件逐字节一致。现有 SeriesPlot 大 chunk 提示仍保留。桌面与 390 像素手机实测无横向溢出、无脚本错误；执行历史使用限高滚动列表，当前结果保留在主工作区。三维截图使用无头浏览器的软件 WebGL，原生 Viewer 如实提示其性能限制。

本机截图：`.workbench/previews/candidate-rss-validation-20261020.png`、`candidate-rss-mobile-20261020.png`、`candidate-rss-replay-20261020.png`。图片为本机验证产物，不纳入源码提交。

直接运行已有候选请求也可使用原生入口：

```bash
python run_robosimstudio.py candidate \
  --scene-request /path/to/scene_request.json \
  --config dexagent.yaml --output runs/new_candidate_run
```

请求由工作台在私有执行目录中生成，绑定读取版本和物体位移；运行目录保留原生报告、配置/代码来源、候选资产、录像与录制数据。该命令不调用 SimFoundry 重建。
