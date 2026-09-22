# LocalLectureAI（本地网课 AI 助手）

全本地运行的网课助手：**实时语音转写 + 屏幕课件视觉理解 + 增量课程笔记 + 课后大模型总结**。不依赖任何云端 API，仅访问本机 `localhost:11434`（Ollama）。

## 功能

- **实时转写**：WASAPI loopback 系统声音 → faster-whisper（turbo / 中文 / CUDA）
- **视觉理解**：屏幕区域连续帧 → OpenCV 差分门控 → Qwen3-VL 单次调用（截图只在内存处理，不落盘）
- **增量笔记**：转写 + 视觉事件按间隔融合，实时写 `live_notes.md` / `course_state.jsonl`
- **课后总结**：释放实时模型后加载 `qwen38-27b` 独占生成 `final_summary.md`（结构化 Markdown）

## 环境要求

- Windows 10/11 + NVIDIA GPU（实测 RTX 5070 12GB）
- [Ollama](https://ollama.com)（本机 `11434`）
- Python 3.12 + CUDA 运行库（`requirements.txt` 内含 `nvidia-cublas-cu12` 等）

### 模型

```powershell
ollama pull qwen3-vl:8b          # 实时视觉（回退 4b）
ollama pull qwen3-vl:4b
ollama pull qwen38-27b-main:latest  # 课后总结（约 10GB）
# 可选：QLens 原生默认模型
ollama pull qwen2.5vl:7b
ollama pull qwen2.5:7b
```

Whisper 模型首次运行自动下载；国内网络请先设置 `HF_ENDPOINT=https://hf-mirror.com`。

## 快速开始

```powershell
.\start_course.ps1        # 启动 GUI（自动选择屏幕区域）
# 上课中：实时转写与笔记自动更新
.\stop_course.ps1         # 或点击 GUI「结束课程」→ 生成课后总结
```

会话产物位于 `sessions/<时间戳>/`：`transcript.md`、`live_notes.md`、`final_summary.md` 等。

## 测试

```powershell
$env:HF_ENDPOINT='https://hf-mirror.com'
.venv\Scripts\python.exe test_e2e.py       # 端到端（约 10 分钟）
.venv\Scripts\python.exe test_stop_flag.py # 结束流程
```

详见 [docs/TEST_REPORT.md](docs/TEST_REPORT.md) · 实现说明 [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) · 复用清单 [docs/REUSE_MAP.md](docs/REUSE_MAP.md)。

## 第三方组件（均已保留原 LICENSE）

| 组件 | 上游 | 修改 |
|---|---|---|
| [qlens](https://github.com/tomaszwi66/qlens) (MIT) | 视觉捕获与 Ollama 客户端 | 模型名 env 可配 + `think`/`num_ctx`/`num_predict` 可选参数，见 `docs/qlens.patch` |
| [Hearsay](https://github.com/parkscloud/Hearsay) (MIT) | WASAPI + faster-whisper | 无（`docs/hearsay.patch` 为空） |

vendored 源码基线：qlens `9df0095`、Hearsay `4d5a56d`。
