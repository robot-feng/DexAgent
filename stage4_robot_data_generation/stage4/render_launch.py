"""Launch the same Cycles script through bpy or a standalone Blender build."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def blender_executable():
    """The standalone Blender selected by both rendering and provenance checks."""
    return os.environ.get("DEXAGENT_BLENDER") or shutil.which("blender")


def render_command(view, chunk=None, *, python=None):
    executable = blender_executable()
    args = [str(Path(view).resolve())] + ([str(chunk)] if chunk is not None else [])
    if executable:
        if not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise FileNotFoundError(f"Blender executable is unavailable: {executable}")
        return [executable, "--background", "--factory-startup", "--python-exit-code", "1",
                "--python", str(Path(__file__).with_name("render_cycles.py")), "--", *args]
    return [python or sys.executable, "-m", "stage4.render_cycles", *args]
