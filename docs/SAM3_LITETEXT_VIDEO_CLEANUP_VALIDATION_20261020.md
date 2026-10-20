# SAM3 LiteText 视频清理接入与验证

## 目标与实现

Workbench 背景清理默认使用本机 SAM3 LiteText-S0 文本编码器，并接入 SAM3 原生视频跟踪器。LiteText 只替换文本编码器；SAM3 的图像检测、视频状态和双向时序传播仍由本机 SAM3 视频 checkpoint 提供，因此无需把 LiteText 逐帧调用再与另一套传播器拼接，也不依赖 SAM2。

适配器将 Hugging Face LiteText 权重映射到 EfficientSAM3 的 MobileCLIP-S0 文本编码器，按严格 state dict 加载，并保留转换缓存。三条提示词（`robot arm`、`cup`、`trash can`）的 Transformers 与原生编码输出已比对，最大绝对误差为 `3.34e-6`。视频任务为每个提示词单独创建 SAM3 session，先在主关键帧附近最多 16 个抽样帧中寻找可见种子，再从该帧向前和向后传播。独立 session 避免不同提示词之间的对象 ID 冲突。

模型来源固定在 [`efficientsam3` LiteText 分支](https://github.com/SimonZeng7108/efficientsam3/tree/sam3_litetext)，代码安装脚本锁定 commit `bef17f5c24dc5ef19dc1d8e9663345a2ae7f2f5a`。LiteText 论文在所测基准上报告了与原模型相近的图像和视频分割表现；这不等于任意机器人场景没有精度损失，本次也没有人工标注集可用于计算精度差异。

SimFoundry 的 Stage 1b、Hydra mask stage、对象提案、VOID 补洞、人工复核门和 AutoBG 构建顺序仍由原流程管理。LiteText 跟踪器在独立的本机 SAM3 Python 环境运行，Workbench 保持轻量。Stage 1b 和 mask stage 通过 `simfoundry` Conda 环境启动；scene `root_dir` 使用绝对路径，宿主机 ffmpeg 路径显式传入，避免 Hydra 路径解析和 Conda PATH 差异导致失败。

运行预检将 SimFoundry Stage 1b/Hydra 基础依赖与传统 SAM3 图像分割依赖分开：LiteText 路线需要基础环境和独立 LiteText tracker；原生 SAM3 或 SAM3+SAM2 路线额外要求 SimFoundry 环境能导入 SAM3。mask 通过人工复核后运行 VOID 时，只重查基础环境与 VOID，不要求分割模型环境仍在机器上。

本机也有 SAM2.1 Hiera-Small checkpoint（约 184 MB），但 SimFoundry 原始 SAM3+SAM2 路线固定查找 `sam2.1_hiera_large.pt` 并使用 Hiera-L 配置；Workbench 不会把 Small 权重冒充 Large。LiteText-S0 只替换文本编码器，SAM3 视觉主干与视频 tracker 仍保留，因此适合优先维持 SAM3 的跟踪能力，但它不是与 SAM2-Small 同级的整体显存缩小方案。上游 LiteText 基准报告性能接近教师模型，不能保证每个机器人场景零精度损失；本次真实视频冒烟没有逐像素真值，仍需人审 mask。

因此当前默认选 SAM3 LiteText，而不是仅因 checkpoint 文件小就切到本机 SAM2 Small。LiteText 复用 SAM3 原生图像与视频跟踪路径；它主要减轻文本编码器，不会把整套视觉跟踪器缩小。本次 A100 推理记录的 SAM3 峰值显存约 9.3 GiB。SAM2 Small 的 184 MB 是权重文件大小，不能直接代表显存占用，也没有在同一场景上完成效果对照。已有文本编码器输出对照最大绝对误差 `3.34e-6`，但不能据此声称整段视频的 mask 与原始 SAM3 完全无损。

## 真实冒烟结果

使用 SimFoundry 示例 `ThrowAwayTrash.mp4` 经 Workbench `/background-cleanup` 页面和 API 创建任务，选择三个手动提示词，完整运行 Stage 1b 与 LiteText mask 阶段。Workbench 任务最终进入 `review_mask`，并生成人工复核所需 overlay、黑白 mask 视频和 `quadmask_meta.json`。

| 对象提示词 | 自动种子帧 | SAM3 分数 | 双向传播覆盖 |
| --- | ---: | ---: | ---: |
| robot arm | 0 | 0.805 | 400 / 400 帧 |
| cup | 79 | 0.605 | 400 / 400 帧 |
| trash can | 0 | 0.543 | 400 / 400 帧 |

输入和 mask 视频均为 400 帧、672×384。元数据记录 LiteText checkpoint SHA-256、SAM3 视频 checkpoint SHA-256、每对象种子帧和双向传播身份；worker log 另记录每对象分数与设备。视频抽样检查了开头、中间和结尾的 9 个时点：机械臂只在可见时被覆盖，杯子和垃圾桶持续跟踪，未观察到明显漂移。Workbench 默认模型与提交路径通过真实浏览器检查，Playwright 无页面脚本错误。

为单独检查机械臂，另用相同 `ThrowAwayTrash.mp4` 仅以 `robot arm` 为提示词生成 mask：种子帧为 0，分数 `0.805`，400 / 400 帧均生成输出。黑色待移除 mask 在 341 帧非空；另外 59 帧分布于 126–148 和 252–287 两段。逐段查看这些空帧及边界原图，机械臂已完全转出画面；重新入镜后 mask 随之恢复。非空相邻帧 IoU 中位数为 `0.943`、第 10 百分位为 `0.882`；较低值集中在目标进出画面的边缘帧。该检查说明 tracker 能在本示例的视角变化中隐藏画外机器人并恢复可见目标，但它仍是操作演示视频，且没有逐像素标注，不能证明任意场景均无漏检或可直接完成背景修复。

此示例属于操作演示而非相机扫描素材；视频中有视角变化，但场景围绕操作台和抓取任务组织，因此仅用于 mask / 补洞流程冒烟，不能替代真实场景扫描重建。当前任务停在 `review_mask`；VOID 尚未运行，避免未经人工检查就进入补洞。屏幕中的黑白 mask 以黑色表示待移除区域。

另外使用 SimFoundry 示例 `Fruits.mp4` 做了相机扫描视角变化下的 SAM3 LiteText mask 冒烟。该视频 1280×720、15.07 秒；抽帧后为 400 帧、672×384。手动提示词 `banana` 在第 0 帧播种，分数为 `0.965`，SAM3 原生跟踪器双向传播覆盖 400 / 400 帧。对 mask 视频按黑色待移除区域统计，面积占比为 `1.67%–3.52%`、中位数 `2.91%`；相邻帧 IoU 中位数 `0.970`、最低 `0.892`，没有空 mask。抽查第 0、40、80、120、160、200、240、280、320、360、399 帧，目标 mask 随相机视角变化保持在香蕉上，未见明显覆盖相邻水果。这个输入不含机器人，因此只验证多视角目标分割与传播，不能替代机器人移除或背景补全验证。

mask 人审界面现在将原始视频与 mask 视频并排同步播放，任一侧的播放、暂停、进度拖动和速度调整都会同步到另一侧；VOID 复核界面复用同一组件对照输入与清理结果。默认 8765 上的真实浏览器回归验证了当前 `review_mask` 任务两边拖动时间轴的同步；测试没有调用确认接口，因此任务仍停在人审门。当前缺少 VOID 真实产物，清理前后视图尚未有运行数据可做端到端验证。

## VOID 补洞依赖预检

补洞启动门现在按 SimFoundry 实际执行路径检查 Pass 1/2 driver、VOID 推理源码和配置、`auto_bg.yaml` 中配置的两份 VOID checkpoint，以及本地 CogVideoX-Fun 基础模型的配置、分词器、调度器和 transformer/VAE/text-encoder 权重文件。这样 Conda 环境里模块可导入、但模型未下载完整时，Workbench 会明确报缺项并阻止创建昂贵的补洞任务；mask 复核页也使用同一预检禁用补洞按钮并显示原因，后端启动检查继续作为最终门禁。自动背景流程实际调用本地模型目录；仅有 Hugging Face 缓存或权重文件名不能代替该目录。

当前本机预检确认 VOID Python 模块和上游脚本已存在，仍缺 `void_pass1.safetensors`、`void_pass2.safetensors` 和 `CogVideoX-Fun-V1.5-5b-InP` 基础模型目录，因此没有运行 VOID。CogVideoX-Fun 官方模型页标明 5B Transformer 权重按 CogVideoX License 发布，代码仓库的 Apache-2.0 许可不等同于 5B 权重许可；若用于商业服务，模型许可要求先单独核验。[CogVideoX-Fun 5B 模型卡](https://huggingface.co/alibaba-pai/CogVideoX-Fun-V1.5-5b-InP) · [CogVideoX License](https://github.com/alibaba/Tora/blob/main/CogVideoX_LICENSE)

补齐时不需要运行会尝试下载 SimFoundry 全部 checkpoint 的总脚本。先在 Hugging Face 完成本账号需要的模型访问/许可步骤，然后在仓库根目录只下载这三项；VOID 权重必须放在 `SimFoundry/deps/void-model`，基础模型目录名也必须与配置一致：

```bash
hf auth login
hf download alibaba-pai/CogVideoX-Fun-V1.5-5b-InP \
  --local-dir SimFoundry/deps/void-model/CogVideoX-Fun-V1.5-5b-InP
hf download netflix/void-model void_pass1.safetensors \
  --local-dir SimFoundry/deps/void-model
hf download netflix/void-model void_pass2.safetensors \
  --local-dir SimFoundry/deps/void-model
```

完成后重新打开背景清理页或等待环境目录刷新；预检会逐项确认基础模型组件和两份 checkpoint，再启用 mask 复核后的 VOID 操作。不要把权重提交到 Git。

## 验证命令与边界

已通过：

- `scripts/setup_sam3_litetext_video.sh`：本机原生视频依赖和锁定源码检查通过。
- `.workbench/venv/bin/python -m pytest -q workbench/tests/test_cleanup_segmentation.py`：1 passed。
- `.workbench/venv/bin/python -m pytest -q workbench/tests/test_workbench.py -k background_cleanup`：7 passed。
- `.workbench/venv/bin/python -m pytest -q workbench/tests/test_cleanup_readiness.py workbench/tests/test_workbench.py -k 'cleanup or inpainting or void_preflight'`：24 passed，覆盖 VOID 两遍实际脚本、推理配置、配置指定权重及 CogVideoX 基础模型完整/缺失。
- `npm run build`：TypeScript 与 Vite 生产构建通过。
- `npm run test:e2e -- --grep 'background cleanup defaults'`：1 passed；默认 LiteText 可提交，页面展示 VOID 基础模型和两份 checkpoint 的真实缺项；其他传播组合按依赖预检禁用或启用；操作演示提示准确说明其不保证完整场景覆盖，不再误称固定机位。
- `npm run test:e2e -- --grep 'background cleanup'`：2 passed；在真实 `review_mask` 任务上验证缺少 VOID 模型时补洞按钮禁用、缺项说明可见，并验证原视频与 mask 视频双向拖动同步；未推进人工确认门。
- Workbench API 冒烟：Stage 1b → SAM3 LiteText 原生双向跟踪 → 400 帧 mask → 人工复核状态。
- 机械臂单目标冒烟：`ThrowAwayTrash.mp4` → 400 帧 mask → 画外区间与入镜恢复检查、非空帧相邻 IoU 统计 → 人工复核状态。
- `Fruits.mp4` scene-scan 冒烟：SAM3 LiteText 原生双向跟踪 → 400 帧 mask → 11 个视角样本视觉检查和整段 mask 面积 / 相邻帧 IoU 检查 → 人工复核状态。

运行环境、checkpoint 或论文结果不能替代场景级质量评估。当前冒烟没有逐像素真值，也没有证明遮挡边缘、极小目标和所有视角都与完整 SAM3 相同。原 `droid_v1.usdz` 的机器人残影问题仍需该场景原始扫描视频重新分割与 VOID 修复；`ThrowAwayTrash.mp4` 不代表它已经清理。没有人工确认 mask 前，不应运行 VOID 或把结果作为干净背景使用。
