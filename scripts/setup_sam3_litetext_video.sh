#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${SAM3_LITETEXT_SOURCE:-${REPO_ROOT}/.workbench/resources/efficientsam3-litetext}"
EXPECTED_REVISION="bef17f5c24dc5ef19dc1d8e9663345a2ae7f2f5a"

if [[ ! -d "${SOURCE_DIR}/.git" ]]; then
  mkdir -p "$(dirname "${SOURCE_DIR}")"
  git clone --depth 1 --branch sam3_litetext \
    https://github.com/SimonZeng7108/efficientsam3.git "${SOURCE_DIR}"
fi

ACTUAL_REVISION="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
if [[ "${ACTUAL_REVISION}" != "${EXPECTED_REVISION}" ]]; then
  echo "LiteText video source revision mismatch: ${ACTUAL_REVISION}" >&2
  echo "Expected ${EXPECTED_REVISION}; review the upstream change before updating this pin." >&2
  exit 1
fi

SAM3_VIDEO_PYTHON="${SAM3_LITETEXT_VIDEO_PYTHON:-/data/miniconda3/envs/sam3/bin/python}"
if [[ ! -x "${SAM3_VIDEO_PYTHON}" ]]; then
  echo "SAM3 video Python is unavailable: ${SAM3_VIDEO_PYTHON}" >&2
  echo "Set SAM3_LITETEXT_VIDEO_PYTHON to an environment with SAM3's torch/CUDA and video dependencies." >&2
  exit 1
fi

PYTHONPATH="${SOURCE_DIR}/sam3:${REPO_ROOT}" "${SAM3_VIDEO_PYTHON}" - <<'PY'
import importlib.util
required = ("torch", "torchvision", "timm", "ftfy", "safetensors", "iopath", "numpy", "PIL", "cv2", "einops")
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("SAM3 LiteText video runtime is missing: " + ", ".join(missing))
print("SAM3 LiteText native video runtime dependencies are ready.")
PY

echo "LiteText video source: ${SOURCE_DIR}/sam3"
echo "Pinned revision: ${ACTUAL_REVISION}"
