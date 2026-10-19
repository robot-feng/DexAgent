# EmbodiForge Studio 背景清理环境预检验证

日期：2026-10-20。

## 问题与修改

Conda 创建目录后，`conda-meta` 已存在，但 Python 包可能仍在下载或安装。原背景清理目录接口仅检查这个目录就显示“环境已安装”，可能允许用户启动尚不具备运行依赖的昂贵任务。

`workbench/background_cleanups.py` 现在以环境内的 `bin/python` 检查上游脚本需要的模块是否可发现。SimFoundry、VOID、DA3、Nerfstudio、3DGRUT、Hunyuan 使用各自依赖集合。检查缺少解释器、缺包、超时、异常退出或无效 JSON 时均返回不可用，并提供缺项或明确失败类型。VOID 仍同时检查已有的源码和 pass1/pass2 权重文件。

检查超时为 5 秒；目录轮询可复用 30 秒缓存，启动每一阶段前清空缓存并重查。检查不导入模型包，不初始化 CUDA、下载权重或启动 OmniGibson。环境位置沿用现有 Conda/当前前缀发现，并补充 `CONDA_ENVS_PATH`、`MAMBA_ROOT_PREFIX`。

此变更只覆盖已选上游工具链的 Python 模块发现，不代表模型加载、共享库/CUDA 扩展、GPU 显存或真实推理可用。模块齐全后仍需实际运行验证；任何推理失败应保留到阶段日志与终态。当前 canonical Stage 8 在主 `simfoundry` 环境运行，未额外要求独立 Any6D 环境。

## 验证结果

- `.workbench/venv/bin/python -m pytest workbench/tests -q`：54 passed，18.71 秒。
- `ruff check workbench/background_cleanups.py workbench/tests/test_cleanup_readiness.py`：通过。
- 新测试覆盖：已创建但无解释器的环境、真实目标 Python 的缺包检测、启动前阻断且不建任务、探测超时/异常输出、损坏结果的失败关闭、轮询缓存与启动强制重查、VOID 权重文件不能代替运行依赖、非默认环境根目录。
- 真实主环境仍在安装：检查发现 `simfoundry`、`torch`、`PIL`、`hydra`、`omegaconf`、`sam3`、`transformers`、`skimage` 缺失，返回不可用；未加载任何模型。
- 默认端口 8765 已恢复，`/api/workbench/v1/health`、`/api/workbench/v1/background-cleanups/catalog` 和 `/background-cleanup` 均为 HTTP 200。目录接口的 `preview_available`、`build_available` 均为 false。
- Playwright 实际访问默认端口的背景清理页，确认显示上述依赖缺项、“生成 mask 预览”按钮禁用，无浏览器脚本错误。本机截图：`.workbench/previews/cleanup-runtime-preflight-20261020.png`。

本轮没有执行真实 SAM、VOID、DA3 推理，也没有把既有 `droid_v1` 背景标记为已清理。SimFoundry 既有安装进程保持运行，继续向完整复现推进。
