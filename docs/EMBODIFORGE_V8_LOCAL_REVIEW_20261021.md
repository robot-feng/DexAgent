# v8 方案与当前工程核对

已完整阅读根目录 `Architecture_v8.md`、`Development_Agent_Prompt_v8.md`、
`Work_Packages_v8.md`，并核对下列本地实现。三份原文件保留不改。
结论：有直接实施价值，尤其是功能融合、控制证据和独立验收；不需要新建顶层框架。
本次是源码审查，不是 SPIDER、盲测或三本体任务通过证明。

| v8 提议 | 本地核对 | 采用方式 |
|---|---|---|
| 不重复开发 SPIDER wrapper | `tool_library/common/spider_bridge.py`、`skills/physics_informed_retargeting.py` 已存在；当前以 Sharpa、每手一个自由刚体等范围为基础 | 复用现有桥接，补实际 provider/版本/支持范围与目标执行证据，不能直接宣称支持 G1/G2 |
| 前置方法选择 | `stage3_trajectory_generation/methods.py` 当前在 Agent 预算耗尽后按顺序调用方法，非阶段能力路由 | 在现有方法入口增加轻量能力选择；保留原路径作同条件对照，不重新实现优化器 |
| 策略训练可关闭 | 默认方法列表包括 `reinforcement_learning_tracking`；该工具实际创建 PPO 并迭代训练 | 这是实质缺口；保护应覆盖调度、工具及子作业，不能只隐藏 UI 训练按钮。本文尚未实现这一保护 |
| 完整实际执行记录 | `agent/rss_episode.py` 已校验逐子步 ctrl、时间、初态、状态映射并执行真实控制 | 补齐专家目标/发送/施加/状态语义，复用 Episode，不另写执行循环；现有证据不能自动扩展到全部本体 |
| 独立 VirtualReal | RSS `robosimstudio_policy_agent/graph_solution.py` 默认 `privileged=True, perception='oracle'` | 已知场景生成可合法用真值；盲测另设可见性和文件权限，不能只修改提示词就声称独立 |
| 依赖驱动的变体与重验 | `agent/task_variants.py` 已冻结候选和输入 hash、核对报告身份并进入现有规划器 | 在现有机制上补读依赖和重算范围，不建第二套变体注册表 |
| 功能几何与局部修复 | 已有资产、语义、gate、候选执行和报告路径；统一参考文档为 `EMBODIFORGE_UNIFIED_WORKFLOW.md` | 首例采用现有可执行任务的容器开口/释放后状态，加入缺陷和无缺陷对照，避免先做通用评审训练平台 |

## 需要按现有要求收紧的地方

1. v8 的“先一个本体”“实际可行的两种”可以作为开发顺序，不能替代可迁移任务
   **YAM + Sharpa、G1 Omnipicker、G2 Omnipicker 同任务全部通过**的最终条件。
   保真 Real2Sim 仍保留源本体和场景，不强制换成本体通用桌面。
2. WorldRevision、Candidate、Evidence 等先映射现有资产、报告和 Episode 字段。
   文档自己也要求不新增同义 DTO，应坚持这一点。
3. VirtualReal 第一版只做一个固定参考案例、一个故障候选和无故障对照。隐藏真值
   隔离必须实际生效；同引擎参考也不能充当真机证据。
4. 自修复先使用有边界的局部工具与固定判据。更换布局、质量、驱动或验收阈值来
   避开失败不是修复成功。配方进入默认生产需要回归，单次诊断不能直接晋级。
5. `Sources_v8.md` 在本次工作区检索中未找到；本文只确认已读本地源码事实，
   不把文档全部上游引用和论文效果视为已经独立核验。

## Newton 与 MuJoCo 的实际关系

当前 G1 诊断路径为 RSS → Newton `SolverMuJoCo` → native MuJoCo CPU。
`agent/rss_mujoco_cpu.py` 明确设置 `use_mujoco_cpu=True`；它是可选诊断后端。
Newton 的另一条主要路径是 `SolverMuJoCo` → MuJoCo Warp → GPU 多环境执行。
这符合 Newton 的官方架构，不是两个互斥的引擎选项：
[Newton 仓库](https://github.com/newton-physics/newton)、
[SolverMuJoCo 文档](https://newton-physics.github.io/newton/latest/api/_generated/newton.solvers.SolverMuJoCo.html)。

“MuJoCo 3.13 动力学字段未被比较”是新导出器审查覆盖不足：例如表面速度或
驱动延迟变化可能未被拒绝，不能解释为 Newton 没有用上。已补版本感知比较及
有限值检查；连同缩写修复，MuJoCo 3.12/3.13 各 16 项测试通过，当前 G1 原产物升级审计的
126 项比较及 20 个驱动身份通过。这仅是导出资格。

实际 RSS 零步重载又发现接触参数缩写被导入器错误补零，以及实验脚本重力符号
错误和 RSS 自带地面的计数差异；预检正确阻止了物理运行。上述问题分别处理，
不能把导出通过写成 RSS 任务通过。

用户随后明确要求更新最新 Newton 并使用 GPU 并行。接下来优先建立版本一致的
Newton/MuJoCo Warp/Warp 环境，在真实 GPU 上验证多 world 控制和物理步进，再
迁移机器人验收。CPU 调试结果保留作诊断证据，不作为 GPU 并行性能或兼容性证明。
