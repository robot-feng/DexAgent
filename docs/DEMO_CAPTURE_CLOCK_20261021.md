# 示范采集时间到手指动作的接入（2026-10-21）

本轮复用已有 HaMeR 提取、抓取先验和手指重定向工具，补齐源时间传递。
没有运行完整 HumanEgo 策略，也没有把时钟检查当成机器人任务通过。

## 问题与修改

此前 HaMeR 输出有本地图像编号及叠加视频 fps，但缺少原始采集时间。
语义任务窗口以秒乘 fps 转换为本地编号，稀疏抽帧后容易选错样本。
手指限速也使用固定 fps；最终残差却取自平滑、限速之前的 IK。

现在沿已有链路保留以下区别：

| 数据 | 含义 |
|---|---|
| `frame_idx` | 当前手轨迹的样本 ID；HaMeR 为输入图像的本地编号 |
| `source_frame_idx` | 对应原始视频帧，按 episode 显式映射关联 |
| `timestamps_s` | 相对原始源时间起点的采集秒数，截取子集不归零 |
| `timestamps_ns` / `source_first_timestamp_ns` | 来源提供时保留原始整数纳秒与源起点 |
| HaMeR `fps` | 仅控制叠加视频播放 |

`extract_hand_meshes` 从 episode 的观测归档逐帧关联时间。声明了观测归档却
缺映射、缺时间、重复 ID、非递增时间或纳秒/秒不一致时拒绝执行。
无时间的旧归档仍能使用调用者提供的名义采集 fps，通过源帧号计算名义时间，
并在 `time_source` 标注其不是实测时钟。

`human_grasp_seeds` 对 `time_window_s` 直接使用采集秒数；
显式 `frames` 则仍表示本地样本 ID。`pre_s` 使用秒数，不按样本数量估算。
`human_grasp_pose_prior` 从实际存储的前序样本取窗口，允许非连续样本 ID；
产物记录源帧、接触/预形时间及平滑方式。

`retarget_hand_motion(timestamps_s=...)` 按逐段 `dt` 限速。
不规则采样禁用等样本间隔 Savitzky–Golay 平滑，避免错误时间假设。
无时间的原调用保持固定 fps 行为。平滑、限速和关节约束之后重新计算 FK，
使 `residual_mm`、`unreachable` 和 `ok` 描述实际返回的轨迹。
单个样本没有相邻速度约束，不能据此声称整个动作满足限速。

## 验证

使用 `.workbench/venv/bin/python`：

```bash
python -m pytest -q \
  tool_library/tests/test_retarget_clock.py \
  tool_library/tests/test_demo_clock.py \
  tool_library/tests/test_moving_camera_prior.py \
  tool_library/tests/test_humanego_import.py \
  tool_library/tests/test_egodex_description.py
python tool_library/tests/test_trajectory.py
python tool_library/tests/test_human_grasp_pose_prior.py
python tool_library/tests/test_human_grasp_seeds.py
```

- pytest：113 项通过，其中新增时间链路 28 项、实际 MuJoCo 限速/FK 21 项。
- trajectory 脚本：48 个 PASS，退出 0。
- pose prior 脚本：5 个 PASS，退出 0，含真实 EgoDex fixture 与 Sharpa MJCF。
- seeds 脚本：8 个 PASS、2 个 FAIL，退出 1；原断言未改。

完整 HumanEgo `serve_bread/040` 的 544 帧、稀疏帧 `[180,413]` 和末尾
44 帧均验证了原始秒/纳秒的精确保留、子集不归零、不同 overlay fps 不影响
选帧。稀疏两帧仍对应 5.999035875 / 13.764465375 秒。这里验证的是提取器
实际使用的元数据写入路径，未加载 HaMeR 模型或推断手网格。
数值证据见 [capture-clock-validation.json](references/humanego/capture-clock-validation.json)。

## 物理失败及边界

在相同存档示范与精确测试几何下，独立进程载入提交
`af4da9d944cd0fb2d19590e5d98fdb1417821cfe` 的旧工具进行有边界对照：

- `box_right`：四个种子的新旧最终关节和位置相同、排名相同。60/100/150 mm
  举升通过，但 80 mm 握持测试路径在举升阶段失去对象，随后接触数为零。
  旧工具亦失败。新的最终 FK 残差使各评分增加 10，未改变选择。
- `tall_box_right_hamer`：旧工具遇缺帧回退单帧，依旧没有通过举升的种子。
  新工具保留实际前序样本，候选排序和部分姿态有变化，但新旧皆失败。
  frame49 几乎相同且在 seated 阶段没有手指接触；frame45 新旧均在
  60 mm 举升失败。

这不是完整历史仓库回归，只隔离对照了相关先验/重定向工具；不能将两例
记为物理通过。详细数值、比较范围与失败阶段见
[grasp-clock-regression.json](references/humanego/grasp-clock-regression.json)。

本阶段结束时，`human_grasp_seeds(verify=True)` 仅调用举升验证，不能据此
判定握持稳定。后续已接入双验收排序，并复用
`optimize_grasp_pose(family="seeds")` 恢复上述两例，见
[抓取双验收与有限修复](HUMAN_GRASP_RECOVERY_20261021.md)。这仍不代替完整机器人任务。
HumanEgo 的实际场景配准、Aria21 到目标手/TCP 的适配与三本体同任务验收
仍是独立未完成项。
