# 中文安装指南（B 站视频知识提取 skill）

> 适用人群：在中国大陆使用，想跑通 B 站真实视频的开发者
> 适用版本：woodfreeman fork（2026-06-21+）
> 上游仓库：https://github.com/ADong-W/bilibili-video-knowledge-extractor

---

## 1. 系统依赖

```bash
# ffmpeg（必需）—— 大部分 Linux 发行版自带
ffmpeg -version   # 检查

# 如未装：
# Debian/Ubuntu:  sudo apt install ffmpeg
# macOS:           brew install ffmpeg
# 飞牛 OS / 内核: 系统通常已自带
```

---

## 2. Python 依赖

```bash
# 推荐用 uv（快、清华源）；pip 也行
cd bilibili-video-knowledge-extractor

# 方式 A：uv + 清华源（推荐）
UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/ \
  uv pip install --python /path/to/research-venv/bin/python \
  -r requirements.txt

# 方式 B：原生 pip + 清华源
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple/ -r requirements.txt
```

`requirements.txt` 已锁定 `yt-dlp>=2025.10.0`（B 站 2025+ WBI 签名要求）。

---

## 3. Hugging Face 模型（ASR + OCR）

> ⚠️ 国内访问 Hugging Face Hub 经常被墙，需要**镜像 + 重定向**。

### 3.1 准备 model cache 目录

```bash
# 默认 ~/.cache/huggingface/ 在某些系统是 root 拥有（写不了）
# 改用项目级 cache：
mkdir -p /vol1/1000/cc00/.cache/huggingface   # 路径自定，可改
```

### 3.2 设置环境变量

```bash
# 方式 A：每次命令前加
HF_ENDPOINT=https://hf-mirror.com \
HF_HUB_CACHE=/vol1/1000/cc00/.cache/huggingface \
  python scripts/run.py ...

# 方式 B：写进 ~/.bashrc（推荐，永久）
echo 'export HF_ENDPOINT=https://hf-mirror.com' >> ~/.bashrc
echo 'export HF_HUB_CACHE=/vol1/1000/cc00/.cache/huggingface' >> ~/.bashrc
source ~/.bashrc
```

---

## 4. B 站 cookies 导出与转换

B 站大部分视频需要登录态才能拿到 CDN URL。

### 4.1 浏览器导出 cookies

任选一种：

- **Chrome/Firefox 扩展**：*Get cookies.txt LOCALLY* → 选 `https://www.bilibili.com` → Export → 拿到 **Netscape 格式**文件
- **DevTools**：F12 → Network → 找一个 bilibili.com 请求 → 复制 `Cookie:` 头 → 拿到 **HTTP Cookie 头格式**（`name=value;name=value;...`）

### 4.2 转成 yt-dlp 兼容的 Netscape 格式

```bash
python scripts/import_cookies.py /path/to/exported.txt --convert ./cookies-netscape.txt
# 输出文件自动 chmod 600
```

`import_cookies.py` 自动识别两种格式，并验证必需字段：
- `DedeUserID`（你的 UID）
- `DedeUserID__ckMd5`（UID 哈希）
- `SESSDATA`（登录态）
- `bili_jct`（CSRF token）

### 4.3 验证 cookies 有效

```bash
python scripts/import_cookies.py ./cookies-netscape.txt --check
# 输出 "Logged in as <你的昵称>" 即可
```

---

## 5. 跑 skill

### 5.1 第一次跑（推荐用小模型快速验证）

```bash
# 完整环境变量 + 小模型（30 秒视频约 12 秒处理完）
HF_ENDPOINT=https://hf-mirror.com \
HF_HUB_CACHE=/vol1/1000/cc00/.cache/huggingface \
CUDA_VISIBLE_DEVICES="-1" \  # 强制 CPU（无 GPU 时）
  python scripts/run.py \
  --input "https://www.bilibili.com/video/BV1xxxxxxxxx" \
  --output ./outputs/test \
  --cookies ./cookies-netscape.txt \
  --asr-model tiny \
  --enable-ocr true \
  --enable-vlm false \
  --generate-html true
```

### 5.2 正式跑（medium 模型）

```bash
HF_ENDPOINT=https://hf-mirror.com \
HF_HUB_CACHE=/vol1/1000/cc00/.cache/huggingface \
CUDA_VISIBLE_DEVICES="-1" \
  python scripts/run.py \
  --input "https://www.bilibili.com/video/BV1xxxxxxxxx" \
  --output ./outputs/real \
  --cookies ./cookies-netscape.txt \
  --asr-model medium \
  --enable-ocr true
```

### 5.3 性能预期

| 视频时长 | 模型 | CPU 估算 | GPU 估算 |
|---|---|---|---|
| 30 秒 | tiny | ~12 秒 | ~5 秒 |
| 8 分钟 | small | ~4 分 50 秒 | ~1 分钟 |
| 1 小时 | medium | ~1 小时 | ~10 分钟 |
| 1 小时 | large-v3 | ~5 小时 | ~30 分钟 |

---

## 6. 常见错误

| 错误 | 原因 | 解决 |
|---|---|---|
| `RuntimeError: Library libcublas.so.12 is not found` | faster-whisper 尝试 GPU | 加 `CUDA_VISIBLE_DEVICES="-1"` |
| `yt-dlp ... 412 Precondition Failed` | yt-dlp 版本太旧 | `pip install -U yt-dlp>=2025.10.0` |
| `does not look like a Netscape format cookies file` | cookies 格式不对 | 用 `import_cookies.py --convert` 转 |
| `Missing required fields: SESSDATA` | 没登录或 cookies 过期 | 重新导出 cookies |
| `Could not download JSON metadata: HTTP Error 412` | B 站风控 | 升级 yt-dlp + 加 UA/Referer（本 fork 已加） |
| `LocalEntryNotFoundError: ConnectTimeout` | Hugging Face 墙了 | 设置 `HF_ENDPOINT=https://hf-mirror.com` |

---

## 7. 进阶：纳入自动化流程

这个 skill 的设计目标就是**可脚本化**，4 步流程示例：

```bash
#!/bin/bash
# 4 步流程 step 1：读 B 站视频 → 产出 transcript + frames + summary

set -e
VIDEO_URL="$1"
COOKIES="/path/to/cookies-netscape.txt"
TS=$(date +%Y%m%d-%H%M%S)
OUTPUT_DIR="/vol1/1000/notes/videos/${TS}"

HF_ENDPOINT=https://hf-mirror.com \
HF_HUB_CACHE=/vol1/1000/cc00/.cache/huggingface \
CUDA_VISIBLE_DEVICES="-1" \
  python scripts/run.py \
  --input "$VIDEO_URL" \
  --output "$OUTPUT_DIR" \
  --cookies "$COOKIES" \
  --asr-model medium

# 然后人工/AI 读 summary_report.md 写心得
```

---

## 8. 与原版（ADong-W）的区别

| 修改 | 上游原版 | woodfreeman fork |
|---|---|---|
| ASR 返空 transcript | raise 杀死流程 | 写空 `[]`，pipeline 继续 |
| numpy int32 JSON 序列化 | 报错 | 序列化成功 |
| B 站视频下载 | 2026 年 412/Av gzip 失败 | 加 UA/Referer/extractor-args，能下 |
| 无音轨视频 | raise 杀死流程 | 生成静音占位，pipeline 继续 |
| cleanup | 只删 candidate_frames | 也删 source.mp4/audio.wav |
| yt-dlp 最低版本 | 2024.12 | 2025.10 |
| 中文文档 | 无 | 本文档 |

---

**测试记录（woodfreeman 2026-06-21）**：
- 视频：BV1TZ5Y6CEH1（"47天，飞书CLI破万星"）
- 时长：8 分 03 秒 / 1080P
- 转写：286 段
- 关键帧：10 个
- 耗时：4 分 50 秒（CPU + small ASR）
- 结果：✅ 全 12 stage 成功
