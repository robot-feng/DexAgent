# SimFoundry 刚体资产接入 RSS：执行、记录与三维回放

日期：2026-10-20。复用现有统一入口、RSS Scene/物理引擎、EpisodeRecorder、StateReplay 和 Viser；不增加场景 DSL 或独立查看器。

## 已实现的数据流

```text
Stage 8 canonical GLB + residual scale
Stage 11 单 link URDF、复合碰撞代理、质量/摩擦
Stage 12 世界位姿（米、Z-up、XYZW）
  → 现有 ObjectSpec/AssetSpec 适配
  → RSS void 场景（地面 z=0）
  → 真实物理静置与接触检查
  → 原生 EpisodeStore + 诊断视频
  → Studio 8765 内嵌原生 Viser 状态回放
```

上游文件保持只读；GLB 和派生碰撞代理复制到新 run。快照记录输入 SHA、导入器源码、资产 SHA、对象声明、相机以及编译后 body 顺序和 shape 几何变换身份，episode 元数据绑定快照 SHA。回放先核对快照、资产、模型及时间/位姿数组，再构建 CPU 场景并直接写入已记录的 body_q；禁用 warm-up，不执行物理步进。修改网格、声明或 body 顺序后不允许旧记录静默显示为另一场景。

## 命令

在已有 DexAgent/RSS Python 3.12 环境运行，不向 SimFoundry Python 3.11 环境导入 RSS：

```bash
python run_dexagent.py dexagent.yaml --backend-eval simfoundry \
  --simfoundry-stage8 /path/to/s8_pose \
  --simfoundry-stage11 /path/to/s11_sim \
  --simfoundry-stage12 /path/to/s12_physics
```

默认采用 Stage 11 复合碰撞代理和 CPU MuJoCo 物理，GPU 用于离线视频渲染。可显式选择 `--simfoundry-collision convex_hull|bounding_box`。上游如果选择交互式 pose info，传入 `--simfoundry-pose-info /path/to/info_interactive...`。

没有 GPU 时可用后端原有参数关闭渲染：

```bash
python run_robosimstudio.py simfoundry --config dexagent.yaml \
  --simfoundry-stage8 /path/to/s8_pose \
  --simfoundry-stage11 /path/to/s11_sim \
  --simfoundry-stage12 /path/to/s12_physics \
  --physics-device cpu --gpu '' --no-render --output /path/to/new_run
```

输入目录和输出目录不得重叠；不支持的多 link 刚体或非均匀尺度会明确失败，不能静默压平。物理检查默认 3 秒，末尾 0.5 秒检查速度；不在步进时强制清零速度。阈值可在现有 `evaluation.robosimstudio.simfoundry` 配置中调整：

```yaml
seconds: 3.0
rest_window_s: 0.5
max_drift_m: 0.01
max_rotation_rad: 0.05
max_speed_mps: 0.02
max_angular_speed_rad_s: 0.05
max_penetration_m: 0.004
```

## 实际验证

**以下为手工构造的合成立方体，不是 SimFoundry 视频重建结果。** 它有一个视觉 GLB、两个各占一半的碰撞代理、0.2 kg 质量和 0.5 摩擦。输入位于本机 `.workbench/baseline/rss_simfoundry_cli_20261020`。

统一入口产物：`runs/rss_simfoundry_20261020_094059_1890481`。

| 检查 | 实测 |
| --- | --- |
| 报告 | 17 项必需检查通过；1 项惯性坐标系复核警告 |
| 原生记录 | 150 控制帧、151 capture 帧，3.0 秒 |
| 机器人动作 | `(150, 0)`；无机器人监督标签 |
| 峰值位置漂移 | 0.00002053 m |
| 峰值旋转变化 | 0.00032154 rad；计算前在 float64 中归一化四元数 |
| 末段最大线/角速度 | 0.001475 m/s、0.02104 rad/s |
| 最大接触穿透 | 0.00002087 m |
| 离线视频 | 解码 45 帧，15 FPS；碰撞几何诊断渲染 |
| 交互回放 | 150 帧，2.98 秒；模型身份匹配；物理步数 0 |
| 发布 | `training_eligible=false`；`full_pipeline_complete=false` |

浏览器实际访问默认服务：

`http://127.0.0.1:8765/runs/47bf1440b99b4812e6a88b13?view=scene&preview=762e340df1bbdb84ad2d28f7`

Chromium 检查内嵌 canvas 可见、页面无脚本错误，并显示“单次全流程未完成/发布资格未确认”。截图：`.workbench/previews/simfoundry-native-replay-20261020.png`。无头浏览器使用软件 WebGL，截图有 Viser 性能提示，不代表客户端 GPU 已验证。

回归命令与结果：

```bash
/data/miniconda3/envs/dexagent/bin/python -m unittest \
  agent.tests.test_simfoundry_assets agent.tests.test_rss_bridge \
  agent.tests.test_rss_simfoundry -v
# 16 tests passed, 24.428 s

.workbench/venv/bin/python -m pytest workbench/tests -q
# 当前工作区 55 passed, 29.09 s

cd workbench/frontend
npm exec playwright test -- --grep 'recorded rigid scene|standard DexAgent trajectory'
# 2 passed, 2.2 min：已有 RSS/MJCF 与 DexAgent 两类原生三维回放回归
```

故障路径覆盖：悬空初始物体因漂移超限返回失败并保留诊断 tape；多 link 在创建 episode 前失败；输出与源目录重叠时不写上游；修改资产/快照/body 顺序会拒绝回放；预览缓存跟踪网格来源变化。已有文件存在 Ruff 基线告警，本次未宣称仓库全量 lint 通过。

## 尚未完成的验收

这条路径只检查刚体资产导入与静置；不含机器人、抓取、任务完成或训练数据放行。Stage 11 COM/惯性坐标系未传递，RSS 以视觉几何及质量估算惯量；质量/摩擦本身仍是上游估计值，因此保留显式警告。背景 splat、USDZ、关节体、柔体及跨引擎动态一致性仍需单独验证。

真实 SimFoundry Stage 8/11/12 资产尚未生成，P2 不能宣告通过。主环境基础依赖已安装，但完整官方工具链仍在恢复安装；当前无原始用户扫描视频。下一步完成环境/权重后先运行已授权 `ThrowAwayTrash.mp4` 的 mask/补洞冒烟测试，并另选具有相机视差的扫描视频做真实背景与资产重建。
