# Newton 运行环境与 Studio 接入验证（2026-10-20）

## 结论与复用范围

现有 RoboSimStudio 已基于 Newton，不需要重新迁移一套仿真框架。继续复用
RSS 的 Scene / Binding / BuildRequest、控制与记录接口。Newton 的
`SolverMuJoCo` 可以使用 MuJoCo CPU 或 MuJoCo Warp GPU；Newton 与 MuJoCo
不是互斥的引擎选择。场景重建、任务理解与策略动作源保持原有职责。

当前配置 `dexagent.yaml` 的执行解释器为
`/data/miniconda3/envs/dexagent/bin/python`。实际检测：

| 包 | 版本 |
| --- | --- |
| Newton | 1.6.0 |
| Warp | 1.18.0 |
| MuJoCo / mujoco-warp | 3.12.0 / 3.12.0 |
| RoboSimStudio | 0.1.0 |

本地 RSS 源码及安装元数据要求 `newton[sim]>=1.6,<1.7`。本轮没有升级共享环境。
官方当前 [安装指南](https://newton-physics.github.io/newton/latest/guide/installation.html)
与 [源码](https://github.com/newton-physics/newton) 的最新版本不能直接替代本地兼容约束。
`newton[examples]==1.6.0` 的 pip dry-run 会额外引入 53 个包，包括约 448 MB 的
Open3D wheel，以及 USD、ONNX、GUI 和 notebook 依赖；本轮未安装这些额外包。
已运行的无窗口基础示例不需要补齐所有示例依赖。需要完整 GUI 示例时应在独立、
固定版本环境中安装，避免与项目运行环境发生依赖冲突。

## 已实现

- 总览与执行页增加“仿真运行环境”，读取选中配置的实际 Python 包元数据。
  API 不导入物理引擎、不分配 CUDA，也不把 API 自己的环境冒充执行环境。
- `GET /api/workbench/v1/simulation/runtime?config_id=…` 返回包版本、
  RSS 声明的 Newton 兼容约束检查结果、各模式默认求解器与物理设备。
  不兼容、缺包、解释器失败、超时、无效配置均明确显示。
- CLI 与前端探测共用默认物理设备选择逻辑。当前 rigid / sharpa 使用 GPU，
  towel 使用耦合刚体与布料求解；grasp / reference 按配置选择，当前是 CPU；
  simfoundry / candidate 使用 CPU。GPU 状态和渲染不作为 GPU 物理通过证据。
- 环境检查保持中性“环境已检测”，不显示任务通过。pipeline / replan 明确
  各阶段可以使用不同后端。执行模式 URL 支持全部合法模式，非法值回退。
- 前端沿用 8765；用户确认保存活动编辑会话后完成服务重启。

## 实际验证

1. 官方 Newton 1.6 无窗口示例在 GPU 0 / A100 上通过原始断言：

   ```bash
   CUDA_VISIBLE_DEVICES=0 .workbench/venv/bin/python -m newton.examples basic_pendulum --viewer null --device cuda:0 --test
   CUDA_VISIBLE_DEVICES=0 .workbench/venv/bin/python -m newton.examples basic_dzhanibekov --solver mujoco --viewer null --device cuda:0 --test
   ```

   两项均使用官方默认 100 帧。第一次探索性 pendulum 120 帧运行失败，原因是
   原始测试的结束区域断言随结束时刻变化：body1 的 y 在 100 帧为 0.161956，
   120 帧为 1.537343，后者超出 `abs(y)<1`。状态均有限，保留失败日志，
   不把任意运行长度都描述为通过。

2. RSS 原生 `TestThePanelTossesTheCube` 通过（1 项，117.229 秒，含编译）：
   Panda、桌面、立方体的接触支撑以及 1.5 / 3 m/s 抛物高度检查。
   四条 linesearch 迭代上限警告保留，未调整参数隐藏警告。

3. Studio 更新后通过真实 API 发起 `rigid` 运行：
   - execution：`500659d9bc72470997b3c899`
   - run：`studio_rigid_20261020_232353_500659`
   - [本机运行与验收](http://127.0.0.1:8765/runs/ba3f957dec3ec4cd0cb69f51)
   - 9/9 原生检查通过；150 帧状态记录、151 个控制采集时点、45 帧 / 15 fps 视频。
   - 立方体支撑高度 0.774995 m，要求 0.775 ± 0.003 m。
   - 日志含一次 linesearch 迭代上限警告；当前渲染是碰撞几何诊断画面。

4. 后端相关回归：60 通过，覆盖配置实际解释器、缺包、版本冲突、失败输出脱敏、
   超时、无效 YAML、模式与物理设备，以及既有执行和运行索引行为。
   前端构建通过；新增 UI 契约测试 4 通过；既有执行/候选/重规划 UI 回归
   3 通过、3 原生执行项按默认配置跳过。另在更新后的 8765 用真实 API 和浏览器
   检查 rigid GPU、grasp CPU、版本展开及页面无 JavaScript 错误。

本轮证据位于 `.workbench/previews/newton-runtime-20261020/`，包括原始示例日志、
状态 NPZ、测量 JSON、安装 dry-run、真实环境响应和页面截图。Studio 原生报告、
视频及 Episode 位于上述 run 目录。临时证据和视频不纳入源码提交。

## 后续验收边界

此次证明环境、基础接触物理及工作台启动→执行→报告→记录链路可用；
未证明 YAM + Sharpa、G1 Omnipicker、G2 Omnipicker 的相同可迁移任务全部通过。
也未证明 HumanEgo policy 已在 Newton 中完成闭环运行。下一步应沿用现有
任务/本体映射，逐个检查接触、控制、观测及成功谓词，并针对目标任务处理
求解器收敛警告；不通过改网格外观或复制轨迹替代真实验收。
