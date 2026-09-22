# REUSE_MAP — 本地网课 AI 助手

本文档记录 QLens / Hearsay 两个上游项目中**可直接复用**的能力、事件/线程接口、
必须最小修改的位置，以及最终课程 AI 的数据流。上游代码位于：

- `third_party/qlens/`（tomaszwi66/qlens, MIT）
- `third_party/Hearsay/`（parkscloud/Hearsay, MIT）

---

## 1. QLens 可直接复用的代码

| 上游文件 | 复用方式 | 用途 |
|---|---|---|
| `core/capture.py` (`Region`, `grab`) | **直接 import** | 屏幕区域捕获（mss，BGR ndarray，纯内存，无落盘） |
| `core/coords.py` (`prepare_image`, `CoordinateMapper`) | **直接 import** | letterbox 缩放到推理尺寸；本项目只需要返回的图像 |
| `core/ollama_client.py` (`chat`, `chat_text`) | **直接 import** | Ollama HTTP 客户端（视觉 chat / 纯文本 chat），支持 `model=` 覆盖 |
| `core/region_selector.py` (`RegionSelector`) | **直接 import** | Snipping-Tool 式全屏区域选择（DPI 感知），用于框选网课播放区域 |
| `core/prompt.py` | **不复用其 prompt**（定位/counting 用途不符），但复用其结构 | 课程视觉 prompt 由 `app/course_session/prompts.py` 提供 |
| `core/video.py` (`VideoAnalyzer`) | **不直接复用类，复用其循环结构** | 原类把每帧描述攒到结束后聚合；课程模式改为：帧循环 + 变化检测 + 逐次发 `VisualEvent`，循环结构（grab → prepare_image → chat → 间隔等待 → stop event）照搬 |
| `main.py` DPI awareness 前置代码 | **复制 3 行**（`SetProcessDpiAwareness(2)`） | 保证 mss 抓到物理像素 |

**QLens 调用链（已追踪）**：
```
screen capture (mss, core/capture.grab)
→ frame ndarray (内存)
→ letterbox resize (core/coords.prepare_image)
→ Ollama /api/chat (core/ollama_client.chat, images=[jpeg b64], format json 或纯文本)
→ parser (core/parser.parse，仅定位任务需要)
→ aggregation (core/video.VideoAnalyzer: timeline → chat_text)
```
关键结论：**frame 全程在内存中处理，QLens 没有截图文件流水线**；`Video Mode`
即“上一帧处理完立即抓下一帧”（interval=0 时）。

## 2. Hearsay 可直接复用的代码

| 上游文件 | 复用方式 | 用途 |
|---|---|---|
| `audio/recorder.py` (`AudioRecorder`, `AudioChunk`) | **直接 import** | WASAPI Loopback 系统声音捕获（callback 驱动，30s 窗口 + 1s overlap，静音看门狗） |
| `audio/resampler.py` (`resample`) | **直接 import** | 任意采样率 → 16kHz mono float32 |
| `audio/devices.py` | **直接 import** | loopback/麦克风设备枚举与按名解析 |
| `transcription/engine.py` (`TranscriptionEngine`, `TranscriptionResult`) | **直接 import** | faster-whisper 推理封装（load/transcribe/unload） |
| `transcription/pipeline.py` (`TranscriptionPipeline`) | **直接 import** | 音频窗口 → 转写 → overlap 去重 → `transcript_queue` |
| `transcription/gpu_detect.py` (`detect_gpu`) | **直接 import** | CUDA 检测与模型推荐 |
| `output/markdown_writer.py` (`MarkdownWriter`) | **直接 import** | 转写结果增量写 Markdown、finalize、post_process |
| `output/formatter.py` | **直接 import** | 时间戳/时长格式化 |
| `constants.py` (`MODEL_TABLE`, `CHUNK_DURATION_S` 等) | **直接 import** | 模型表（含中文可用的多语言模型：turbo/large-v3/medium/small） |
| `utils/threading_utils.py` (`StoppableThread`) | **直接 import** | 可停止线程基类 |
| `app.py` (`HearsayApp` 的装配逻辑) | **照抄装配顺序，不复用其 tray/UI** | 我们的 `AudioBridge` 按同样顺序装配：engine.load → pipeline.start → recorder.start；停止时 recorder.stop/join → pipeline.stop/join → engine.unload → writer.finalize |
| `ui/tray.py`, `ui/wizard.py`, customtkinter UI | **不复用** | 第一版不做托盘/向导；Hearsay 原 GUI 可独立启动 |

**Hearsay 调用链（已追踪）**：
```
WASAPI Loopback (pyaudiowpatch, callback)
→ _SourceBuffer 累积 (内存)
→ 每 30s 切窗 AudioChunk(parts={source: 16k mono f32})
→ audio_queue
→ TranscriptionPipeline: engine.transcribe (faster-whisper)
→ overlap 去重 + mic 回声过滤
→ transcript_queue (TranscriptionResult: text/segments/window_start)
→ MarkdownWriter.append / 我们的 TranscriptEvent
```

## 3. 现有的事件 / 回调 / 线程接口

### QLens
- `VideoAnalyzer`（QObject + QThread）：`status` / `frame_done(idx, ts, desc, jpeg)` /
  `summary_done` / `failed` 四个 Qt signal；`stop()` 由 Event 驱动。
- `RegionSelector.on_done(region)` 回调。
- `MainWindow.request_capture` signal（热键 → GUI 线程）。
- **复用点**：我们的 `VisualStream` 沿用 QThread + signal 模式，新增
  `visual_event(...)` signal；或在无 GUI 场景下用普通线程 + 回调。

### Hearsay
- `AudioRecorder(audio_queue, source, on_fatal, on_no_audio)` → 生产者线程。
- `TranscriptionPipeline(audio_queue, transcript_queue, engine)` → 消费/生产线程。
- `TranscriptionResult` dataclass = 天然的 TranscriptEvent 载体
  （text, segments, window_start, language, chunk_index）。
- `MarkdownWriter.append/finalize/post_process`。
- **复用点**：`AudioBridge` 不新建音频线程，直接持有上述三件套，只额外轮询
  `transcript_queue` 转成 `TranscriptEvent`。

## 4. 必须最小修改的地方

| 位置 | 修改 | 原因 | 行数量级 |
|---|---|---|---|
| `qlens/config.py` | `MODEL_NAME` 默认值改为可被环境变量覆盖（`VISION_MODEL`），新增 `FALLBACK_VISION_MODEL` | 第一版视觉模型指定 `qwen3-vl:8b`，且模型名必须可配置 | ~5 行 |
| `qlens/core/ollama_client.py` | 无需改（`chat()` 已有 `model=` 参数；`chat_text()` 需加可选 `model=` 参数） | `chat_text` 硬编码 `TEXT_MODEL_NAME`，课后总结要换 `qwen38-27b-main` | ~3 行 |
| Hearsay 配置（`%APPDATA%\Hearsay\config.json`，非源码） | `language=zh`、`model_name=turbo`（多语言）、`device=cuda`、`audio_source=system` | 中文普通话优先；`.en` 模型不支持中文 | 0 行源码（配置数据） |
| 无其他上游源码修改 | — | 其余全部通过 import / adapter / wrapper 完成 | — |

upstream diff 汇总见 `docs/IMPLEMENTATION.md`。

## 5. 绝对不应重写的能力

1. **屏幕捕获**（mss + Region + DPI 处理）— QLens `core/capture.py` + `region_selector.py`
2. **WASAPI Loopback 音频捕获** — Hearsay `audio/recorder.py`（重写极易踩设备/回声/切窗坑）
3. **faster-whisper 推理与 overlap 去重** — Hearsay `transcription/engine.py` + `pipeline.py`
4. **Ollama HTTP 客户端** — QLens `core/ollama_client.py`
5. **frame letterbox / 推理尺寸流水线** — QLens `core/coords.py`
6. **Markdown 转写落盘** — Hearsay `output/markdown_writer.py`
7. **窗口切分（30s + 1s overlap）、静音看门狗、回声过滤** — Hearsay recorder/pipeline

新增的东西只允许是：**adapter / callback / event bridge / wrapper** +
两块真正的新逻辑：①画面变化过滤 ②增量课程笔记融合（含课后总结 prompt）。

## 6. 最终课程 AI 数据流

```
[网课窗口区域]
   │  mss grab（内存 BGR frame，连续，不落盘）
   ▼
VisualStream (adapter, 模式照搬 QLens VideoAnalyzer)
   ├─ FrameDiff (OpenCV absdiff, 新增, 轻量)
   │     无明显变化 → 丢弃（不调 VLM）
   │     有明显变化 → prepare_image → ollama_client.chat (qwen3-vl:8b)
   ▼
VisualEvent {timestamp, frame_id, description, changed, source=screen}
   │
   │   [系统声音]
   │      │ WASAPI loopback → AudioChunk(30s)
   │      ▼
   │   Hearsay TranscriptionPipeline (faster-whisper, turbo, zh)
   │      ▼
   │   TranscriptEvent {timestamp, duration, text, source=system_audio}
   │
   ▼
CourseSession (事件总线, 两条队列)
   ├─ 实时: 每 30~60s 或队列空闲时
   │    最近 transcript 窗口 + 最近 VisualEvents + 上一 CourseState
   │    → ollama chat_text（复用当前驻留的 qwen3-vl:8b 做文本融合，不另占显存）
   │    → delta update → CourseState
   │    → storage: course_state.jsonl + live_notes.md（增量追加）
   ├─ events.jsonl / visual_events.jsonl / transcript.md 持续落盘
   ▼
课程结束 (stop_course.ps1 / GUI 停止)
   1) 停 Hearsay recorder/pipeline、停 VisualStream（各自 unload）
   2) ollama 释放 qwen3-vl（keep_alive=0）
   3) 读取本 session 全部产物
   4) qwen38-27b-main:latest 独占生成 final_summary.md
```

显存策略：**实时阶段只有 Whisper(+可选 CPU) 与 qwen3-vl:8b；
qwen38-27b 仅在课后独占加载**，二者不同时驻留。

## 7. 当前项目运行方式

### 上游原样运行（P1 已验证）
```powershell
# QLens
.\.venv\Scripts\Activate.ps1
cd third_party\qlens
python main.py          # 需 Ollama + qwen2.5vl:7b / qwen2.5:7b（其默认模型）

# Hearsay
cd third_party\Hearsay\src
python -m hearsay       # 首次启动有 Setup Wizard；托盘 → Start Recording → System Audio
```

### 本项目（课程模式）
```powershell
.\start_course.ps1      # 启动 Course App（区域选择 + 音频桥 + 事件总线）
.\stop_course.ps1       # 结束课程，生成 final_summary.md
```

## 8. 原始项目测试结果

见 `docs/TEST_REPORT.md`（P1 阶段小节）。要点：

- QLens：进程启动存活；`grab → prepare_image → chat` 核心链路单独实测（脚本级，
  不依赖 GUI 操作），frame 不落盘。
- Hearsay：进程启动存活；WASAPI loopback 实录 + faster-whisper 中文转写实测；
  Markdown 输出实测。
- 具体模型、延迟、显存数字以 TEST_REPORT.md 实测数据为准，未验证项一律标注
  “未验证”，不声称支持。
