---
name: bilibili-video-knowledge-extractor
description: Extract knowledge from Bilibili URLs or local videos by transcribing speech, selecting content-rich keyframes, aligning images to transcript segments, and generating Markdown/HTML learning reports.
---

# bilibili-video-knowledge-extractor

## 适用场景

当用户提供 B 站视频链接或本地视频文件，并希望沉淀为可检索、可复用的知识材料时使用本 Skill。典型输入包括课程、演讲、产品演示、屏幕录制、代码讲解、PPT 汇报、表格分析、白板推导和操作教程。

## 不适用场景

- 用户无权访问、下载或处理的视频。
- 受 DRM、付费墙、登录限制保护且用户未提供合法访问凭据的视频。
- 只需要下载视频而不需要转写、关键画面和总结报告的任务。
- 纯娱乐、纯口播、无知识画面的视频，输出可能只有语音转写和少量关键帧。
- 需要逐字级人工校对、法律证据级转写精度或医学诊断级结论的场景。

## 输入参数

统一入口：

```bash
python scripts/run.py \
  --input "https://www.bilibili.com/video/BVxxxx" \
  --output "./outputs/demo" \
  --cookies "./cookies.txt" \
  --asr-model "medium" \
  --enable-ocr true \
  --enable-vlm false \
  --generate-html true
```

本地视频：

```bash
python scripts/run.py \
  --input "./demo.mp4" \
  --output "./outputs/demo" \
  --asr-model "medium"
```

主要参数：

- `--input`: B 站视频 URL 或本地视频路径，必填。
- `--output`: 输出目录，默认 `./outputs/<timestamp>`。
- `--config`: YAML 配置文件路径，默认读取 `config.example.yaml` 中的同名配置结构。
- `--cookies`: B 站 cookies.txt 路径。只用于用户有权访问的视频，不硬编码账号、密码、Cookie 或 Token。
- `--asr-model`: faster-whisper/whisper 模型大小，例如 `tiny`、`base`、`small`、`medium`、`large-v3`。
- `--enable-ocr`: 是否启用 OCR，默认由配置决定。
- `--enable-vlm`: 是否启用视觉模型判断，默认关闭。
- `--only-content-frames`: 是否只保留“有内容的画面”，默认开启。
- `--generate-html`: 是否生成 `summary_report.html`。
- `--generate-markdown`: 是否生成 Markdown 报告，默认开启。
- `--keep-intermediate`: 是否保留音频、候选帧等中间文件。
- `--resume`: 从已存在 checkpoint 继续执行，默认开启。

## 输出文件

输出目录包含：

- `transcript_full.md`: 完整语音转文本，按时间轴排列，并在相邻语音段插入有内容的关键画面。
- `summary_report.md`: 视频内容总结报告，包含主题、章节、知识点、重要画面、SOP 和可沉淀 Skill 内容。
- `summary_report.html`: 可选 HTML 报告。
- `frames/`: 筛选后的关键画面，命名如 `keyframe_00001_00-01-23.jpg`。
- `transcript_segments.json`: ASR 结构化分段。
- `keyframes.json`: 关键画面结构化结果。
- `ocr_results.json`: OCR 结果。
- `alignment.json`: 转写段落与关键画面对齐结果。
- `run_log.json`: 每个阶段的状态、耗时、错误原因和修复建议。
- `process.log`: 详细运行日志。

## 执行流程

1. 解析配置和命令行参数。
2. 判断输入类型：B 站 URL 使用 `yt-dlp` 下载，本地视频直接处理。
3. 使用 `ffprobe` 检查时长、分辨率和音频流。
4. 使用 `ffmpeg` 抽取 16kHz 单声道 WAV 音频。
5. 使用 `faster-whisper` 优先执行 ASR；若未安装可回退到 `whisper`。
6. 使用 PySceneDetect 检测场景；未安装时回退 OpenCV 直方图差异检测。
7. 对每个场景抽取候选关键帧，长场景按配置补充候选帧。
8. 可选执行 OCR，提取文字数量、文字区域和文本内容。
9. 使用多层策略过滤“有内容的画面”：清晰度、文本密度、视觉结构复杂度、去重和可选 VLM。
10. 将关键画面对齐到最近的语音转写片段。
11. 使用模板生成 `transcript_full.md`、`summary_report.md` 和可选 HTML。
12. 写入 checkpoint 和日志，失败时给出明确修复建议。

## 关键算法说明

- **场景切分**：优先使用 PySceneDetect 内容检测，回退方案为 OpenCV HSV 直方图差异。检测到的长场景会按 `long_scene_interval_seconds` 追加候选帧，避免长时间屏幕录制只抽一张。
- **候选帧抽取**：每个场景抽中点帧，并可按间隔追加。截图使用 ffmpeg 的 `-ss` 精确定位和可配置缩放。
- **OCR 密度**：统计文字数量、文本框面积占比、平均置信度和文本行数。文字多、面积合理、置信度高的帧优先保留。
- **视觉结构判断**：使用边缘密度、清晰度和颜色复杂度估计是否包含 PPT、代码、表格、界面、图表或白板内容。
- **重复画面去重**：优先使用 perceptual hash；没有 `imagehash` 时使用内置平均哈希。连续或近似重复帧只保留分数最高的一张。
- **时间轴对齐**：关键帧按时间戳匹配包含该时间点的 ASR 片段；不在片段内时匹配最近片段，并受 `max_alignment_distance_seconds` 约束。
- **报告生成**：MVP 使用转写文本和关键帧做抽取式总结，不依赖商业 LLM。需要更强总结能力时，可在 `generate_report.py` 中接入本地或企业内部模型。

## “有内容的画面”判断规则

保留倾向：

- PPT 页面、白板内容、代码页面、Excel/表格、流程图、架构图、产品界面、公式、关键文字说明。
- 屏幕录制中的重要操作页面。
- 与讲解内容强相关、静态且清晰的画面。

过滤倾向：

- 纯 UP 主露脸讲话。
- 无文字、无图表、无操作的空镜头。
- 片头片尾、转场动画。
- 重复画面、模糊画面、内容变化极小的连续帧。

评分规则：

- `ocr_score`: 文字数量、文字面积和置信度。
- `visual_score`: 边缘密度、清晰度、结构复杂度。
- `dedupe_score`: 与已保留帧的哈希距离。
- `vlm_score`: 可选视觉模型的 0-100 判断分。
- `content_score`: 综合分。默认阈值见 `config.example.yaml`。

## 失败重试机制

- 每个阶段都写出 checkpoint 文件；再次运行时默认跳过已完成阶段。
- 失败会记录到 `run_log.json` 和 `process.log`。
- 常见失败与建议：
  - `ffmpeg/ffprobe not found`: 安装 ffmpeg 并确保命令在 PATH 中。
  - B 站下载失败：确认 URL 可访问；如需登录，导出合法 `cookies.txt` 后传入 `--cookies`。
  - 视频无音频：确认源文件含音轨，或将配置改为允许空转写后仅生成画面报告。
  - ASR 为空：尝试更大的 `--asr-model`，检查音频是否静音。
  - OCR 为空：安装 EasyOCR/PaddleOCR，或关闭 OCR 仅使用视觉结构启发式。
  - 没有关键画面：降低 `content_score_threshold` 或关闭 `only_content_frames`。
  - 视频过长/分辨率过高：调整 `max_duration_seconds`、`frame_max_width` 或先转码压缩。

## 依赖安装

系统依赖：

```bash
brew install ffmpeg
```

Python 依赖：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

OCR 是可选能力。若 OCR 安装较重，可以先关闭：

```bash
python scripts/run.py --input ./demo.mp4 --output ./outputs/demo --enable-ocr false
```

## 示例调用

B 站视频：

```bash
python scripts/run.py \
  --input "https://www.bilibili.com/video/BVxxxx" \
  --output "./outputs/bv-demo" \
  --cookies "./cookies.txt" \
  --asr-model "medium" \
  --enable-ocr true \
  --enable-vlm false \
  --generate-html true
```

本地视频：

```bash
python scripts/run.py \
  --input "./demo.mp4" \
  --output "./outputs/local-demo" \
  --asr-model "medium" \
  --generate-markdown true
```

## 质量验收标准

- 输入 10-30 分钟知识类视频后生成 `transcript_full.md`。
- 转写按时间段排列，包含开始和结束时间。
- 关键画面与语音时间段基本匹配。
- 不机械地每隔固定秒截图。
- 不大量保留纯露脸画面、转场或模糊画面。
- `summary_report.md` 包含核心主题、章节摘要、关键知识点、重要画面与对应讲解。
- 所有 Markdown 图片引用路径存在且可打开。
- 支持本地视频和 B 站 URL。
- 配置项可通过 `config.yaml` 或命令行覆盖。
