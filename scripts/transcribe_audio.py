from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from utils import StageError, read_json, seconds_to_timestamp, write_json


def transcribe_audio(
    audio_path: Path,
    output_json: Path,
    config: dict[str, Any],
    logger: logging.Logger,
    resume: bool = True,
) -> list[dict[str, Any]]:
    if resume and output_json.exists():
        cached = read_json(output_json, [])
        if cached:
            logger.info("Reusing transcript segments: %s", output_json)
            return cached

    asr_config = config.get("asr", {})
    model_size = str(asr_config.get("model_size", "medium"))
    language = asr_config.get("language") or None

    try:
        segments = _transcribe_with_faster_whisper(audio_path, asr_config, logger)
    except ImportError:
        logger.warning("faster-whisper is not installed; trying openai-whisper fallback.")
        segments = _transcribe_with_openai_whisper(audio_path, model_size, language, logger)

    if not segments:
        # Patched 2026-06-21: write empty list and keep pipeline moving (so downstream
        # scene/keyframe/ocr/report stages can still run for silent test videos).
        logger.warning("ASR returned empty transcript; writing empty list to keep pipeline moving.")
        write_json(output_json, [])
        return segments
    write_json(output_json, segments)
    return segments


def _transcribe_with_faster_whisper(
    audio_path: Path,
    asr_config: dict[str, Any],
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    from faster_whisper import WhisperModel

    model_size = str(asr_config.get("model_size", "medium"))
    device = str(asr_config.get("device", "auto"))
    compute_type = str(asr_config.get("compute_type", "auto"))
    language = asr_config.get("language") or None
    vad_filter = bool(asr_config.get("vad_filter", True))
    beam_size = int(asr_config.get("beam_size", 5))

    logger.info("Loading faster-whisper model: %s", model_size)
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    raw_segments, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=vad_filter,
        beam_size=beam_size,
    )
    logger.info("ASR language=%s probability=%.3f", info.language, info.language_probability)
    segments: list[dict[str, Any]] = []
    for index, segment in enumerate(raw_segments, start=1):
        text = (segment.text or "").strip()
        if not text:
            continue
        segments.append(
            {
                "id": f"seg_{index:05d}",
                "index": index,
                "start": float(segment.start),
                "end": float(segment.end),
                "start_text": seconds_to_timestamp(segment.start),
                "end_text": seconds_to_timestamp(segment.end),
                "text": text,
            }
        )
    return segments


def _transcribe_with_openai_whisper(
    audio_path: Path,
    model_size: str,
    language: str | None,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    try:
        import whisper
    except ImportError as exc:
        raise StageError(
            "Neither faster-whisper nor openai-whisper is installed.",
            "Install dependencies with `pip install -r requirements.txt`.",
            "transcribe",
        ) from exc

    logger.info("Loading openai-whisper model: %s", model_size)
    model = whisper.load_model(model_size)
    result = model.transcribe(str(audio_path), language=language)
    segments: list[dict[str, Any]] = []
    for index, segment in enumerate(result.get("segments", []), start=1):
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        segments.append(
            {
                "id": f"seg_{index:05d}",
                "index": index,
                "start": start,
                "end": end,
                "start_text": seconds_to_timestamp(start),
                "end_text": seconds_to_timestamp(end),
                "text": text,
            }
        )
    return segments
