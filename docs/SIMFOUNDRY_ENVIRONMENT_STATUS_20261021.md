# SimFoundry 环境阶段状态（2026-10-21）

## 结论

当前不是完整 SimFoundry 复现。Hunyuan3D 的 Python/CUDA 环境已完成安装并通过核心模块导入验证；独立 Any6D 环境仍在下载 PyTorch CUDA 依赖，尚未编译 FoundationPose、NVDiffRast、Kaolin 或 PyTorch3D 扩展。没有 Hunyuan3D、VOID 或 CogVideoX 推理权重，因此本阶段不代表完整重建推理通过。

## Hunyuan3D 环境

- 环境：`/data/miniconda3/envs/hunyuan`，Python 3.10。
- PyTorch：`2.7.0+cu128`；CUDA 可用，设备为 NVIDIA A100-SXM4-80GB。
- CUDA 扩展：`custom_rasterizer_kernel` 与 `DifferentiableRenderer.mesh_inpaint_processor` 已编译并从 Hunyuan3D 源码目录成功导入。
- 验证还导入了 Hunyuan3D shape pipeline（`hy3dshape.pipelines.Hunyuan3DDiTFlowMatchingPipeline`）、texture pipeline（`textureGenPipeline.Hunyuan3DPaintPipeline`）、OpenCV、PyBullet 和 Blender Python 模块。
- 本次验证只覆盖包导入、CUDA 设备识别及本地扩展加载；缺少模型权重，没有运行形状生成或纹理生成推理。

关键版本：OpenCV 4.11.0、PyBullet 3.2.7、Blender Python 4.0.0。`pip check` 仍报告部分上游包声明需要 `opencv-python`，而该环境按无头服务安装的是同版本 `opencv-python-headless`；直接 `cv2` 导入和上述流水线模块导入通过。该元数据差异尚未在完整服务运行中验证。

## Any6D 环境

- 环境：`/data/miniconda3/envs/any6d`，Python 3.10；创建成功。
- 截至 2026-10-21 01:45 CST，PyTorch 2.7.0 / torchvision 0.22.0 / torchaudio 2.7.0（CUDA 12.8）和 xformers 已安装。Any6D `requirements.txt` 正由安装脚本继续安装（当前正在下载 Open3D）；大体积 CUDA wheel 阶段曾遇到代理 SSL EOF，pip 自动续传并完成。当前环境仍不能用于 Any6D 推理。
- 安装脚本后续还会安装 Any6D 依赖并构建 NVDiffRast、FoundationPose、Kaolin 与 PyTorch3D。所有扩展必须编译后通过导入和 CUDA 冒烟测试，才可标为可用。

## 分割与模型权重边界

SAM3 LiteText-S0 模型已存在本机 Hugging Face 缓存；`ThrowAwayTrash.mp4` 冒烟测试曾完成 400 帧 SAM3 原生视频跟踪并进入 `review_mask` 人工复核门。该结果证明分割产物链路，不证明任意场景精度，也不证明背景修复或完整重建通过。当前不把 SAM2 Small 设为默认分割器，也不声称 SAM3 Lite 与其它模型质量完全等同。

当前缓存未发现 Hunyuan3D、VOID 或 CogVideoX-Fun 所需推理权重。完整 SimFoundry 场景重建仍需安装/验证 DA3 与 VOID 推理环境、补齐授权可用的模型权重，并对端到端 A 流水线进行真实输入验证。

## 安装改动与复验

Hunyuan3D 的固定版本 `tb_nightly==2.18.0a20240726` 在当前区域 PyPI 镜像不可用，安装脚本现先从官方 PyPI 获取该 wheel，再通过原镜像安装其余依赖；`requirements_hunyuan.txt` 固定了与其兼容的 `protobuf==4.25.9`、`pydantic==2.10.6`。这些变更的可应用补丁保存在 `patches/simfoundry-hunyuan-install-fix.patch`，因为嵌套 SimFoundry 仓库 remote 指向 NVIDIA 上游，不能直接推送到该上游。

复验入口（在 SimFoundry 仓库根目录执行）：

```bash
cd /data/tzq/DexAgent/SimFoundry
PYTHONPATH="$PWD/deps/Hunyuan3D-2.1/hy3dshape:$PWD/deps/Hunyuan3D-2.1/hy3dpaint:$PWD/deps/Hunyuan3D-2.1/hy3dpaint/custom_rasterizer" \
  /data/miniconda3/envs/hunyuan/bin/python - <<'PY'
import torch, cv2, pybullet, bpy
import custom_rasterizer_kernel
from DifferentiableRenderer import mesh_inpaint_processor
from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
from textureGenPipeline import Hunyuan3DPaintPipeline
assert torch.cuda.is_available()
print(torch.__version__, torch.cuda.get_device_name(0))
print(cv2.__version__, pybullet, bpy.app.version_string)
print(custom_rasterizer_kernel.__file__, mesh_inpaint_processor.__file__)
print(Hunyuan3DDiTFlowMatchingPipeline, Hunyuan3DPaintPipeline)
PY
```

下一验收点：Any6D 安装脚本完成后，确认 CUDA 扩展编译、核心模块导入、A100 上实际 CUDA 运算，再更新该记录；完成权重配置和真实输入推理前，不将 SimFoundry 标记为完整复现。
