# 原场景到 RSS 世界的坐标接入验证

2026-10-20。当前主线是单目扫描建场景、示范配准及机器人执行；双目/RGB-D 适配后置。本轮修复原整场景 MJCF 导入忽略 RSS 表面坐标的问题，是场景接入基础，不是已经完成单目扫描配准或三本体公共环境。

## 修改与接口

`TaskSceneEntity` 通过 RSS `Entity.placed()` 和 Newton 原生 `add_mjcf(xform=...)` 放置整份场景，保留固定根、自由根与 worldbody 几何的相对位置。没有改写源资产、增加虚构父机器人或强制把自由物体固定到世界。

`SharpaBridge` 增加可选 `rig_translation=(x,y,z)`，单位米，默认零保持既有行为。沿现有 `CellSpec` 的 surface frame 组装；原额外地面 `z=-1` 同步移到 `z=-1+dz`，保持源环境关系。`dz=1` 时桌面零点移到世界一米、额外地面到世界零；这不是测得了真实桌高，也没有替换桌面几何或添加桌腿。

CLI 当前只在 `sharpa` / `grasp` 两条原生验收路径开放：

```bash
OMP_NUM_THREADS=2 /data/miniconda3/envs/dexagent/bin/python run_robosimstudio.py sharpa \
  --physics-device cpu --gpu 2 --no-render \
  --rig-translation 0.4 -0.7 1.0 --output runs/rss_sharpa_translated_example

OMP_NUM_THREADS=2 /data/miniconda3/envs/dexagent/bin/python run_robosimstudio.py grasp \
  --physics-device cpu --gpu 2 --no-render \
  --rig-translation 0.4 -0.7 1.0 --output runs/rss_grasp_translated_example
```

输出目录每次使用新路径。例中的坐标是验证用位移，不能充当扫描标定结果。reference/candidate 的非零平移暂未接通，CLI 明确拒绝；原有零变换入口继续可用。

坐标契约：

| 数据 | 坐标与处理 |
| --- | --- |
| 源 MJCF、源任务判据、源轨迹 qpos | 保持 rig 坐标；`runtime_scene.xml` 是导入输入，不独立表达 RSS 的世界放置 |
| Newton free joint q、录制 body_q | RSS world 坐标，四元数 xyzw |
| source → runtime | 自由根位置加平移；四元数 wxyz 转 xyzw；hinge/slide 数值不变 |
| runtime → source | 自由根位置减平移并恢复 wxyz；抓取对象/手部观测先回 rig 再应用原任务判据 |
| 相机 | eye、look_at 同步平移；朝向、内参不变 |
| 控制、质量、惯量与 COM | 56 维控制和 body-local 物理参数保持；显式保留并审计源 z 轴重力 |
| 回放 | 从录制 manifest 读取位移重建场景及相机；body_q 已在 world 中，不能再平移一次 |

manifest 记录 `rig_to_world_xyzw`、额外地面高度、源场景和录制 body 的坐标含义。新增 `task_measurement_frame` 明确抓取测量数组及派生指标使用 rig 坐标；既有验证记录未追写这个新增说明字段。回放拒绝缺失/非有限/非平移配置及地面不一致的记录，而不是猜当前桌高。

边界：仅支持平移；倾斜和旋转需后续明确的重力、地面和观测适配。非零平移下，连接世界的 CONNECT/WELD equality 因导入器不会同步其世界锚点而被拒绝；以 body 指定的本体间约束保持原路径。site-based equality 的原生转换尚未验收，因此平移入口明确拒绝，避免将 site ID 错当作 body ID。零变换的世界 equality 导入保留原行为及 Newton 警告，本轮未验证其动态正确性。未知源重力方向不能静默用默认重力替代。

## 实际验证

使用当前源 YAM + Sharpa，物理为 MuJoCo CPU，原生运行的状态/记录使用 GPU 2；回放重建使用 CPU。未运行新模型推理或重建流程。

| 运行 | 结果 |
| --- | --- |
| `runs/rss_sharpa_identity_20261020` | 625 控制帧，所有必需检查通过 |
| `runs/rss_sharpa_translated_20261020` | 平移 `[0.4,-0.7,1.0]`，625 控制帧，所有必需检查通过 |
| `runs/rss_grasp_translated_20261020` | 3,400 控制帧，11 个抓取/搬运/释放阶段全部通过，所有必需检查通过 |
| `runs/rss_grasp_translated_negative_20261020` | 不闭合手指，close 阶段失败；后续阶段 blocked，负样例拒绝检查通过；整份报告保持 failed |

原坐标和平移运动的 56 关节轨迹最大差异为 **5.126×10⁻⁶ rad**。平移运动相对于独立源 MuJoCo 控制回放的最大误差为 **7.581×10⁻⁶ rad**。两份运动报告仍为 `warning`：开放环原始扭矩长程回放存在漂移，抓取未在该自由空间样例评估，且使用 `--no-render`；没有降低阈值或抹去这些状态。

抓取样例物体最高抬升 **0.102715 m**，独立源 MuJoCo 回放位置偏差最大 **0.002235 m**，最大穿透 **0.000305 m**。原生记录首帧物体 world 位置约 `[0.6200,-0.3500,1.0250]`，任务观测 rig 位置约 `[0.2200,0.3500,0.0250]`；全部 3,400 帧相减与声明平移一致。抓取报告保留可选检查的 skipped/warning 状态，未宣称通过渲染验收或三本体任务。

自动测试：

- 原生场景导入、控制、参考状态和 bridge 回归：**40 项通过，70 个 subtests 通过**。包含真实 Newton CPU FK 与独立 MuJoCo FK 的固定根、子体、自由根、world geom 对照；2 条警告来自零变换下原生世界 equality 导入。
- Workbench 回放配置与进程生命周期：**16 项通过**，包括位移恢复、非有限/旋转/地面不一致拒绝。
- Workbench 现有 8765 API 实际生成了 3,400 帧记录的原生回放，抽取 301 帧，模型身份验证通过，回放物理步进 **0**；不需要重启默认服务。
- 真实浏览器打开默认 8765 场景页、加载 iframe 与三维 canvas，页面无 JavaScript 异常，显示 3,400 原始帧 / 301 预览帧。已检查截图中的手、物体与播放时间轴；无头浏览器使用软件 WebGL，未作 GPU 渲染性能验收。

本机证据：`.workbench/previews/rig-translation-measurements-20261020.json`、`rig-translation-replay-20261020/result.json`；实际抓取回放保存在 `.workbench/previews/native/8367c1c1ddfe923fdc0cc559/`。测试日志和四份原生 stage report 保留，不将大型 Episode 提交到 Git。

## 待完成

先推进单目扫描与操作示范在源场景内的尺度、时间、相机轨迹与配准验证，再显式映射到目标运行时。本轮位移参数不能代替真实 SE(3)/尺度标定。整场景中的环境、机器人和对象尚未全部拆成可组合资产；源桌面仍保留，不能额外叠加桌子。G1/G2 物理适配、三本体同任务、单目端到端数据验收仍未完成。
