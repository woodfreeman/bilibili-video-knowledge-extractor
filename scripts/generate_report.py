from __future__ import annotations

import html
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from utils import read_json, safe_text, seconds_to_timestamp, write_json


def generate_reports(
    output_dir: Path,
    metadata: dict[str, Any],
    media_info: dict[str, Any],
    alignment: dict[str, Any],
    keyframes: list[dict[str, Any]],
    config: dict[str, Any],
    logger: logging.Logger,
) -> dict[str, str]:
    report_config = config.get("report", {})
    processed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    segments = alignment.get("segments", [])
    context = build_report_context(metadata, media_info, segments, keyframes, config, processed_at)
    result_paths: dict[str, str] = {}

    if bool(report_config.get("generate_markdown", True)):
        transcript = render_template(output_dir, "transcript_full.md.j2", context)
        transcript_path = output_dir / "transcript_full.md"
        transcript_path.write_text(transcript, encoding="utf-8")
        summary = render_template(output_dir, "summary_report.md.j2", context)
        summary_path = output_dir / "summary_report.md"
        summary_path.write_text(summary, encoding="utf-8")
        result_paths["transcript_full"] = str(transcript_path)
        result_paths["summary_report"] = str(summary_path)

    if bool(report_config.get("generate_html", False)):
        summary_md = (output_dir / "summary_report.md").read_text(encoding="utf-8")
        context["markdown_html"] = markdown_to_html(summary_md)
        html_text = render_template(output_dir, "summary_report.html.j2", context)
        html_path = output_dir / "summary_report.html"
        html_path.write_text(html_text, encoding="utf-8")
        result_paths["summary_report_html"] = str(html_path)

    write_json(output_dir / "report_context.json", {k: v for k, v in context.items() if k != "markdown_html"})
    logger.info("Reports generated: %s", result_paths)
    return result_paths


def build_report_context(
    metadata: dict[str, Any],
    media_info: dict[str, Any],
    segments: list[dict[str, Any]],
    keyframes: list[dict[str, Any]],
    config: dict[str, Any],
    processed_at: str,
) -> dict[str, Any]:
    duration = float(media_info.get("duration") or metadata.get("duration") or 0)
    full_text = "\n".join(segment.get("text", "") for segment in segments)
    keywords = extract_keywords(full_text, limit=12)
    chapters = build_chapters(segments, keyframes, config)
    knowledge_points = build_knowledge_points(segments, keyframes, keywords)
    timeline_limit = int(config.get("report", {}).get("max_timeline_items", 120))

    return {
        "video": {
            "source": metadata.get("source") or metadata.get("webpage_url") or "",
            "title": metadata.get("title") or "未命名视频",
            "duration": duration,
            "duration_text": seconds_to_timestamp(duration),
        },
        "processed_at": processed_at,
        "segments": segments,
        "segment_count": len(segments),
        "keyframes": enrich_keyframes_for_report(keyframes, segments),
        "keyframe_count": len(keyframes),
        "core_theme": summarize_core_theme(full_text, keywords),
        "chapters": chapters,
        "knowledge_points": knowledge_points,
        "sop_text": extract_sop_text(full_text),
        "skill_candidates": extract_skill_candidate_text(full_text),
        "timeline": [
            {
                "start_text": segment.get("start_text"),
                "end_text": segment.get("end_text"),
                "text": safe_text(segment.get("text", ""), 180),
            }
            for segment in segments[:timeline_limit]
        ],
    }


def render_template(output_dir: Path, template_name: str, context: dict[str, Any]) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:
        raise RuntimeError("Jinja2 is required to render reports. Install requirements.txt.") from exc

    template_dir = Path(__file__).resolve().parents[1] / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(enabled_extensions=("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template(template_name).render(**context)


def extract_keywords(text: str, limit: int = 12) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_+-]{2,}", text)
    stopwords = {
        "这个",
        "我们",
        "就是",
        "一个",
        "可以",
        "然后",
        "这里",
        "进行",
        "通过",
        "因为",
        "所以",
        "大家",
        "视频",
        "内容",
        "the",
        "and",
        "for",
        "with",
    }
    filtered = [token for token in tokens if token.lower() not in stopwords]
    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(limit)]


def summarize_core_theme(text: str, keywords: list[str]) -> str:
    if not text.strip():
        return "未获得足够转写内容，无法稳定判断核心主题。"
    first_sentence = split_sentences(text)[0] if split_sentences(text) else safe_text(text, 160)
    keyword_text = "、".join(keywords[:8]) if keywords else "暂无稳定关键词"
    return f"本视频围绕“{keyword_text}”展开。开篇内容显示：{safe_text(first_sentence, 180)}"


def build_chapters(
    segments: list[dict[str, Any]],
    keyframes: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if not segments:
        return []
    max_seconds = float(config.get("report", {}).get("max_chapter_seconds", 300))
    chapters = []
    current = []
    chapter_start = float(segments[0].get("start", 0))
    for segment in segments:
        current.append(segment)
        if float(segment.get("end", 0)) - chapter_start >= max_seconds:
            chapters.append(_make_chapter(current, keyframes))
            current = []
            chapter_start = float(segment.get("end", 0))
    if current:
        chapters.append(_make_chapter(current, keyframes))
    return chapters


def _make_chapter(segments: list[dict[str, Any]], keyframes: list[dict[str, Any]]) -> dict[str, Any]:
    start = float(segments[0].get("start", 0))
    end = float(segments[-1].get("end", start))
    text = " ".join(segment.get("text", "") for segment in segments)
    frames = [frame for frame in keyframes if start <= float(frame.get("timestamp", 0)) <= end][:3]
    title = infer_title(text, start)
    return {
        "title": title,
        "start": start,
        "end": end,
        "start_text": seconds_to_timestamp(start),
        "end_text": seconds_to_timestamp(end),
        "summary": safe_text(extractive_summary(text), 260),
        "frames": frames,
    }


def infer_title(text: str, start: float) -> str:
    keywords = extract_keywords(text, limit=4)
    if keywords:
        return " / ".join(keywords[:3])
    return f"{seconds_to_timestamp(start)} 附近内容"


def extractive_summary(text: str) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return safe_text(text, 240) or "该章节缺少可用转写内容。"
    if len(sentences) == 1:
        return safe_text(sentences[0], 240)
    return safe_text(" ".join(sentences[:2]), 260)


def split_sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[。！？!?])\s*", text) if item.strip()]


def build_knowledge_points(
    segments: list[dict[str, Any]],
    keyframes: list[dict[str, Any]],
    keywords: list[str],
) -> list[dict[str, Any]]:
    points = []
    for keyword in keywords[:10]:
        segment = next((seg for seg in segments if keyword in seg.get("text", "")), None)
        if not segment:
            continue
        frame = nearest_keyframe((float(segment.get("start", 0)) + float(segment.get("end", 0))) / 2, keyframes)
        points.append(
            {
                "title": keyword,
                "time_text": f"{segment.get('start_text')} - {segment.get('end_text')}",
                "frame_ref": frame.get("relative_path", "") if frame else "",
                "note": safe_text(segment.get("text", ""), 120),
            }
        )
    if not points:
        points.append({"title": "待人工复核", "time_text": "", "frame_ref": "", "note": "转写内容不足，未抽取到稳定知识点。"})
    return points


def enrich_keyframes_for_report(keyframes: list[dict[str, Any]], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for index, frame in enumerate(keyframes, start=1):
        segment = nearest_segment(frame.get("timestamp", 0), segments)
        nearby_text = safe_text(segment.get("text", ""), 180) if segment else ""
        result = dict(frame)
        result["title"] = infer_frame_title(frame, index)
        result["nearby_text"] = nearby_text or "未找到相邻话术。"
        result["reusable_note"] = infer_reusable_note(nearby_text)
        enriched.append(result)
    return enriched


def infer_frame_title(frame: dict[str, Any], index: int) -> str:
    ocr_text = frame.get("ocr_text") or ""
    keywords = extract_keywords(ocr_text, limit=3)
    if keywords:
        return " / ".join(keywords)
    return f"{frame.get('timestamp_text', '')} 关键画面 {index}"


def infer_reusable_note(text: str) -> str:
    if any(word in text for word in ["步骤", "流程", "方法", "配置", "操作", "实现", "代码"]):
        return "该片段可能包含可复用流程或操作方法，建议人工复核后沉淀为 SOP。"
    return "可作为理解该时间段讲解内容的视觉证据。"


def nearest_segment(timestamp: float, segments: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not segments:
        return None
    return min(
        segments,
        key=lambda seg: 0
        if float(seg.get("start", 0)) <= timestamp <= float(seg.get("end", 0))
        else min(abs(timestamp - float(seg.get("start", 0))), abs(timestamp - float(seg.get("end", 0)))),
    )


def nearest_keyframe(timestamp: float, keyframes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not keyframes:
        return None
    return min(keyframes, key=lambda frame: abs(float(frame.get("timestamp", 0)) - timestamp))


def extract_sop_text(text: str) -> str:
    if not text.strip():
        return "未获得足够转写内容，暂无法抽取 SOP。"
    markers = ["第一", "第二", "第三", "步骤", "流程", "方法", "操作", "配置", "实现"]
    sentences = [s for s in split_sentences(text) if any(marker in s for marker in markers)]
    if not sentences:
        return "MVP 未识别到明确步骤。建议人工阅读完整转写，查找可复用流程、配置方法和操作路径。"
    return "\n".join(f"- {safe_text(sentence, 180)}" for sentence in sentences[:8])


def extract_skill_candidate_text(text: str) -> str:
    if any(word in text for word in ["脚本", "自动化", "流程", "模板", "配置", "工具", "步骤"]):
        return "视频中可能包含可沉淀为 Skill 的流程化内容。建议重点复核出现“步骤、配置、脚本、模板、自动化”的片段。"
    return "暂未识别到明确可沉淀为 Skill 的操作流程；如视频是课程或演示，可结合完整转写人工补充。"


def markdown_to_html(markdown: str) -> str:
    try:
        import markdown as md

        return md.markdown(markdown, extensions=["tables", "fenced_code"])
    except Exception:
        return fallback_markdown_to_html(markdown)


def fallback_markdown_to_html(markdown: str) -> str:
    lines = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("# "):
            lines.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            lines.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            lines.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("![](") and line.endswith(")"):
            src = html.escape(line[4:-1])
            lines.append(f'<img src="{src}" alt="">')
        elif line.startswith("- "):
            lines.append(f"<p>{html.escape(line)}</p>")
        elif line:
            lines.append(f"<p>{html.escape(line)}</p>")
    return "\n".join(lines)
