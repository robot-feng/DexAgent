# HumanEgo 示范输入接入（2026-10-21）

本轮把源码审阅转为可调用的数据入口：新增
`tool_library.skills.read_humanego_episode`，注册到既有 Agent 工具库。
Studio 的“工具库”搜索 `humanego` 即可查看参数。没有新增 Scene / Robot / Task
注册表，没有修改 HumanEgo 上游源码，也没有把其演示标签当作物理 Episode。

## 输入与输出

输入是已处理会话的 `preprocess/aria_cam_rgb_config.json` 与
`preprocess/all_data/00000…/`。每个选中帧必须具有真实 `rgb.png` 和
`aria_cam_rgb.json`；人手及 training_data 可缺失，原始记录仍保留。
默认要求 `total_frames` 声明的全帧序列，显式 `--frames` 才允许子集。

```bash
.workbench/venv/bin/python -m tool_library.skills.read_humanego_episode \
  /path/to/mps_serve_bread_040_vrs /path/to/new_demo

# 明确选择稀疏子集，仍保留原始源帧号
.workbench/venv/bin/python -m tool_library.skills.read_humanego_episode \
  /path/to/mps_serve_bread_040_vrs /path/to/new_subset --frames 180 413
```

输出包括：

- `frameNNNNN.png`：原始去畸变图，逐字节复制，不使用去臂、夹爪叠加或标注视频代替。
- `observations.npz`：源帧号、原始 int64 纳秒时间戳、相对于源首帧的实测秒数、
  逐帧内外参、世界坐标手点、原始像素、置信度及缺失掩码。
- `source_records.json`：原始相机、人手、训练记录，包括固定 cam0、虚拟锚点、
  对象锁定标签和原始图像路径；不重新解释为独立测得的对象运动。
- `episode.json`：沿用现有示范元数据文件名，保存帧映射、完整/子集标记、来源
  SHA256、配准状态及边界。它不是机器人动作 Episode。

Aria MPS21 与 MediaPipe21 不同：没有拇指 CMC，包含掌心点。输出保留
`aria_mps21` 和原始关节顺序；不补造缺失关节、不转成 Sharpa 关节动作。
无人手帧保留 NaN 与 invalid，不用零位置冒充观测。置信度原样保存；
存在有效几何记录不等于达到目标任务精度。

源文件包含旧机器绝对路径和过时 `aria/all_data` 路径。适配器只从指定会话的
标准帧目录读取原始 RGB，保留嵌入路径作来源记录；不跟随它们访问别处的文件。
帧身份、时间不一致、非刚体矩阵、非零畸变、损坏图像均拒绝发布输出。

## 显式场景配准

未提供配准时，输出仅在 HumanEgo 源世界中，不生成 `camera.json`。不能自动
套用 EgoDex 的 ARKit Y-up、指尖百分位桌面或标准机器人安装位置。

提供 `--registration registration.json` 才生成现有 rigcam 可读取的相机：

```json
{
  "R_world_rig": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
  "origin_world": [0, 0, 0],
  "source": "填写实际场景标定或配准证据；上方单位变换仅为格式示例"
}
```

`R_world_rig` 的列是 rig 坐标轴在源世界中的方向；`origin_world` 单位为米。
必须填入真实配准，不应照抄示例作为场景对齐结果。
输出相机通过 `trajectory_npz` 和源帧号使用已有 `rigcam.camera_poses()`；
文件内保留相对路径，函数返回值解析为可立即使用的绝对路径。
当前契约只支持固定内参，逐帧变化内参可以导入源观测，但不能直接输出 rigcam。

适配器只验证变换形式和数据一致性。提供配准后的状态仍是
`supplied_unverified`，不自动授予训练资格。尺度、桌面/对象、遮挡及几何精度
需要结合真实 Scene 另行验证。

## 完整公开会话实测

[公开数据源](https://huggingface.co/datasets/Leo-TX/HumanEgo) 固定 revision
`f0aa87fade6a41316d65322512f56f066dec62e3`，任务 serve_bread，会话 040。
只下载该会话的 `all_data.tar`（782,848,000 字节），整包 SHA256 与 HF LFS
元数据一致。只安全提取 RGB 和三种 JSON 普通文件；没有下载模型或全量数据。
保留源数据卡的 CC BY-NC 4.0 来源说明，原始数据不随本仓库提交。

| 检查 | 实测结果 |
| --- | --- |
| 完整源帧 | 544，相机、RGB、人手记录文件各 544 |
| training_data | 430，源帧 104–533 |
| 右手 / 左手有记录 | 468 / 0 帧 |
| 无人手记录 | 76 帧，保留缺失 |
| 实测时间跨度 | 18.097107625 秒 |
| 帧间隔 | 33.278912–33.375750 ms，严格递增 |
| 原始与导入 RGB 字节哈希 | 544/544 一致 |
| 相机 c2w 与 d2w·c2d 最大差 | 2.22e-16 |
| 手点投影与保存像素最大分量差 | 0.002174 px |
| 本次实际配准 | 未提供 |
| 训练资格 / 机器人任务验证 | 未通过验收 / 未执行 |

投影误差和矩阵组合来自同源数据，证明内部一致性，不证明独立几何真值。
上游人手预处理会复制相机时间戳，因此时间字段一致也不证明原始传感器同步。

本机证据根目录为 `.workbench/previews/humanego-import-20261020/`：
`full040-provenance.json`、`full040-validation.json`、`full040-download.log`；
完整输入在 `session040-complete/`，导入结果在 `session040-imported-complete/`。
小型数值报告另存 [import-validation.json](references/humanego/import-validation.json)。

## 回归与下游边界

新导入测试与既有移动相机、EgoDex 描述回归共 **64 项通过**。测试覆盖移动
相机下静止世界手点、旋转和平移配准后的像素不变、稀疏源帧重排、实测时钟、
缺失手、无效姿态、损坏 RGB、源目录逃逸及不覆盖已有输出。

已有 `extract_hand_meshes` 能读取新 `source_frame_indices`，不会把源帧 413
错当为 0。尚未运行 HaMeR 重提取或机器人重定向；HaMeR 输出仍使用自身叠加
视频的 fps，后续动作处理必须按源帧号关联 `observations.npz` 的实测时间，
不能把叠加视频 fps 当作采集时钟。原始 Aria21 也不能直接交给要求 ARKit /
MANO 关节的工具。下一步应完成 Demo 到 Scene 的真实配准以及目标手/TCP
映射，再进入现有动作生成和物理验收。
