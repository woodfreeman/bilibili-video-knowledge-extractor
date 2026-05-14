from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


class StageError(RuntimeError):
    def __init__(self, message: str, suggestion: str = "", stage: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.suggestion = suggestion
        self.stage = stage


@dataclass
class StageRecord:
    name: str
    status: str
    started_at: str
    ended_at: str | None = None
    duration_seconds: float | None = None
    message: str = ""
    suggestion: str = ""


@dataclass
class RunState:
    input: str
    output_dir: str
    started_at: str
    stages: list[StageRecord] = field(default_factory=list)
    ended_at: str | None = None
    status: str = "running"

    def add(self, record: StageRecord) -> None:
        self.stages.append(record)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_yaml(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:
        raise StageError(
            "PyYAML is not installed.",
            "Install dependencies with `pip install -r requirements.txt`.",
            "config",
        ) from exc
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise StageError("Config file must contain a YAML mapping.", "Check config.yaml syntax.", "config")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        elif value is not None:
            result[key] = value
    return result


def setup_logging(output_dir: Path, level: str = "INFO") -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bilibili-video-knowledge-extractor")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(output_dir / "process.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(to_jsonable(data), f, ensure_ascii=False, indent=2)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    return value


def run_command(
    command: list[str],
    logger: logging.Logger,
    stage: str,
    cwd: Path | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    logger.debug("Running command: %s", " ".join(command))
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise StageError(
            f"Command not found: {command[0]}",
            f"Install `{command[0]}` and make sure it is available in PATH.",
            stage,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise StageError(
            f"Command timed out in stage `{stage}`.",
            "Increase the timeout in config or process a shorter video.",
            stage,
        ) from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"exit code {result.returncode}"
        raise StageError(
            f"Command failed in stage `{stage}`: {detail}",
            "Inspect process.log, verify input paths and dependencies, then rerun with --resume true.",
            stage,
        )
    return result


def require_command(name: str, logger: logging.Logger) -> None:
    if shutil.which(name) is None:
        raise StageError(
            f"`{name}` is not installed or not in PATH.",
            f"Install `{name}` and rerun. On macOS, use `brew install ffmpeg` for ffmpeg/ffprobe.",
            "preflight",
        )
    logger.debug("Found command: %s", name)


def bool_from_cli(value: str | bool | None) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def seconds_to_timestamp(seconds: float | int | None, sep: str = ":") -> str:
    if seconds is None or math.isnan(float(seconds)):
        seconds = 0
    total = max(0, int(round(float(seconds))))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}{sep}{m:02d}{sep}{s:02d}"


def timestamp_for_filename(seconds: float | int | None) -> str:
    return seconds_to_timestamp(seconds, sep="-")


def relpath(path: str | Path, root: str | Path) -> str:
    return os.path.relpath(str(path), str(root)).replace(os.sep, "/")


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def safe_text(value: str, max_len: int = 280) -> str:
    text = " ".join((value or "").split())
    return text[: max_len - 1] + "…" if len(text) > max_len else text


def stage_runner(state: RunState, state_path: Path, name: str, logger: logging.Logger):
    class _StageContext:
        def __enter__(self):
            self.start = time.monotonic()
            self.record = StageRecord(name=name, status="running", started_at=now_iso())
            state.add(self.record)
            write_json(state_path, state)
            logger.info("Stage started: %s", name)
            return self

        def __exit__(self, exc_type, exc, tb):
            self.record.ended_at = now_iso()
            self.record.duration_seconds = round(time.monotonic() - self.start, 3)
            if exc is None:
                self.record.status = "success"
                logger.info("Stage finished: %s", name)
            else:
                self.record.status = "failed"
                if isinstance(exc, StageError):
                    self.record.message = exc.message
                    self.record.suggestion = exc.suggestion
                    logger.error("Stage failed: %s; %s", name, exc.message)
                    if exc.suggestion:
                        logger.error("Suggestion: %s", exc.suggestion)
                else:
                    self.record.message = str(exc)
                    logger.exception("Stage failed: %s", name)
            write_json(state_path, state)
            return False

    return _StageContext()


def cleanup_workdir(work_dir: Path, keep_intermediate: bool, logger: logging.Logger) -> None:
    if keep_intermediate:
        return
    candidate_dir = work_dir / "candidate_frames"
    if candidate_dir.exists():
        shutil.rmtree(candidate_dir)
        logger.info("Removed candidate frame directory: %s", candidate_dir)
