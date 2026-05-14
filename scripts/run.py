from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from align_transcript_frames import align_transcript_frames
from detect_scenes import detect_scenes
from download_video import prepare_video_input
from extract_audio import extract_audio, probe_video
from extract_keyframes import extract_candidate_keyframes
from filter_keyframes import filter_keyframes
from generate_report import generate_reports
from ocr_frames import ocr_frames
from transcribe_audio import transcribe_audio
from utils import (
    RunState,
    StageError,
    bool_from_cli,
    cleanup_workdir,
    deep_merge,
    load_yaml,
    now_iso,
    require_command,
    setup_logging,
    stage_runner,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract transcript, content keyframes and reports from Bilibili/local videos.")
    parser.add_argument("--input", required=True, help="Bilibili URL or local video path.")
    parser.add_argument("--output", default=None, help="Output directory.")
    parser.add_argument("--config", default=None, help="YAML config path.")
    parser.add_argument("--cookies", default=None, help="Optional cookies.txt path for Bilibili downloads.")
    parser.add_argument("--asr-model", default=None, help="ASR model size, e.g. small, medium, large-v3.")
    parser.add_argument("--enable-ocr", default=None, help="true/false")
    parser.add_argument("--enable-vlm", default=None, help="true/false")
    parser.add_argument("--only-content-frames", default=None, help="true/false")
    parser.add_argument("--generate-html", default=None, help="true/false")
    parser.add_argument("--generate-markdown", default=None, help="true/false")
    parser.add_argument("--keep-intermediate", default=None, help="true/false")
    parser.add_argument("--resume", default=None, help="true/false")
    return parser.parse_args()


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config.example.yaml"


def build_config(args: argparse.Namespace) -> dict[str, Any]:
    base = load_yaml(default_config_path())
    user_config = load_yaml(Path(args.config).expanduser()) if args.config else {}
    config = deep_merge(base, user_config)

    overrides: dict[str, Any] = {
        "asr": {"model_size": args.asr_model},
        "ocr": {"enabled": bool_from_cli(args.enable_ocr)},
        "vlm": {"enabled": bool_from_cli(args.enable_vlm)},
        "keyframe": {"only_content_frames": bool_from_cli(args.only_content_frames)},
        "report": {
            "generate_html": bool_from_cli(args.generate_html),
            "generate_markdown": bool_from_cli(args.generate_markdown),
        },
        "runtime": {
            "keep_intermediate": bool_from_cli(args.keep_intermediate),
            "resume": bool_from_cli(args.resume),
        },
    }
    return deep_merge(config, overrides)


def main() -> int:
    args = parse_args()
    config = build_config(args)
    output_dir = Path(args.output or f"./outputs/run-{now_iso().replace(':', '-')}").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(output_dir, str(config.get("runtime", {}).get("log_level", "INFO")))
    state_path = output_dir / "run_log.json"
    state = RunState(input=args.input, output_dir=str(output_dir), started_at=now_iso())
    resume = bool(config.get("runtime", {}).get("resume", True))
    work_dir = output_dir / "work"

    try:
        with stage_runner(state, state_path, "preflight", logger):
            require_command("ffmpeg", logger)
            require_command("ffprobe", logger)
            write_json(output_dir / "resolved_config.json", config)

        with stage_runner(state, state_path, "prepare_input", logger):
            metadata = prepare_video_input(args.input, work_dir, args.cookies, config, logger, resume=resume)
            video_path = Path(metadata["video_path"])

        with stage_runner(state, state_path, "probe_video", logger):
            media_info = probe_video(video_path, config, logger)
            write_json(output_dir / "media_info.json", media_info)
            metadata.update(
                {
                    "duration": media_info.get("duration") or metadata.get("duration"),
                    "width": media_info.get("width"),
                    "height": media_info.get("height"),
                }
            )
            write_json(output_dir / "video_metadata.json", metadata)
            config["_runtime"] = {"media_info": media_info}

        with stage_runner(state, state_path, "extract_audio", logger):
            audio_path = extract_audio(video_path, work_dir / "audio.wav", media_info, logger, resume=resume)

        with stage_runner(state, state_path, "transcribe", logger):
            segments = transcribe_audio(audio_path, output_dir / "transcript_segments.json", config, logger, resume=resume)

        with stage_runner(state, state_path, "detect_scenes", logger):
            scenes = detect_scenes(video_path, output_dir / "scenes.json", config, logger, resume=resume)

        with stage_runner(state, state_path, "extract_candidate_keyframes", logger):
            candidates = extract_candidate_keyframes(
                video_path,
                scenes,
                output_dir / "candidate_keyframes.json",
                output_dir,
                config,
                logger,
                resume=resume,
            )

        with stage_runner(state, state_path, "ocr_frames", logger):
            ocr_results = ocr_frames(candidates, output_dir / "ocr_results.json", config, logger, resume=resume)

        with stage_runner(state, state_path, "filter_keyframes", logger):
            keyframes = filter_keyframes(
                candidates,
                ocr_results,
                output_dir / "keyframes.json",
                output_dir / "frames",
                output_dir,
                config,
                logger,
                resume=resume,
            )

        with stage_runner(state, state_path, "align", logger):
            alignment = align_transcript_frames(
                segments,
                keyframes,
                output_dir / "alignment.json",
                config,
                logger,
                resume=resume,
            )

        with stage_runner(state, state_path, "generate_report", logger):
            generate_reports(output_dir, metadata, media_info, alignment, keyframes, config, logger)

        with stage_runner(state, state_path, "cleanup", logger):
            cleanup_workdir(work_dir, bool(config.get("runtime", {}).get("keep_intermediate", True)), logger)

        state.status = "success"
        state.ended_at = now_iso()
        write_json(state_path, state)
        logger.info("Done. Output directory: %s", output_dir)
        return 0
    except StageError as exc:
        state.status = "failed"
        state.ended_at = now_iso()
        write_json(state_path, state)
        logger.error("Failed: %s", exc.message)
        if exc.suggestion:
            logger.error("Fix suggestion: %s", exc.suggestion)
        return 2
    except Exception as exc:
        state.status = "failed"
        state.ended_at = now_iso()
        write_json(state_path, state)
        logger.exception("Unexpected failure: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
