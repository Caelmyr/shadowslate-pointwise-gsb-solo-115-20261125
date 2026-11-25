"""Tests for two-card horizontal registration and common-dark logic."""

import unittest
from io import BytesIO

import numpy as np
from PIL import Image

from image_lab.validation import Invalid
from shadowslate import pairing
from shadowslate.reading import LUMA


def config(**over):
    base = {"left": 40, "right": 200, "top": 30, "bottom": 150,
            "row": 90, "cutoff": 120, "mm_per_px": 1.0}
    base.update(over)
    return base


def textured(rng, w, h):
    yy, xx = np.indices((h, w))
    field = (200 - 18 * xx / w - 12 * yy / h
             + 6 * np.sin(xx * 1.3) + 4 * np.cos(yy * 0.9 + xx * 0.4)
             + rng.normal(0, 2.2, (h, w)))
    return np.repeat(field[..., None], 3, axis=2).clip(0, 255).astype("uint8")


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(7)
        self.w, self.h = 260, 190
        self.img_a = textured(self.rng, self.w, self.h)
        self.shift = 7
        img_b = np.zeros_like(self.img_a)
        img_b[:, self.shift:] = self.img_a[:, :self.w - self.shift]
        self.img_b = img_b
        self.cfg = config()

    def test_shift_recovered_from_texture_bands(self):
        result = pairing.analyze(self.img_a, self.cfg, self.img_b, config())
        self.assertEqual(result["registration"]["shift"], -self.shift)
        self.assertEqual(result["registration"]["best_shift"], -self.shift)
        self.assertTrue(all(b["supported"] for b in result["registration"]["bands"].values()))
        scores = result["registration"]["candidates"]
        self.assertEqual(scores[0]["shift"], -self.shift)
        self.assertEqual(len(scores), 2 * pairing.MAX_SHIFT + 1)

    def test_bands_are_top_and_bottom_eight_rows(self):
        la, ra, ta, ba = self.cfg["left"], self.cfg["right"], self.cfg["top"], self.cfg["bottom"]
        lv = pairing.luma(self.img_a.astype(np.float64))
        top = pairing._band(lv, ta, ba, la, ra, "top")
        bottom = pairing._band(lv, ta, ba, la, ra, "bottom")
        self.assertEqual(top.shape, (8, ra - la + 1))
        self.assertEqual(bottom.shape, (8, ra - la + 1))

    def test_shift_capped_to_integer_range(self):
        for item in pairing.analyze(self.img_a, self.cfg, self.img_b, config())["registration"]["candidates"]:
            self.assertIsInstance(item["shift"], int)
            self.assertLessEqual(abs(item["shift"]), pairing.MAX_SHIFT)

    def test_tie_prefers_smallest_movement(self):
        # A period-3 texture makes shifts ..., -3, 0, 3, ... score identically;
        # the rule must then pick the least motion among the equally likely ones.
        yy, xx = np.indices((self.h, self.w))
        wave = (200 + 12 * np.sin(xx * 2 * np.pi / 3) + 5 * np.cos(yy * 0.7)).astype(np.int16)
        periodic = np.repeat(wave[..., None], 3, axis=2).clip(0, 255).astype("uint8")
        result = pairing.find_shift(pairing.luma(periodic.astype(np.float64)),
                                    pairing.luma(periodic.astype(np.float64)),
                                    (40, 200, 30, 150), (40, 200, 30, 150))
        self.assertEqual(result["shift"], 0)
        scores = {item["shift"]: item["score"] for item in result["candidates"]}
        self.assertAlmostEqual(scores[3], scores[0])
        self.assertAlmostEqual(scores[-3], scores[0])
        self.assertGreater(scores[1], scores[0] * 5)  # non-matching shifts visibly worse

    def test_tie_with_best_off_center_prefers_smaller_abs_shift(self):
        # Period-3 texture displaced by one period: every multiple of 3 matches
        # equally well (the largest even sits at the range edge), so the tie
        # rule must keep the least motion instead of following the first minimum.
        yy, xx = np.indices((self.h, self.w))
        wave = (200 + 12 * np.sin(xx * 2 * np.pi / 3) + 5 * np.cos(yy * 0.7)).astype(np.int16)
        a = np.repeat(wave[..., None], 3, axis=2).clip(0, 255).astype("uint8")
        b = np.zeros_like(a)
        b[:, 3:] = a[:, :-3]
        result = pairing.find_shift(pairing.luma(a.astype(np.float64)),
                                    pairing.luma(b.astype(np.float64)),
                                    (40, 200, 30, 150), (40, 200, 30, 150))
        self.assertEqual(result["shift"], 0)
        scores = {item["shift"]: item["score"] for item in result["candidates"]}
        self.assertAlmostEqual(scores[-12], scores[0])
        self.assertAlmostEqual(scores[-3], scores[0])
        self.assertGreater(scores[1], scores[0] + 1.0)

    def test_textureless_band_rejected(self):
        blank = np.full((self.h, self.w, 3), 200, dtype="uint8")
        with self.assertRaises(Invalid):
            pairing.analyze(blank, self.cfg, blank, config())

    def test_frame_row_mismatch_rejected(self):
        with self.assertRaises(Invalid):
            pairing.analyze(self.img_a, self.cfg, self.img_b, config(top=31, bottom=150))

    def test_height_mismatch_rejected(self):
        with self.assertRaises(Invalid):
            pairing.prepare(self.img_a, self.cfg, self.img_b[:-1], config())

    def test_overlap_excludes_outside_pixels_without_padding(self):
        result = pairing.analyze(self.img_a, self.cfg, self.img_b, config())
        shift = result["registration"]["shift"]
        ov = result["common"]["overlap"]
        # x_b = x_a + shift must remain inside B's frame on both sides.
        self.assertGreaterEqual(ov["x0"] + shift, self.cfg["left"])
        self.assertLessEqual(ov["x1"] + shift, self.cfg["right"])
        # The interior band excludes the reference rows themselves.
        self.assertEqual(ov["y0"], self.cfg["top"] + pairing.BAND_ROWS)
        self.assertEqual(ov["y1"], self.cfg["bottom"] - pairing.BAND_ROWS)
        # B's leading columns have no real pixels: A columns mapping there must be excluded.
        self.assertEqual(ov["x0"], self.cfg["left"] + self.shift)

    def test_common_dark_uses_each_own_cutoff(self):
        cfg_a, cfg_b = config(cutoff=100), config(cutoff=140)
        result = pairing.analyze(self.img_a, cfg_a, self.img_b, cfg_b)
        shift = result["registration"]["shift"]
        ov = result["common"]["overlap"]
        lv_a = pairing.luma(self.img_a.astype(np.float64))
        lv_b = pairing.luma(self.img_b.astype(np.float64))
        region_a = lv_a[ov["y0"]:ov["y1"] + 1, ov["x0"]:ov["x1"] + 1]
        region_b = lv_b[ov["y0"]:ov["y1"] + 1, ov["x0"] + shift:ov["x1"] + shift + 1]
        expected = np.count_nonzero((region_a <= 100) & (region_b <= 140))
        self.assertEqual(result["common"]["dark_px"], int(expected))

    def test_paired_point_maps_coordinates_and_darkness(self):
        shift = -self.shift
        point = pairing.paired_point(self.img_a, self.cfg, self.img_b, config(), shift, 100, 80)
        self.assertEqual(point["peer"]["x"], 100 + shift)
        self.assertEqual(point["peer"]["y"], 80)
        self.assertFalse(point["pending"])
        np.testing.assert_array_equal(self.img_b[80, 100 + shift],
                                      np.array(point["peer"]["rgb"], dtype="uint8"))
        self.assertEqual(point["common_dark"], point["here"]["dark"] and point["peer"]["dark"])

    def test_paired_point_outside_other_frame_pends(self):
        shift = -self.shift
        # x=44 is inside A's frame (left=40), but maps to B x=37, outside B's frame.
        point = pairing.paired_point(self.img_a, self.cfg, self.img_b, config(), shift, 44, 80)
        self.assertTrue(point["pending"])
        self.assertIsNotNone(point["reason"])
        self.assertEqual(point["peer"]["x"], 44 + shift)
        self.assertIsNone(point["peer"]["dark"])

    def test_paired_point_beyond_photo_has_no_peer(self):
        point = pairing.paired_point(self.img_a, self.cfg, self.img_b, config(), -12, 5, 80)
        self.assertIsNone(point["peer"])
        self.assertTrue(point["pending"])

    def test_proof_is_valid_png_with_both_photos(self):
        result = pairing.analyze(self.img_a, self.cfg, self.img_b, config())
        data = pairing.proof(self.img_a, self.img_b,
                             {"name": "A", "photo_name": "a.png", "settings": self.cfg},
                             {"name": "B", "photo_name": "b.png", "settings": config()}, result)
        with Image.open(BytesIO(data)) as shot:
            self.assertEqual(shot.format, "PNG")
            self.assertEqual(shot.size[0], self.w)
            self.assertGreater(shot.size[1], 2 * self.h)

    def test_practice_pair_self_consistent(self):
        (size_a, png_a, cfg_a), (size_b, png_b, cfg_b) = pairing.practice_pair()
        self.assertEqual(size_a, size_b)
        img_a = np.asarray(Image.open(BytesIO(png_a)).convert("RGB"))
        img_b = np.asarray(Image.open(BytesIO(png_b)).convert("RGB"))
        result = pairing.analyze(img_a, cfg_a, img_b, cfg_b)
        self.assertEqual(result["registration"]["shift"], -5)
        self.assertGreater(result["common"]["dark_px"], 0)


if __name__ == "__main__":
    unittest.main()
