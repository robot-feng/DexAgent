# SAM3 LiteText 视频清理接入与验证

## 目标与实现

Workbench 背景清理默认使用本机 SAM3 LiteText-S0 文本编码器，并接入 SAM3 原生视频跟踪器。LiteText 只替换文本编码器；SAM3 的图像检测、视频状态和双向时序传播仍由本机 SAM3 视频 checkpoint 提供，因此无需把 LiteText 逐帧调用再与另一套传播器拼接，也不依赖 SAM2。

适配器将 Hugging Face LiteText 权重映射到 EfficientSAM3 的 MobileCLIP-S0 文本编码器，按严格 state dict 加载，并保留转换缓存。三条提示词（`robot arm`、`cup`、`trash can`）的 Transformers 与原生编码输出已比对，最大绝对误差为 `3.34e-6`。视频任务为每个提示词单独创建 SAM3 session，先在主关键帧附近最多 16 个抽样帧中寻找可见种子，再从该帧向前和向后传播。独立 session 避免不同提示词之间的对象 ID 冲突。

模型来源固定在 [`efficientsam3` LiteText 分支](https://github.com/SimonZeng7108/efficientsam3/tree/sam3_litetext)，代码安装脚本锁定 commit `bef17f5c24dc5ef19dc1d8e9663345a2ae7f2f5a`。LiteText 论文在所测基准上报告了与原模型相近的图像和视频分割表现；这不等于任意机器人场景没有精度损失，本次也没有人工标注集可用于计算精度差异。

SimFoundry 的 Stage 1b、Hydra mask stage、对象提案、VOID 补洞、人工复核门和 AutoBG 构建顺序仍由原流程管理。LiteText 跟踪器在独立的本机 SAM3 Python 环境运行，Workbench 保持轻量。Stage 1b 和 mask stage 通过 `simfoundry` Conda 环境启动；scene `root_dir` 使用绝对路径，宿主机 ffmpeg 路径显式传入，避免 Hydra 路径解析和 Conda PATH 差异导致失败。

## 真实冒烟结果

使用 SimFoundry 示例 `ThrowAwayTrash.mp4` 经 Workbench `/background-cleanup` 页面和 API 创建任务，选择三个手动提示词，完整运行 Stage 1b 与 LiteText mask 阶段。Workbench 任务最终进入 `review_mask`，并生成人工复核所需 overlay、黑白 mask 视频和 `quadmask_meta.json`。

| 对象提示词 | 自动种子帧 | SAM3 分数 | 双向传播覆盖 |
| --- | ---: | ---: | ---: |
| robot arm | 0 | 0.805 | 400 / 400 帧 |
| cup | 79 | 0.605 | 400 / 400 帧 |
| trash can | 0 | 0.543 | 400 / 400 帧 |

输入和 mask 视频均为 400 帧、672×384。元数据记录 LiteText checkpoint SHA-256、SAM3 视频 checkpoint SHA-256、每对象种子帧和双向传播身份；worker log 另记录每对象分数与设备。视频抽样检查了开头、中间和结尾的 9 个时点：机械臂只在可见时被覆盖，杯子和垃圾桶持续跟踪，未观察到明显漂移。Workbench 默认模型与提交路径通过真实浏览器检查，Playwright 无页面脚本错误。

此示例属于固定机位操作演示，仅适用于 mask / 补洞冒烟，不能用于 3D 相机扫描重建。当前任务停在 `review_mask`；VOID 尚未运行，避免未经人工检查就进入补洞。屏幕中的黑白 mask 以黑色表示待移除区域。

## 验证命令与边界

已通过：

- `scripts/setup_sam3_litetext_video.sh`：本机原生视频依赖和锁定源码检查通过。
- `.workbench/venv/bin/python -m pytest -q workbench/tests/test_cleanup_segmentation.py`：1 passed。
- `.workbench/venv/bin/python -m pytest -q workbench/tests/test_workbench.py -k background_cleanup`：6 passed。
- `npm run build`：TypeScript 与 Vite 生产构建通过。
- `npm run test:e2e -- --grep 'background cleanup defaults'`：1 passed；默认 LiteText 可提交，其他传播组合按依赖预检禁用或启用。
- Workbench API 冒烟：Stage 1b → SAM3 LiteText 原生双向跟踪 → 400 帧 mask → 人工复核状态。

运行环境、checkpoint 或论文结果不能替代场景级质量评估。当前冒烟没有逐像素真值，也没有证明遮挡边缘、极小目标和所有视角都与完整 SAM3 相同。原 `droid_v1.usdz` 的机器人残影问题仍需该场景原始扫描视频重新分割与 VOID 修复；`ThrowAwayTrash.mp4` 不代表它已经清理。没有人工确认 mask 前，不应运行 VOID 或把结果作为干净背景使用。
