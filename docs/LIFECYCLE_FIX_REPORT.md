# LIFECYCLE_FIX_REPORT — Round 3 生命周期/VRAM 安全修复

日期：2026-09-22 · 环境：Windows 11 · RTX 5070 12GB · Ollama 0.33.3 · 全程 localhost。

原则：只修生命周期/VRAM 安全，不加功能，不引 Bonsai 2，不重构 QLens/Hearsay。

Codex Round 2 报告的 6 个问题 + 4 个硬约束，全部修复如下。

---

## P1-1 AudioBridge worker gate 假阴性

**Root cause**：`audio_bridge.stop()` 在 `join()` 之前就清空 `_recorder/_pipeline/_engine`，
导致 `workers_alive()` 返回空列表，即使线程仍在运行。

**Changed files**：
- `app/course_session/audio_bridge.py`

**Fix**：
- `stop()` 改为：request stop → signal → join loop → `is_alive()` 确认 → 才清引用。
- join 超时（60s recorder / 60s pipeline / 3s poll）后**保留引用**，返回 `cleanup_ok=False`。
- `workers_alive()` 只在 `is_alive()==True` 时报告 worker；引用保留到确认死亡。
- `_rollback_start()` 同样遵循：join 后确认死亡才清引用。
- `stop()` 返回 `bool`（True=全部确认死亡，False=有超时/失败）。

**Test**：Test A、Test H（fake worker stop 后仍 alive → `workers_alive` 非空，refs 保留）。

**Result**：`test_stability.py` PASS。

---

## P1-2 模型释放失败仍加载 FINAL_MODEL

**Root cause**：`_release_tracked_models()` 只发 `/api/generate keep_alive=0`，不验证
Ollama 是否真释放；HTTP 500 或超时后仍继续加载 27B → OOM 风险。

**Changed files**：
- `app/course_session/settings.py`：新增 `list_loaded_models_via_api()`、
  `is_model_resident()`、`CleanupStatus`、`UNLOAD_RETRY_COUNT/DELAY`。
- `app/course_session/final_summary.py`：`release_realtime_models()` 重写为
  unload → `/api/ps` verify → retry → 仍存在则 FAIL；`can_load_final_model()` 门闩。

**Fix**：
- `release_realtime_models(exclude)` 返回 `(success, errors)`；每模型 unload 后查
  `/api/ps`，`UNLOAD_RETRY_COUNT`（默认 3）次仍 resident → 整体 FAIL。
- `can_load_final_model()` 检查 `/api/ps` 中无实时模型（VISION/FUSION/registry 中
  非 FINAL 的模型）才允许加载 27B。
- `generate_final_summary()` 入口 + 释放后双重门闩：任一 FAIL 即 `raise`，不加载 27B。
- 终态只有 `CLEANUP_OK` 或 `CLEANUP_FAILED`；权威依据是 `/api/ps` 而非本地 registry。

**Test**：Test B（unload HTTP 500 → release False + `can_load_final_model` 拒绝）、
Test C（`/api/ps` 仍显示 VLM → release False + 拒绝）。

**Result**：`test_stability.py` PASS。

---

## P1-3 上一节 27B 跨 session 残留

**Root cause**：上一节 `generate_final_summary` 用 `keep_alive="10m"` 加载 27B，
进程不退出则跨 session 残留；`CourseSession.start()` 不检查就启动实时阶段 → OOM。

**Changed files**：
- `app/course_session/final_summary.py`：新增 `preflight_cleanup()`。
- `app/course_session/session.py`：`start()` 开头调用 `preflight_cleanup()`。

**Fix**：
- `preflight_cleanup()` 只查询本应用声明管理的模型（`FINAL_MODEL`/`VISION_MODEL`/
  `FUSION_MODEL`），不碰其他进程的 Ollama 模型（约束 3）。
- 发现管理模型 resident → unload + `/api/ps` verify + retry；仍 resident → 返回
  `(False, "Previous final model is still resident.")`。
- `CourseSession.start()` 在 audio/visual/fusion start **之前**调用；失败则 `raise`
  拒绝启动实时阶段。

**Test**：Test D（`/api/ps` 显示 27B resident → preflight 返回 False）。

**Result**：`test_stability.py` PASS。

---

## P2-1 fuse=False 仍 mark_model_loaded

**Root cause**：`note_engine.fuse()` 在 `chat_text` 返回非空后立即 `mark_model_loaded`，
即使响应不是合法 JSON（`_parse_json_loose` 返回 None）也算“已加载”。

**Changed files**：
- `app/course_session/note_engine.py`

**Fix**：
- `fuse()` 只在响应解析成功（含 `skip=True`）且 `chat_text` 未抛异常时调用
  `mark_model_loaded`。
- 非法 JSON / 空响应 / 异常路径均不登记。

**Test**：Test E（fake `chat_text` 返回垃圾 → `fuse` False 且模型不在 registry）。

**Result**：`test_stability.py` PASS。

---

## P2-2 Audio fatal 不释放 VLM/Fusion

**Root cause**：`_on_audio_fatal` → `stop(wait_final_summary=False, failed=True)` 只停
worker，不执行模型释放 + `/api/ps` 验证；VLM/Fusion 可能驻留 VRAM。

**Changed files**：
- `app/course_session/session.py`：抽出 `_cleanup_core()` 统一清理路径。

**Fix**：
统一路径（约束 4）：
```
stop/fatal/exception
     ↓
stop workers (visual + audio + fusion)
     ↓
join + liveness verify (workers_alive / is_alive)
     ↓
release tracked realtime models (release_realtime_models)
     ↓
/api/ps verify (can_load_final_model)
     ↓
cleanup result (StopResult)
```

- `_cleanup_core(stop_visual, stop_audio, stop_fusion, release_models,
  wait_final_summary, failed)` 是唯一清理入口。
- `stop()` 和 `_on_audio_fatal` 都调用它，参数不同。
- audio fatal 路径：`release_models=True`、`wait_final_summary=False`、`failed=True`
  → 停全部 worker → 释放模型 → `/api/ps` 验证 → 写 `partial_notes.md`。
- cleanup FAILED → `wait_final_summary=False` 硬门闩，绝不加载 27B。

**Test**：Test F（audio fatal → visual stopped）、Test G（audio fatal → release 被调用）。

**Result**：`test_stability.py` PASS。

---

## P2-3 测试恒真断言 / 假 PASS

**Root cause**：
- `test_stability.py` 有 `or True` 恒真断言、`print error 但 exit 0` 模式。
- fake pipeline/recorder 缺 `join` 方法 → `AttributeError` 被吞，测试仍 exit 0。

**Changed files**：
- `test_stability.py`

**Fix**：
- 删除所有 `or True` 断言。
- 所有 fake 实现完整接口：`start/stop/join/is_alive/close/unload`。
- 新增 Test A–H（见上），每项独立 PASS/FAIL 汇总，任一 FAIL 即 `sys.exit(1)`。
- GUI `format_stop_result` 增加文件存在且 `size>0` 才显示“已生成”。

**Test**：Test A–H 全部在 `test_stability.py` 中执行。

**Result**：`test_stability.py` PASS（0 failures）。

---

## VRAM Safety — 什么条件下允许加载 qwen38-27b-main

硬门闩（全部满足才允许）：

1. **Worker 未退出 → 不能进入 final summary**
   - `_lingering_workers()` 为空（visual/fusion/audio 全部 `is_alive()==False`）
   - `AudioBridge.workers_alive()` 为空（引用保留到 join 确认死亡）

2. **模型仍在 /api/ps → 不能进入 final summary**
   - `release_realtime_models()` 返回 `(True, ...)`
   - `can_load_final_model()` 返回 `(True, ...)`
   - `/api/ps` 中无 VISION_MODEL / FUSION_MODEL / registry 中非 FINAL 模型

3. **cleanup 失败 → 不能加载 qwen38-27b-main**
   - `_cleanup_core` 中 `cleanup_status == CLEANUP_OK`
   - `generate_final_summary` 入口 + 释放后双重 `can_load_final_model()` 检查

4. **上一节 27B 仍驻留 → 新 session 不能启动实时阶段**
   - `preflight_cleanup()` 在 `CourseSession.start()` 最先执行
   - `/api/ps` 中管理模型（FINAL/VISION/FUSION）resident → unload + verify
   - 仍 resident → `raise` 拒绝启动

权威事实来源：**Ollama `/api/ps`**，不是本地 registry（registry 只记录“程序认为加载过什么”）。

---

## 本轮测试清单执行记录

| 命令 | 结果 |
|---|---|
| AST parse（app/course_session/*.py） | PASS（12 files） |
| `test_stability.py`（含 Test A–H） | PASS（0 failures，exit 0） |
| `third_party/Hearsay/tests/test_pipeline_writer.py` | PASS（ALL CHECKS PASSED，exit 0） |
| `/api/ps` 残留检查 | `{"models":[]}`（无残留） |

### Round 3 落盘补丁（提交时发现）

1. **`_cleanup_core` 此前未写入 `session.py`**（早先 edit 失败未重试）：`stop()`/`_on_audio_fatal`/Test F–G 均引用该方法。现已补上统一路径，`stop()` 变薄包装。
2. **`/api/ps` 空集 vs 查询失败语义**：`list_loaded_models_via_api` 原先错误与“无模型”都返回 `set()`，导致成功卸载后仍判 cleanup 失败。现改为：查询失败 → `None`；成功且无模型 → `set()`（可加载 27B）。`is_model_resident` / `can_load_final_model` / `release_realtime_models` / `preflight_cleanup` 同步区分。
3. **Test B/C 的 `can_load_final_model` 断言**移入 mock 上下文内，确保断言的是“/api/ps 仍 resident → 拒绝”而非真实空 `/api/ps`。

---

## 交给下一轮 Codex 审核的重点

1. `AudioBridge.stop()` 是否**真**等完 join 才清引用；超时是否**保留**引用并报 cleanup failure。
2. `release_realtime_models` 是否**真**查 `/api/ps`，HTTP 500 / 仍 resident 是否 FAIL 且禁 27B。
3. `preflight_cleanup` 是否**只**处理本应用管理的模型（不误杀其他进程）。
4. `stop/fatal/exception` 是否走**同一个** `_cleanup_core`（无三套释放逻辑）。
5. Test A–H 是否覆盖：hang worker → refs kept；HTTP 500 → no 27B；/api/ps resident → no 27B；
   preflight block start；fuse False → no mark；audio fatal → visual stopped + release called；
   join timeout → refs kept。
6. `format_stop_result` 是否要求文件存在且 `size>0` 才显示“已生成”。
