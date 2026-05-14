# bilibili-video-knowledge-extractor

一个可在 OpenClaw 中使用的视频知识提取 Skill。它接收 B 站视频链接或本地视频文件，输出完整语音转写、内容型关键画面、时间轴对齐结果和总结报告。

## 快速开始

```bash
cd bilibili-video-knowledge-extractor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

确保系统已安装 ffmpeg：

```bash
brew install ffmpeg
```

处理本地视频：

```bash
python scripts/run.py \
  --input "./demo.mp4" \
  --output "./outputs/demo" \
  --asr-model "medium"
```

处理 B 站视频：

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

本工具只处理用户有权限访问的视频。不要把账号、密码、Cookie 或 Token 写入代码；如需登录态，请使用浏览器导出的 `cookies.txt`。

## 输出结构

```text
outputs/demo/
  transcript_full.md
  summary_report.md
  summary_report.html
  frames/
    keyframe_00001_00-01-23.jpg
  transcript_segments.json
  keyframes.json
  ocr_results.json
  alignment.json
  run_log.json
  process.log
  work/
    audio.wav
    candidate_frames/
```

## MVP 能力

- B 站 URL 下载：通过 `yt-dlp`。
- 本地视频输入：直接处理本地路径。
- 音频抽取：通过 `ffmpeg` 输出 16kHz 单声道 WAV。
- 语音转写：优先 `faster-whisper`，可回退 `whisper`。
- 场景检测：优先 PySceneDetect，回退 OpenCV 直方图差异检测。
- 关键帧筛选：OCR 密度、边缘密度、清晰度、结构复杂度和感知哈希去重。
- 时间轴对齐：把关键画面插入最近语音段。
- 报告生成：Markdown 默认开启，HTML 可选。

## 配置

复制配置文件后按需调整：

```bash
cp config.example.yaml config.yaml
python scripts/run.py --config config.yaml --input ./demo.mp4 --output ./outputs/demo
```

常用配置：

- `asr.model_size`: ASR 模型大小。
- `ocr.enabled`: 是否启用 OCR。
- `keyframe.content_score_threshold`: 内容型画面阈值。
- `keyframe.hash_distance_threshold`: 重复画面哈希距离阈值。
- `report.generate_html`: 是否输出 HTML。
- `runtime.keep_intermediate`: 是否保留中间文件。

## VLM 扩展

默认不启用视觉模型。`filter_keyframes.py` 已预留 `score_with_vlm` 接口，可接入本地 Ollama vision model、Qwen-VL、LLaVA 或企业内部视觉模型。接口要求返回：

```json
{
  "content_score": 0,
  "reason": "判断理由"
}
```

## 故障排查

- `ffmpeg not found`: 安装 ffmpeg 并确认 PATH。
- B 站下载失败：确认链接可访问；需要登录时传入合法 `cookies.txt`。
- ASR 为空：检查视频音轨，尝试更大 ASR 模型。
- OCR 没有结果：先关闭 OCR 跑通主流程，或安装 EasyOCR/PaddleOCR。
- 关键帧太少：降低 `keyframe.content_score_threshold`。
- 关键帧太多：提高阈值或降低 `max_frames_per_video`。

## 测试

```bash
python -m unittest discover tests
```

这些测试覆盖关键帧评分/去重和时间轴对齐的核心逻辑，不需要真实视频文件。
