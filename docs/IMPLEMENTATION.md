# 实现说明（IMPLEMENTATION）

本地网课 AI 助手：复用 QLens（屏幕视觉流）+ Hearsay（WASAPI 音频 + faster-whisper），新增薄层 `app/course_session/`。全部本地（Ollama `localhost:11434`），无云端 API，截图不落盘。

## 架构

```
start_course.ps1
  └─ python -m app.course_session        # GUI (gui.py) 或直接 CourseSession
       ├─ AudioBridge (audio_bridge.py)  # 包装 Hearsay AudioBridge
       │    ├─ WASAPI loopback → chunks → faster-whisper (turbo, zh, cuda)
       │    └─ transcript events → NoteEngine
       ├─ VisualStream (visual.py)       # 包装 QLens pipeline
       │    ├─ Region.grab() → in-memory frame
       │    ├─ OpenCV absdiff 门控（连续帧，不落盘）
       │    └─ 变化超阈值才调 Ollama VLM → VisualEvent
       ├─ NoteEngine (note_engine.py)    # 增量融合（复用常驻 qwen3-vl）
       │    └─ 每 FUSION_INTERVAL_S 将新 transcript+visual 融合进 course_state / live_notes.md
       ├─ FinalSummary (final_summary.py)# 课后总结：先释放 VLM，再独占加载 qwen38-27b
       └─ Storage (storage.py)           # sessions/<ts>/ 下 events/transcript/notes/summary
```

模型分工（RTX 5070 12GB）：

| 阶段 | 模型 | 说明 |
|---|---|---|
| 实时视觉 | `qwen3-vl:8b`（回退 `qwen3-vl:4b`） | 门控后单次调用，图像 1024px |
| 实时融合 | 同 `qwen3-vl:8b`（`FUSION_MODEL`，文本复用已驻留 VLM） | 不另载 27B |
| 课后总结 | `qwen38-27b-main:latest` | 先 `释放实时视觉模型` 再加载 |
| Whisper | `faster-whisper turbo`, language=zh, device=auto | Hearsay venv 内置 |

环境变量均由 `settings.py` 读取，`start_course.ps1` 提供默认值。

## Upstream 最小修改（权威 diff）

本仓库 vendored 上游后移除了嵌套 `.git`，**无法在本仓库内直接 `git diff` 重放**。
下列内容为：

1. `docs/qlens.patch` / `docs/hearsay.patch`：**记录用补丁文本**（UTF-8），
   可在 `git clone` 上游对应 SHA 后尝试 `git apply`（上下文冲突时以本文件代码为准）。
2. 下方内嵌 diff：**从当前 vendored 文件 vs 上游基线语义整理的可核对修改说明**，
   不是保证 `git apply` 一次成功的严格 unified diff。

基线 SHA：

- qlens：`https://github.com/tomaszwi66/qlens.git` @ `9df009501d713dda4fb3cc29128e7a3629bc756d`
- Hearsay：`https://github.com/parkscloud/Hearsay.git` @ `4d5a56d76ed67b6af9d823ef1c9691b9e5dc907c`

Hearsay：`git status --short` 为空——**源码零修改**；`docs/hearsay.patch` 为空记录
（说明见文件头注释，空文件本身不是“唯一证据”）。

### 1) `third_party/qlens/config.py`

```diff
-OLLAMA_URL = "http://localhost:11434/api/chat"
-MODEL_NAME = "qwen2.5vl:7b"
-TEXT_MODEL_NAME = "qwen2.5:7b"  # for video-frame aggregation (text-only)
+import os  # Course-app: model names must be configurable (documented upstream diff)
+
+OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
+MODEL_NAME = os.environ.get("VISION_MODEL", "qwen2.5vl:7b")
+TEXT_MODEL_NAME = os.environ.get("TEXT_MODEL_NAME", "qwen2.5:7b")
+FALLBACK_VISION_MODEL = os.environ.get("FALLBACK_VISION_MODEL", "qwen3-vl:4b")
-REQUEST_TIMEOUT = 120
+REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "120"))
```

### 2) `third_party/qlens/core/ollama_client.py`

`chat()`：签名新增可选 `think` / `num_predict` / `num_ctx`（默认 `None`，不传时行为与上游完全一致）。

```diff
 def chat(image_bgr, system_prompt, user_prompt, temperature=0.1, as_json=True,
-         timeout=None, model=None) -> str:
+         timeout=None, model=None,
+         think: Optional[bool] = None, num_predict: Optional[int] = None,
+         num_ctx: Optional[int] = None) -> str:
     ...
     if as_json:
         payload["format"] = "json"
+    if think is not None:
+        payload["think"] = think
+    if num_predict is not None:
+        payload["options"]["num_predict"] = num_predict
+    if num_ctx is not None:
+        payload["options"]["num_ctx"] = num_ctx
```

`chat_text()`：签名新增可选 `model` / `keep_alive` / `think` / `num_predict` / `num_ctx`。

```diff
-def chat_text(system_prompt, user_prompt, temperature=0.3, timeout=None) -> str:
+def chat_text(system_prompt, user_prompt, temperature=0.3, timeout=None,
+              model=None, keep_alive=None,
+              think=None, num_predict=None, num_ctx=None) -> str:
     payload = {
-        "model": TEXT_MODEL_NAME,
+        "model": model or TEXT_MODEL_NAME,
         ...
     }
+    if keep_alive is not None:
+        payload["keep_alive"] = keep_alive
+    if think is not None:
+        payload["think"] = think
+    if num_predict is not None:
+        payload["options"]["num_predict"] = num_predict
+    if num_ctx is not None:
+        payload["options"]["num_ctx"] = num_ctx
```

### 3) `third_party/Hearsay`

源码零修改（vendoring 时对基线 SHA 的 `git status --short` 为空）。
Whisper 模型落盘在 `%APPDATA%\Hearsay\models`（运行时数据，非源码）。

### 4) 本应用层（非上游）

应用层 `app/course_session/` 新增代码量以 `Get-ChildItem app/course_session/*.py`
统计行数为准（约 1600+ 行，随稳定性修复变动；**不引用任何未经实测的行数数字**）。
E2E 使用桌面 SlideDeck + TTS 产生真实屏幕/音频输入，**不是**自建 fixture 单测。

`app/course_session/settings.py`：

- `_ensure_cuda_dlls()`：import 时把 `.venv/Lib/site-packages/nvidia/{cublas,cudnn,cuda_nvrtc}/bin` 加入 PATH + `os.add_dll_directory`，解决无系统 CUDA toolkit 时 faster-whisper 报缺 `cublas64_12.dll`。
- `NUM_CTX_LIVE = 8192`、`NUM_CTX_FINAL = 16384`（env 可覆盖）。

`app/course_session/final_summary.py`：`chat_text(..., think=False)` —— 见下节调研结论。

`app/course_session/visual.py` / `note_engine.py`：显式传 `num_ctx`/`num_predict`；视觉 JSON 解析失败时 `as_json=False` 重试一次；融合失败重试一次。

## Ollama think / 上下文调研（关键约束）

Ollama 0.33.3 + qwen 思考模型，本地探针验证 + GitHub issue 确认：

- #17588：部分路径下 `think:false` 被忽略，思考仍生成。
- #14645：`think:false` + `format:"json"` 组合会导致 JSON 进 thinking、content 为空。
- content 为空的根因：思考耗尽 `num_ctx`/`num_predict`（默认 4096 上下文 + 图像 token 不够）。

本地探针得出的可行配方：

| 调用 | 配方 | 结果 |
|---|---|---|
| 视觉（qwen3-vl + image） | **不传 think** + `num_ctx=8192` + `num_predict=8192` + `format:"json"` | JSON 正常，单次 37–53s |
| 融合（qwen3-vl 纯文本） | 不传 think + `num_ctx=8192` + `num_predict=6144` | 正常，27–40s/次 |
| 总结（qwen38-27b） | `think:false` + `num_ctx=16384` + `num_predict=8192` | 关思考生效，126s（关思考前 494s，约 3.9×） |
| 总结（对照） | 默认 think + 同上下文 | 27.6s 短题 / 494s 真实课后总结 |

qwen3-vl 上 `think:false` 无效（思考仍占位），故视觉/融合不传 think；qwen38-27b 上 `think:false` 实测生效（thinking=0）。

## 磁盘要求

- Ollama：`qwen3-vl:8b` 6.1GB + `qwen3-vl:4b` 3.3GB + `qwen38-27b` 10GB +（可选原模型 `qwen2.5vl:7b` 6.0GB / `qwen2.5:7b` 4.7GB）。
- Whisper turbo：`%APPDATA%\Hearsay\models`（经 `HF_ENDPOINT=https://hf-mirror.com` 下载，直连超时）。
- 会话产物：`sessions/<ts>/`（transcript / events / live_notes / course_state / final_summary）。

## 稳定性约束（P1/P2 审计项）

- **模型释放**：不硬编码待释放模型名；用 `settings.loaded_models()` 登记实际调用过的模型，
  经 `ollama_base_url()`（urllib 解析 scheme://host，禁止 `replace("/api/chat","")`）调用
  `/api/generate keep_alive=0`；释放失败只 log，不覆盖总结阶段的原始异常。
- **Worker 门闩**：`WORKER_JOIN_TIMEOUT = REQUEST_TIMEOUT + CLEANUP_GRACE`；
  `stop()` 在加载 `FINAL_MODEL` 前检查 `_lingering_workers()`，有存活线程则拒绝加载 27B。
- **会话隔离**：目录名精确到秒 + 冲突后缀；`sessions/current_session.json` 为 active marker，
  `stop_course.ps1` 优先读 marker，不再只按目录名排序猜测。
- **音频启动回滚**：engine → pipeline → recorder 任一步失败按逆序 teardown；`stop()` 幂等。
- **音频 fatal**：状态机 `RUNNING→STOPPING→FAILED`，停 worker，**不跑**完整 27B 总结，
  写 `partial_notes.md`；GUI 展示 `StopResult` 真实成败。

## 已知限制

- 视觉单次 VLM 37–53s（qwen 思考模型 + 图像），门控（absdiff）保证每 10s 帧只偶尔调用（E2E 88 帧只调 1 次）。
- VRAM 峰值紧：课后 qwen38-27b 11.7GB / 12GB；实时阶段 qwen3-vl:8b 8.6GB，若同时驻留多个模型需先 `ollama stop`。
- Whisper 对口播数学有同音误转（如「判别式→判别是」），融合阶段用视觉公式交叉纠正。
- 网络分区下载需镜像；并发 pull 曾全停滞，改为串行重试（`pull_remaining.ps1`，已完成全部模型）。
