# EmbodiForge Stage 4 管线实跑记录

日期：2026-10-22

运行：`runs/embodiforge_runtime_replay_yam_sharpa_20261022`

Workbench：[`d313b177091d7af418ca308a`](http://127.0.0.1:8765/runs/d313b177091d7af418ca308a?view=workflow)

## 结论

本次完成了一个**有明确复用边界的部分端到端运行**：对蓝杯放入绿碗任务，当前运行重新记录并验证了 I2RT YAM + Sharpa Wave 的 MuJoCo 物理回放，并完成 Stage 4 的随机化、背景板、场景导出、渲染和合成。它不是从原始视频冷启动的九阶段全流程成功运行。

输入视频、Stage 2 场景与资产、Stage 1 任务语义、参考轨迹和 Stage 3 动作计划来自已缓存样例；本次没有重跑场景重建、背景/运动对象分离、任务模型推理、轨迹提取或跨本体重定向。运行级 `task_objective.json` 记录 `transferable_task_generation`，并如实标记 G1/G2 未测试、`aggregate_complete=false`。策略训练和闭环评估未运行，也没有生成原生 EpisodeStore 记录，因此输出视频和回放数组还不是训练合格 Episode。

## 各阶段输入与输出

| 阶段 | 本次输入 | 本次输出 / 验收 | 结果 |
| --- | --- | --- | --- |
| 1. 输入与校验 | 229 帧、30 fps、1280×1024 操作视频；任务指令；缓存的输入契约 | 源视频身份与 17 项预检结果来自源 Workbench 运行；本次未重新执行预检 | 复用 |
| 2. 场景与资产重建 | 操作视频及匹配的场景包 | 场景 MJCF、场景重建元数据、杯/碗资产、相机与 mask 文件 | 复用；没有本次新重建或背景/运动对象分离 |
| 3. 任务理解 | “Put the blue cup into the green bowl.” 与场景语义 | 对象级目标：抓起、移到碗上方、放入并松手；目标门禁重新检查为 3/3 通过 | 语义复用；目标检查重跑 |
| 4. 轨迹提取 | 缓存的演示参考与物体轨迹 | 472 帧 `trajectory_manifest.json` 与轨迹数组 | 复用；没有新轨迹提取报告 |
| 5. 重定向与动作生成 | YAM + Sharpa 原动作映射、场景和缓存动作计划 | 当前运行的场景绑定轨迹与物理记录 | 同一本体回放；没有跨机器人重定向或新规划 |
| 6. 物理执行与验证 | 轨迹、场景、MuJoCo 3.12.0、任务目标谓词 | 472 帧、7,850/7,850 子步回放；最大 qpos/qvel 误差均为 0；任务子目标 3/3 通过；Stage 4 原始布局审计 39/39 通过 | YAM + Sharpa 通过 |
| 7. 数据生成与扩增 | 通过的轨迹、场景、操作视频和随机化约束 | `randomize / plate / export / render / composite` 五份通过报告；1 个随机布局接受、1 个因最终朝向条件拒绝；原始与随机布局各生成 236 帧 H.264 视频 | Stage 4 通过 |
| 8. 策略训练 | 尚无训练合格 Episode、数据集版本或训练配置 | 无训练报告或 checkpoint | 未运行 |
| 9. 闭环评估与真机反馈 | 无策略 checkpoint 和闭环评测输入 | 无闭环仿真或真机评测报告 | 未运行 |

## 背景清理的阶段边界

背景与运动操作对象分离用于从扫描/场景输入中恢复静态环境和动态对象，应归入 **Stage 2：场景与资产重建**。它影响背景几何/外观、对象资产以及后续场景构建，需结合多帧运动、遮挡和补洞结果进行检查。

本次 Stage 2 只复用了已有场景。Stage 4 内的 SAM3-LiteText + LaMa 则从源视频帧中擦除人手和操作对象，供仿真渲染视频合成使用；这项视频 clean-plate 处理不等同于场景重建，也不能证明扫描背景已清理完成。

## 实际产物与可视检查

- Stage 4 子步骤报告：`outputs/reports/{randomize,plate,export,render,composite}.json`
- 汇总报告：`outputs/run_report.json`（`passed`）
- 输出视频：`outputs/samples/0000/ego.mp4`、`outputs/samples/original/ego.mp4`
- 物理执行报告：`execution/stage_report.json`、`physics_recording_report.json`、`stage1_goal_report.json`
- 逐阶段输入输出、视觉抽查和模型身份的完整本机审计：`runs/embodiforge_runtime_replay_yam_sharpa_20261022/PIPELINE_IO_AUDIT.md`（运行产物位于本机 `runs/`，不纳入 Git）
- Workbench 已索引该运行；九个环节页面均可打开，无浏览器页面错误，Stage 2 文件与 Stage 4 视频预览可见。服务保持在默认 8765，没有重启。

## 验收限制与后续工作

- **仅部分管线实跑**：Stage 1–4 上游主要复用缓存；不可把复用状态标成当前运行的新推理或重建通过。
- **场景重建仍需独立验证**：包括背景/操作对象分离、扫描视频输入、几何与尺度检查、补洞结果和场景配准。本运行不能覆盖该项。
- **本体覆盖不完整**：当前只有配置中的 I2RT YAM + Sharpa Wave 有该任务的物理证据。G1 Omnipicker 与 G2 Omnipicker 均未测试，Transferable 任务汇总仍不通过。
- **训练和闭环未完成**：缺少 EpisodeStore 原生 Episode、策略训练报告/checkpoint 及闭环仿真/真机评测报告。

下一步应先用可识别的场景扫描输入，从 Stage 2 实跑背景与操作对象分离及重建验收；然后对同一任务分别完成三种基线本体的物理执行，生成原生 Episode，再接入训练和闭环评测。任一阶段缺少证据时继续显示未完成。
