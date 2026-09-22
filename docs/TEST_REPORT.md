# 测试报告（TEST_REPORT）

日期：2026-09-22 · 机器：Windows 11 · RTX 5070 12GB / 32GB RAM · Ollama 0.33.3 · 全程 localhost，无云端调用。

## P1 单项验证

| 项 | 结果 | 关键数据 |
|---|---|---|
| QLens `grab()` + `prepare_image()` | PASS | ~48ms，in-memory，无截图落盘 |
| QLens **原生默认模型** `qwen2.5vl:7b` 视觉 chat | PASS | 8.7s，返回正确 JSON |
| QLens 原生文本 `qwen2.5:7b` chat_text | PASS | 6.4s，JSON |
| `qwen3-vl:8b` 视觉 JSON（课程实际用） | PASS | 首次冷载 14.9s；稳定后单次 37–53s |
| Whisper turbo 中文 | PASS | load 2.4s；13.2s 音频转写 7.4s；`lang=zh p=1.00` |
| WASAPI loopback 端到端（`test_loopback.py`） | PASS | 启动 2.6s；5 events / 2 chunks；Remote 标签；transcript 生成；stop 0.2s |
| 融合 NoteEngine（`test_fusion_visual.py`） | PASS | fuse1 40.9s / fuse2 39.2s；topic+concepts+formulas 抽出；`live_notes.md`+`course_state.jsonl` 落盘 |
| 视觉 absdiff 门控（`test_visual_only.py`） | PASS | 88 帧仅 1 次 VLM、跳过 87；首事件 t=32.7s |
| 启动脚本默认模型 | PASS | `qwen3-vl:8b` + `turbo` + `zh`，fallback `qwen3-vl:4b` 已就位 |

## P10 端到端

### test_e2e.py — PASS

模拟真实课（屏幕 SlideDeck 窗口 + 中文 TTS 循环 + Whisper + VLM + 融合 + 课后 27B 总结）：

- 启动 4.0s；live 100s；stop 全流程 578.6s（含 27B 冷载+生成）。
- 100s 内：tr=27、vis=1、vlm=1、skip=62（门控比 1:62）、upd=2、topic=`二次函数`。
- 6 个产物文件全部生成且非空：`events.jsonl` 4661B、`visual_events.jsonl` 980B、`course_state.jsonl` 3364B、`live_notes.md` 2978B、`transcript.md` 1224B、`final_summary.md` 3487B。
- `final_summary.md` 由 `qwen38-27b-main:latest` 生成，含「完整知识体系/核心知识点」结构，对不确定转写标注「需要确认」。
- 该轮 27B 生成耗时 494s（含思考）；修复 `think:false` 后见下表。

### test_stop_flag.py — PASS（STOP_REQUEST 全路径）

- `sessions/2026-09-22_18-58`：写入 `STOP_REQUEST` → 4s 内 `_flag_loop` 检测 → 触发 on_stop_requested → 停视觉/音频 → 最终融合 → qwen38 总结。
- 检查：flag_seen / stopped / transcript / final_summary / live_notes / events **全部 OK**。
- 修复 `think:false` 后课后总结：**126s**（18:59:54 → 19:02:00），相对修复前 494s 约 3.9×。
- 空融合响应自动重试（`note_engine.fuse` 2 次尝试）。

### GUI 冒烟 — PASS

`python -m app.course_session` 启动 8s 存活（pid 134624），仅 Qt DPI 警告（非致命），随后干净 kill。

## VRAM 实测（`vram_bench.py`，2s 采样）

| 状态 | 峰值 |
|---|---|
| 空闲（`ollama stop` 后 0 模型驻留） | 基线 |
| 实时 `qwen3-vl:8b` 驻留 | **8591 MiB** |
| 课后 `qwen38-27b-main:latest` 驻留 | **11694 MiB**（≈11.7GB / 12GB） |

结论：两模型按设计**分时独占**，课后必须先释放 VLM 再加载 27B；同时驻留会 OOM。

## 模型就绪清单（`pull_remaining.ps1` 串行拉取，全部 OK）

`qwen3-vl:8b` 6.1GB · `qwen3-vl:4b` 3.3GB · `qwen38-27b-main:latest` 10GB · `qwen2.5vl:7b` 6.0GB · `qwen2.5:7b` 4.7GB · Whisper turbo（hf-mirror）。

## 缺陷修复记录（测试中发现）

1. 无系统 CUDA → faster-whisper 缺 `cublas64_12.dll`：`_ensure_cuda_dlls()` + pip `nvidia-cublas-cu12`/`nvidia-cudnn-cu12`。
2. qwen 思考模型 content 为空：见 IMPLEMENTATION「think/上下文调研」，视觉/融合加大 `num_ctx`+`num_predict`，总结 `think:false`。
3. `live_notes.md` 标题双重 `#`：session.py 去掉多余前缀。
4. STOP 测试过早断言：`stop()` 在 summary 完成前就置 `running=False`，测试改为轮询 `final_summary.md`。
5. 融合偶发空响应：`fuse()` 增加一次重试。
6. `start_course.ps1` 拼写 `WHISHER_DEVICE` → `WHISPER_DEVICE`。

## 限制 / 未覆盖

- 视觉与融合单次延迟受 qwen 思考模型限制（37–53s / 27–40s），靠门控与 FUSION_INTERVAL_S 控制节奏，未做非思考 VLM 对照。
- Whisper 数学同音误转存在；已靠视觉公式交叉纠正，未测专用领域词表。
- GUI 仅冒烟（启动/存活），未做区域选择等交互自动化；真实上课需人工选屏。
- 并发 pull 曾停滞：现串行脚本验证通过，未再复现排查根因。
- 未跑 Hearsay 自带 `tests/test_pipeline_writer.py`（源码零修改，非本次改动范围）。

## 如何复跑

```powershell
$env:HF_ENDPOINT='https://hf-mirror.com'
.venv\Scripts\python.exe test_loopback.py      # 音频
.venv\Scripts\python.exe test_visual_only.py   # 门控
.venv\Scripts\python.exe test_fusion_visual.py # 融合
.venv\Scripts\python.exe test_e2e.py           # 端到端（~10min）
.venv\Scripts\python.exe test_stop_flag.py     # STOP_REQUEST（~5min）
.venv\Scripts\python.exe vram_bench.py         # VRAM
# 真实启动
.\start_course.ps1   # GUI；结束用 GUI 按钮或 .\stop_course.ps1
```
