#!/usr/bin/env bash
# One-time setup for DexAgent Stage 4 (robot data generation).
# Run inside a fresh Python 3.11 environment:
#
#   conda create -y -n dexagent-stage4 python=3.11
#   conda activate dexagent-stage4
#   bash setup.sh
#
# Steps: (1) prepare the selected inpainting backend, (2) install Python packages, (3) download model weights.
# A VOID checkout elsewhere: point VOID_DIR at it (run_dexagent.py passes models.void_dir).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

python - <<'PY'
import sys
assert sys.version_info[:2] == (3, 11), f"Python 3.11 is required (bpy wheel), found {sys.version.split()[0]}"
PY

# The exact revision Stage 4 was tested with (override at your own risk)
VOID_COMMIT="${VOID_COMMIT:-e3914f8f551dd4b880661991fd6b28cd1699a97a}"
INPAINT_BACKEND="${DEXAGENT_STAGE4_INPAINT_BACKEND:-void}"

# 1. Prepare the selected inpainting backend. VOID is needed only for the default temporal backend.
mkdir -p third_party
if [ "$INPAINT_BACKEND" = "void" ]; then
  if [ ! -d third_party/void-model ]; then
    git clone https://github.com/netflix/void-model.git third_party/void-model
  fi
  if [ "$(git -C third_party/void-model rev-parse HEAD)" != "$VOID_COMMIT" ]; then
    git -C third_party/void-model fetch origin
    git -C third_party/void-model checkout --detach "$VOID_COMMIT"
  fi
  pip install -r third_party/void-model/requirements.txt
elif [ "$INPAINT_BACKEND" = "lama" ]; then
  python -c 'import torch; print("Using existing PyTorch", torch.__version__)'
else
  echo "DEXAGENT_STAGE4_INPAINT_BACKEND must be 'void' or 'lama' (got '$INPAINT_BACKEND')" >&2
  exit 2
fi

# 2. Stage 4 extras (SAM3-LiteText, MuJoCo, Blender)
pip install -r requirements.txt

# 3. Model weights. Public SAM3-LiteText weights are cached under $HF_HOME.
if [ "${SKIP_WEIGHTS:-0}" != "1" ]; then
  python - <<'PY'
import os
from pathlib import Path
from huggingface_hub import hf_hub_download, snapshot_download
sam_model = os.environ.get("DEXAGENT_SAM3_LITETEXT_PATH")
if sam_model and Path(sam_model).is_dir():
    print(f"Using local SAM3-LiteText model: {sam_model}")
else:
    print("SAM3-LiteText weights ...")
    snapshot_download("yonigozlan/sam3-litetext-s0")
if os.environ.get("DEXAGENT_STAGE4_INPAINT_BACKEND", "void") == "void":
    print("VOID pass-1 checkpoint ...")
    hf_hub_download("netflix/void-model", "void_pass1.safetensors")
    print("CogVideoX-Fun base model (VOID backbone) ...")
    snapshot_download("alibaba-pai/CogVideoX-Fun-V1.5-5b-InP")
else:
    from urllib.request import urlretrieve
    lama = Path(os.environ.get("DEXAGENT_LAMA_MODEL", "third_party/big-lama.pt"))
    lama.parent.mkdir(parents=True, exist_ok=True)
    if not lama.is_file():
        url = "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt"
        print(f"Downloading LaMa checkpoint to {lama} ...")
        urlretrieve(url, lama)
    print(f"Use inpaint.backend='lama' and inpaint.lama_model='{lama.resolve()}' in stage4.json")
PY
fi
echo "Stage 4 setup complete."
