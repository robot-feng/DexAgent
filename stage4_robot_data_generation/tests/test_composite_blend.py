"""The transparent shadow catcher must not overpower the recorded plate."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stage4.composite import blend_rgba  # noqa: E402


class CompositeBlendTests(unittest.TestCase):
    def test_only_black_shadow_pixels_are_softened(self):
        plate = np.full((1, 4, 3), 200, dtype=np.uint8)
        # BGRA: opaque shadow, half-alpha shadow, dark robot surface, colored object.
        rgba = np.array([[[0, 0, 0, 255], [0, 0, 0, 128], [12, 12, 12, 255],
                          [30, 60, 90, 255]]], dtype=np.uint8)

        got = blend_rgba(plate, rgba, shadow_opacity=0.30)

        np.testing.assert_array_equal(got[0, 0], [140, 140, 140])
        np.testing.assert_array_equal(got[0, 1], [169, 169, 169])
        np.testing.assert_array_equal(got[0, 2], [12, 12, 12])
        np.testing.assert_array_equal(got[0, 3], [90, 60, 30])

    def test_shadow_opacity_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            blend_rgba(np.zeros((1, 1, 3), np.uint8),
                       np.zeros((1, 1, 4), np.uint8), shadow_opacity=1.1)


if __name__ == "__main__":
    unittest.main()
