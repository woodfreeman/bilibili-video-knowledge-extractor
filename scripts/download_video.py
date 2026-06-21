from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from utils import StageError, is_url, read_json, run_command, write_json


def is_bilibili_url(value: str) -> bool:
    return is_url(value) and ("bilibili.com/video/" in value or "b23.tv/" in value)


def prepare_video_input(
    input_value: str,
    work_dir: Path,
    cookies: str | None,
    config: dict[str, Any],
    logger: logging.Logger,
    resume: bool = True,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = work_dir / "video_metadata.json"
    if resume and metadata_path.exists():
        metadata = read_json(metadata_path, {})
        video_path = Path(metadata.get("video_path", ""))
        if video_path.exists():
            logger.info("Reusing downloaded/prepared video: %s", video_path)
            return metadata

    if is_bilibili_url(input_value):
        return download_bilibili_video(input_value, work_dir, cookies, config, logger)

    local_path = Path(input_value).expanduser()
    if not local_path.exists():
        raise StageError(
            f"Input video does not exist: {input_value}",
            "Provide a valid local path or an accessible Bilibili URL.",
            "prepare_input",
        )
    metadata = {
        "source": str(local_path),
        "source_type": "local_file",
        "title": local_path.stem,
        "video_path": str(local_path.resolve()),
        "webpage_url": "",
    }
    write_json(metadata_path, metadata)
    return metadata


def download_bilibili_video(
    url: str,
    work_dir: Path,
    cookies: str | None,
    config: dict[str, Any],
    logger: logging.Logger,
) -> dict[str, Any]:
    output_template = str(work_dir / "source.%(ext)s")
    info_json = work_dir / "source.info.json"
    download_config = config.get("download", {})

    # P0 patch 2026-06-21: expand b23.tv short URLs to full bilibili URLs first
    # (b23.tv 短链 yt-dlp 经常 302 后 gzip decode 失败)
    if "b23.tv/" in url:
        try:
            import subprocess
            expanded = subprocess.run(
                ["curl", "-sLI", "-m", "10", url],
                capture_output=True, text=True, timeout=15
            )
            location = ""
            for line in expanded.stdout.splitlines():
                if line.lower().startswith("location:"):
                    location = line.split(":", 1)[1].strip()
                    break
            if location and "bilibili.com" in location:
                logger.info("Expanded b23.tv short URL to: %s", location)
                url = location
        except Exception as e:
            logger.warning("Could not expand b23.tv URL (will try as-is): %s", e)

    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--write-info-json",
        "--no-write-comments",
        "--no-write-thumbnail",
        "--retries",
        str(download_config.get("retries", 3)),
        "--socket-timeout",
        str(download_config.get("socket_timeout_seconds", 30)),
        "-f",
        str(download_config.get("format", "bv*+ba/best[height<=1080]/best")),
        "--merge-output-format",
        str(download_config.get("merge_output_format", "mp4")),
        # P0 patch 2026-06-21: B 站 412 风控需要 UA + Referer
        # (read from config, fallback to Chrome 120 default)
        "--add-header",
        f"User-Agent:{download_config.get('user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')}",
        "--add-header",
        "Referer:https://www.bilibili.com/",
        # P0 patch 2026-06-21: B 站 2025+ WBI 签名需要 extractor-args
        # (read from config, fallback to bilibili:api_version=v2)
        "--extractor-args",
        str(download_config.get("extractor_args", "bilibili:api_version=v2")),
        "-o",
        output_template,
        url,
    ]
    if cookies:
        cookie_path = Path(cookies).expanduser()
        # P0 patch 2026-06-21: cookies 不存在 warn skip 而不 raise（用户可能想下不需登录的视频）
        if not cookie_path.exists():
            logger.warning(
                "Cookies file not found at %s — downloading WITHOUT login. "
                "If the video requires login, this will fail.",
                cookie_path,
            )
        else:
            command[3:3] = ["--cookies", str(cookie_path)]

    logger.info("Downloading Bilibili video with yt-dlp. This may take a while.")
    try:
        run_command(command, logger, "download")
    except StageError as exc:
        exc.suggestion = (
            "Confirm the video is accessible. If it requires login, provide a legal cookies.txt "
            "with --cookies. If 412, upgrade yt-dlp (`pip install -U yt-dlp>=2025.10.0`). "
            "This Skill does not bypass payment, DRM, or permission restrictions."
        )
        raise

    video_candidates = sorted(work_dir.glob("source.*"))
    video_candidates = [
        p for p in video_candidates if p.suffix.lower() not in {".json", ".part", ".ytdl"}
    ]
    if not video_candidates:
        raise StageError(
            "yt-dlp finished but no video file was found.",
            "Inspect process.log and try another yt-dlp format in config.yaml.",
            "download",
        )
    video_path = video_candidates[0].resolve()

    title = video_path.stem
    raw_info: dict[str, Any] = {}
    if info_json.exists():
        with info_json.open("r", encoding="utf-8") as f:
            raw_info = json.load(f)
        title = raw_info.get("title") or title

    metadata = {
        "source": url,
        "source_type": "bilibili_url",
        "title": title,
        "video_path": str(video_path),
        "webpage_url": raw_info.get("webpage_url") or url,
        "uploader": raw_info.get("uploader"),
        "duration": raw_info.get("duration"),
        "id": raw_info.get("id"),
    }
    write_json(work_dir / "video_metadata.json", metadata)
    return metadata
