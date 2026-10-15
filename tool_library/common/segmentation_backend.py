"""Internal text-segmentation registry shared by perception and Stage 4.

Plugins implement ``segment(image, prompt)`` and expose ``model_identity`` with
``version``, ``repo_id`` and a SHA-256 ``checkpoint_sha256``. The returned
mapping uses ``masks`` plus optional ``scores`` and ``boxes``.
"""
from __future__ import annotations

from importlib.metadata import entry_points

BACKENDS = {}


def backend_available(name):
    """Check a backend name without loading its weights or running inference."""
    if name in {"sam3-litetext-s0", "sam3"} or name in BACKENDS:
        return True
    return any(ep.name == name for ep in entry_points(group="dexagent.segmentation_backends"))


def register_backend(name, factory):
    if not name or not callable(factory):
        raise ValueError("a segmentation backend needs a name and callable factory")
    BACKENDS[str(name)] = factory


def load_backend(name="sam3-litetext-s0", min_score=0.3):
    """Load a registered backend or a package entry point named by ``name``."""
    if name in {"sam3-litetext-s0", "sam3"}:
        from tool_library.skills.detect_objects import load_sam3_litetext
        backend = load_sam3_litetext(min_score=min_score, version="sam3-litetext-s0")
        if not hasattr(backend, "segment"):
            raise TypeError("SAM3-LiteText adapter does not implement segment(image, prompt)")
        return backend
    factory = BACKENDS.get(name)
    if factory is None:
        factory = next((entry.load() for entry in entry_points(group="dexagent.segmentation_backends")
                        if entry.name == name), None)
    if factory is None:
        raise ValueError(f"unknown text-segmentation backend {name!r}; register it or install a dexagent.segmentation_backends entry point")
    backend = factory(min_score=min_score)
    if not callable(getattr(backend, "segment", None)):
        raise TypeError(f"backend {name!r} must implement segment(image, prompt)")
    identity = getattr(backend, "model_identity", None)
    if (not isinstance(identity, dict) or not all(isinstance(identity.get(key), str) and identity[key].strip()
                                                   for key in ("version", "repo_id", "checkpoint_sha256"))
            or len(identity["checkpoint_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in identity["checkpoint_sha256"])):
        raise TypeError(f"backend {name!r} must expose version, repo_id and a SHA-256 checkpoint identity")
    return backend
