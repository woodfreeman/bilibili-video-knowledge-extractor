from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from utils import read_json, write_json


def align_transcript_frames(
    segments: list[dict[str, Any]],
    keyframes: list[dict[str, Any]],
    output_json: Path,
    config: dict[str, Any],
    logger: logging.Logger,
    resume: bool = True,
) -> dict[str, Any]:
    if resume and output_json.exists():
        cached = read_json(output_json, {})
        if cached:
            logger.info("Reusing alignment: %s", output_json)
            return cached

    alignment = align_segments_to_keyframes(segments, keyframes, config)
    write_json(output_json, alignment)
    logger.info("Aligned %s keyframes to %s transcript segments.", len(keyframes), len(segments))
    return alignment


def align_segments_to_keyframes(
    segments: list[dict[str, Any]],
    keyframes: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    max_distance = float(config.get("alignment", {}).get("max_alignment_distance_seconds", 20.0))
    attach_limit = int(config.get("alignment", {}).get("attach_frames_per_segment", 2))
    segment_frames: dict[str, list[dict[str, Any]]] = {segment["id"]: [] for segment in segments}
    frame_segments: dict[str, str | None] = {}

    for frame in keyframes:
        segment, distance = find_nearest_segment(frame["timestamp"], segments)
        if segment is None or distance > max_distance:
            frame_segments[frame["id"]] = None
            continue
        frame_segments[frame["id"]] = segment["id"]
        segment_frames.setdefault(segment["id"], []).append({**frame, "alignment_distance": distance})

    for segment_id, frames in segment_frames.items():
        frames.sort(key=lambda item: (item.get("alignment_distance", 0), item.get("timestamp", 0)))
        segment_frames[segment_id] = frames[:attach_limit]

    enriched_segments = []
    for segment in segments:
        enriched = dict(segment)
        enriched["frames"] = segment_frames.get(segment["id"], [])
        enriched_segments.append(enriched)

    return {
        "segments": enriched_segments,
        "segment_frames": segment_frames,
        "frame_segments": frame_segments,
    }


def find_nearest_segment(timestamp: float, segments: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    if not segments:
        return None, float("inf")
    best = None
    best_distance = float("inf")
    for segment in segments:
        start = float(segment.get("start", 0))
        end = float(segment.get("end", start))
        if start <= timestamp <= end:
            return segment, 0.0
        distance = min(abs(timestamp - start), abs(timestamp - end))
        if distance < best_distance:
            best = segment
            best_distance = distance
    return best, best_distance
