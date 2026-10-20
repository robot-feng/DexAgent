# SimFoundry 核心复现与环境状态（2026-10-21）

## 当前结论

**还没有完整复现 SimFoundry。** 主环境、VOID、Any6D、Hunyuan、DA3、Nerfstudio、3DGRUT 和 SAM3 LiteText 的环境搭建及各自局部导入/GPU 冒烟已完成。更关键的是，当前没有 VOID 两阶段视频修复、DA3、Hunyuan3D 等完整推理所需的模型权重，因此尚未跑通“扫描视频 → 前景分割 → 背景补洞 → 深度/点云 → 3DGS/场景资产”的端到端构建。

这里的“统一系统”是统一流程、配置、状态与产物契约，不是强行把所有模型塞进一个 Python 环境。SAM3 LiteText 单独运行在本机 Python 3.12 环境，SimFoundry 主环境是 Python 3.11，其他 CUDA 扩展继续隔离在各自环境。

## 环境和证据

| 部分 | 环境/状态 | 已验证 | 尚缺 |
| --- | --- | --- | --- |
| SimFoundry 主环境 | `/data/miniconda3/envs/simfoundry`，Python 3.11 | 核心包导入、CUDA 可用；`pip check` 通过；Hydra 能解析 AutoBG 配置 | 无完整重建权重与端到端运行 |
| Hunyuan3D | `/data/miniconda3/envs/hunyuan`，Python 3.10 | shape/texture pipeline、CUDA 扩展和依赖导入 | Hunyuan 推理权重；`pip check` 对上游 `opencv-python` 元数据及 bpy/ninja 平台标记有告警，但运行导入已通过 |
| Any6D | `/data/miniconda3/envs/any6d`，Python 3.10 | `pip check` 通过；PyTorch CUDA、torch-cluster、Kaolin、PyTorch3D、NVDiffRast、SAM2、FoundationPose、FAISS GPU 冒烟 | 需要真实物体/场景输入做 Any6D 推理验收 |
| VOID | `/data/miniconda3/envs/void`，Python 3.11 | `pip check` 通过，环境可导入 | Pass 1/2 checkpoint 和 CogVideoX-Fun 基础权重缺失，不能运行真实补洞 |
| DA3 | `/data/miniconda3/envs/da3-simfoundry`，Python 3.11 | 安装完成；`pip check` 通过；DA3 API 与 SimFoundry wrapper 可导入；A100 上 torch-cluster KNN、FAISS GPU 检索通过 | DA3 推理权重与真实帧推理 |
| Nerfstudio | `/data/miniconda3/envs/nerfstudio_simfoundry`，Python 3.10 | 安装完成；`ns-train` CLI、CUDA 12.8 和 gsplat 1.5.3 在 A100 上完成真实 CUDA rasterization | `pip check` 仅报告已知约束：Nerfstudio 1.1.5 要求 gsplat 1.4.0，而为新 GPU 架构使用 1.5.3；需继续做真实场景 splat/export |
| 3DGRUT | `/data/miniconda3/envs/3dgrut`，Python 3.11 | `pip check` 通过；Torch 2.8.0+cu128；3DGRUT、Kaolin 0.18、Warp 1.18、tiny-cuda-nn、PPISP、Fused-SSIM 导入通过；A100 上 Torch CUDA 运算与 tiny-cuda-nn 编码冒烟通过；`ply_to_usd.py --help` 正常 | 尚未用真实 3DGS PLY 执行 PLY→USDZ；3DGUT 插件会在首次真实导出时 JIT 编译 |
| SAM3 LiteText | `/data/miniconda3/envs/sam3` + 本机权重 | 示例 `ThrowAwayTrash.mp4` 已做 400 帧双向跟踪；LiteText worker 接入 SimFoundry AutoBG 默认后端；新增 2 项包装器测试通过 | 示例结果停留在人审 mask 阶段；无逐像素标注，不能声称任意场景零精度损失 |

`ThrowAwayTrash.mp4` 是获准的冒烟测试视频，不是用户原始扫描素材。此前的 SAM3 LiteText 结果只证明这段输入的分割/跟踪和复核产物链路，不代表 VOID 补洞、深度重建、3DGS 或数字孪生资产已经通过。

## SAM3 LiteText 选择

AutoBG 默认使用 `sam3_litetext`，它通过外部 worker 调用 SAM3 原生双向视频跟踪，无需另装 SAM2。LiteText 替换的是文本编码器；SAM3 的视觉主干与视频 tracker 仍然保留。这样尽量保留 SAM3 视频能力，但不是整体模型的小型化，也不能保证每个输入与原始 SAM3 完全无损。示例视频的实际 mask 仍须人工检查后才能提交 VOID。

对象提名默认由上层 Agent/操作者提供，不强制调用 Gemini。AutoBG wrapper 支持重复传入 `--object "robot arm" --object "cup"`；仅在需要 Vertex 提案时加 `--gemini-proposal`。Canonical A 流水线和 AutoBG wrapper 会从 Conda base 自动发现本机 `mamba-bootstrap` 路径，默认 shell 无需每次手动改 PATH。

本机 SAM3 LiteText runtime 记录位于 `.workbench/sam3_litetext_runtime_smoke.json`。AutoBG 的 Hydra 配置已指向 `da3-simfoundry`，避免误用已存在但不是有效 Conda 环境的 `/data/miniconda3/envs/da3` 目录。

## 缺失项与完整验收门

完整复现仍需完成以下实际推理，而不仅是安装包：

1. 下载或配置许可可用的 DA3、VOID Pass 1/2、CogVideoX-Fun、Hunyuan3D 等模型权重，并确认文件校验值。
2. 对 `ThrowAwayTrash.mp4` 先产出分割 mask，经人工复核后再执行 VOID Pass 1/2；检查机器人/操作对象是否完整移除、背景是否合理补全。
3. 跑 DA3、背景点云/3DGS、场景坐标对齐和 PLY→USDZ，检查最终场景资产与相机轨迹。
4. 按 SimFoundry 的其余目标路径运行 Hunyuan3D 与 Any6D，并记录输入、权重、配置、输出和失败原因。

当前 `hf auth whoami` 未登录，且本机没有 `gcloud` CLI；需要授权下载的模型或 Gemini/Vertex 提案不会自动可用。AutoBG 的对象提名现在由 Agent/操作者提供，Gemini 提案是可选项；但 canonical A 重建的部分场景分解/仿真属性阶段仍默认使用 Vertex VLM，完整运行时要么配置这些阶段的可用凭据，要么继续接入本地/Agent 后端。人工/Agent 提供对象提示词不能跳过 mask 复核和 VOID 模型权重要求。

本机可调用的 Mamba 位于 `/data/miniconda3/envs/mamba-bootstrap/bin/mamba`，它使用 `/data/miniconda3` 为环境根目录；该目录当前不在默认 PATH。直接运行安装脚本前可执行：

```bash
export PATH="/data/miniconda3/envs/mamba-bootstrap/bin:$PATH"
```

## 当前代码与复验

本阶段修改集中在嵌套 NVIDIA SimFoundry checkout。该 checkout 的 remote 指向上游，修改应通过 DexAgent 中的补丁文件交付，不能推送到 NVIDIA 上游。补丁文件：`patches/simfoundry-core-pipeline-environment.patch`、`patches/simfoundry-void-model-compat.patch` 和此前的 `patches/simfoundry-hunyuan-install-fix.patch`。

已完成的快速验证：

- `.workbench/venv/bin/python -m pytest SimFoundry/tests/test_sam3_litetext_autobg.py -q`：2 passed。
- SimFoundry Hydra 成功读取 `auto_bg.yaml`，默认 propagation 为 `sam3_litetext`、DA3 环境为 `da3-simfoundry`。
- Hydra 成功解析 Agent 提供的多对象提示词；A Reconstruction 的 Mamba 自动发现 dry-run 通过。
- 相关 Python 脚本 `py_compile` 和安装/运行 shell 脚本 `bash -n` 通过。
- Any6D 的独立 GPU 冒烟和 Hunyuan 的 CUDA 扩展导入证据见上表；它们都不是完整场景推理。
- 3DGRUT：`pip check` 无依赖冲突，Torch CUDA 与 tiny-cuda-nn 在 A100 上执行通过；PLY→USDZ 只验证了 CLI，尚无真实输入 PLY 做导出验收。
