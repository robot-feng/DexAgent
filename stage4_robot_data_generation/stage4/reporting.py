"""Small provenance helpers for Stage 4 recovery and explicit visual review."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from agent.run_reports import sha256_file

DEPENDENCIES = {"randomize": (), "plate": (), "export": ("randomize", "plate"),
                "render": ("export",), "composite": ("randomize", "plate", "render")}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str, allow_nan=False).encode()).hexdigest()


def input_identity(cfg):
    """Include external config paths and MJCF assets, not generated output files."""
    files, visited = {}, set()

    def add(path):
        path = Path(path).resolve()
        if path in visited or path == cfg.out.resolve() or cfg.out.resolve() in path.parents:
            return
        visited.add(path)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    add(child)
        elif path.is_file():
            files[str(path)] = sha256_file(path)
            if path.suffix.lower() == ".xml":
                tree = ET.parse(path).getroot()
                compiler = tree.find("compiler")
                attrs = compiler.attrib if compiler is not None else {}
                for node in tree.iter():
                    if "file" in node.attrib:
                        directory = attrs.get("meshdir" if node.tag == "mesh" else "texturedir", attrs.get("assetdir", ""))
                        if node.tag == "include":
                            directory = ""
                        asset = path.parent / directory / node.attrib["file"]
                        if not asset.is_file():
                            raise ValueError(f"missing scene asset: {asset}")
                        add(asset)

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, str) and value:
            path = cfg.path_of(value)
            try:
                exists = path.exists()
            except OSError:  # Free text (for example a long prompt) is not a path.
                exists = False
            if exists and path.resolve() != cfg.root.resolve():
                add(path)

    walk({k: v for k, v in cfg.items() if k != "output_dir"})
    environment = {key: os.environ.get(key) for key in
                   ("DEXAGENT_SEGMENTATION_BACKEND", "DEXAGENT_SAM3_LITETEXT_PATH",
                    "DEXAGENT_LAMA_MODEL", "VOID_DIR", "HF_HOME", "DEXAGENT_ISAAC_PYTHON")}
    # Model files explicitly supplied through environment are inputs too.
    for key in ("DEXAGENT_SAM3_LITETEXT_PATH", "DEXAGENT_LAMA_MODEL"):
        if environment[key]:
            add(environment[key])
    return {"files": files, "environment": environment}


def artifact_manifest(cfg, step, artifacts):
    """Hash step-owned files; later steps may add files in the same directories."""
    paths = set()
    roots = {"randomize": [cfg.out / "randomize.json", cfg.out / "samples"],
             "plate": [cfg.out / "plate"], "export": [cfg.out / "render"],
             "render": [cfg.out / "render"], "composite": [cfg.out / "samples"]}[step]
    for root in [*roots, *(Path(p) for p in artifacts)]:
        paths.update(root.rglob("*") if root.is_dir() else [root])
    result = {}
    for path in sorted(paths):
        if not path.is_file():
            continue
        rel = path.relative_to(cfg.out) if path.is_relative_to(cfg.out) else path
        if step == "randomize" and (path.suffix == ".mp4" or path.name == "composite.json"):
            continue
        if step == "export" and ("frames" in rel.parts or path.suffix == ".log" or
                                  path.name in {"render_chunks.json", "render_device_report.json", "contact_sheet.jpg"}):
            continue
        if step == "render" and not ("frames" in rel.parts or path.suffix == ".log" or
                                      path.name in {"render_chunks.json", "render_device_report.json", "contact_sheet.jpg"}):
            continue
        if step == "composite" and path.suffix != ".mp4" and path.name != "composite.json":
            continue
        if step == "randomize" and path.name == "sample.json":
            data = json.loads(path.read_text())
            data.pop("textures", None)  # export adds this annotation; its assets are hashed by export.
            result[str(path)] = digest(data)
        else:
            result[str(path)] = sha256_file(path)
    return result


def reusable(cfg, step, report, identity):
    if report.get("status") not in {"passed", "warning"} or report.get("identity_sha256") != identity:
        return False
    manifest = report.get("artifact_hashes")
    return bool(manifest) and manifest == artifact_manifest(cfg, step, report.get("artifacts", []))


def descendants(step):
    found = {step}
    for name, dependencies in DEPENDENCIES.items():
        if found.intersection(dependencies):
            found.add(name)
    return found - {step}
