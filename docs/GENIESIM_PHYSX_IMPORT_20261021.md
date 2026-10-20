# Omnipicker 导入：恢复源 PhysX 碰撞语义

G1/G2 原生 USD 完整导入后的 MuJoCo CPU 求解器构建已通过，原先的栈溢出已消除。
本阶段仍是**本体导入资格检查**，未进行夹爪开合、机器人步进或任务执行。
两份报告仍为 `failed`，不能作为三本体 demo 通过证据。

## 根因与修复

两个源机器人 articulation 都声明
`physxArticulation:enabledSelfCollisions = false`。
安装的 Newton 1.6.0 `import_usd.py` 默认只启用 `SchemaResolverNewton`；
此前 `agent.geniesim_import` 没有显式传入 PhysX 解析器，因而使用了默认开启自碰撞。
导入的关节、闭环和 mimic 结构存在，并不意味着扩展物理属性也被正确读取。

小范围诊断先跳过凸分解，仅检查源碰撞声明的映射：

| 项目 | G1 | G2 |
|---|---:|---:|
| 启用的源碰撞 shape | 18 | 22 |
| 跨 body 内部 shape 对 | 153 | 231 |
| 原默认解析后未过滤的内部对 | 133 | 211 |
| 加入 PhysX 解析器后未过滤的内部对 | 0 | 0 |
| 被过滤的机器人—独立外部球对 | 0 | 0 |

旧完整导入仅含机器人，没有地面或任务物体。其报错中的 28,650 / 30,441 个
接触来自本体内部，违反源 articulation 自碰撞设置。凸分解数量偏大加剧了计算成本，
但不需要先改写分解器才能修复这个已证实的过滤缺陷。

现在通过既有 RSS `add_usd_stage` 明确传入：

```python
from newton.usd import SchemaResolverNewton, SchemaResolverPhysx

schema_resolvers = [SchemaResolverNewton(), SchemaResolverPhysx()]
```

保留 Newton 显式属性优先级，其次解析 PhysX 属性，不改变源 USD，
不全局关闭接触，也不增大求解器栈来掩盖错误。
新增报告检查 `authored_self_collision_filters_preserved`：
读取源 articulation 设置，按实际启用碰撞的分解 hull 检查全部跨 body 内部对，
任何缺失过滤都会使必需检查失败，并保留缺失对示例。
刚体成员读取 USD 原生 articulation 描述；根挂在关节上时也能识别兄弟刚体，
成员列表缺失或为空时必需检查失败，避免路径前缀推断造成漏检。
检查使用布尔矩阵并迭代 Newton 紧凑过滤存储，避免再构造数百万 Python pair 的副本。

## 完整原生导入复验

随后恢复默认完整凸分解，使用原 USD、原关节和全部碰撞 hull，
未使用 `skip_mesh_approximation`、简化夹爪或替代 MJCF：

| 项目 | G1（声明的缺失相机适配） | G2（原样） |
|---|---:|---:|
| 带碰撞标记的 hull/shape | 3,134 | 4,076 |
| 应过滤的跨 body 内部对 | 3,675,353 | 6,935,434 |
| 实际缺失过滤 | 0 | 0 |
| CPU 求解器构建 | 通过 | 通过 |
| MuJoCo nq / nv / neq | 34 / 34 / 6 | 53 / 52 / 6 |
| 本次导入耗时 | 98.372 s | 143.433 s |
| 物理步进 | 0 | 0 |

原关节、连接、限位、mimic、闭环及源资产不变检查仍通过。
G1 仍仅在匿名 session 停用缺失的装饰性相机引用，原样组合失败仍保留。
最终报告和旧报告的 shape 数略有变化，不以一次数量差异推断分解确定性，
也不宣称凸分解几何保真问题已解决。

旧报告中的栈溢出、原始失败记录没有覆盖。精简证据见
[physx-resolver-validation-20261021.json](references/geniesim/physx-resolver-validation-20261021.json)；
本机完整报告、日志与前后对照诊断在
`.workbench/previews/geniesim-physx-resolver-20261021/`。
这些实验基于启动 HEAD `ea0f166` 加本轮未提交修复。
原始报告保留当时 sweep 原因文案；随后代码已改为明确“导入审计不执行物理步进”，
避免求解器已构建时仍提示必须解决构建问题。
完整导入之后，审计的成员识别从路径推断改为原生 USD 描述，并完成上述回归。
对同一源 USD 重新读取成员集合，确认覆盖完整报告的全部 3,134 / 4,076 个碰撞 shape，
且应过滤对数与完整导入审计完全一致；记录在 `native_membership_recheck.json`。
这项集合复核不冒充第二次完整导入或物理运行。

## 回归与工作台

原有 16 项和新增 7 项测试共 **23 项通过**（4.71 s；16 条 USD 原生解析弃用警告）。新增小型原生 USD 测试包含
三刚体链与外部球，检查：

- PhysX 关闭自碰撞时，非相邻内部对也正确过滤。
- PhysX 开启自碰撞，以及 Newton 显式 true 覆盖 PhysX false 时，不被强制关闭。
- 外部球不在排除对内，且真实 MuJoCo 接触检测仍产生机器人—球接触。
- 人为删除一个非相邻过滤后，报告精确拒绝并指出缺失对。
- articulation 根挂在固定关节上时仍覆盖三个实际成员；缺失或空成员列表均被拒绝。

测试没有 mock 核心导入或物理；小场景是原生 primitive，不需要跳过网格分解。
这里只检测求解器初始化时的接触，不把它写成受控运动或抓取成功。

```bash
.workbench/geniesim-venv/bin/python -m pytest -q \
  tests/test_geniesim_import.py tests/test_geniesim_collision_semantics.py
```

通过现有报告页面查看本次结果：
[G1](http://127.0.0.1:8765/runs/3a081bff19c143868e1f7d57?stage=backend&view=validation) ·
[G2](http://127.0.0.1:8765/runs/c4272da25bc69cebc0fbe643?stage=backend&view=validation)。
旧入口仍保留原来的失败报告，无需更换端口或重启服务。

## 尚未解决

G1 的原始驱动目标越限、缺失相机组合错误仍在；G1/G2 的部分辅助坐标体
未指定质量（`physics:mass` 返回 schema fallback 0），却携带显式非零主惯量。
Newton 导入后这些 body 的质量为零，finalize 将其惯量清零，现有一致性检查仍拒绝。
不能把 fallback 0 解释为源文件显式指定零质量；辅助 frame 的源引擎有效物理语义需继续核对。
此外，惯量方向、完整关节局部坐标、凸分解几何、闭环与 mimic 动态、
轮地接触、夹爪开合/接触力、相机和真实任务都尚需验证。

G1 只有左侧部分 collision 父 Xform 找到 `maxConvexHulls=32`，
G2 本次遍历未发现同类 authored 参数。不能笼统声称两者都声明相同分解预算。
当前 CoACD 的分组件分解与 `merge=False` 下输出数量仍需另行研究。

恢复源关闭自碰撞的语义，不等于证明目标机器人臂间永不碰撞。
任务运行若要求更严格的自碰撞检查，应明确目标场景的碰撞策略并重新验证，
不能沿用导入资格检查的结论。
