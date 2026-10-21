# Newton 1.7 GPU 运行环境验证

后续执行入口迁移见 [RSS 运行环境接入](NEWTON_RSS_RUNTIME_MIGRATION_20261021.md)。
下文保留最初 64 world 运行时验证时的环境与边界。

按用户要求优先升级 Newton 并验证 GPU 并行。2026-10-21 实际查询 PyPI：Newton
最新稳定版为 1.7.0；其官方 `sim` extra 约束 MuJoCo / MuJoCo Warp `~=3.15.0`。
本次按该兼容组合安装，不将独立包的 3.16 版本强行混入。

| 包 | 已安装版本 |
|---|---|
| Newton | 1.7.0 |
| MuJoCo | 3.15.0 |
| MuJoCo Warp | 3.15.0 |
| NVIDIA Warp | 1.18.0 |

环境：`.workbench/venvs/newton-1.7-gpu`，Python 3.12.14，没有继承基础环境 site-packages。
`uv pip check` 检查全部 117 个包通过。现有感知、SPIDER、CPU 诊断和 8765 服务
未被原地覆盖；新环境不是旧 3.13/3.12 混装环境。

```bash
uv venv --python /data/miniconda3/envs/dexagent/bin/python .workbench/venvs/newton-1.7-gpu
uv pip install --python .workbench/venvs/newton-1.7-gpu/bin/python -r requirements-newton-gpu.txt
source .workbench/venvs/newton-1.7-gpu/bin/activate
python -m newton.examples
```

## GPU 物理验证

在 A100-SXM4-80GB 上实际构造 64 个 Newton world，明确
`SolverMuJoCo(..., use_mujoco_cpu=False)`。各环境控制不同关节目标，同时让独立
自由刚体在重力下落地。每个环境运行 250 个 2 ms 物理步，不写入逐帧状态，不训练策略。

| 路径 | 环境物理步总数 | 最大目标误差 | 物体最终高度（期望 30 mm） |
|---|---:|---:|---:|
| GPU implicitfast | 16,000 | 0.004117 rad | 29.889792 mm |
| GPU discrete | 16,000 | 0.004435 rad | 29.890534 mm |

两次运行所有 world 的时钟推进至约 0.5 s，全部状态有限，控制和落地判据通过。
首次编译包含在墙钟日志中，没有据此报告加速比或生产吞吐量。
这些是多环境运行时检查，不是 G1/G2/Sharpa 任务覆盖。

Newton 1.7 的 `_parse_integrator` 尚不识别 `"discrete"` 字符串，首次构造因此在
零物理步失败，证据保留。实际支持的整数枚举入口可用：

```python
import mujoco
from newton.solvers import SolverMuJoCo

solver = SolverMuJoCo(
    model,
    use_mujoco_cpu=False,
    integrator=int(mujoco.mjtIntegrator.mjINT_DISCRETE),
)
```

MuJoCo Warp 3.15 有真正的 discrete 积分实现；本次 GPU 实际步进验证了该入口，
没有复用此前 CPU adapter 的构造后改值技巧。

## 与现有工程的关系

现有 RSS `Scene` / `ArrangementSpec` 已在新环境分别通过 GPU implicitfast 和
discrete 落地检查；每次 100 个控制帧、200 个物理子步、1 s。discrete 通过现有
`backend("mujoco", integrator=4)` 配置，并检查实际后端枚举。实际 solver 的
`use_mujoco_cpu=False`；没有把 GPU 渲染
当作 GPU 物理。导出和 RSS 控制映射的 16 项零步回归在新环境同样通过。

Newton 1.7 仍有 MJCF solref 缩写的 custom-attribute 补尾问题；已提交导出器的
完整向量兼容措施仍需要保留，不能假设升级自动解决所有导入差异。

本地 RSS 分发元数据目前声明 `newton[sim]>=1.6,<1.7`。以上检查通过本地源码导入，
尚未将该元数据和主工作流默认解释器切换为 1.7；当前也未完成完整机器人任务回归。
下一步在此 GPU 后端重验完整 G1 机构与接触，按实际兼容结果更新 RSS 约束和任务
运行配置。原任务数据不能只凭升级后小场景通过就重新认证。

## 证据

完整依赖锁定、安装日志、原始脚本、输入、状态和失败报告保存在
`.workbench/previews/newton-gpu-upgrade-20261021/`。该目录的
`requirements.lock.txt` 是本次已安装环境快照。

[结构化结果](references/newton/gpu-runtime-20261021.json)与
[实测图](references/newton/gpu-runtime-20261021.png)保留本次验证范围和原始证据哈希。
[8765 工作台记录](http://127.0.0.1:8765/runs/4fb128cd6d746a6df91d463c)
已通过浏览器检查：页面返回 200、无 JavaScript 错误，验收页明确显示
“未验证机器人操作任务”。本次发布复用现有索引，无需重启共享服务。

[Newton 安装指南](https://newton-physics.github.io/newton/latest/guide/installation.html)、
[Newton 官方仓库](https://github.com/newton-physics/newton)、
[MuJoCo Warp 官方仓库](https://github.com/google-deepmind/mujoco_warp)。
