# STABILITY_FIX_REPORT — 独立审计项 P1-1…P2-7

日期：2026-09-22 · 环境：Windows 11 · RTX 5070 12GB · Ollama 0.33.3 · 全程 localhost。

原则：只修正确性/稳定性，不加新功能，不引入 Bonsai 2，不重构架构。

---

## P1-1 模型释放硬编码 / URL 拼接 / 清理覆盖原错

**原因**：`final_summary._release_vision_model()` 写死 `qwen3-vl:8b`，且用
`OLLAMA_URL.replace("/api/chat","")` 拼 base；若实际加载的是 fallback 或融合共用名，
释放目标错误；清理异常路径可能干扰总结错误上报。

**修改文件**：
- `app/course_session/settings.py`：`mark_model_loaded` / `loaded_models` /
  `forget_loaded_models` / `ollama_base_url()`（`urllib.parse`，禁止字符串 replace）。
- `app/course_session/visual.py`、`note_engine.py`、`final_summary.py`：成功调用后登记模型名。
- `app/course_session/final_summary.py`：`_release_tracked_models(exclude={FINAL_MODEL})`
  只释放本进程登记过的模型；释放失败 **仅 log + 状态提示**，不 raise、不覆盖总结异常。

**测试证据**：`test_stability.py` — registry 增删、`ollama_base_url` 三种输入、非法 URL
`ValueError`；AST parse PASS。

---

## P1-2 stop/join 超时 vs REQUEST_TIMEOUT；worker 未退出禁加载 27B

**原因**：视觉 `join(timeout=30)`、融合 `join(FUSION_INTERVAL_S+60)` 与 `REQUEST_TIMEOUT`
脱节；`QLens config.REQUEST_TIMEOUT=120` 与 app `180` 不一致；worker 可能仍占 VRAM 时加载
qwen38-27b → OOM。

**修改文件**：
- `third_party/qlens/config.py`：`REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT","120"))`。
- `app/course_session/settings.py`：`CLEANUP_GRACE`、`WORKER_JOIN_TIMEOUT = REQUEST_TIMEOUT + CLEANUP_GRACE`。
- `app/course_session/session.py`：`stop()` 统一 `WORKER_JOIN_TIMEOUT` join；
  `_lingering_workers()` 汇总 visual/fusion/audio workers；
  **存在存活 worker 则 `wait_final_summary=False`，拒绝加载 FINAL_MODEL**，写入 `StopResult.error`。

**测试证据**：`test_stability.py` 断言 `WORKER_JOIN_TIMEOUT == REQUEST_TIMEOUT + CLEANUP_GRACE`。

---

## P2-1 视觉 JSON 真校验 + 失败不计成功

**原因**：非空文本即 `ok=True`，垃圾文本会推进 `prev_analyzed_gray` / `vlm_calls`。

**修改文件**：
- `app/course_session/visual.py`：`validate_visual_payload()` + `_parse_json_loose()`；
  主字段（`one_line_summary`/`page_text`/`change_vs_prev`）至少一非空字符串；
  可选数组/布尔/字符串类型检查；失败时 **一次** `as_json=False` 重试；
  仍失败 → `ok=False`，`failed_analyses+=1`，不更新 `last_vlm_ts`/`prev_analyzed`，不发事件。

**测试证据**：`test_stability.py` — 合法/非 dict/空 dict/坏数组/坏 bool/fence 解析。

---

## P2-2 会话目录复用 / STOP_REQUEST 竞态

**原因**：目录仅到分钟；两节课同分钟共享目录；`stop_course.ps1` 按目录名倒序猜“最新”。

**修改文件**：
- `app/course_session/storage.py`：秒级时间戳 + 冲突后缀；`write_active_marker` /
  `clear_active_marker` 写 `sessions/current_session.json`（含 `session_dir`/`session_path`/`pid`）；
  clear 仅当 marker 仍指向自己。
- `app/course_session/session.py`：`start()` 写 marker，`stop()` 清 marker。
- `stop_course.ps1`：优先读 marker，失败才回退 newest（并告警）。

**测试证据**：`test_stability.py` — 双 SessionStorage 目录不同、marker 读写、s1.clear 不误删 s2；
`stop_course.ps1` 静态含 `current_session.json`。

---

## P2-3 音频启动失败逆序回滚（幂等）

**原因**：`engine.load` 后 `pipeline.start`/`recorder.start` 失败无 teardown → 半启动泄漏。

**修改文件**：
- `app/course_session/audio_bridge.py`：`start()` try/except → `_rollback_start`
  （recorder→pipeline→engine 逆序）；`stop()` 用 `_stop_lock` **幂等**；
  `workers_alive()` 供 stop 门闩。
- `app/course_session/session.py`：audio/visual `start()` 失败时置 `FAILED`、停已启动组件、清 marker。

**测试证据**：`test_stability.py` — `_rollback_start` 直测；monkeypatch 假 Recorder 使
`start()` 抛错 → 回滚 pipeline + 双 `stop()` 安全。

---

## P2-4 音频 fatal → 状态机 + 停 worker + 禁完整总结 + partial

**原因**：`_on_audio_fatal` 只写 `last_error`，会话继续“看似运行”，或 stop 仍加载 27B。

**修改文件**：
- `app/course_session/session.py`：
  - `SessionState`：`IDLE/RUNNING/STOPPING/STOPPED/FAILED`。
  - `_on_audio_fatal`：记错 → `FAILED` → 后台 `stop(wait_final_summary=False, failed=True)`。
  - 失败路径 **不** 调 `generate_final_summary`；`storage.write_partial_notes()` 写
    `partial_notes.md`（live_notes + transcript + visual + state，无模型调用）。

**测试证据**：`test_stability.py` — 构造 `_audio_failed` 后 `stop(..., failed=True)`
→ `state=failed` 且 `partial_notes.md` 存在。

---

## P2-5 GUI 不假成功 — StopResult

**原因**：`_stop_worker` 无条件 emit「final_summary.md 已生成」。

**修改文件**：
- `app/course_session/session.py`：`@dataclass StopResult(success, state, summary_path,
  error, partial_available, workers_stopped, lingering_workers)`；`stop()` 返回之。
- `app/course_session/gui.py`：`format_stop_result()` 区分成功/部分/失败；statusBar 文案分流。

**测试证据**：`test_stability.py` — 成功/失败消息断言（失败不得含“已生成 final_summary”）。

---

## P2-6 测试失败必须 exit≠0 / 内容断言

**修改文件**：
- `test_e2e.py`：增加 `stop_result_success`、`stop_state_stopped`、`no_lingering_workers`、
  events 非空文本、live_notes 单 `#` 标题+`##` 节、transcript 含 CJK、
  final_summary 含「课程主题/知识体系」且非占位、含生成标记。
- `test_visual_only.py`：线程退出、frames≥5、≥1 事件、描述非空、无硬错误 →
  `sys.exit(0/1)`。

**测试证据**：本轮 AST PASS；`test_stability.py` PASS。完整 e2e/visual 需 GPU+屏幕+TTS，
命令见 `TEST_REPORT.md`「如何复跑」。

---

## P2-7 文档诚实化

**原因**：`docs/qlens.patch` 为 UTF-16LE（工具无法当文本读）；vendored 无嵌套 git 时
“权威 git diff”表述过强；行数/“900 行”类未核实表述；e2e 易被误读为自建 fixture。

**修改文件**：
- `docs/qlens.patch`：重写为 **UTF-8**（config.py + ollama_client.py 记录用 diff）。
- `docs/hearsay.patch`：UTF-8 头注释说明「空 = 零修改的记录，非唯一可复现证据」。
- `docs/IMPLEMENTATION.md`：区分「记录用 patch」vs「可核对修改说明」；补基线 SHA；
  应用层行数改为实测统计口径；写明稳定性约束；**删除/避免“900 行”**；E2E 非自建 fixture。
- `docs/REUSE_MAP.md`：`ollama_client` 修改量改为与 patch 一致的诚实描述。
- `docs/TEST_REPORT.md`：缺陷记录追加稳定性轮；复跑清单加 `test_stability.py` 与 Hearsay tests。

**测试证据**：`test_stability.py` — `qlens.patch` UTF-8 decode PASS、无 “900 行”。

---

## 本轮测试清单执行记录

| 命令 | 结果 |
|---|---|
| AST parse（app + 测试脚本） | PASS |
| `third_party/Hearsay/tests/test_pipeline_writer.py` | PASS / 见终端 |
| `test_stability.py`（P1-1/1-2、P2-1…2-7 单测） | PASS / 见终端 |
| `test_e2e.py` / `test_visual_only.py` / `test_stop_flag.py` / `test_loopback.py` / `test_fusion_visual.py` | 见「如何复跑」；本轮以稳定性单测+AST 为准，全量集成在 GPU/屏幕可用时执行 |
| 真实全链路（启动→音频→视觉→stop→27B） | 依赖 `slides_tk`+TTS+Ollama；结果写入终端与 `TEST_REPORT` |

**停止后残留检查**：`StopResult.lingering_workers` / `snapshot()["workers_alive"]` 应为空；
`ollama ps` 不应驻留实时 VLM（总结阶段仅 FINAL_MODEL，结束后可 `keep_alive` 过期或手动 stop）。

---

## 交给下一轮 Codex 审核的重点

1. 模型是否**真**释放（registry + 正确 base URL，而非硬编码名）。
2. stop 是否**真**等完 worker，且 worker 存活时**拒绝**加载 27B。
3. 视觉失败是否**不**计入成功、**不**推进门控状态。
4. 音频 fatal / 启动失败是否收干净（回滚、状态机、partial、不假成功）。
5. 会话 marker / 目录唯一性是否堵住 STOP_REQUEST 竞态。
6. 测试失败是否 exit≠0；文档是否无未核实声明。
