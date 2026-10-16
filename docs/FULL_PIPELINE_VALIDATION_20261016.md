# 标准杯子入碗任务：四阶段实跑与验收

日期：2026-10-16。运行名：`e2e_reference_layout_20261016`。本轮从人类操作视频重新生成语义、场景、机器人轨迹和 Stage 4 数据。

## 结果

四个阶段都有当前有效的验收报告。Stage 1、Stage 2 在首次运行中生成；首次 Stage 3 因随机布局的朝向偏差未通过。修正随机化朝向范围后，恢复运行重新执行 Stage 3 并完成 Stage 4。随后对合成步骤做了一次保留上游报告的视觉修正重跑。

因此，这组证据覆盖完整的 Stage 1–4 流程，但不是单次无恢复运行。阶段报告的跨运行关系和哈希汇总保存在[机器可读验收清单](../runs/e2e_reference_layout_20261016/pipeline_validation.json)。原生 [run_summary.json](../runs/e2e_reference_layout_20261016/run_summary.json) 显示 `scope_completed`、`requested_stages=[stage3, stage4]`、`full_pipeline_complete=false`；这是恢复调用的准确范围，未将其改写成单次全流程。

| 阶段 | 结果 | 检查与说明 |
| --- | --- | --- |
| 预检查 | 通过 | 17/17；Python、模型缓存、输出目录均位于 `/data`，Codex 使用 external 文件传递，没有模型 API。 |
| Stage 1 语义理解 | 通过 | 5/5；识别蓝杯、绿碗和 3 个可测子目标。 |
| Stage 2 场景重建 | 通过并带警告 | 4 项检查通过；场景门禁提示通用抓取姿态搜索没有证明杯子不可达，并提示原始场景有 98 个绝对网格路径。 |
| Stage 3 轨迹生成 | 通过 | 4/4；3 个子目标全部执行，物理、任务目标、关节/执行器、碰撞和 Stage 4 交付门禁均通过。 |
| Stage 4 数据生成 | 通过 | `randomize`、`plate`、`export`、`render`、`composite` 五步均通过；一个随机化布局和原始布局均有物理回放与视频。 |

## 输入、环境和运行边界

- 输入视频：[video.mp4](../stage1_semantic_understanding/examples/place_cup_in_bowl/video.mp4)，SHA-256 `e819f48dc73bbdaedd25efb8a9018cea0808a942deeb9ae07c24f8a2c6b6d803`；229 帧，30 fps，7.633 秒，1280×1024。
- 任务是把蓝杯放入绿碗。相机内参和位姿来自显式提供的标定，不是本次从单目视频独立求得的；绝对尺度也没有被本次运行独立标定。
- 使用 `/data/miniconda3/envs/dexagent/bin/python`。预检查记录 SAM3-LiteText 权重 SHA `7f490d87…efa35ab1`、LaMa 权重 SHA `7ba7aa7a…0c9e6b4c`；模型缓存为 `/data/miniconda3/hf_cache`。
- `examples.reuse=false`。Stage 1、2 重新执行工具流程；Stage 3 重新规划和执行；Stage 4 对接受的轨迹重新随机化、物理回放、背景板处理、导出和渲染。没有从旧示例复制轨迹或场景输出。
- 阶段使用 Codex external 请求/响应文件，不调用模型 API。RoboSimStudio 参考后端对照是另一组独立运行；本次四阶段结果使用 DexAgent 的 Stage 3 物理执行与 Stage 4 渲染，不能说成由 RoboSimStudio 生成。

## Stage 1 与 Stage 2

Stage 1 把任务拆成：抓起杯子并抬离桌面、把杯子移至碗口上方、释放后杯子留在碗内且不再受手支撑。任务成功要求杯子在碗内、碗没有被移动。完整报告为 [Stage 1 report](../runs/e2e_reference_layout_20261016/stage1/stage_report.json)。

Stage 2 使用 SAM3-LiteText 生成对象掩码，杯与碗的投影 IoU 分别为 `0.771` 和 `0.846`。杯、碗静置支撑、空腔和放入关系等场景门禁通过。两项警告保留在 [Stage 2 report](../runs/e2e_reference_layout_20261016/stage2/stage_report.json)：通用采样手姿没有提供抓取可达见证（这表示证据不足，不是证明不可达）；98 个网格引用为机器上的绝对路径。Stage 3 后续对具体抓姿做了实际执行，不能用它替代 Stage 2 几何或资产可移植性的警告。

## Stage 3：失败原因、修复和实际执行

首次 Stage 3 执行的杯子位置误差约 `10.7 mm`，但随机化碗和杯子的独立 yaw 后，最终 `at_target_orientation` 误差为 `19.633°`，Stage 4 随机布局门禁拒绝该轨迹。这次失败被保留；它不是抓取接触失败，也没有被标成通过。

恢复后，杯/碗仍分别使用 `±3 cm` 的平面位置扰动，但将各自 yaw 限制为 `±5°`。机器人关节、接触力、物理参数、任务目标与验收阈值没有放宽。用新轨迹重新执行并打包：3 个子目标全部通过，532 帧、30 fps、17.73 秒；抓取、抬升、搬运、放置、释放后的目标检查及 Stage 3 完整门禁通过。执行接触表在 [simulation_contact_sheet_fresh.jpg](../runs/e2e_reference_layout_20261016/stage3/simulation_contact_sheet_fresh.jpg)，轨迹和交接报告见 [Stage 3 report](../runs/e2e_reference_layout_20261016/stage3/stage_report.json)。

## Stage 4：逐步结果

完整报告：[Stage 4 run report](../runs/e2e_reference_layout_20261016/stage3/stage4_task/outputs/run_report.json)；逐步报告位于同一目录的 `reports/`。

| 子步骤 | 结果 | 关键反馈 |
| --- | --- | --- |
| `randomize` | 通过，431 秒 | 请求 1 个、接受 1 个随机样本；同时回放原始布局。两个样本都有完整轨迹和物理回放证据，接受样本未检测到新的穿透或终态漂移。 |
| `plate` | 通过，202 秒 | SAM3-LiteText 对分布在整段视频的 85 帧生成掩码，空掩码 0；平均掩码覆盖约 4.45%，相邻帧 mask IoU 均值约 0.881。使用本地 LaMa 权重生成 1280×1024 背景板。 |
| `export` | 通过，11 秒 | 随机与原始布局各导出 1 个 ego 视角场景、变换矩阵和 178 帧轨迹映射。 |
| `render` | 通过，699 秒 | 两个布局各渲染 178 帧，两个 chunk 均退出码 0，所有图像可解码；日志记录 Cycles `OPTIX`。 |
| `composite` | 通过，86 秒 | 随机与原始 ego 视频各 178 帧，1280×1024、10 fps、17.8 秒；全帧解码和 ffprobe 元信息一致。另对 `preview.mp4` 和真人/仿真 `compare.mp4` 做了全帧解码复核，也都是 178 帧。 |

合成输出：[随机布局 ego.mp4](../runs/e2e_reference_layout_20261016/stage3/stage4_task/outputs/samples/0000/ego.mp4)、[原始布局 ego.mp4](../runs/e2e_reference_layout_20261016/stage3/stage4_task/outputs/samples/original/ego.mp4)、[真人/仿真/生成视频对照](../runs/e2e_reference_layout_20261016/stage3/stage4_task/outputs/samples/original/compare.mp4)。对照视频把真人视频重采样到模拟轨迹长度，只用于视觉展示，**没有声称时间同步**。最终抽帧见[合成结果 contact sheet](../runs/e2e_reference_layout_20261016/stage4/final_composite_contact_sheet.jpg)和[三路对照 contact sheet](../runs/e2e_reference_layout_20261016/stage4/human_sim_comparison_contact_sheet.jpg)。

### 材质和合成观感

本次 Stage 4 实际导出的 Blender 场景包含材质参数，不是只在 `materials.json` 中声明：12 个机械臂几何体使用 `yam_black`（base `[0.08, 0.09, 0.105]`、metallic `0.25`、roughness `0.58`、specular `0.25`）；手部银色件保留 Sharpa 材质（24 个几何体 metallic `1.0`）；杯和碗保留蓝色、绿色底色。导出的 `scene.json` 与最终复合视频都能确认这些材质已加载。

最终视觉检查发现 Cycles 透明 shadow catcher 产生的纯黑阴影太重。现在只把纯黑阴影像素的 alpha 按 `0.30` 衰减，机器人和物体表面颜色、抗锯齿边缘不变；该参数写进每个 `composite.json` 和逐步报告。复合步骤重跑后，阴影更轻，桌布背景仍有自然纹理。该参数是视觉合成调节，不会改变物理轨迹或仿真接触结果。

## 运行恢复与报告口径

运行记录分为三段：首次 `pipeline.log` 生成 Stage 1、Stage 2 并在 Stage 3 留下失败证据；`resume_stage3_pipeline.log` 从 Stage 3 恢复，生成通过的轨迹和完整 Stage 4；视觉复核后只重做了 `composite`，通过同一配置身份检查继续复用有效的 `randomize/plate/export/render` 报告。那次没有继承正式 `/data` 模型环境的尝试被拒绝，失败报告和此前通过报告均保存在 Stage 4 `reports/history/`，没有被覆盖成通过。

`run_summary.json` 保持 `full_pipeline_complete=false`，因为最后一次 DexAgent 主流程恢复运行只请求 Stage 3–4。`pipeline_validation.json` 是跨调用汇总：它用 Stage 1–2 的首次新跑报告、恢复后的 Stage 3–4 报告和最终复合报告建立可追溯关系，不伪造单次全流程的运行范围。综合状态为 `passed_with_warnings`，唯一阶段级警告来自 Stage 2。

## 回归测试

- `/data/miniconda3/envs/dexagent/bin/python -m pytest -q agent/tests stage4_robot_data_generation/tests --ignore=agent/tests/test_config.py --ignore=agent/tests/test_report_blocked.py`：**246 passed，49 subtests passed**。两个被排除的文件是直接运行后 `sys.exit` 的脚本测试，已分别以脚本方式通过。
- `agent/tests/test_cross_stage_repair.py` 直接运行通过；它的 verifier、诊断和发布 stub 已改为作用域内 patch，避免污染同进程其他测试。
- `stage4_robot_data_generation/tests/test_composite_blend.py` 覆盖阴影衰减与物体/边缘 alpha 保持，2 项通过。

## 已知限制

- 本次只有蓝杯入绿碗刚体任务，不是毛巾或其他柔性物体的重建/抓取验证；柔性数据导出和真机训练可用性也未验证。
- 单目尺度、实际物体质量/摩擦和桌面材料没有从视频独立标定；机器人场景使用已提供的相机标定和已知机器人模型。
- Stage 2 场景报告仍有绝对资产路径和通用可达性搜索警告；本次交付的局部打包门禁通过，但不能据此宣称所有 Stage 2 重建资产已经跨机器可移植。
- 运行通过意味着这条单样本管线具备逐步机器检查与可见产物，不代表对新任务、软体或实机的成功率已经建立统计基线。
