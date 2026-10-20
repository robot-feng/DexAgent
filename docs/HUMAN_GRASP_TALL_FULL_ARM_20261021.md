# 高箱体抓取：原位全臂修复与独立验证

高箱体 `tall_box_right_hamer` 已在原 **I2RT YAM + Sharpa Wave** 上完成
固定全臂抓取举升，并通过六项独立物理审计及实际选中姿态的重新双验收。
目标模式为 **transferable task generation**；输入来自合成示范回归，
不是 HumanEgo 实采数据，也不是源演示的逐点忠实重建。
G1/G2 尚缺同任务证据，**aggregate_complete = false**。

## 保留布局，重新求解动作

复用上一轮高箱体 bench 的原始 XML，字节内容不变。对象质量 0.1 kg，
半尺寸 `[0.02, 0.024, 0.04]` m，几何中心相对 body 偏移 `[0, 0, 0.04]` m，
body 位置 `[0.15, 0.44, 0]` m、yaw 40°。原 YAM 双臂固定安装位置与
`setup/scene.xml` 一致，没有移动对象、移动站点或缩放模型。
通过 `hand_scene_from_recon` 重新绑定相同初始状态。

上一轮手部已通过的候选仍在 80 mm 接近位置被拒：位置误差 **145.9 mm**、
姿态误差 **74.63°**。该失败及旧报告均保留，没有把被拒的 IK 投影当作执行轨迹。

复用 `optimize_grasp_pose(family="rigid")`，同时传入原 bench 和真实对象 body
的 60/100/150 mm 举升目标，每段八个筛查采样。预算保持
`max_evals=64, iterations=4, population=16, max_starts=6, workers=2`，
seed 为 `20261021`，所有物理门槛沿用默认值。
这是根据任务重新生成适合该手臂的动作，未要求保留不适合 YAM 的人手腕姿态。

预览实验从此前手部修复后的一个候选出发，优化耗时 **234.5 s**：
17 次评估中 12 次因接触垫未贴合、1 次因重叠、3 次因手臂不可达被拒，
1 个候选进入物理测试并通过。生成的拇指/食指 pinch 候选又独立通过了
全举升路径的手臂筛查、默认 `grasp_lift_test` 和 `grasp_hold_test`。
内部 rigid 候选生成也耗时；64 次预算限制的是优化评估数，不是总运行秒数。

## 固定全臂物理结果

`execute_subgoal_plan` 从原 home 状态执行接近、闭合和 60/100/150 mm 举升，
使用真实固定法兰树，没有 `attach_arms` 或事后拟合手臂。

| 指标 | 实测 |
|---|---:|
| 输出帧 / 视频时长 | 172 / 5.733 s |
| 实际举升 / 峰值举升 | 153.0 / 153.3 mm |
| 手内位移 / 旋转滑移 | 0.2 mm / 0.017° |
| 最大手 / 对象穿透 | 0.23 / 0.16 mm |
| 最大位置 / 姿态跟踪误差 | 1.0 mm / 0.3° |
| 右臂峰值实测速度 | 87.39°/s |

现有默认成功判据为举升至少 75 mm、对象在手内、无超限滑移及动力学检查通过。
153.0 mm 是测量结果，不能把默认通过解释为精确 150 mm 跟踪。

六项独立审计均通过：`rigid_arm_hand_check`、`physics_recording_check`、
`swept_collision_audit`、`object_visual_penetration_audit`、
`contact_impulse_spike_audit` 和 `held_object_slip_audit`。
其中物理录制校验重放 **2,850 个子步 / 5.7 s**，qpos/qvel 最大误差均为零；
这是物理时间跨度，与 172 帧视频的编码时长分开记录。

执行器实际选中姿态、实际起始 hand scene、起始状态和 runtime scene 的 SHA
均经核对，再对该姿态和起始 hand scene 重新执行默认举升与持续握持测试，均通过。
这项独立手部测试的举升为 59.8/99.8/149.8 mm、最大接触穿透 0.465 mm，
与上表全臂轨迹属于不同验证，不能混用数值。

## 从原始失败输入复现

`tool_library/tests/test_human_grasp_full_arm.py` 已参数化覆盖
`box_right` 与 `tall_box_right_hamer`。新增 tall case 只读取已提交的
`fixtures/human_grasp_recovery.json` 中三个原始失败种子和 primitive 定义，
不读取本机成功姿态或轨迹。布局、预算、物理门槛及执行/审计逻辑不变。

该独立回归实测 **1 passed in 270.01 s**：优化 222.6 s、19 次评估、
1 个进入物理测试并通过的候选；固定全臂执行 172 帧，实际举升 153.0 mm，
滑移 0.2 mm / 0.017°。实际选中姿态双验收与六项审计全部通过。
它与上面的 17 次评估实验起点不同，统计分别保留。本次仅运行新增 tall case，
box case 沿用上一轮 169.34 s 的通过证据，没有重复宣称两例本轮一起运行。

```bash
MUJOCO_GL=egl OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  .workbench/venv/bin/python -m pytest -q \
  'tool_library/tests/test_human_grasp_full_arm.py::test_failed_human_seeds_recover_on_fixed_arm_and_pass_physical_audits[tall_box_right_hamer]'
```

## 证据与边界

摘要和关键产物哈希见
[tall-full-arm-validation-20261021.json](references/human_grasp/tall-full-arm-validation-20261021.json)。
本机完整产物在 `.workbench/previews/human-grasp-tall-full-arm-20261021/`，
包含初始失败、输入与调用、优化、固定树录制、独立审计及渲染。
`manifest.json` 记录启动 HEAD、源码和 setup 资产哈希及运行版本；
记录范围内的源码/资产在执行期间未变化，但启动 HEAD 不能代替工作树内容证明。
原始输入文件哈希在结束时再次核对未变。

[全臂视频](../.workbench/previews/human-grasp-tall-full-arm-20261021/rendered/preview.mp4)
与[关键帧](../.workbench/previews/human-grasp-tall-full-arm-20261021/rendered/keyframe.png)
直接渲染匹配的 `scene_physics.xml + trajectory.npz`，完整双臂可见。
ffprobe 确认 1280×720、30 fps、172 帧；视频是已验收记录的可视化，不是新增物理测试。

主代理在默认 8765 的 Studio 高箱体条目完成浏览器复查：页面 HTTP 200，
视频解码为 1280×720、时长 5.733333 s，播放进度推进到 0.525161 s，
没有媒体或页面错误。浏览器记录位于
`.workbench/previews/geniesim-physx-resolver-20261021/browser-check.json` 的 `tall` 项，
[页面截图](../.workbench/previews/human-grasp-tall-full-arm-20261021/studio.png)保留了查看结果。

本轮补齐了合成高箱体在 YAM + Sharpa 上的局部证据；它不能替代真实示范输入、
G1/G2 同任务验证、完整任务数据验收或训练与真机闭环。
