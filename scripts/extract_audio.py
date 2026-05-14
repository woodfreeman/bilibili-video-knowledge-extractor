from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from utils import StageError, require_command, run_command, write_json


def probe_video(video_path: Path, config: dict[str, Any], logger: logging.Logger) -> dict[str, Any]:
    require_command("ffprobe", logger)
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    result = run_command(command, logger, "probe_video")
    info = json.loads(result.stdout)
    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float(info.get("format", {}).get("duration") or 0)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)

    video_config = config.get("video", {})
    max_duration = float(video_config.get("max_duration_seconds", 14400))
    allow_long = bool(video_config.get("allow_long_videos", False))
    if duration and duration > max_duration and not allow_long:
        raise StageError(
            f"Video is too long: {duration:.1f}s > {max_duration:.1f}s.",
            "Increase video.max_duration_seconds, set allow_long_videos true, or process a shorter clip.",
            "probe_video",
        )

    result_data = {
        "duration": duration,
        "width": width,
        "height": height,
        "has_audio": bool(audio_streams),
        "video_codec": video_stream.get("codec_name"),
        "audio_stream_count": len(audio_streams),
        "format": info.get("format", {}),
    }
    logger.info(
        "Video probed: duration=%.1fs, resolution=%sx%s, audio_streams=%s",
        duration,
        width,
        height,
        len(audio_streams),
    )
    return result_data


def extract_audio(
    video_path: Path,
    audio_path: Path,
    media_info: dict[str, Any],
    logger: logging.Logger,
    resume: bool = True,
) -> Path:
    require_command("ffmpeg", logger)
    if resume and audio_path.exists() and audio_path.stat().st_size > 0:
        logger.info("Reusing extracted audio: %s", audio_path)
        return audio_path
    if not media_info.get("has_audio"):
        raise StageError(
            "Video has no audio stream.",
            "Provide a video with audio, or extend the Skill to generate a visual-only report.",
            "extract_audio",
        )
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio_path),
    ]
    run_command(command, logger, "extract_audio")
    write_json(audio_path.with_suffix(".json"), {"audio_path": str(audio_path)})
    return audio_path
