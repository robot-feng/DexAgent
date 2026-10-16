# DexAgent 与 RoboSimStudio：代码复查、融合边界与标准任务对照

日期：2026-10-16。依据本机两个仓库的源码和实跑记录。RoboSimStudio 版本为 `73739abe212f963e8a9f5862b27bca370df091a7`。

## 1. 融合应保留哪些能力

目标是把人类演示转成可验证、可扩展的机器人任务，并覆盖不同物体表示、接触求解、控制和数据生成。双方值得吸收的部分如下。

| 环节 | DexAgent 可取之处 | RoboSimStudio 可取之处 | 合并方式 |
| --- | --- | --- | --- |
| 视频与任务 | Stage 1 对象身份、物理属性、动作分解、可测目标；支持 Codex external 文件交互 | real2sim 工具可对照照片、关键时刻和模拟执行，保留场景版本 | 保留 DexAgent 任务入口；把 RSS 的逐物体位置、面积和运动对比纳入反馈 |
| 场景重建 | 按任务所需属性选择参数化资产；SAM3 掩码、相机、投影和场景 gate 有来源检查 | 统一 Scene、Entity、Ref；可组合刚体、机构、布料、绳索和体积柔体 | 增加后端适配和材料/形变字段；场景中的每个对象继续绑定同一语义 id |
| 机器人 | 固定的 YAM + Sharpa 安装关系、关节身份、执行器上限和手型搜索 | 多本体、原生 IK/控制、交互驱动与可扩展机器人接口 | 保留每个本体自己的动作契约；通过任务目标、接触区域和轨迹先验迁移 |
| 动作生成 | 子目标规划、抓法候选、物理执行、独立验收及跨阶段修复 | 可执行任务图、检查后重试、连续目标与稳定时间组合 | Codex 负责生成和修改程序；运行时反馈与重试可吸收任务图的实现 |
| 物理 | 现有刚体门禁与经过回放的基线 | 多求解器分域组合、布料/柔体材料、自接触、耦合配置与 manifest | 用相同场景和任务做对照，再按需要选择物理配置；记录每次配置差异 |
| 评估 | 目标由独立 Stage 1 输入确定，失败、复用、跳过、缺证据可区分 | 布料覆盖、折叠、粒子保持、静置等连续测量；目标组合有时间语义 | 复用 RSS 测量，继续由可信任务契约确定是否通过 |
| 数据 | Stage 4 布局随机化、物理回放、背景板、Cycles 和发布门禁 | 原生 episode、完整粒子/刚体状态、离线回放及重渲染 | 共用一份物理记录，再生成 RGB、状态、动作和帧映射；柔体增加动态几何导出 |

源码入口：DexAgent 的 [Stage 1 schema](../stage1_semantic_understanding/schema.json)、[Stage 2 提示](../stage2_scene_reconstruction/prompts/stage2.md)、[Stage 3 提示](../stage3_trajectory_generation/prompts/stage3.md)、[独立任务测量](../tool_library/common/task_gates.py)、[Stage 4 导出](../stage4_robot_data_generation/stage4/export.py)；RSS 的 [物理组合器](../robosimstudio/robosimstudio/scene/physics/compose.py)、[视频对照](../robosimstudio/robosimstudio_real2sim_agent/checks.py)、[布料测量](../robosimstudio/robosimstudio/assetlib/vocabulary/measures.py)、[带检查和重试的任务图示例](../robosimstudio/examples/07_policies/01_check_and_retry.py)、[原生记录格式](../robosimstudio/robosimstudio/record/store.py)。

## 2. 代码复查发现的实质问题

### 2.1 仿真支持更多物体，不代表自动重建更准

RSS 的 real2sim 入口同样依赖人工或 coding agent 创建场景。照片/视频比较工具提供反馈，不会自动恢复不可见形状、绝对尺度、布料内部结构或材料常数。

DexAgent 的单目深度与网格拟合也有尺度、遮挡和相机误差。相机和已知尺寸需要独立约束，重建报告应区分实测、拟合、先验与未知。RSS 刚体路径同样使用 MuJoCo，因此本轮刚体对照能够证明适配一致性，不能证明真实物理精度提高。

### 2.2 场景格式转换会改变物理参数

本轮实际发现复合网格导入后，碗的质量从源 MuJoCo 的约 `0.150 kg` 变成 `0.102894 kg`，杯子从约 `0.030 kg` 变成 `0.028248 kg`。此前只恢复惯量张量不足以保留源物理模型。

现在在求解器创建前按刚体身份恢复源质量、逆质量、质心、惯量与逆惯量；原有门禁同时检查质心。源模型必须保留在产物中。此前已修复的 `solref="0.02"` 简写问题仍然需要保留：源 MuJoCo 的隐式第二值是 `1`，导入时要显式展开。

### 2.3 逐条复制力矩不能替代闭环执行

标准参考首次完整开环回放失败，机械臂最大状态差约 `3.31 rad`，杯子最大位置差约 `0.237 m`，三个任务目标均失败。早期误差很小，之后在接触过程中扩大。该结果保留在 `runs/rss_reference_cpu_20261016_v4/`。

本轮执行器保留原 Stage 3 的逐子步命令作为前馈，并根据 RSS 实测关节状态增加有上限的机械臂反馈：

`Δτ = M(q) · [ω²(q_ref - q) + 2ω(v_ref - v)]`

校正后的命令仍受源执行器力矩上限约束。手指继续使用原有的逐指位置命令；物体保持自由动力学，执行期间不写物体状态，不增加 weld、mocap 或外力。保存 nominal 和 actual 两套控制，以便核对控制变化。

该适配首先用于标准 episode 的迁移验证。参考状态来自原执行记录，尚未把 RSS 的状态反馈接入 Stage 3 的完整规划与控制搜索。

### 2.4 双方的“成功”语义需要逐项核对

RSS 的 `once` 会锁存曾经成立的成功，`hold_for` 使用模拟秒数；DexAgent 的最终目标检查实际末状态及释放稳定窗口。不能直接把“曾经进入容器”当作“最终释放并稳定留在容器”。

RSS 的 `held_particles` 支持运动学 latch，也支持按工具中心附近粒子判定；源码的近场分支不能单独证明摩擦抓稳。融合后的物理验收应同时检查：无固定粒子、真实接触、粒子随手运动、保持期间滑移、桌面支持、释放后静置，并加入张开手指的负向试验。

### 2.5 布料能力的条件必须随数据保存

[毛巾折叠示例](../robosimstudio/examples/08_demos/fold_the_towel/fold_the_towel.py)确实通过摩擦接触执行，没有把粒子固定到手上。但使用单向耦合，机械臂不接收布料的反作用力；内部剪切模量也从材料参考值 `100 kPa` 调成 `3 kPa`。默认 proxy 耦合与经过调节的 one-way 配置不能互换后仍沿用通过结论。

柔性物体需要完整粒子位置、速度、拓扑、厚度、拉伸/弯曲/剪切配置和耦合身份。单一物体位姿不能表达褶皱。对于初始已折叠或遮挡的布料，也不能仅用一个矩形 mask 自动宣称完整三维重建。

### 2.6 原生 episode 还需要训练数据转换

RSS 的 `privileged.npz` 是仿真真值，适合评估和重渲染。策略是否可用这些字段，要由部署时的传感器决定。本机 checkout 未发现完整的 LeRobot 数据集导出器；LeRobot 控制器和原生记录格式不能代替数据集发布验收。

当前 Stage 4 导出固定网格与逐帧刚体变换。布料如果进入 Blender，还需要逐顶点形变或等效的动态网格缓存。先使用 RSS 已记录状态的原生离线渲染能够验证形变与视频一致，但正式训练还要补齐相机槽位、动作单位、因果时序、终止状态、划分与 loader 校验。

## 3. 本轮标准参考对照的契约

按用户指定，采用 `runs/e2e_top_smoke_20261013/` 的杯子入碗样例，其初始画面是 [原 frame0 对照](../runs/e2e_top_smoke_20261013/stage2/frame0_compare.png)。

本轮复用 Stage 1 语义、Stage 2 场景和 Stage 3 参考，重新执行 RSS 导入、逐子步物理、独立目标门禁和渲染。保持相同对象、尺寸、质量、相机、机器人与任务条件。报告明确标记上游复用，`full_pipeline_complete=false`，不把这次对照记成新的冷启动人类视频重建。

新增 [episode 适配器](../agent/rss_episode.py)；入口复用现有 `run_robosimstudio.py` 和 `run_dexagent.py`，报告、录制与基础离线渲染也复用现有实现。每个子目标有独立的 `running → passed/failed` 报告；未通过时，其后的机器人动作不启动。

```bash
python run_dexagent.py dexagent.yaml --backend-eval reference \
  --reference-run runs/e2e_top_smoke_20261013

# 显式选择输出目录：
python run_robosimstudio.py reference \
  --reference-run runs/e2e_top_smoke_20261013 \
  --physics-device cpu --gpu 2 --output runs/my_reference_compare

# 用于分析误差传播的开环诊断，预期可能被任务门禁拒绝：
python run_robosimstudio.py reference \
  --reference-run runs/e2e_top_smoke_20261013 \
  --reference-control raw --no-render --output runs/my_raw_diagnostic
```

初始对照使用相同相机和物理状态。动态对照中，两个模拟器列使用相同物理时间；人类视频列仅为重采样视觉参考，有独立映射文件，不声称与机器人执行时间同步。

质量门槛位于 `evaluation.robosimstudio.reference`：机械臂状态差 `0.025 rad`，手指状态差 `0.08 rad`，物体位置差 `0.01 m`，初始轮廓 IoU `0.85`。手指采用已有 Sharpa 接触验收的独立门槛；这些是适配一致性门槛，不是相机/真实世界误差。

本轮最终 [验收报告](../runs/rss_reference_cpu_final_20261016/stage_report.json)为 **37/37 必需检查通过，三个子目标通过**。总状态 `passed_with_warnings`，仅因上游标准参考复用。完整执行 `7850` 个物理子步、`15.7 s`，三个边界分别写入 [子目标报告目录](../runs/rss_reference_cpu_final_20261016/phases)。

| 指标 | 本轮结果 | 门槛 |
| --- | --- | --- |
| 最大机械臂差 | `0.022135 rad` | `0.025 rad` |
| 最大手指差 | `0.017522 rad` | `0.08 rad` |
| 杯子最大位置差 | `6.110 mm` | `10 mm` |
| 杯子末位置差 | `5.529 mm` | 记录观测值 |
| 碗最大位置差 | `0.146 mm` | `10 mm` |
| 同相机杯子/碗轮廓 IoU | `0.92562 / 0.96429` | 各 `0.85` |
| 最大手与物体 / 物体间穿透 | `0.17 / 0.48 mm` | 原 episode 物理门禁 |

可查看 [初始四列对照](../runs/rss_reference_cpu_final_20261016/frame0_compare_rss.png)、[动态三列对照](../runs/rss_reference_cpu_final_20261016/comparison.mp4)、[工作台执行](../runs/rss_reference_cpu_final_20261016/simulation.mp4)和 [实际轨迹](../runs/rss_reference_cpu_final_20261016/trajectory.npz)。中间较低反馈增益的运行虽然通过任务目标，但没有通过机械臂跟踪门槛，失败报告保留；最终提高反馈响应后通过，原有力矩上限保持有效。

画面采用 RSS tiled 诊断渲染，可见网格分面和与原渲染不同的配色/阴影；它用于检查几何、动作和物体状态。正式视觉生成继续复用已有 Stage 4 Cycles，不能从本轮画面得出 RSS 外观更真实的结论。

后续材质核对发现，适配器曾将机器人覆盖为统一灰色，已移除该覆盖。源 MJCF 颜色会保留，但 tiled 渲染不实现 Stage 4 PBR。使用真实 RSS 状态生成的 [三帧 Cycles 材质预览](../runs/rss_material_preview_20261016/material_contact_sheet.png)确认了石墨灰、金属和橡胶参数；它不代表五步 Stage 4 已运行。新的 [四阶段验收记录](FULL_PIPELINE_VALIDATION_20261016.md)同时记录了参考执行基座布局与默认布局的差异、未通过候选以及重新配置输入后的运行口径。

本轮回归执行了 37 个 unittest，覆盖引用身份、控制/时间损坏、自由关节映射、有界反馈、录制连续性和顶层转发等，日志为 `runs/rss_reference_regression_final.log`。另有 [实际开环失败阻断试验](../runs/rss_reference_raw_blocking_final_20261016/stage_report.json)：第一子目标因杯子未被握住和抬起而失败，第二、第三子目标保持 `blocked`；只执行了 `2950` 个物理子步，未进入搬运与放置。

[独立产物复核](../runs/rss_reference_validation_20261016.json)核对了 38 个产物哈希、86 个源资产哈希、轨迹维度与有限性，以及两个视频各 236 帧的完整解码。回归检查和仿真门禁是不同的统计口径。

## 4. 后续融合的实现顺序与验收

1. **先固定标准刚体对照。** 同一份输入经过新后端，三个目标和末状态独立通过；保留开环失败，验证失败会阻断后续子目标。对照完成后才扩展其他任务。
2. **把新后端接入新视频生成。** Stage 2 输出后端中立的对象/相机/材料证据，Stage 3 根据后端选择执行与反馈。仍由同一 Stage 1 任务契约验收；复用参考与冷启动分别统计。
3. **接入柔性任务。** 先从可测的平铺布巾开始，重建展开形状、尺度、厚度与抓取区域；在目标本体上验收边缘夹持、抬升、保持、放下与释放。材料不确定时保留参数范围并做扰动，不以折叠观感代替材料标定。
4. **迁移可执行任务图和重试。** 将规划得到的接近、夹持、搬运、释放组织成带检测的动作程序。运行时只使用该本体实际可获得的状态；Codex external 继续负责程序生成/修复，不增加模型 API。
5. **完成柔体数据交付。** 共用原生物理记录，补动态几何、RGB/动作映射及训练转换，再检查学习策略的闭环成功率。仿真验证和真机验证分别报告。

柔性验收至少包含张开手指负向控制、完整粒子记录、最大拉伸、桌面/自接触穿透、保持期滑移、释放与终态稳定。若更换本体或改成双向耦合，必须重新验收；现有 YAM 平行夹爪毛巾示例不能直接证明 Sharpa 灵巧手抓布成功。

当前用户选择先完成已有标准参考的对照。柔性原型、EgoDex 候选视频与初步 SAM3 检查保存在 `runs/rss_video_cloth_inspect/`，尚未接入主流程或计为柔性任务通过。

此后已单独用标准人类杯子入碗视频完成 DexAgent Stage 1–4 的新跑与恢复验证，结果见[四阶段实跑记录](FULL_PIPELINE_VALIDATION_20261016.md)。该运行确实执行了 SAM3/LaMa 背景板、场景导出、两种布局的 Cycles 渲染和视频合成，并验证 Stage 4 材质参数实际写入和载入。它与上文复用旧 Stage 1–3 的 RSS 后端对照分开统计；两个任务的范围都不含毛巾柔体验收。
