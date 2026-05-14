import unittest

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from align_transcript_frames import align_segments_to_keyframes, find_nearest_segment


class AlignmentTest(unittest.TestCase):
    def test_frame_inside_segment_aligns_with_zero_distance(self):
        segments = [
            {"id": "seg_00001", "start": 0.0, "end": 10.0, "text": "intro"},
            {"id": "seg_00002", "start": 11.0, "end": 20.0, "text": "main"},
        ]

        segment, distance = find_nearest_segment(12.0, segments)

        self.assertEqual(segment["id"], "seg_00002")
        self.assertEqual(distance, 0.0)

    def test_alignment_respects_max_distance(self):
        segments = [{"id": "seg_00001", "start": 0.0, "end": 10.0, "text": "intro"}]
        keyframes = [
            {"id": "keyframe_00001", "timestamp": 9.0, "relative_path": "frames/a.jpg"},
            {"id": "keyframe_00002", "timestamp": 50.0, "relative_path": "frames/b.jpg"},
        ]
        config = {"alignment": {"max_alignment_distance_seconds": 5.0, "attach_frames_per_segment": 2}}

        alignment = align_segments_to_keyframes(segments, keyframes, config)

        self.assertEqual(alignment["frame_segments"]["keyframe_00001"], "seg_00001")
        self.assertIsNone(alignment["frame_segments"]["keyframe_00002"])
        self.assertEqual(len(alignment["segments"][0]["frames"]), 1)


if __name__ == "__main__":
    unittest.main()
