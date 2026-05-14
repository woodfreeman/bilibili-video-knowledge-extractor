from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from utils import read_json, relpath, seconds_to_timestamp, timestamp_for_filename, write_json


def filter_keyframes(
    candidates: list[dict[str, Any]],
    ocr_results: list[dict[str, Any]],
    output_json: Path,
    frames_dir: Path,
    output_dir: Path,
    config: dict[str, Any],
    logger: logging.Logger,
    resume: bool = True,
) -> list[dict[str, Any]]:
    if resume and output_json.exists():
        cached = read_json(output_json, [])
        if cached and all(Path(item["path"]).exists() for item in cached):
            logger.info("Reusing filtered keyframes: %s", output_json)
            return cached

    frames_dir.mkdir(parents=True, exist_ok=True)
    ocr_by_id = {item["candidate_id"]: item for item in ocr_results}
    scored = []
    for candidate in candidates:
        ocr = ocr_by_id.get(candidate["id"], {})
        metrics = inspect_image_metrics(Path(candidate["path"]))
        enriched = dict(candidate)
        enriched.update(metrics)
        score = score_candidate(enriched, ocr, config)
        enriched.update(score)
        scored.append(enriched)

    only_content = bool(config.get("keyframe", {}).get("only_content_frames", True))
    threshold = float(config.get("keyframe", {}).get("content_score_threshold", 45.0))
    if only_content:
        scored = [item for item in scored if item.get("content_score", 0) >= threshold]
    else:
        logger.info("only_content_frames=false; keeping candidates after dedupe and max-frame limit.")

    deduped = dedupe_candidates_by_hash(scored, int(config.get("keyframe", {}).get("hash_distance_threshold", 6)))
    deduped.sort(key=lambda item: (item["timestamp"], -item.get("content_score", 0)))
    max_frames = int(config.get("keyframe", {}).get("max_frames_per_video", 160))
    selected = deduped[:max_frames]

    result = []
    for index, item in enumerate(selected, start=1):
        timestamp = float(item["timestamp"])
        file_name = f"keyframe_{index:05d}_{timestamp_for_filename(timestamp)}.jpg"
        target = frames_dir / file_name
        if Path(item["path"]).resolve() != target.resolve():
            shutil.copy2(item["path"], target)
        keyframe = {
            "id": f"keyframe_{index:05d}",
            "candidate_id": item["id"],
            "timestamp": timestamp,
            "timestamp_text": seconds_to_timestamp(timestamp),
            "path": str(target),
            "relative_path": relpath(target, output_dir),
            "content_score": round(float(item.get("content_score", 0)), 2),
            "ocr_score": round(float(item.get("ocr_score", 0)), 2),
            "visual_score": round(float(item.get("visual_score", 0)), 2),
            "vlm_score": round(float(item.get("vlm_score", 0)), 2),
            "hash": item.get("hash"),
            "reason": item.get("reason", ""),
            "ocr_text": ocr_by_id.get(item["id"], {}).get("text", ""),
        }
        result.append(keyframe)

    write_json(output_json, result)
    logger.info("Selected %s content-rich keyframes from %s candidates.", len(result), len(candidates))
    return result


def score_candidate(candidate: dict[str, Any], ocr: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    keyframe_config = config.get("keyframe", {})
    min_chars = max(1, int(keyframe_config.get("min_ocr_chars", 8)))
    min_text_area = max(0.0001, float(keyframe_config.get("min_text_area_ratio", 0.002)))
    min_edge = max(0.0001, float(keyframe_config.get("min_edge_density", 0.025)))
    blur_threshold = max(1.0, float(keyframe_config.get("blur_threshold", 45.0)))

    char_count = float(ocr.get("char_count") or 0)
    text_area_ratio = float(ocr.get("text_area_ratio") or 0)
    confidence = float(ocr.get("avg_confidence") or 0)
    line_count = float(ocr.get("line_count") or 0)
    edge_density = float(candidate.get("edge_density") or 0)
    color_complexity = float(candidate.get("color_complexity") or 0)
    blur_score = float(candidate.get("blur_score") or 0)

    text_amount_score = min(100.0, (char_count / min_chars) * 45.0)
    text_area_score = min(30.0, (text_area_ratio / min_text_area) * 20.0)
    confidence_score = min(15.0, confidence * 15.0)
    line_score = min(10.0, line_count * 2.0)
    ocr_score = min(100.0, text_amount_score + text_area_score + confidence_score + line_score)

    edge_score = min(50.0, (edge_density / min_edge) * 35.0)
    sharp_score = min(30.0, (blur_score / blur_threshold) * 22.0)
    color_score = min(20.0, color_complexity * 20.0)
    visual_score = min(100.0, edge_score + sharp_score + color_score)

    vlm_score = 0.0
    weights = keyframe_config
    content_score = (
        float(weights.get("prefer_ocr_weight", 0.55)) * ocr_score
        + float(weights.get("prefer_visual_weight", 0.35)) * visual_score
        + float(weights.get("prefer_vlm_weight", 0.10)) * vlm_score
    )

    reasons = []
    if char_count >= min_chars:
        reasons.append(f"OCR text chars={int(char_count)}")
    if text_area_ratio >= min_text_area:
        reasons.append(f"text area={text_area_ratio:.4f}")
    if edge_density >= min_edge:
        reasons.append(f"structured edges={edge_density:.4f}")
    if blur_score < blur_threshold:
        content_score *= 0.65
        reasons.append(f"possible blur={blur_score:.1f}")
    if not reasons:
        reasons.append("low text/structure density")

    return {
        "ocr_score": ocr_score,
        "visual_score": visual_score,
        "vlm_score": vlm_score,
        "content_score": content_score,
        "reason": "; ".join(reasons),
    }


def inspect_image_metrics(path: Path) -> dict[str, Any]:
    metrics = {
        "edge_density": 0.0,
        "blur_score": 0.0,
        "color_complexity": 0.0,
        "hash": None,
    }
    if not path.exists():
        return metrics
    try:
        from PIL import Image
        import numpy as np

        image = Image.open(path).convert("RGB")
        small = image.resize((160, max(1, int(160 * image.height / max(1, image.width)))))
        arr = np.asarray(small).astype("float32")
        gray = np.asarray(small.convert("L")).astype("float32")
        gx = np.abs(np.diff(gray, axis=1))
        gy = np.abs(np.diff(gray, axis=0))
        edge_density = float(((gx > 18).mean() + (gy > 18).mean()) / 2.0)
        color_complexity = float(np.std(arr.reshape(-1, 3), axis=0).mean() / 128.0)
        metrics["edge_density"] = max(0.0, min(1.0, edge_density))
        metrics["color_complexity"] = max(0.0, min(1.0, color_complexity))
        metrics["blur_score"] = _blur_score(gray)
        metrics["hash"] = compute_perceptual_hash(path)
    except Exception:
        return metrics
    return metrics


def _blur_score(gray_array: Any) -> float:
    try:
        import cv2

        return float(cv2.Laplacian(gray_array.astype("uint8"), cv2.CV_64F).var())
    except Exception:
        import numpy as np

        gx = np.diff(gray_array, axis=1)
        gy = np.diff(gray_array, axis=0)
        return float(np.var(gx) + np.var(gy))


def compute_perceptual_hash(path: Path) -> str | None:
    try:
        from PIL import Image
        import imagehash

        return str(imagehash.phash(Image.open(path)))
    except Exception:
        return average_hash(path)


def average_hash(path: Path, hash_size: int = 8) -> str | None:
    try:
        from PIL import Image

        image = Image.open(path).convert("L").resize((hash_size, hash_size))
        pixels = list(image.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if pixel > avg else "0" for pixel in pixels)
        return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"
    except Exception:
        return None


def hash_distance(left: str | None, right: str | None) -> int:
    if not left or not right:
        return 10**9
    try:
        return bin(int(left, 16) ^ int(right, 16)).count("1")
    except ValueError:
        return 10**9


def dedupe_candidates_by_hash(candidates: list[dict[str, Any]], max_distance: int = 6) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: float(item.get("content_score", 0)), reverse=True):
        candidate_hash = candidate.get("hash")
        if candidate_hash and any(hash_distance(candidate_hash, item.get("hash")) <= max_distance for item in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: float(item.get("timestamp", 0)))


def score_with_vlm(frame_path: Path, config: dict[str, Any], logger: logging.Logger) -> dict[str, Any]:
    vlm_config = config.get("vlm", {})
    if not bool(vlm_config.get("enabled", False)):
        return {"content_score": 0.0, "reason": "vlm_disabled"}
    logger.warning("VLM scoring is enabled but no provider adapter is implemented in the MVP.")
    return {"content_score": 0.0, "reason": "vlm_adapter_not_configured"}
