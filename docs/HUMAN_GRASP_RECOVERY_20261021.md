# 人类示范抓取：双验收与有限修复（2026-10-21）

复用现有 `human_grasp_seeds`、`optimize_grasp_pose` 和物理验证器，完成
“示范先验 → 验证 → 失败修正 → 独立复验”。本轮验证范围是 MuJoCo 中
Sharpa 手与对象的抓取；没有执行完整 YAM 手臂，也没有完成 G1/G2 同任务验收。

后续固定臂搜索与箱体实际执行见
[全臂验证记录](HUMAN_GRASP_FULL_ARM_20261021.md)。本页保留原手部实验范围与失败证据。

## 候选验收

`human_grasp_seeds(verify=True)` 现在对 `verify_top` 内的候选调用优化器已有的
验证序列，先检查默认 60/100/150 mm 举升，再检查默认 80 mm 举升与 0.8 s
持续握持。只有两项都通过的候选优先排序。第一项失败时第二项不执行，
记录为未执行，不伪造握持失败或成功。

每个种子保存本轮 `stress_tests` 和 `verification`：

- `passed`：所要求的两个物理检查都通过。
- `failed`：本轮验证失败，记录工具及首个失败阶段。
- `not_tested`：未要求验证或超出候选预算；没有物理通过结论。

子任务摘要包含测试、通过、未测的数量。原有 `lift` 摘要保留，增加 `hold`
摘要；消费者应读取联合 `verification.status`，不能继续仅凭 `lift.ok`
接受抓取。无通过候选时明确建议使用已有优化工具，不自动扩大搜索预算。

优化器生成候选时删除输入附带的旧 `lift`、`hold`、`verification`、
`stress_tests`，防止改变动作后仍携带原来的验收标签。优化输出以本轮重新
运行的 `stress_tests` 为准，源帧与采集时间仍保留作示范来源。

## 两例失败恢复

使用上一阶段保存的失败种子、精确 primitive 场景和原始 Sharpa 资产。
为避免并行修改影响 checkpoint，实验使用固定源码快照；随后新增回归直接
在当前仓库源码上重新执行优化并独立复验。

搜索参数：`family="seeds"`、`max_evals=64`、`iterations=4`、
`population=16`、`workers=2`、`seed=20261021`。未指定宽松测试参数，
举升高度、握持时间、接触/滑移/旋转/穿透门槛均使用验证器默认值。

| 失败先验 | 候选评估 / 进入物理测试 | 返回通过且独立复验通过 | 最佳举升实测（mm） |
|---|---:|---:|---|
| `box_right` | 32 / 16 | 2 / 2 | 59.8 / 99.8 / 149.8 |
| `tall_box_right_hamer` | 16 / 3 | 1 / 1 | 59.9 / 99.9 / 149.9 |

两个最佳结果的握持滑移均为 0 mm/s，结束时保持两个手指接触，
最大接触穿透 0.465 mm（默认上限 3 mm）。

箱体最佳候选保持位置、朝向和接近方向，压紧量从 0.2 改为 0.152 rad。
这说明更大的压紧量并不必然更稳。高箱体通过有限的位姿、接近方向和闭合量
调整通过；这些修正不是对人类动作的逐点忠实回放。

数值与搜索范围见
[grasp-recovery-validation.json](references/humanego/grasp-recovery-validation.json)。
本机完整输入、固定源码快照、哈希、checkpoint、失败分类和独立验证在
`.workbench/previews/human-grasp-recovery-20261021/`。
此目录不纳入源码提交。小型可重现输入保存在
`tool_library/tests/fixtures/human_grasp_recovery.json`，无绝对路径、模型权重
或预录制成功姿态。

## 调用方式

```python
seeds = human_grasp_seeds(..., verify=True, verify_top=3)
# 查看对应子任务 verification.passed。若为零，将其 seeds_json 交给：
result = optimize_grasp_pose(
    hand_scene=scene,
    family="seeds",
    starts=seeds_json,
    max_evals=64, iterations=4, population=16, workers=2, seed=20261021,
)
# result.ok 为真才有通过候选；完整机器人任务仍须进入既有执行与验收路径。
```

修复输出应独立重跑默认 `grasp_lift_test` 和 `grasp_hold_test`。
若需要手臂到达性，向优化器提供实际 `bench_scene` 和任务路径；
仅手场景通过不能推导完整机器人无碰撞、可达或任务成功。

## 回归结果

- 种子排序、预算、异常、旧标签清除及实际 Sharpa 双验收等 8 项新测试通过。
- 结合时钟链路、搜索预算和控制优化的测试共 61 项通过。
- 两例新增恢复测试 2 项通过：先证实原首选失败，再调用当前优化器真正搜索，
  并对最佳及所有返回通过姿态独立重验；同时注入旧失败标签检查其不会泄漏。
- 种子验收与优化器 checkpoint 回归 19 项、4 个子测试通过，覆盖中断、恢复、
  缓存通过结果重新物理验收及源码/输入变化拒绝恢复。
- 原 `test_human_grasp_seeds.py` 断言未改，重新运行得到 9 PASS、1 FAIL：
  三个原始先验案例通过，高箱体原始先验仍无通过候选。它需要上述优化步骤，
  不能把“修复后通过”写成“原始先验全部通过”。

复现新增物理恢复：

```bash
.workbench/venv/bin/python -m pytest -q \
  tool_library/tests/test_human_seed_verification.py \
  tool_library/tests/test_human_grasp_recovery.py
```

这些案例的输入是原测试构造的、包含几何误差和缺帧的合成示范，
不是 HumanEgo 真实采集数据，也不是三本体任务结果。下一步需要把已验证的
抓取接入实际机器人全臂路径、碰撞及任务验收；继续保留原始失败和修复来源。
