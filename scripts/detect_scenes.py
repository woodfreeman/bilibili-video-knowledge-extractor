from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from utils import StageError, read_json, seconds_to_timestamp, write_json


def detect_scenes(
    video_path: Path,
    output_json: Path,
    config: dict[str, Any],
    logger: logging.Logger,
    resume: bool = True,
) -> list[dict[str, Any]]:
    if resume and output_json.exists():
        cached = read_json(output_json, [])
        if cached:
            logger.info("Reusing detected scenes: %s", output_json)
            return cached

    try:
        scenes = _detect_with_pyscenedetect(video_path, config, logger)
    except ImportError:
        logger.warning("PySceneDetect is not installed; falling back to OpenCV histogram detection.")
        scenes = _detect_with_opencv(video_path, config, logger)

    if not scenes:
        logger.warning("No scene boundary detected; using one full-video scene.")
        media_info = config.get("_runtime", {}).get("media_info", {})
        duration = float(media_info.get("duration") or 0)
        scenes = [{"index": 1, "start": 0.0, "end": duration, "duration": duration}]

    scenes = _split_long_scenes(scenes, config)
    for index, scene in enumerate(scenes, start=1):
        scene["index"] = index
        scene["start_text"] = seconds_to_timestamp(scene["start"])
        scene["end_text"] = seconds_to_timestamp(scene["end"])
    write_json(output_json, scenes)
    logger.info("Detected %s scenes after long-scene splitting.", len(scenes))
    return scenes


def _detect_with_pyscenedetect(
    video_path: Path,
    config: dict[str, Any],
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    from scenedetect import SceneManager, VideoManager
    from scenedetect.detectors import ContentDetector

    scene_config = config.get("scene", {})
    threshold = float(scene_config.get("threshold", 27.0))
    min_scene_len_seconds = float(scene_config.get("min_scene_len_seconds", 2.0))

    video_manager = VideoManager([str(video_path)])
    scene_manager = SceneManager()
    video_manager.start()
    fps = video_manager.get_framerate() or 25.0
    min_scene_len = max(1, int(min_scene_len_seconds * fps))
    scene_manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_len))
    scene_manager.detect_scenes(frame_source=video_manager)
    raw_scenes = scene_manager.get_scene_list()
    video_manager.release()

    scenes = []
    for index, (start_time, end_time) in enumerate(raw_scenes, start=1):
        start = start_time.get_seconds()
        end = end_time.get_seconds()
        if end <= start:
            continue
        scenes.append({"index": index, "start": start, "end": end, "duration": end - start})
    logger.info("PySceneDetect found %s scenes.", len(scenes))
    return scenes


def _detect_with_opencv(
    video_path: Path,
    config: dict[str, Any],
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise StageError(
            "Neither PySceneDetect nor OpenCV is available for scene detection.",
            "Install dependencies with `pip install -r requirements.txt`.",
            "detect_scenes",
        ) from exc

    scene_config = config.get("scene", {})
    sample_fps = float(scene_config.get("fallback_sample_fps", 1.0))
    hist_threshold = float(scene_config.get("fallback_hist_threshold", 0.45))
    min_scene_len_seconds = float(scene_config.get("min_scene_len_seconds", 2.0))

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise StageError("OpenCV could not open the video.", "Check the video file format.", "detect_scenes")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = frame_count / fps if fps else 0
    step = max(1, int(fps / max(sample_fps, 0.1)))

    scenes = []
    current_start = 0.0
    last_hist = None
    frame_index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % step != 0:
            frame_index += 1
            continue
        timestamp = frame_index / fps
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        if last_hist is not None:
            diff = 1.0 - float(cv2.compareHist(last_hist, hist, cv2.HISTCMP_CORREL))
            if diff >= hist_threshold and timestamp - current_start >= min_scene_len_seconds:
                scenes.append(
                    {
                        "index": len(scenes) + 1,
                        "start": current_start,
                        "end": timestamp,
                        "duration": timestamp - current_start,
                        "scene_score": diff,
                    }
                )
                current_start = timestamp
        last_hist = hist
        frame_index += 1
    cap.release()

    if duration > current_start:
        scenes.append(
            {
                "index": len(scenes) + 1,
                "start": current_start,
                "end": duration,
                "duration": duration - current_start,
            }
        )
    logger.info("OpenCV fallback found %s scenes.", len(scenes))
    return scenes


def _split_long_scenes(scenes: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    interval = float(config.get("scene", {}).get("long_scene_interval_seconds", 45.0))
    if interval <= 0:
        return scenes
    result: list[dict[str, Any]] = []
    for scene in scenes:
        start = float(scene["start"])
        end = float(scene["end"])
        duration = end - start
        if duration <= interval * 1.5:
            result.append(scene)
            continue
        cursor = start
        while cursor < end:
            next_end = min(end, cursor + interval)
            result.append({"start": cursor, "end": next_end, "duration": next_end - cursor, "parent_scene": scene.get("index")})
            cursor = next_end
    return result
