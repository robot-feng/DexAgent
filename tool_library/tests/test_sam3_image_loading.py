"""SAM3-LiteText loading, backend routing, and scoped inference tests; no download or GPU required."""
import importlib
import os
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
D = importlib.import_module("tool_library.skills.detect_objects")
S = importlib.import_module("tool_library.skills.segment_with_text_prompts")


class _Model:
    device = "cpu"

    def to(self, device):
        self.device = device
        return self

    def eval(self):
        return self

    def __call__(self, **inputs):
        return {}


class _Torch:
    __version__ = "test-torch"
    cuda = types.SimpleNamespace(is_available=lambda: False)

    @staticmethod
    def as_tensor(value):
        return np.asarray(value)


class SamLiteTextLoadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        (self.model_dir / "model.safetensors").write_bytes(b"fixture checkpoint")
        self.model = _Model()
        self.auto_model = Mock()
        self.auto_model.from_pretrained.return_value = self.model
        self.auto_processor = Mock()
        self.auto_processor.from_pretrained.return_value = Mock()
        self.download = Mock(return_value=str(self.model_dir))
        self.modules = {
            "torch": _Torch,
            "huggingface_hub": types.SimpleNamespace(snapshot_download=self.download),
            "transformers": types.SimpleNamespace(AutoModel=self.auto_model, AutoProcessor=self.auto_processor),
        }

    def load(self, local=True):
        env = {"DEXAGENT_SAM3_LITETEXT_PATH": str(self.model_dir)} if local else {}
        with patch.dict(os.environ, env, clear=False), patch.dict(sys.modules, self.modules), \
             patch.object(D.importlib.metadata, "version", return_value="test-transformers"):
            return D.load_sam3_litetext(min_score=0.3)

    def test_local_checkpoint_loads_on_cpu_and_records_exact_identity(self):
        result = self.load()
        self.auto_model.from_pretrained.assert_called_once_with(str(self.model_dir))
        self.auto_processor.from_pretrained.assert_called_once_with(str(self.model_dir))
        self.assertEqual(result.model_identity["version"], D.SAM3_LITETEXT_VERSION)
        self.assertEqual(result.model_identity["repo_id"], D.SAM3_LITETEXT_REPO)
        self.assertEqual(len(result.model_identity["checkpoint_sha256"]), 64)
        self.assertEqual(result.model_identity["device"], "cpu")
        self.download.assert_not_called()

    def test_loaded_model_is_reused_within_a_worker(self):
        first, second = self.load(), self.load()
        self.auto_model.from_pretrained.assert_called_once_with(str(self.model_dir))
        self.auto_processor.from_pretrained.assert_called_once_with(str(self.model_dir))
        self.assertIs(first.processor.model, second.processor.model)

    def test_missing_local_path_uses_configured_huggingface_cache(self):
        with patch.dict(os.environ, {"HF_HOME": str(self.root / "hf")}, clear=False):
            self.load(local=False)
        self.download.assert_called_once_with(D.SAM3_LITETEXT_REPO, cache_dir=str(self.root / "hf"))

    def test_unsupported_model_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "sam3-litetext-s0"):
            D.load_sam3_litetext(version="sam3.1")

    def test_autocast_and_inference_are_scoped_even_when_inference_fails(self):
        active = []

        @contextmanager
        def context(name):
            active.append(name)
            try:
                yield
            finally:
                self.assertEqual(active.pop(), name)

        torch = types.SimpleNamespace(inference_mode=lambda: context("inference"),
            autocast=lambda device, dtype: context("autocast"), bfloat16="bfloat16", float16="float16",
            cuda=types.SimpleNamespace(is_bf16_supported=lambda: True))
        processor = Mock()
        processor.set_image.side_effect = lambda image: "state"
        processor.set_text_prompt.side_effect = RuntimeError("inference failure")
        wrapped = D._InferenceProcessor(processor, torch, "cuda", {})
        self.assertEqual(wrapped.set_image("image"), "state")
        self.assertEqual(active, [])
        with self.assertRaisesRegex(RuntimeError, "inference failure"):
            wrapped.set_text_prompt(prompt="object", state="state")
        self.assertEqual(active, [])

    def test_segmentation_skill_uses_registered_backend_and_records_identity(self):
        frames = self.root / "frames"
        frames.mkdir()
        Image.fromarray(np.zeros((20, 30, 3), np.uint8)).save(frames / "frame00000.png")
        masks = np.zeros((1, 1, 20, 30), bool)
        masks[0, 0, 5:15, 5:20] = True
        class Processor:
            model_identity = {"version": "fixture", "checkpoint_sha256": "a" * 64}

            def segment(self, image, prompt):
                return {"masks": masks, "scores": np.array([.9])}

        processor = Processor()
        with patch("tool_library.common.segmentation_backend.load_backend", return_value=processor) as load:
            result = S.segment_with_text_prompts(frames, ["object"], self.root / "masks", instances=True)
        load.assert_called_once_with(D.SAM3_LITETEXT_VERSION, min_score=0.3)
        self.assertEqual(result["model_identity"], processor.model_identity)
        self.assertEqual(result["n_instances"]["object"], [1])
        self.assertEqual(result["instances"]["object"][0][0]["area_px"], 150)


if __name__ == "__main__":
    unittest.main()
