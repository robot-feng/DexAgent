# 单目演示：逐帧相机接入与验证

2026-10-20。本轮属于[统一工作流](EMBODIFORGE_UNIFIED_WORKFLOW.md)的可选 Demo 入口，不要求指令驱动或机器人数据任务先采集 Ego 演示。

## 问题与修复

原 `human_prior.keypoints_from_hamer()` 把整段 HaMeR 相机坐标手部网格都乘同一个 `camera.json` 位姿。头部相机移动时，静止的手也可能在 rig 中出现错误位移与旋转。EgoDex 已保存 `camera_world`，但旧 HaMeR 消费路径没有使用它。

现在 `rigcam.camera_poses(camera, frames)` 按**源视频帧号**查询相机位置和朝向，`keypoints_from_hamer()` 先把 HaMeR 图片序号映射回源帧，再使用对应变换。现有 `human_grasp_pose_prior`、`human_grasp_seeds` 和 human reference 调用继续复用同一个转换函数；没有新建任务系统或替换 HaMeR/机器人 IK。

`read_egodex_episode` 输出的 `camera.json` 新增：

```json
{
  "camera_motion": "moving",
  "reference_frame": 20,
  "trajectory_npz": "/absolute/episode/output/hands.npz"
}
```

其它内参、`pos`、`R_cv` 等字段保留。`pos/R_cv` 对应选定参考帧，供静态视图使用；不能将其重复用于整段移动视频。相对 `trajectory_npz` 路径由 `rigcam.load()` 相对 camera.json 所在目录解析，直接传 dict 时需给可解析路径。

复用既有 NPZ 契约：

| 字段 | 含义 |
| --- | --- |
| `camera_world[N,4,4]` | OpenCV 光学坐标到源世界的刚体变换，位置以米表达 |
| `R_world_rig[3,3]`、`origin_world[3]` | rig 到源世界的基底与原点 |
| `frame_idx[N]` | 可选，唯一递增的源帧号；旧 EgoDex 文件缺少时使用 0…N−1 |
| `timestamps_s[N]` | 新读取器保存的帧号 / 视频 fps；明确是名义帧时间，不是实测曝光时刻 |

查询支持稀疏帧和调用方重排；不按 NPZ 行号冒充源帧号，不自动插值或选择最近帧。相机到 rig 的旋转为 `R_world_rig.T @ R_world_camera`，位置为 `R_world_rig.T @ (p_world_camera - origin_world)`。

HaMeR 自己的 `frame_idx` 仍是输入图片序号，保持旧调用约定。EgoDex `episode.json` 新增 `source_frame_indices`（图片文件名 → 源视频帧号）；`extract_hand_meshes` 读取它，另存 `source_frame_idx`。例如抽取原视频 10、25、60 帧，HaMeR 仍编号 0、1、2，查相机时明确对应 10、25、60。没有映射的旧 HaMeR 数据仅保留静止相机用法；移动相机路径拒绝使用。不能从任意 `frame00001.jpg` 名称猜原视频帧号，因为普通视频工具也可能按抽帧次序重编号。

声明移动相机却缺轨迹、请求帧缺失、非有限/缩放/反射变换、帧号重复或非整数、参考帧与 camera.json 不一致均明确失败。旧固定机位输入仍保留原行为。读取器拒绝视频/相机帧数不一致与无效 fps，不能无依据填入 30 fps；选中帧解码或写出失败也不再静默跳过。

`place_egodex_camera_for_reach._reframe()` 在既有 rig 调整后，camera.json 引用变换后的 hands.npz；原 `camera_world` 与时间保持不变，只有 world←rig 及 rig 手部坐标改变。它只接受配套 hands.npz，避免误把无关轨迹留在旧坐标。该工具仍是显式布局迁移，不是扫描到新示范的视觉定位。

## 验证

执行环境 `/data/miniconda3/envs/dexagent/bin/python`：

```bash
python -m pytest tool_library/tests/test_moving_camera_prior.py \
  tool_library/tests/test_egodex_description.py -q
MUJOCO_GL=egl python tool_library/tests/test_human_grasp_pose_prior.py
```

- **54 项 pytest 通过**。独立前向投影生成固定世界手部，使用平移与旋转相机、非单位 world←rig、稀疏帧号 `[10,25,60]`；新路径恢复静止手，旧静态路径产生明显位移，确认测试能捕获原问题。另覆盖实际抽帧映射生产函数到 HaMeR 消费函数的编号约定、真实编码的小型 HDF5/MP4 读取、相对路径、异常输入及布局调整。
- 既有手部先验脚本 **5 组检查通过、0 failed**。保留真实样例的拇指不可达和约 74 mm snap 调整警告，不据此宣称完整任务物理通过。
- 实际读取 `/data/tzq/datasets/Egodex/part1/build_unstack_lego/59.hdf5` 及其 MP4：**388 帧、68 个关节/变换条目、30 fps**；选择第 20 帧作静态参考，输出全程相机轨迹。
- 源相机最大平移约 **0.044096 m**。20,719 个正深度手部样本的 rig→像素结果与源世界直接投影最大差异 **0.001475 px**。额外平移 `[0.12,-0.08,0]` 并绕 z 旋转 23° 后，投影最大变化 **0.000415 px**，源相机数组和时间逐项不变。

本机实际产物：`.workbench/previews/monocular-camera-20261020/{source,placed}/`；测量记录 `validation.json`；既有先验回归日志 `/tmp/embodiforge-human-prior-regression-20261020.log`。真实样例验证的是读取、转换和投影一致性；本轮没有重新执行 HaMeR 推理、三本体任务或扫描重建。

## SimFoundry 对接与未完成项

源码复核显示，SimFoundry `s2_da/da/exports/npz/results.npz` 的 `extrinsics` 是 OpenCV world→camera；选定帧 `image_<idx>_cam2world.npy` 是 camera→地面对齐世界。要恢复每个抽样帧，需组合：

```text
T_ground_camera_i = T_ground_camera_anchor @ E_anchor @ inverse(E_i)
```

这里 `E` 是 world→camera。AutoBG 的 `transforms.json` 还包含 OpenGL 轴变换，不能直接混用。抽样行号、原视频帧号、重新编码的 12 fps 播放时间必须区分；当前相关路径没有保留完整原始 PTS。学习式米制深度是预测尺度，仍需要外部检查。

依据：[depth_backends.py](../SimFoundry/simfoundry/pipeline/depth_backends.py)、[4_unify_world_frame.py](../SimFoundry/scripts/pipeline/A_reconstruction/stages/4_unify_world_frame.py)、[5_train_bg_splat.py](../SimFoundry/scripts/pipeline/A_reconstruction/stages/auto_bg_reconstruction/5_train_bg_splat.py)、[6_bridge_bg_splat_to_og.py](../SimFoundry/scripts/pipeline/A_reconstruction/stages/auto_bg_reconstruction/6_bridge_bg_splat_to_og.py)。本轮只读源码，没有改动另一对话的 SimFoundry。

AutoBG 的相机对齐通过同一源视频帧编号匹配两个重建结果，不提供独立扫描与新操作视频的视觉重定位。此次核查时本机没有可用于真实配准验收的 ready 扫描场景或 DA3 轨迹产物。

当前仍缺：普通 MP4 的相机轨迹自动恢复、独立会话配准、尺度验证及场景版本绑定。读取器仍用原指尖启发式估计桌面；不能把这个 rig 当成已知扫描场景。固定内参假设、视频名义帧时间与后续参考轨迹重采样也需分别验证；本轮没有证明所有下游模块已支持变焦、VFR 或稀疏采样时钟。
