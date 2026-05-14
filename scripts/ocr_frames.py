from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from utils import read_json, write_json


def ocr_frames(
    candidates: list[dict[str, Any]],
    output_json: Path,
    config: dict[str, Any],
    logger: logging.Logger,
    resume: bool = True,
) -> list[dict[str, Any]]:
    if resume and output_json.exists():
        cached = read_json(output_json, [])
        if cached:
            logger.info("Reusing OCR results: %s", output_json)
            return cached

    ocr_config = config.get("ocr", {})
    if not bool(ocr_config.get("enabled", True)):
        results = [_empty_ocr(item, "ocr_disabled") for item in candidates]
        write_json(output_json, results)
        return results

    engine_name = str(ocr_config.get("engine", "easyocr")).lower()
    try:
        if engine_name == "paddleocr":
            engine = _PaddleOCREngine(ocr_config, logger)
        else:
            engine = _EasyOCREngine(ocr_config, logger)
    except ImportError as exc:
        logger.warning("OCR engine is unavailable: %s. Continuing with empty OCR results.", exc)
        results = [_empty_ocr(item, "ocr_dependency_missing") for item in candidates]
        write_json(output_json, results)
        return results

    results = []
    for candidate in candidates:
        try:
            results.append(engine.run(candidate))
        except Exception as exc:
            logger.warning("OCR failed for %s: %s", candidate.get("path"), exc)
            results.append(_empty_ocr(candidate, "ocr_failed"))
    write_json(output_json, results)
    logger.info("OCR finished for %s frames.", len(results))
    return results


def _empty_ocr(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate["id"],
        "timestamp": candidate["timestamp"],
        "text": "",
        "text_blocks": [],
        "char_count": 0,
        "text_area_ratio": 0.0,
        "avg_confidence": 0.0,
        "line_count": 0,
        "status": reason,
    }


class _EasyOCREngine:
    def __init__(self, config: dict[str, Any], logger: logging.Logger) -> None:
        import easyocr

        languages = config.get("languages", ["ch_sim", "en"])
        self.min_confidence = float(config.get("min_confidence", 0.35))
        self.reader = easyocr.Reader(languages, gpu=False)
        logger.info("Loaded EasyOCR languages=%s", languages)

    def run(self, candidate: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image

        path = candidate["path"]
        image = Image.open(path)
        width, height = image.size
        raw = self.reader.readtext(path)
        blocks = []
        texts = []
        area = 0.0
        confidences = []
        for box, text, confidence in raw:
            if confidence < self.min_confidence or not text.strip():
                continue
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            block_area = max(0, max(xs) - min(xs)) * max(0, max(ys) - min(ys))
            area += block_area
            confidences.append(float(confidence))
            texts.append(text.strip())
            blocks.append({"box": box, "text": text.strip(), "confidence": float(confidence)})
        joined = "\n".join(texts)
        return {
            "candidate_id": candidate["id"],
            "timestamp": candidate["timestamp"],
            "text": joined,
            "text_blocks": blocks,
            "char_count": len(joined.replace(" ", "").replace("\n", "")),
            "text_area_ratio": area / max(1, width * height),
            "avg_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
            "line_count": len(blocks),
            "status": "ok",
        }


class _PaddleOCREngine:
    def __init__(self, config: dict[str, Any], logger: logging.Logger) -> None:
        from paddleocr import PaddleOCR

        languages = config.get("languages", ["ch_sim", "en"])
        lang = "ch" if any(str(item).startswith("ch") for item in languages) else "en"
        self.min_confidence = float(config.get("min_confidence", 0.35))
        self.ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)
        logger.info("Loaded PaddleOCR lang=%s", lang)

    def run(self, candidate: dict[str, Any]) -> dict[str, Any]:
        from PIL import Image

        path = candidate["path"]
        image = Image.open(path)
        width, height = image.size
        raw = self.ocr.ocr(path, cls=True)
        blocks = []
        texts = []
        area = 0.0
        confidences = []
        for page in raw or []:
            for item in page or []:
                box = item[0]
                text = item[1][0].strip()
                confidence = float(item[1][1])
                if confidence < self.min_confidence or not text:
                    continue
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                block_area = max(0, max(xs) - min(xs)) * max(0, max(ys) - min(ys))
                area += block_area
                confidences.append(confidence)
                texts.append(text)
                blocks.append({"box": box, "text": text, "confidence": confidence})
        joined = "\n".join(texts)
        return {
            "candidate_id": candidate["id"],
            "timestamp": candidate["timestamp"],
            "text": joined,
            "text_blocks": blocks,
            "char_count": len(joined.replace(" ", "").replace("\n", "")),
            "text_area_ratio": area / max(1, width * height),
            "avg_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
            "line_count": len(blocks),
            "status": "ok",
        }
