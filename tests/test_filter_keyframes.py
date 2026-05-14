import unittest

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from filter_keyframes import dedupe_candidates_by_hash, hash_distance, score_candidate


class FilterKeyframesTest(unittest.TestCase):
    def test_score_prefers_text_dense_content(self):
        config = {
            "keyframe": {
                "min_ocr_chars": 8,
                "min_text_area_ratio": 0.002,
                "min_edge_density": 0.025,
                "blur_threshold": 45.0,
                "prefer_ocr_weight": 0.55,
                "prefer_visual_weight": 0.35,
                "prefer_vlm_weight": 0.10,
            }
        }
        candidate = {"edge_density": 0.08, "blur_score": 120.0, "color_complexity": 0.45}
        ocr = {"char_count": 60, "text_area_ratio": 0.02, "avg_confidence": 0.82, "line_count": 6}

        score = score_candidate(candidate, ocr, config)

        self.assertGreater(score["content_score"], 60)
        self.assertIn("OCR text chars", score["reason"])

    def test_blurry_low_text_frame_gets_low_score(self):
        config = {
            "keyframe": {
                "min_ocr_chars": 8,
                "min_text_area_ratio": 0.002,
                "min_edge_density": 0.025,
                "blur_threshold": 45.0,
                "prefer_ocr_weight": 0.55,
                "prefer_visual_weight": 0.35,
                "prefer_vlm_weight": 0.10,
            }
        }
        candidate = {"edge_density": 0.005, "blur_score": 10.0, "color_complexity": 0.1}
        ocr = {"char_count": 0, "text_area_ratio": 0.0, "avg_confidence": 0.0, "line_count": 0}

        score = score_candidate(candidate, ocr, config)

        self.assertLess(score["content_score"], 25)

    def test_hash_dedupe_keeps_highest_scored_near_duplicate(self):
        candidates = [
            {"id": "a", "timestamp": 1, "hash": "0000000000000000", "content_score": 50},
            {"id": "b", "timestamp": 2, "hash": "0000000000000001", "content_score": 90},
            {"id": "c", "timestamp": 3, "hash": "ffffffffffffffff", "content_score": 70},
        ]

        result = dedupe_candidates_by_hash(candidates, max_distance=2)

        self.assertEqual([item["id"] for item in result], ["b", "c"])
        self.assertEqual(hash_distance("0", "1"), 1)


if __name__ == "__main__":
    unittest.main()
