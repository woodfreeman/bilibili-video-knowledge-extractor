from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from utils import read_json, relpath, require_command, run_command, seconds_to_timestamp, timestamp_for_filename, write_json


def extract_candidate_keyframes(
    video_path: Path,
    scenes: list[dict[str, Any]],
    output_json: Path,
    output_dir: Path,
    config: dict[str, Any],
    logger: logging.Logger,
    resume: bool = True,
) -> list[dict[str, Any]]:
    if resume and output_json.exists():
        cached = read_json(output_json, [])
        if cached:
            existing = [item for item in cached if Path(item["path"]).exists()]
            if existing:
                logger.info("Reusing candidate keyframes: %s", output_json)
                return cached

    require_command("ffmpeg", logger)
    candidate_dir = output_dir / "work" / "candidate_frames"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    max_width = int(config.get("video", {}).get("frame_max_width", 1280))
    candidates: list[dict[str, Any]] = []

    for scene in scenes:
        timestamps = _candidate_timestamps_for_scene(scene, config)
        for timestamp in timestamps:
            file_name = f"candidate_{len(candidates) + 1:05d}_{timestamp_for_filename(timestamp)}.jpg"
            frame_path = candidate_dir / file_name
            if not (resume and frame_path.exists()):
                command = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale='min({max_width},iw)':-2",
                    "-q:v",
                    "2",
                    str(frame_path),
                ]
                try:
                    run_command(command, logger, "extract_keyframes")
                except Exception as exc:
                    logger.warning("Failed to extract frame at %.3fs: %s", timestamp, exc)
                    continue
            candidates.append(
                {
                    "id": f"candidate_{len(candidates) + 1:05d}",
                    "source_scene_index": scene.get("index"),
                    "timestamp": timestamp,
                    "timestamp_text": seconds_to_timestamp(timestamp),
                    "path": str(frame_path),
                    "relative_path": relpath(frame_path, output_dir),
                    "selected": False,
                }
            )

    write_json(output_json, candidates)
    logger.info("Extracted %s candidate keyframes.", len(candidates))
    return candidates


def _candidate_timestamps_for_scene(scene: dict[str, Any], config: dict[str, Any]) -> list[float]:
    start = float(scene.get("start", 0))
    end = float(scene.get("end", start))
    if end <= start:
        return [start]
    duration = end - start
    interval = float(config.get("scene", {}).get("long_scene_interval_seconds", 45.0))
    timestamps = [start + duration / 2]
    if interval > 0 and duration > interval * 1.5:
        cursor = start + interval
        while cursor < end - 1:
            timestamps.append(cursor)
            cursor += interval
    return sorted({round(max(start, min(end - 0.05, ts)), 3) for ts in timestamps})
