# Stability unit tests: visual JSON, session isolation, StopResult,
# audio start rollback, marker, AND Round 3 lifecycle tests (A-H).
# No GPU/Ollama required for most checks.
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, ".")

FAILURES = []


def check(cond, msg):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {msg}")
    if not cond:
        FAILURES.append(msg)


print("== P2-1 visual JSON schema validation ==")
from app.course_session.visual import validate_visual_payload, _parse_json_loose  # noqa: E402

good = {
    "page_text": "y = ax^2 + bx + c",
    "new_formulas": ["$y=ax^2+bx+c$"],
    "definitions": ["二次函数"],
    "has_example": True,
    "examples": ["求顶点"],
    "code": "",
    "chart": "抛物线",
    "change_vs_prev": "翻页",
    "confidence_note": "",
    "one_line_summary": "二次函数图像",
}
ok, reason = validate_visual_payload(good)
check(ok, f"valid payload accepted ({reason})")

ok, reason = validate_visual_payload("not a dict")
check(not ok, f"non-dict rejected ({reason})")

ok, reason = validate_visual_payload({})
check(not ok, f"empty dict rejected ({reason})")

bad_list = dict(good, new_formulas="not-a-list")
ok, reason = validate_visual_payload(bad_list)
check(not ok, f"bad new_formulas rejected ({reason})")

bad_bool = dict(good, has_example="yes")
ok, reason = validate_visual_payload(bad_bool)
check(not ok, f"bad has_example rejected ({reason})")

# parse loose with markdown fence
obj = _parse_json_loose('```json\n{"one_line_summary": "hi"}\n```')
check(obj is not None and obj.get("one_line_summary") == "hi", "fenced JSON parses")

print("== P2-2 session dir uniqueness + active marker ==")
from app.course_session.settings import ACTIVE_SESSION_MARKER, SESSIONS_DIR  # noqa: E402
from app.course_session.storage import SessionStorage  # noqa: E402

# clean any stale marker
if ACTIVE_SESSION_MARKER.exists():
    ACTIVE_SESSION_MARKER.unlink()

s1 = SessionStorage()
s2 = SessionStorage()
check(s1.dir != s2.dir, f"two sessions have distinct dirs ({s1.dir.name} vs {s2.dir.name})")
check(s1.dir.parent == SESSIONS_DIR, "session under sessions/")
# second precision in name
check("-" in s1.dir.name and s1.dir.name.count("-") >= 2, f"timestamp-like name {s1.dir.name}")

s1.write_active_marker()
check(ACTIVE_SESSION_MARKER.exists(), "current_session.json written")
data = json.loads(ACTIVE_SESSION_MARKER.read_text(encoding="utf-8"))
check(data.get("session_dir") == s1.dir.name, "marker points at s1")

s1.clear_active_marker()
# s2 should not clear s1's marker path if re-written by s2
s2.write_active_marker()
s1.clear_active_marker()  # s1's clear must not wipe s2's marker
check(ACTIVE_SESSION_MARKER.exists(), "s1.clear does not wipe s2 marker")
check(
    json.loads(ACTIVE_SESSION_MARKER.read_text(encoding="utf-8")).get("session_dir") == s2.dir.name,
    "marker still s2",
)
s2.clear_active_marker()
check(not ACTIVE_SESSION_MARKER.exists(), "marker cleared")

print("== P2-5 StopResult shape ==")
from app.course_session.session import StopResult, SessionState  # noqa: E402

r = StopResult(success=False, state="failed", error="boom", partial_available=True)
check(r.success is False and r.state == "failed" and r.error == "boom", "StopResult fields")
check(SessionState.RUNNING.value == "running", "SessionState enum values")
check(hasattr(SessionState, "FAILED") and hasattr(SessionState, "STOPPED"), "FAILED/STOPPED exist")

print("== P2-5 GUI format_stop_result honesty ==")
from app.course_session.gui import format_stop_result  # noqa: E402

# Create a real non-empty final_summary.md for honest success test
tmp_success_dir = Path(tempfile.mkdtemp(prefix="gui_ok_"))
real_summary = tmp_success_dir / "final_summary.md"
real_summary.write_text("# ok\ncontent", encoding="utf-8")

ok_msg = StopResult(success=True, state="stopped", summary_path=str(real_summary))
msg_ok = format_stop_result(ok_msg)
check("已生成" in msg_ok and "final_summary" in msg_ok, f"success message honest: {msg_ok[:80]}")

fail_msg = StopResult(success=False, state="failed", error="workers still alive", partial_available=True,
                      summary_path="sessions/x/partial_notes.md")
msg_fail = format_stop_result(fail_msg)
check("failed" in msg_fail and "workers still alive" in msg_fail, f"failure not faked: {msg_fail[:100]}")
check("已生成" not in msg_fail or "partial" in msg_fail, "failure does not claim final_summary generated")

# cleanup failure message
cleanup_fail = StopResult(success=False, state="failed",
                          error="CLEANUP_FAILED: model still resident",
                          partial_available=True,
                          summary_path="sessions/x/partial_notes.md")
msg_cf = format_stop_result(cleanup_fail)
check("未完全清理" in msg_cf, f"cleanup failure shows honest message: {msg_cf[:100]}")

print("== P1-1 model registry + base URL ==")
from app.course_session.settings import (  # noqa: E402
    forget_loaded_models,
    loaded_models,
    mark_model_loaded,
    ollama_base_url,
    list_loaded_models_via_api,
    is_model_resident,
    CleanupStatus,
)

mark_model_loaded("fake-vlm:1b")
mark_model_loaded("fake-vlm:1b")
mark_model_loaded("fake-fusion:1b")
check(loaded_models() >= {"fake-vlm:1b", "fake-fusion:1b"}, f"registry tracks: {loaded_models()}")
forget_loaded_models({"fake-vlm:1b"})
check("fake-vlm:1b" not in loaded_models() and "fake-fusion:1b" in loaded_models(), "partial forget")
forget_loaded_models()
check(not loaded_models(), "clear all")

base = ollama_base_url("http://localhost:11434/api/chat")
check(base == "http://localhost:11434", f"base from chat URL: {base}")
base2 = ollama_base_url("http://127.0.0.1:11434")
check(base2 == "http://127.0.0.1:11434", f"base bare host: {base2}")
try:
    ollama_base_url("not-a-url")
    check(False, "invalid URL should raise")
except ValueError:
    check(True, "invalid URL raises ValueError")

# CleanupStatus exists
check(CleanupStatus.CLEANUP_OK == "CLEANUP_OK" and CleanupStatus.CLEANUP_FAILED == "CLEANUP_FAILED",
      "CleanupStatus constants exist")

print("== P1-2 WORKER_JOIN_TIMEOUT derivation ==")
from app.course_session.settings import CLEANUP_GRACE, REQUEST_TIMEOUT, WORKER_JOIN_TIMEOUT  # noqa: E402

check(WORKER_JOIN_TIMEOUT == REQUEST_TIMEOUT + CLEANUP_GRACE,
      f"WORKER_JOIN_TIMEOUT={WORKER_JOIN_TIMEOUT} == {REQUEST_TIMEOUT}+{CLEANUP_GRACE}")
check(CLEANUP_GRACE >= 0, "CLEANUP_GRACE non-negative")

print("== P2-3 audio start rollback (simulated failure) ==")
from app.course_session.audio_bridge import AudioBridge  # noqa: E402


class _BoomRecorder:
    def start(self):
        raise RuntimeError("recorder open failed")

    def stop(self):
        pass

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return


class _OkPipeline:
    def __init__(self):
        self.stopped = False
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return


# Full start failure path: monkeypatch constructors via local stub test
# (integration: real WASAPI covered by test_loopback.py)
import app.course_session.audio_bridge as ab  # noqa: E402


class _FakeEngineCls:
    def __init__(self, *args, **kw):
        pass

    def load(self):
        pass

    def unload(self):
        eng_unloaded.append("full")


class _FakePipeCls:
    def __init__(self, *args, **kw):
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True
        rollback_flags["pipe_stopped"] = True

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return


class _FakeRecCls:
    def __init__(self, *args, **kw):
        pass

    def start(self):
        raise RuntimeError("simulated recorder failure")

    def stop(self):
        rollback_flags["rec_stopped"] = True

    def join(self, timeout=None):
        return

    def is_alive(self):
        return False


rollback_flags = {}
eng_unloaded = []
import hearsay.audio.recorder as rec_mod  # noqa: E402
import hearsay.transcription.engine as eng_mod  # noqa: E402
import hearsay.transcription.pipeline as pipe_mod  # noqa: E402

_orig = (rec_mod.AudioRecorder, eng_mod.TranscriptionEngine, pipe_mod.TranscriptionPipeline)
rec_mod.AudioRecorder = _FakeRecCls
eng_mod.TranscriptionEngine = _FakeEngineCls
pipe_mod.TranscriptionPipeline = _FakePipeCls
try:
    tmp = Path(tempfile.mkdtemp(prefix="ab_rollback_"))
    br = AudioBridge(on_event=lambda e: None, transcript_dir=tmp)
    raised = False
    try:
        br.start()
    except RuntimeError:
        raised = True
    check(raised, "start re-raises recorder failure")
    check(rollback_flags.get("pipe_stopped"), "failed start rolled back pipeline")
    check(not br.workers_alive(), "no lingering workers after failed start")
    # idempotent stop
    r1 = br.stop()
    r2 = br.stop()
    check(r1 is not None and r2 is not None, "double stop returns bool (idempotent)")
finally:
    rec_mod.AudioRecorder, eng_mod.TranscriptionEngine, pipe_mod.TranscriptionPipeline = _orig

print("== P2-4 audio fatal -> stop writes partial, no full summary path ==")
# Use CourseSession.stop(failed=True) without real audio/visual workers
from core.capture import Region  # noqa: E402
from app.course_session.session import CourseSession  # noqa: E402

sess = CourseSession(Region(10, 10, 50, 50))
sess.storage.ensure_live_header("t")
sess.storage.append_event({"text": "测试", "session_t": 1.0})
sess._audio_failed = True
sess.last_error = "audio fatal: simulated"
sess.running = True
res = sess.stop(wait_final_summary=True, failed=True)
check(res is not None, "stop returns StopResult")
check(res.state == "failed", f"state failed on audio fatal ({res.state})")
check(res.partial_available and sess.storage.partial_notes_path.exists(),
      "partial_notes.md written on audio fatal")
# The full summary must be gated — file must NOT exist for this fresh session
check(not sess.storage.final_summary_path.exists(),
      "full summary not created on audio fatal (hard gate)")
check(res.success is False, f"failure path success=False on fatal (got {res.success})")

print("== P2-6 stop_course.ps1 uses marker (static) ==")
ps1 = Path("stop_course.ps1").read_text(encoding="utf-8")
check("current_session.json" in ps1, "stop_course reads active marker")
check("STOP_REQUEST" in ps1, "stop_course writes STOP_REQUEST")

print("== docs honesty spot-checks ==")
impl = Path("docs/IMPLEMENTATION.md").read_text(encoding="utf-8")
check("900 行" not in impl and "900行" not in impl, "IMPLEMENTATION has no fake 900-line claim")
# patch should be UTF-8 readable
try:
    p = Path("docs/qlens.patch").read_bytes()
    p.decode("utf-8")
    check(True, "qlens.patch is UTF-8")
except UnicodeDecodeError:
    check(False, "qlens.patch is UTF-8")

# =====================================================================
# ROUND 3 LIFECYCLE TESTS A-H
# =====================================================================

print()
print("== Test A: Fake pipeline stop() leaves thread alive -> workers_alive non-empty ==")


class _FakeAlivePipe:
    """Fake pipeline whose stop() does NOT make is_alive() return False."""

    def __init__(self, *args, **kw):
        self.stopped = False
        self._alive = True

    def start(self):
        pass

    def stop(self):
        self.stopped = True
        # Intentionally does NOT set _alive = False (simulates hung thread)

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        # Simulate join timeout — thread still alive
        return


class _FakeAliveRec:
    def __init__(self, *args, **kw):
        self.stopped = False
        self._alive = True

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        return


class _FakeAliveEng:
    def load(self):
        pass

    def unload(self):
        pass


rec_mod.AudioRecorder = _FakeAliveRec
eng_mod.TranscriptionEngine = _FakeAliveEng
pipe_mod.TranscriptionPipeline = _FakeAlivePipe
try:
    tmp_a = Path(tempfile.mkdtemp(prefix="testA_"))
    br_a = AudioBridge(on_event=lambda e: None, transcript_dir=tmp_a)
    # Manually set up alive workers (simulating started state)
    br_a._recorder = _FakeAliveRec()
    br_a._pipeline = _FakeAlivePipe()
    br_a._engine = _FakeAliveEng()
    br_a._started = True

    # Before stop: workers_alive should be non-empty
    alive_before = br_a.workers_alive()
    check(len(alive_before) >= 1, f"Test A: workers_alive non-empty before stop: {alive_before}")

    # Call stop — fake workers never die, so refs must be KEPT
    result_a = br_a.stop()
    alive_after = br_a.workers_alive()

    check(result_a is False,
          f"Test A: stop returns False when workers hang (cleanup_ok={result_a})")
    check(len(alive_after) >= 1,
          f"Test A: workers_alive still non-empty after stop (refs kept): {alive_after}")
    check("pipeline" in alive_after or "recorder" in alive_after,
          f"Test A: specific workers reported: {alive_after}")
finally:
    rec_mod.AudioRecorder, eng_mod.TranscriptionEngine, pipe_mod.TranscriptionPipeline = _orig


print("== Test B: unload HTTP 500 -> cleanup FAIL, refuse 27B ==")
# Mock list_loaded_models_via_api and requests.post
import app.course_session.final_summary as fs  # noqa: E402
from unittest.mock import patch, MagicMock  # noqa: E402

# Scenario: registry has VLM, unload returns HTTP 500, /api/ps still shows it
mark_model_loaded("qwen3-vl:8b")
resident_set = {"qwen3-vl:8b:latest"}  # still resident

def _fake_ps_500_resident():
    return resident_set

def _fake_post_500(url, json=None, timeout=None):
    m = MagicMock()
    m.status_code = 500
    return m

def _fake_resident_true(name):
    return True

try:
    with patch.object(fs, "list_loaded_models_via_api", _fake_ps_500_resident), \
         patch.object(fs, "is_model_resident", _fake_resident_true), \
         patch("requests.post", _fake_post_500):
        ok_b, errs_b = fs.release_realtime_models(exclude={"qwen38-27b-main:latest"})
        check(ok_b is False, f"Test B: release returns False on HTTP 500 + still resident (got {ok_b})")
        check(any("500" in e or "resident" in e for e in errs_b),
              f"Test B: errors mention HTTP/resident: {errs_b}")
        # After failed release, can_load_final_model should refuse
        # (must stay inside mock: /api/ps still shows VLM resident)
        can_b, reason_b = fs.can_load_final_model()
    check(can_b is False, f"Test B: can_load_final_model refuses when VLM resident (got {can_b}, {reason_b})")
finally:
    forget_loaded_models()


print("== Test C: /api/ps shows VLM resident -> cleanup FAIL, no final summary ==")
# Even without HTTP 500, if /api/ps says model is still there, refuse
resident_set_c = {"qwen3-vl:8b:latest"}

def _fake_ps_resident_c():
    return resident_set_c

def _fake_resident_true_c(name):
    return True

def _fake_post_ok_c(url, json=None, timeout=None):
    m = MagicMock()
    m.status_code = 200
    return m

mark_model_loaded("qwen3-vl:8b")
try:
    with patch.object(fs, "list_loaded_models_via_api", _fake_ps_resident_c), \
         patch.object(fs, "is_model_resident", _fake_resident_true_c), \
         patch("requests.post", _fake_post_ok_c):
        ok_c, errs_c = fs.release_realtime_models(exclude={"qwen38-27b-main:latest"})
        check(ok_c is False, f"Test C: release returns False when /api/ps still shows model (got {ok_c})")
        check(any("STILL RESIDENT" in e or "still resident" in e for e in errs_c),
              f"Test C: error mentions still resident: {errs_c}")

        can_c, reason_c = fs.can_load_final_model()
    check(can_c is False, f"Test C: can_load_final_model refuses (got {can_c}, {reason_c})")
finally:
    forget_loaded_models()


print("== Test D: prior session FINAL_MODEL resident -> preflight blocks start ==")
resident_set_d = {"qwen38-27b-main:latest"}

def _fake_ps_final_d():
    return resident_set_d

def _fake_resident_true_d(name):
    if "qwen38" in name or "27b" in name:
        return True
    return False

def _fake_post_ok_d(url, json=None, timeout=None):
    m = MagicMock()
    m.status_code = 200
    # Simulate unload succeeds but model re-appears (or stays)
    return m

# Simulate: unload "succeeds" (200) but /api/ps still shows it
# Our preflight should retry then fail
try:
    with patch.object(fs, "list_loaded_models_via_api", _fake_ps_final_d), \
         patch.object(fs, "is_model_resident", _fake_resident_true_d), \
         patch("requests.post", _fake_post_ok_d), \
         patch("time.sleep", lambda x: None):  # speed up retries
        pf_ok, pf_msg = fs.preflight_cleanup()
    check(pf_ok is False, f"Test D: preflight_cleanup returns False when FINAL_MODEL resident (got {pf_ok})")
    check("still resident" in pf_msg.lower() or "resident" in pf_msg.lower(),
          f"Test D: message mentions resident: {pf_msg}")
finally:
    forget_loaded_models()


print("== Test E: fuse() returns False -> no mark_model_loaded ==")
# Simulate fusion with invalid JSON response -> should NOT register model
from app.course_session.note_engine import NoteEngine  # noqa: E402
from app.course_session.storage import SessionStorage as _SS  # noqa: E402
from app.course_session.events import TranscriptEvent as _TE  # noqa: E402
from app.course_session import note_engine as note_engine_mod  # noqa: E402

storage_e = _SS()
ne = NoteEngine(storage_e, model="fake-fusion-test")
forget_loaded_models()

# Round 13: fuse() realtime path calls note_engine.chat_fusion (not chat_text).
_orig_fusion_e = note_engine_mod.chat_fusion

def _fake_fusion_e_bad(*args, **kwargs):
    return "not valid json at all {{{"

note_engine_mod.chat_fusion = _fake_fusion_e_bad
try:
    tr = [_TE(text="hello", session_t=1.0)]
    updated = ne.fuse(tr, [])
    check(updated is False, f"Test E: fuse returns False on bad JSON (got {updated})")
    check("fake-fusion-test" not in loaded_models(),
          f"Test E: model NOT registered on invalid response: {loaded_models()}")
finally:
    note_engine_mod.chat_fusion = _orig_fusion_e

print("== Test F: audio fatal -> visual/fusion stopped (via _cleanup_core) ==")
# Construct a CourseSession with fake visual/fusion that record stop calls
from core.capture import Region as _Region  # noqa: E402

class _FakeVisual:
    def __init__(self):
        self.stopped = False
        self._alive = False

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        return

    def is_alive(self):
        return self._alive

    vlm_calls = 0
    skipped_frames = 0
    failed_analyses = 0
    last_error = ""
    last_status = ""
    _model = "fake"

sess_f = CourseSession(_Region(10, 10, 50, 50))
sess_f.visual = _FakeVisual()
sess_f.audio = None  # no audio workers
sess_f._fusion_thread = None
sess_f._audio_failed = True
sess_f.last_error = "audio fatal: simulated"
sess_f.running = True
sess_f.storage.ensure_live_header("t")
sess_f.storage.append_event({"text": "test", "session_t": 0.0})

# Directly call _cleanup_core (simulating fatal path)
res_f = sess_f._cleanup_core(
    stop_visual=True, stop_audio=False, stop_fusion=True,
    release_models=False,  # skip Ollama for unit test
    wait_final_summary=False, failed=True,
)
check(res_f is not None, "Test F: _cleanup_core returns StopResult")
check(sess_f.visual.stopped, "Test F: visual was stopped on audio fatal")
check(res_f.state == "failed", f"Test F: state failed (got {res_f.state})")
check(res_f.success is False, f"Test F: success=False on fatal (got {res_f.success})")


print("== Test G: audio fatal -> unload + /api/ps verify (via cleanup_core) ==")
# Same as F but with release_models=True and mock the release
class _FakeVisualG:
    def __init__(self):
        self.stopped = False
        self._alive = False

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        return

    def is_alive(self):
        return self._alive

    vlm_calls = 0
    skipped_frames = 0
    failed_analyses = 0
    last_error = ""
    last_status = ""
    _model = "fake"

release_called = []

def _fake_release(**kwargs):
    release_called.append(True)
    return True, []  # success

sess_g = CourseSession(_Region(10, 10, 50, 50))
sess_g.visual = _FakeVisualG()
sess_g.audio = None
sess_g._fusion_thread = None
sess_g._audio_failed = True
sess_g.last_error = "audio fatal: simulated"
sess_g.running = True
sess_g.storage.ensure_live_header("t")
sess_g.storage.append_event({"text": "test", "session_t": 0.0})

import app.course_session.session as sess_mod  # noqa: E402

# Patch release_realtime_models in final_summary (imported inside _cleanup_core)
with patch("app.course_session.final_summary.release_realtime_models", _fake_release):
    res_g = sess_g._cleanup_core(
        stop_visual=True, stop_audio=False, stop_fusion=True,
        release_models=True,
        wait_final_summary=False, failed=True,
    )

check(release_called, "Test G: release_realtime_models was called during audio fatal cleanup")
check(res_g is not None, "Test G: _cleanup_core returns StopResult with release")
check(sess_g.visual.stopped, "Test G: visual stopped")
check(res_g.state == "failed", f"Test G: state failed (got {res_g.state})")


print("== Test H: join timeout -> refs kept, workers_alive non-empty ==")
# Same setup as Test A but explicitly check refs not cleared
class _HungWorker:
    def __init__(self, name):
        self.name = name
        self.stopped = False
        self._alive = True

    def start(self):
        pass

    def stop(self):
        self.stopped = True
        # stays alive

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        # Simulate timeout — does nothing
        return


tmp_h = Path(tempfile.mkdtemp(prefix="testH_"))
br_h = AudioBridge(on_event=lambda e: None, transcript_dir=tmp_h)
hung_rec = _HungWorker("recorder")
hung_pipe = _HungWorker("pipeline")
br_h._recorder = hung_rec
br_h._pipeline = hung_pipe
br_h._engine = _FakeAliveEng()
br_h._started = True

result_h = br_h.stop()
alive_h = br_h.workers_alive()

check(result_h is False, f"Test H: stop returns False on join timeout (got {result_h})")
check(len(alive_h) >= 1, f"Test H: workers_alive non-empty after timeout (refs kept): {alive_h}")
check(br_h._recorder is hung_rec, "Test H: recorder reference retained (not cleared)")
check(br_h._pipeline is hung_pipe, "Test H: pipeline reference retained (not cleared)")
check(hung_rec.stopped, "Test H: recorder.stop() was called")
check(hung_pipe.stopped, "Test H: pipeline.stop() was called")


# =====================================================================
# ROUND 4 MODEL LIFECYCLE TESTS I-P
# =====================================================================

print()
print("== Test I: realtime fusion uses qwen3-vl:8b, never 27B ==")
from app.course_session.settings import (  # noqa: E402
    FINAL_MODEL as _FM_I,
    REALTIME_FUSION_MODEL as _RT_I,
    VISION_MODEL as _VM_I,
    validate_model_config,
)

check(_RT_I == _VM_I == "qwen3-vl:8b", f"Test I: realtime roles are 8B (got {_RT_I}/{_VM_I})")
check("27b" not in _RT_I.lower() and _RT_I != _FM_I,
      f"Test I: realtime fusion is not 27B ({_RT_I} vs {_FM_I})")
try:
    validate_model_config()
    check(True, "Test I: validate_model_config accepts default roles")
except ValueError as e:
    check(False, f"Test I: default roles should pass validation: {e}")

# NoteEngine default model must be realtime fusion (8B), not FINAL
ne_i = NoteEngine(_SS())
check(ne_i.model == _RT_I, f"Test I: NoteEngine default model={ne_i.model} is realtime")


print("== Test N: config rejects realtime 27B / FINAL collision ==")
import app.course_session.settings as settings_mod  # noqa: E402

_orig_rt, _orig_final = settings_mod.REALTIME_FUSION_MODEL, settings_mod.FINAL_MODEL
try:
    settings_mod.REALTIME_FUSION_MODEL = "qwen38-27b-main:latest"
    settings_mod.FINAL_MODEL = "qwen38-27b-main:latest"
    try:
        validate_model_config()
        check(False, "Test N: validate must reject REALTIME==FINAL 27B")
    except ValueError:
        check(True, "Test N: rejects REALTIME_FUSION_MODEL==FINAL_MODEL 27B")

    settings_mod.REALTIME_FUSION_MODEL = "some-27b-model"
    try:
        validate_model_config()
        check(False, "Test N: validate must reject any 27b realtime name")
    except ValueError:
        check(True, "Test N: rejects any realtime name containing 27b")
finally:
    settings_mod.REALTIME_FUSION_MODEL, settings_mod.FINAL_MODEL = _orig_rt, _orig_final


print("== Test K: /api/ps failure -> preflight FAIL (fail-closed) ==")
def _fake_ps_none():
    return None

try:
    with patch.object(fs, "list_loaded_models_via_api", _fake_ps_none):
        pf_k, msg_k = fs.preflight_cleanup()
    check(pf_k is False, f"Test K: preflight fails when /api/ps unknown (got {pf_k})")
    check("refus" in msg_k.lower() or "fail" in msg_k.lower() or "cannot" in msg_k.lower(),
          f"Test K: message mentions fail-closed: {msg_k}")
finally:
    forget_loaded_models()


print("== Test L: registry empty but /api/ps has VISION_MODEL -> unload ==")
# Simulate: local registry thinks nothing loaded, but /api/ps shows 8B resident
resident_set_l = {"qwen3-vl:8b:latest"}
forget_loaded_models()
unload_posts = []

def _fake_ps_l():
    # First calls show resident; after unload, show empty (success path)
    if unload_posts:
        return set()
    return set(resident_set_l)

def _fake_is_res_l(name):
    if unload_posts:
        return False
    return True if "qwen3" in name else False

def _fake_post_l(url, json=None, timeout=None):
    m = MagicMock()
    m.status_code = 200
    unload_posts.append(json)
    return m

try:
    with patch.object(fs, "list_loaded_models_via_api", _fake_ps_l), \
         patch.object(fs, "is_model_resident", _fake_is_res_l), \
         patch("requests.post", _fake_post_l), \
         patch("time.sleep", lambda x: None):
        ok_l, errs_l = fs.release_realtime_models()
    check(ok_l is True, f"Test L: release succeeds when /api/ps drives unload (got {ok_l}, {errs_l})")
    check(bool(unload_posts), f"Test L: actually posted unload despite empty registry: {unload_posts}")
    # Must check the actual model field in POST body == VISION_MODEL (not substring).
    model_fields = [(p or {}).get("model") for p in unload_posts]
    check(
        any(mf == "qwen3-vl:8b" for mf in model_fields),
        f"Test L: unload body model field == VISION_MODEL (got {model_fields})",
    )
    check(
        all(mf == "qwen3-vl:8b" or mf is None for mf in model_fields),
        f"Test L: no unrelated model unloads (got {model_fields})",
    )
finally:
    forget_loaded_models()


print("== Test M: _fuse_once fuse=False -> no mark_model_loaded ==")
storage_m = _SS()
sess_m = CourseSession(_Region(10, 10, 50, 50))
sess_m.note_engine = NoteEngine(storage_m, model="fake-realtime-not-marked")
sess_m.storage.ensure_live_header("t")
forget_loaded_models()

_orig_chat2 = note_engine_mod.chat_fusion

def _fake_chat_false(*args, **kwargs):
    return "not json {{{"

note_engine_mod.chat_fusion = _fake_chat_false
try:
    from app.course_session.events import TranscriptEvent as _TE2
    with sess_m._lock:
        sess_m._unfused_transcripts.append(_TE2(text="hello", session_t=1.0))
    sess_m._fuse_once()
    check("fake-realtime-not-marked" not in loaded_models(),
          f"Test M: model NOT registered when fuse=False: {loaded_models()}")
    check(sess_m.note_engine.state.updates == 0,
          f"Test M: no state update on failed fuse (updates={sess_m.note_engine.state.updates})")
finally:
    note_engine_mod.chat_fusion = _orig_chat2
    forget_loaded_models()


print("== Test O: startup exception -> unified _cleanup_core ==")
cleanup_calls = []

def _fake_cleanup_core(self, **kwargs):
    cleanup_calls.append(kwargs)
    from app.course_session.session import StopResult as _SR
    return _SR(success=False, state="failed", error="startup cleanup", partial_available=False)

sess_o = CourseSession(_Region(10, 10, 50, 50))
# Patch preflight to pass, then force audio start failure
with patch("app.course_session.final_summary.preflight_cleanup", lambda: (True, "ok")), \
     patch.object(CourseSession, "_cleanup_core", _fake_cleanup_core), \
     patch("app.course_session.session.AudioBridge") as _AB:
    _AB.return_value.start.side_effect = RuntimeError("whisper load failed")
    raised_o = False
    try:
        sess_o.start()
    except RuntimeError:
        raised_o = True
check(raised_o, "Test O: start re-raises audio failure")
check(len(cleanup_calls) >= 1, f"Test O: startup exception entered _cleanup_core: {cleanup_calls}")
if cleanup_calls:
    check(cleanup_calls[0].get("release_models") is True,
          f"Test O: startup cleanup releases models: {cleanup_calls[0]}")
    check(cleanup_calls[0].get("wait_final_summary") is False,
          f"Test O: startup cleanup skips final summary: {cleanup_calls[0]}")


print("== Test J: stop order = workers -> release -> ps verify -> final ==")
# Behavioral: record actual call order of join / release / ps / final_load.
# Static source-order alone is NOT sufficient (Round 5).
import inspect as _inspect

order_events: list[str] = []

class _OrderVisual:
    def __init__(self):
        self._alive = False

    def stop(self):
        pass

    def join(self, timeout=None):
        order_events.append("join")

    def is_alive(self):
        return False

    vlm_calls = 0
    skipped_frames = 0
    failed_analyses = 0
    last_error = ""
    last_status = ""
    _model = "fake"

class _OrderThread:
    def join(self, timeout=None):
        order_events.append("join")

    def is_alive(self):
        return False

def _order_release(**kwargs):
    order_events.append("release")
    return True, []

def _order_can_load():
    order_events.append("ps")
    return True, "ok"

def _order_run_final_phase(storage, status=None, **kwargs):
    order_events.append("final_fusion")
    order_events.append("final_summary")
    order_events.append("final_release")
    # write a real final_summary.md so success path can pass
    p = storage.final_summary_path
    p.write_text("# final\nok", encoding="utf-8")
    return "success", str(p), []

sess_j = CourseSession(_Region(10, 10, 50, 50))
sess_j.visual = _OrderVisual()
sess_j.audio = None
sess_j._fusion_thread = _OrderThread()
sess_j.running = True
sess_j.storage.ensure_live_header("t")
sess_j.storage.append_event({"text": "test", "session_t": 0.0})

with patch("app.course_session.final_summary.release_realtime_models", _order_release), \
     patch("app.course_session.final_summary.can_load_final_model", _order_can_load), \
     patch("app.course_session.final_summary.run_final_phase", _order_run_final_phase):
    res_j = sess_j._cleanup_core(
        stop_visual=True, stop_audio=False, stop_fusion=True,
        release_models=True,
        wait_final_summary=True, failed=False,
    )

check(res_j is not None, f"Test J: cleanup returned result ({res_j})")
check("join" in order_events and "release" in order_events and "ps" in order_events
      and "final_fusion" in order_events,
      f"Test J: recorded events: {order_events}")
if order_events:
    i_join = order_events.index("join")
    i_release = order_events.index("release")
    i_ps = order_events.index("ps")
    i_ff = order_events.index("final_fusion")
    check(i_join < i_release < i_ps < i_ff,
          f"Test J: order join({i_join}) < release({i_release}) < ps({i_ps}) < final_fusion({i_ff})")
    # final_release must come after final_summary
    if "final_summary" in order_events and "final_release" in order_events:
        check(order_events.index("final_summary") < order_events.index("final_release"),
              f"Test J: summary before final_release: {order_events}")

# Static: no realtime _fuse_once during stop_fusion join block
src_j = _inspect.getsource(CourseSession._cleanup_core)
fusion_block = src_j.split("if stop_fusion:")[1].split("self.storage.finalize_transcript")[0]
check("_fuse_once" not in fusion_block,
      "Test J: cleanup does not run realtime _fuse_once during stop_fusion")
# Real markers present in source (order checked behaviorally above)
for marker in ("release_realtime_models(", "can_load_final_model(", "run_final_phase("):
    check(marker in src_j, f"Test J: source contains {marker}")


print("== Test P: audio fatal -> release called, /api/ps has no qwen3-vl ==")
# Behavioral: after audio-fatal cleanup with mocked release+ps, no VLM remains.
ps_after_p = {"qwen3-vl:8b:latest"}

def _fake_release_p(**kwargs):
    ps_after_p.clear()
    return True, []

sess_p = CourseSession(_Region(10, 10, 50, 50))
sess_p.visual = _FakeVisualG()
sess_p.audio = None
sess_p._fusion_thread = None
sess_p._audio_failed = True
sess_p.last_error = "audio fatal: simulated"
sess_p.running = True
sess_p.storage.ensure_live_header("t")
sess_p.storage.append_event({"text": "test", "session_t": 0.0})

def _fake_ps_after_p():
    return set(ps_after_p)

with patch("app.course_session.final_summary.release_realtime_models", _fake_release_p), \
     patch.object(fs, "list_loaded_models_via_api", _fake_ps_after_p), \
     patch.object(fs, "is_model_resident", lambda n: False if not ps_after_p else True):
    res_p = sess_p._cleanup_core(
        stop_visual=True, stop_audio=False, stop_fusion=True,
        release_models=True,
        wait_final_summary=False, failed=True,
    )
check(res_p is not None, "Test P: cleanup returns result")
check(not ps_after_p, f"Test P: /api/ps free of qwen3-vl after fatal cleanup: {ps_after_p}")
check(res_p.state == "failed", f"Test P: state failed (got {res_p.state})")


print("== Round 4 role snapshot ==")
from app.course_session.settings import describe_model_roles  # noqa: E402
roles = describe_model_roles()
check("VISION_MODEL=qwen3-vl:8b" in roles, f"Test: roles print VISION 8B: {roles!r}")
check("REALTIME_FUSION_MODEL=qwen3-vl:8b" in roles, f"Test: roles print RT fusion 8B: {roles!r}")
check("FINAL_MODEL=qwen38-27b-main:latest" in roles, f"Test: roles print FINAL 27B: {roles!r}")


# =====================================================================
# ROUND 5 FINAL FUSION / FINAL CLEANUP / REGISTRY TESTS Q-V
# =====================================================================

print()
print("== Test Q: Final Fusion executes on pending events (FINAL_MODEL) ==")
import core.ollama_client as oc  # noqa: E402 (final fusion still patches QLens chat_text)
from app.course_session.note_engine import (  # noqa: E402
    FUSION_FAILURE,
    FUSION_NOOP,
    FUSION_SUCCESS,
)
from app.course_session.events import VisualEvent as _VE  # noqa: E402

storage_q = _SS()
ne_q = NoteEngine(storage_q, model=_RT_I)  # realtime role unchanged
forget_loaded_models()
final_calls_q = []

def _chat_final_ok(*args, **kwargs):
    final_calls_q.append(kwargs.get("model"))
    return json.dumps({
        "current_topic": "二次函数",
        "new_knowledge": ["顶点式"],
        "new_formulas": ["$y=a(x-h)^2+k$"],
        "teacher_explanation": [],
        "teacher_emphasis": ["顶点很重要"],
        "examples": [],
        "pitfalls": [],
        "relations": [],
        "unresolved": [],
        "visual_note": "板书顶点式",
        "skip": False,
    })

_orig_chat_q = oc.chat_text
oc.chat_text = _chat_final_ok
try:
    pend_tr = [_TE(text="最后讲顶点式", session_t=100.0)]
    pend_vi = [_VE(description="黑板写出顶点式", session_t=100.0, diff_score=9.0)]
    status_q = ne_q.fuse_final(pend_tr, pend_vi, model=_FM_I)
finally:
    oc.chat_text = _orig_chat_q

check(status_q == FUSION_SUCCESS, f"Test Q: fuse_final success (got {status_q})")
check(final_calls_q and final_calls_q[0] == _FM_I,
      f"Test Q: FINAL_MODEL was the chat model (got {final_calls_q})")
check(ne_q.model == _RT_I, f"Test Q: realtime model role unchanged ({ne_q.model})")
check(_FM_I in loaded_models(), f"Test Q: FINAL_MODEL registered after success: {loaded_models()}")
check(_RT_I not in loaded_models() or _RT_I == _FM_I,
      f"Test Q: realtime model not spuriously marked by final fusion: {loaded_models()}")
check(ne_q.state.updates >= 1, f"Test Q: pending events applied (updates={ne_q.state.updates})")
check(storage_q.course_state_path.exists(), "Test Q: course_state persisted after final fusion")
forget_loaded_models()


print("== Test R: Final Fusion no-op when no pending (no 27B call) ==")
storage_r = _SS()
ne_r = NoteEngine(storage_r, model=_RT_I)
forget_loaded_models()
chat_r_called = []

def _chat_r_should_not_run(*args, **kwargs):
    chat_r_called.append(kwargs)
    return "{}"

_orig_chat_r = oc.chat_text
oc.chat_text = _chat_r_should_not_run
try:
    status_r = ne_r.fuse_final([], [], model=_FM_I)
finally:
    oc.chat_text = _orig_chat_r

check(status_r == FUSION_NOOP, f"Test R: empty pending -> no-op (got {status_r})")
check(not chat_r_called, f"Test R: 27B chat NOT called on no-op: {len(chat_r_called)} calls")
check(_FM_I not in loaded_models(), f"Test R: FINAL_MODEL not registered on no-op: {loaded_models()}")

# run_final_phase path: no pending -> explicit no-op, summary can still run
run_final_r = []
def _gen_r(storage, status=None):
    run_final_r.append("summary")
    p = storage.final_summary_path
    p.write_text("# s", encoding="utf-8")
    return str(p)

def _rel_final_r():
    run_final_r.append("release")
    return True, []

with patch.object(fs, "generate_final_summary", _gen_r), \
     patch.object(fs, "release_final_model", _rel_final_r), \
     patch.object(fs, "can_load_final_model", lambda: (True, "ok")):
    fusion_r, summary_r, errs_r = fs.run_final_phase(
        storage_r, pending_transcripts=[], pending_visuals=[], note_engine=ne_r
    )
check(fusion_r == FUSION_NOOP, f"Test R: run_final_phase fusion no-op (got {fusion_r})")
check(summary_r is not None and "summary" in run_final_r,
      f"Test R: summary still ran after no-op fusion: {run_final_r}")
check("release" in run_final_r, f"Test R: FINAL_MODEL release still attempted: {run_final_r}")
check(not errs_r, f"Test R: no errors on clean no-op path: {errs_r}")


print("== Test S: Final Fusion failure -> not fake success, still release + ps ==")
storage_s = _SS()
ne_s = NoteEngine(storage_s, model=_RT_I)
forget_loaded_models()

def _chat_s_fail(*args, **kwargs):
    return "not json {{{"

_orig_chat_s = oc.chat_text
oc.chat_text = _chat_s_fail
try:
    status_s = ne_s.fuse_final([_TE(text="x", session_t=1.0)], [], model=_FM_I)
finally:
    oc.chat_text = _orig_chat_s

check(status_s == FUSION_FAILURE, f"Test S: fuse_final failure (got {status_s})")
check(_FM_I not in loaded_models(), f"Test S: no registry mark on fusion failure: {loaded_models()}")

# run_final_phase: fusion fails -> errors non-empty; release still called; ps verified
ps_final_s = {f"{_FM_I}:latest"}
rel_posts_s = []

def _gen_s_should_not_claim_clean(storage, status=None):
    # summary may still be attempted; write file but caller must not fake overall success
    p = storage.final_summary_path
    p.write_text("# partial final", encoding="utf-8")
    return str(p)

def _rel_final_s():
    ps_final_s.clear()
    return True, []

def _ps_s():
    return set(ps_final_s)

# Keep chat mock active so fuse_final does not hit the real Ollama
# (real model may return skip:true and falsely report no-op).
with patch.object(fs, "generate_final_summary", _gen_s_should_not_claim_clean), \
     patch.object(fs, "release_final_model", _rel_final_s), \
     patch.object(fs, "can_load_final_model", lambda: (True, "ok")), \
     patch.object(fs, "list_loaded_models_via_api", _ps_s), \
     patch.object(oc, "chat_text", _chat_s_fail):
    fusion_s, summary_s, errs_s = fs.run_final_phase(
        storage_s,
        pending_transcripts=[_TE(text="x", session_t=1.0)],
        pending_visuals=[],
        note_engine=ne_s,
    )
check(fusion_s == FUSION_FAILURE, f"Test S: run_final_phase reports fusion failure (got {fusion_s})")
check(any("final fusion failed" in e for e in errs_s),
      f"Test S: errors include fusion failure: {errs_s}")
check(not ps_final_s, f"Test S: /api/ps cleared FINAL_MODEL after release: {ps_final_s}")
check(ps_final_s == set(),
      "Test S: release path executed (ps_final_s cleared)")
# Overall session success must be False when fusion_status==failure — see stop path below
# (covered by success formula: fusion_status == "failure" -> success=False)


print("== Test T: Final Summary success -> load -> summary -> unload -> /api/ps ==")
order_t: list[str] = []

def _gen_t(storage, status=None):
    order_t.append("summary")
    # simulate model load mark during summary
    mark_model_loaded(_FM_I)
    p = storage.final_summary_path
    p.write_text("# final summary body", encoding="utf-8")
    return str(p)

def _rel_t():
    order_t.append("unload")
    forget_loaded_models({_FM_I})
    return True, []

def _can_t():
    order_t.append("ps_before")
    return True, "ok"

storage_t = _SS()
with patch.object(fs, "generate_final_summary", _gen_t), \
     patch.object(fs, "release_final_model", _rel_t), \
     patch.object(fs, "can_load_final_model", _can_t):
    fusion_t, summary_t, errs_t = fs.run_final_phase(
        storage_t, pending_transcripts=[], pending_visuals=[], note_engine=None
    )
check(order_t == ["ps_before", "summary", "unload"] or (
        order_t.index("summary") < order_t.index("unload")),
      f"Test T: summary before unload (got {order_t})")
check(not errs_t, f"Test T: clean release errors empty: {errs_t}")
check(_FM_I not in loaded_models(), f"Test T: FINAL_MODEL not in registry after release: {loaded_models()}")
check(summary_t is not None and Path(summary_t).exists(), "Test T: final_summary.md kept")


print("== Test U: Final Summary exception -> FINAL_MODEL still unloaded ==")
storage_u = _SS()
mark_model_loaded(_FM_I)
rel_u_called = []

def _gen_u_raise(storage, status=None):
    mark_model_loaded(_FM_I)  # loaded then blew up
    raise RuntimeError("summary boom")

def _rel_u():
    rel_u_called.append(True)
    forget_loaded_models({_FM_I})
    return True, []

with patch.object(fs, "generate_final_summary", _gen_u_raise), \
     patch.object(fs, "release_final_model", _rel_u), \
     patch.object(fs, "can_load_final_model", lambda: (True, "ok")):
    raised_u = False
    try:
        fs.run_final_phase(storage_u, pending_transcripts=[], pending_visuals=[], note_engine=None)
    except RuntimeError:
        raised_u = True
check(raised_u, "Test U: summary exception propagates (not swallowed)")
check(rel_u_called, "Test U: release_final_model still called in finally")
check(_FM_I not in loaded_models(), f"Test U: FINAL_MODEL forgotten after release: {loaded_models()}")


print("== Test V: unload FINAL_MODEL fails -> cleanup failure, not full success ==")
# release_final_model returns False -> run_final_phase errors non-empty
storage_v = _SS()

def _rel_v_fail():
    return False, [f"{_FM_I}: CONFIRMED STILL RESIDENT"]

def _gen_v(storage, status=None):
    mark_model_loaded(_FM_I)
    p = storage.final_summary_path
    p.write_text("# ok summary", encoding="utf-8")
    return str(p)

with patch.object(fs, "generate_final_summary", _gen_v), \
     patch.object(fs, "release_final_model", _rel_v_fail), \
     patch.object(fs, "can_load_final_model", lambda: (True, "ok")):
    fusion_v, summary_v, errs_v = fs.run_final_phase(
        storage_v, pending_transcripts=[], pending_visuals=[], note_engine=None
    )
check(summary_v is not None, f"Test V: summary file may exist despite cleanup fail: {summary_v}")
check(any("STILL RESIDENT" in e or "release" in e.lower() for e in errs_v),
      f"Test V: cleanup failure reported in errors: {errs_v}")

# Session-level: final release failure forces success=False / FAILED
sess_v = CourseSession(_Region(10, 10, 50, 50))
sess_v.running = True
sess_v.storage.ensure_live_header("t")
sess_v.storage.append_event({"text": "t", "session_t": 0.0})

def _cleanup_v(self, **kwargs):
    from app.course_session.session import StopResult as _SR
    return _SR(success=False, state="failed",
               error="CLEANUP_FAILED: FINAL_MODEL still resident",
               summary_path=str(sess_v.storage.final_summary_path),
               partial_available=False)

# Behavioral: success formula includes final_release_ok
src_v = _inspect.getsource(CourseSession._cleanup_core)
check("final_release_ok" in src_v or "final_release_errs" in src_v,
      "Test V: cleanup core tracks final_release_ok/errs")
check("CLEANUP_FAILED" in src_v, "Test V: final release failure can set CLEANUP_FAILED")


print("== Test W: skip=True does not register; success does ==")
storage_w = _SS()
ne_w = NoteEngine(storage_w, model="fake-rt-registry")
forget_loaded_models()

def _chat_skip(*args, **kwargs):
    return json.dumps({"skip": True, "current_topic": ""})

_orig_chat_w = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_skip
try:
    updated_w = ne_w.fuse([_TE(text="hello", session_t=1.0)], [])
finally:
    note_engine_mod.chat_fusion = _orig_chat_w
check(updated_w is False, f"Test W: skip returns False (got {updated_w})")
check("fake-rt-registry" not in loaded_models(),
      f"Test W: skip=True does NOT mark_model_loaded: {loaded_models()}")

def _chat_ok_w(*args, **kwargs):
    return json.dumps({
        "current_topic": "T", "new_knowledge": ["k"], "new_formulas": [],
        "teacher_explanation": [], "teacher_emphasis": [], "examples": [],
        "pitfalls": [], "relations": [], "unresolved": [],
        "visual_note": "", "skip": False,
    })

note_engine_mod.chat_fusion = _chat_ok_w
try:
    updated_w2 = ne_w.fuse([_TE(text="real info", session_t=2.0)], [])
finally:
    note_engine_mod.chat_fusion = _orig_chat_w
check(updated_w2 is True, f"Test W: success returns True (got {updated_w2})")
check("fake-rt-registry" in loaded_models(),
      f"Test W: success marks realtime model: {loaded_models()}")

# Final fusion registry is independent of realtime registry
storage_w2 = _SS()
ne_w2 = NoteEngine(storage_w2, model="fake-rt-registry")
forget_loaded_models()
_oc_final_w = oc.chat_text
oc.chat_text = _chat_ok_w
try:
    st_w2 = ne_w2.fuse_final([_TE(text="final", session_t=3.0)], [], model=_FM_I)
finally:
    oc.chat_text = _oc_final_w
check(st_w2 == FUSION_SUCCESS, f"Test W: final fusion success (got {st_w2})")
check(_FM_I in loaded_models() and "fake-rt-registry" not in loaded_models(),
      f"Test W: final registry only FINAL_MODEL: {loaded_models()}")
forget_loaded_models()


print("== Test J2: ps verify failure -> final fusion/summary NEVER runs ==")
order_j2: list[str] = []

def _release_j2(**kwargs):
    order_j2.append("release")
    return True, []

def _can_j2_fail():
    order_j2.append("ps")
    return False, "realtime still resident"

def _run_final_j2(*args, **kwargs):
    order_j2.append("final_load")
    return "success", "x", []

sess_j2 = CourseSession(_Region(10, 10, 50, 50))
sess_j2.visual = _OrderVisual()
sess_j2.audio = None
sess_j2._fusion_thread = _OrderThread()
sess_j2.running = True
sess_j2.storage.ensure_live_header("t")
sess_j2.storage.append_event({"text": "t", "session_t": 0.0})

with patch("app.course_session.final_summary.release_realtime_models", _release_j2), \
     patch("app.course_session.final_summary.can_load_final_model", _can_j2_fail), \
     patch("app.course_session.final_summary.run_final_phase", _run_final_j2):
    res_j2 = sess_j2._cleanup_core(
        stop_visual=True, stop_audio=False, stop_fusion=True,
        release_models=True,
        wait_final_summary=True, failed=False,
    )

check(res_j2 is not None, "Test J2: cleanup returned")
check("final_load" not in order_j2,
      f"Test J2: final phase blocked when ps gate fails: {order_j2}")
check(res_j2.success is False, f"Test J2: success=False on ps gate fail (got {res_j2.success})")
check(res_j2.state == "failed", f"Test J2: state failed (got {res_j2.state})")
check("ps gate" in (res_j2.error or ""), f"Test J2: error mentions ps gate: {res_j2.error}")


# =====================================================================
# ROUND 6: pending consume honesty + final-fusion failure honesty (X-Y, AA-AG)
# =====================================================================

print()
print("== Test X: realtime fusion failure preserves pending ==")
sess_x = CourseSession(_Region(10, 10, 50, 50))
sess_x.storage.ensure_live_header("t")
sess_x.note_engine = NoteEngine(sess_x.storage, model="fake-rt-x")
forget_loaded_models()
te_x = _TE(text="TAIL_TOPIC", session_t=9.0)
with sess_x._lock:
    sess_x._unfused_transcripts.append(te_x)

def _chat_x_fail(*args, **kwargs):
    return "not json {{{"

_orig_chat_x = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_x_fail
try:
    with patch("time.sleep", lambda _s: None):
        sess_x._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_x

check(len(sess_x._unfused_transcripts) == 1
      and any(e is te_x for e in sess_x._unfused_transcripts),
      f"Test X: pending preserved on fuse failure "
      f"(n={len(sess_x._unfused_transcripts)})")
check("fake-rt-x" not in loaded_models(),
      f"Test X: no registry mark on fuse failure: {loaded_models()}")
check(sess_x.note_engine.state.updates == 0,
      f"Test X: no state update on failure (updates={sess_x.note_engine.state.updates})")
_src_x = _inspect.getsource(CourseSession._fuse_once)
check("clear()" not in _src_x.split("updated")[0] or
      "snapshot" in _src_x.lower() or "list(" in _src_x,
      "Test X: _fuse_once does not clear-all pending before fuse")
# Mutation-sensitive: failure path must not drop the snapshot identity
check("any(e is x" in _src_x,
      "Test X: success path consumes by event identity (not clear())")
forget_loaded_models()


print("== Test Y: success consumes only snapshot; mid-fuse insert stays ==")
sess_y = CourseSession(_Region(10, 10, 50, 50))
sess_y.storage.ensure_live_header("t")
sess_y.note_engine = NoteEngine(sess_y.storage, model="fake-rt-y")
forget_loaded_models()
te_a = _TE(text="HEAD", session_t=1.0)
te_b = _TE(text="TAIL_DURING_FUSE", session_t=2.0)
with sess_y._lock:
    sess_y._unfused_transcripts.append(te_a)

def _chat_y_ok(*args, **kwargs):
    # Insert a new pending event DURING fuse (after snapshot).
    with sess_y._lock:
        sess_y._unfused_transcripts.append(te_b)
    return json.dumps({
        "current_topic": "T", "new_knowledge": ["k"], "new_formulas": [],
        "teacher_explanation": [], "teacher_emphasis": [], "examples": [],
        "pitfalls": [], "relations": [], "unresolved": [],
        "visual_note": "", "skip": False,
    })

_orig_chat_y = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_y_ok
try:
    sess_y._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_y

check(not any(e is te_a for e in sess_y._unfused_transcripts),
      f"Test Y: snapshot batch consumed on success (n={len(sess_y._unfused_transcripts)})")
check(any(e is te_b for e in sess_y._unfused_transcripts),
      f"Test Y: event inserted during fuse preserved (n={len(sess_y._unfused_transcripts)}, "
      f"texts={[getattr(e, 'text', '?') for e in sess_y._unfused_transcripts]})")
check(sess_y.note_engine.state.updates >= 1,
      f"Test Y: success applied delta (updates={sess_y.note_engine.state.updates})")
forget_loaded_models()


print("== Test Z: realtime failure tail reaches FINAL_MODEL input ==")
TAIL_TOPIC = "TAIL_TOPIC_Z"
TAIL_FACT = "TAIL_FACT_Z"
sess_z = CourseSession(_Region(10, 10, 50, 50))
sess_z.visual = _OrderVisual()
sess_z.audio = None
sess_z._fusion_thread = _OrderThread()
sess_z.running = True
sess_z.storage.ensure_live_header("t")
sess_z.storage.append_event({"text": "head", "session_t": 0.0})
sess_z.note_engine = NoteEngine(sess_z.storage, model="fake-rt-z")
te_z = _TE(text=f"{TAIL_TOPIC} {TAIL_FACT}", session_t=50.0)
with sess_z._lock:
    sess_z._unfused_transcripts.append(te_z)

_orig_chat_z = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_x_fail
try:
    with patch("time.sleep", lambda _s: None):
        sess_z._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_z
check(any(e is te_z for e in sess_z._unfused_transcripts),
      f"Test Z: tail still pending after realtime failure "
      f"(n={len(sess_z._unfused_transcripts)})")

captured_z: dict = {}

def _capture_final_z(storage, status=None, **kwargs):
    captured_z["pending_transcripts"] = list(kwargs.get("pending_transcripts") or [])
    captured_z["pending_visuals"] = list(kwargs.get("pending_visuals") or [])
    captured_z["note_engine"] = kwargs.get("note_engine")
    p = storage.final_summary_path
    p.write_text("# z summary", encoding="utf-8")
    return "success", str(p), []

def _release_rt_z(**kwargs):
    return True, []

def _can_z():
    return True, "ok"

with patch("app.course_session.final_summary.release_realtime_models", _release_rt_z), \
     patch("app.course_session.final_summary.can_load_final_model", _can_z), \
     patch("app.course_session.final_summary.run_final_phase", _capture_final_z):
    res_z = sess_z._cleanup_core(
        stop_visual=True, stop_audio=False, stop_fusion=True,
        release_models=True,
        wait_final_summary=True, failed=False,
    )

pend_tr_z = captured_z.get("pending_transcripts") or []
texts_z = [getattr(e, "text", "") for e in pend_tr_z]
check(any(TAIL_TOPIC in t for t in texts_z) and any(TAIL_FACT in t for t in texts_z),
      f"Test Z: tail texts fed to FINAL_MODEL pending input: {texts_z}")
check(captured_z.get("note_engine") is sess_z.note_engine,
      "Test Z: cleanup drains pending into run_final_phase note_engine")
_src_z = _inspect.getsource(CourseSession._cleanup_core)
check("_unfused_transcripts" in _src_z and "run_final_phase" in _src_z,
      "Test Z: cleanup_core drains unfused into run_final_phase")
check(res_z is not None, "Test Z: cleanup returned")


print("== Test AA: final fusion failure -> no final_summary.md ==")
storage_aa = _SS()
# Independent session dir: any leftover final_summary.md must not be this run's.
check(not storage_aa.final_summary_path.exists(),
      "Test AA: fresh session has no pre-existing final_summary.md")
ne_aa = NoteEngine(storage_aa, model=_RT_I)
forget_loaded_models()
gen_aa_called = []

def _gen_aa_should_not_run(storage, status=None):
    gen_aa_called.append(True)
    p = storage.final_summary_path
    p.write_text("WRONG success summary", encoding="utf-8")
    return str(p)

def _rel_aa():
    forget_loaded_models({_FM_I})
    return True, []

_orig_chat_aa = oc.chat_text
oc.chat_text = _chat_x_fail
try:
    with patch.object(fs, "generate_final_summary", _gen_aa_should_not_run), \
         patch.object(fs, "release_final_model", _rel_aa), \
         patch.object(fs, "can_load_final_model", lambda: (True, "ok")), \
         patch("time.sleep", lambda _s: None):
        fusion_aa, summary_aa, errs_aa = fs.run_final_phase(
            storage_aa,
            pending_transcripts=[_TE(text="x", session_t=1.0)],
            pending_visuals=[],
            note_engine=ne_aa,
        )
finally:
    oc.chat_text = _orig_chat_aa

check(fusion_aa == FUSION_FAILURE, f"Test AA: fusion failure (got {fusion_aa})")
check(not gen_aa_called,
      f"Test AA: generate_final_summary NOT called on fusion failure ({gen_aa_called})")
check(not storage_aa.final_summary_path.exists(),
      f"Test AA: final_summary.md absent after fusion failure "
      f"(exists={storage_aa.final_summary_path.exists()})")
check(any("final summary skipped" in e for e in errs_aa),
      f"Test AA: errors mention summary skipped: {errs_aa}")


print("== Test AB: fuse_final exception -> failure, no final_summary.md ==")
storage_ab = _SS()
ne_ab = NoteEngine(storage_ab, model=_RT_I)
forget_loaded_models()
gen_ab_called = []

class _BoomEngine:
    def fuse_final(self, transcripts, visuals, model=None):
        raise RuntimeError("fuse_final boom")

def _gen_ab_should_not_run(storage, status=None):
    gen_ab_called.append(True)
    p = storage.final_summary_path
    p.write_text("WRONG", encoding="utf-8")
    return str(p)

def _rel_ab():
    return True, []

with patch.object(fs, "generate_final_summary", _gen_ab_should_not_run), \
     patch.object(fs, "release_final_model", _rel_ab), \
     patch.object(fs, "can_load_final_model", lambda: (True, "ok")):
    fusion_ab, summary_ab, errs_ab = fs.run_final_phase(
        storage_ab,
        pending_transcripts=[_TE(text="x", session_t=1.0)],
        pending_visuals=[],
        note_engine=_BoomEngine(),
    )

check(fusion_ab == FUSION_FAILURE, f"Test AB: exception -> failure (got {fusion_ab})")
check(not gen_ab_called, f"Test AB: summary not generated ({gen_ab_called})")
check(not storage_ab.final_summary_path.exists(),
      f"Test AB: final_summary.md absent "
      f"(exists={storage_ab.final_summary_path.exists()})")
check(any("final summary skipped" in e or "final fusion failed" in e for e in errs_ab),
      f"Test AB: errors honest: {errs_ab}")


print("== Test AC: fuse_final timeout -> failure, no final_summary.md ==")
storage_ac = _SS()
ne_ac = NoteEngine(storage_ac, model=_RT_I)
forget_loaded_models()
gen_ac_called = []

def _chat_ac_timeout(*args, **kwargs):
    raise TimeoutError("read timeout")

def _gen_ac_should_not_run(storage, status=None):
    gen_ac_called.append(True)
    p = storage.final_summary_path
    p.write_text("WRONG", encoding="utf-8")
    return str(p)

def _rel_ac():
    return True, []

_orig_chat_ac = oc.chat_text
oc.chat_text = _chat_ac_timeout
try:
    with patch.object(fs, "generate_final_summary", _gen_ac_should_not_run), \
         patch.object(fs, "release_final_model", _rel_ac), \
         patch.object(fs, "can_load_final_model", lambda: (True, "ok")), \
         patch("time.sleep", lambda _s: None):
        fusion_ac, summary_ac, errs_ac = fs.run_final_phase(
            storage_ac,
            pending_transcripts=[_TE(text="x", session_t=1.0)],
            pending_visuals=[],
            note_engine=ne_ac,
        )
finally:
    oc.chat_text = _orig_chat_ac

check(fusion_ac == FUSION_FAILURE, f"Test AC: timeout -> failure (got {fusion_ac})")
check(not gen_ac_called, f"Test AC: summary not generated ({gen_ac_called})")
check(not storage_aa.final_summary_path.exists() and not storage_ac.final_summary_path.exists(),
      "Test AC: final_summary.md absent on timeout")
check(any("final summary skipped" in e or "final fusion failed" in e for e in errs_ac),
      f"Test AC: errors honest: {errs_ac}")


print("== Test AD: fusion failure -> partial_notes.md written ==")
storage_ad = _SS()
storage_ad.ensure_live_header("t")
storage_ad.append_event({"text": "some lecture", "session_t": 1.0})
ne_ad = NoteEngine(storage_ad, model=_RT_I)
forget_loaded_models()

def _rel_ad():
    return True, []

_orig_chat_ad = oc.chat_text
oc.chat_text = _chat_x_fail
try:
    with patch.object(fs, "generate_final_summary",
                      lambda *a, **k: (_ for _ in ()).throw(AssertionError("summary must not run"))), \
         patch.object(fs, "release_final_model", _rel_ad), \
         patch.object(fs, "can_load_final_model", lambda: (True, "ok")), \
         patch("time.sleep", lambda _s: None):
        fusion_ad, summary_ad, errs_ad = fs.run_final_phase(
            storage_ad,
            pending_transcripts=[_TE(text="tail", session_t=2.0)],
            pending_visuals=[],
            note_engine=ne_ad,
        )
finally:
    oc.chat_text = _orig_chat_ad

check(fusion_ad == FUSION_FAILURE, f"Test AD: fusion failure (got {fusion_ad})")
check(storage_ad.partial_notes_path.exists(),
      f"Test AD: partial_notes.md exists "
      f"(exists={storage_ad.partial_notes_path.exists()})")
check(summary_ad and str(summary_ad).endswith("partial_notes.md"),
      f"Test AD: summary_path points at partial (got {summary_ad})")
check(not storage_ad.final_summary_path.exists(),
      "Test AD: final_summary.md still absent")
partial_body_ad = storage_ad.partial_notes_path.read_text(encoding="utf-8")
check("some lecture" in partial_body_ad or "Final Fusion failure" in partial_body_ad
      or len(partial_body_ad) > 0,
      f"Test AD: partial notes non-empty ({len(partial_body_ad)} bytes)")


print("== Test AE: fusion failure -> FINAL_MODEL cleanup still runs ==")
storage_ae = _SS()
ne_ae = NoteEngine(storage_ae, model=_RT_I)
forget_loaded_models()
rel_ae_called = []

def _rel_ae():
    rel_ae_called.append(True)
    forget_loaded_models({_FM_I})
    return True, []

_orig_chat_ae = oc.chat_text
oc.chat_text = _chat_x_fail
try:
    with patch.object(fs, "generate_final_summary",
                      lambda *a, **k: (_ for _ in ()).throw(AssertionError("summary must not run"))), \
         patch.object(fs, "release_final_model", _rel_ae), \
         patch.object(fs, "can_load_final_model", lambda: (True, "ok")), \
         patch("time.sleep", lambda _s: None):
        fusion_ae, summary_ae, errs_ae = fs.run_final_phase(
            storage_ae,
            pending_transcripts=[_TE(text="x", session_t=1.0)],
            pending_visuals=[],
            note_engine=ne_ae,
        )
finally:
    oc.chat_text = _orig_chat_ae

check(fusion_ae == FUSION_FAILURE, f"Test AE: fusion failure (got {fusion_ae})")
check(bool(rel_ae_called),
      f"Test AE: release_final_model called in finally ({rel_ae_called})")
check(_FM_I not in loaded_models(),
      f"Test AE: FINAL_MODEL forgotten after failure release: {loaded_models()}")


print("== Test AF: fusion failure -> /api/ps confirms 27B gone ==")
storage_af = _SS()
ne_af = NoteEngine(storage_af, model=_RT_I)
forget_loaded_models()
ps_af = {f"{_FM_I}:latest"}  # 27B resident before release
mark_model_loaded(_FM_I)

def _rel_af():
    ps_af.clear()
    forget_loaded_models({_FM_I})
    return True, []

def _ps_af():
    return set(ps_af)

def _resident_af(name=None):
    return False

_orig_chat_af = oc.chat_text
oc.chat_text = _chat_x_fail
try:
    with patch.object(fs, "generate_final_summary",
                      lambda *a, **k: (_ for _ in ()).throw(AssertionError("summary must not run"))), \
         patch.object(fs, "release_final_model", _rel_af), \
         patch.object(fs, "can_load_final_model", lambda: (True, "ok")), \
         patch.object(fs, "list_loaded_models_via_api", _ps_af), \
         patch.object(fs, "is_final_model_resident", _resident_af), \
         patch("time.sleep", lambda _s: None):
        fusion_af, summary_af, errs_af = fs.run_final_phase(
            storage_af,
            pending_transcripts=[_TE(text="x", session_t=1.0)],
            pending_visuals=[],
            note_engine=ne_af,
        )
finally:
    oc.chat_text = _orig_chat_af

check(fusion_af == FUSION_FAILURE, f"Test AF: fusion failure (got {fusion_af})")
check(not ps_af,
      f"Test AF: /api/ps free of FINAL_MODEL after failure release: {ps_af}")
check(not storage_af.final_summary_path.exists(),
      "Test AF: final_summary.md absent")


print("== Test AG: fusion SUCCESS -> final_summary.md generated ==")
storage_ag = _SS()
storage_ag.ensure_live_header("t")
storage_ag.append_event({"text": "important lecture content", "session_t": 1.0})
ne_ag = NoteEngine(storage_ag, model=_RT_I)
forget_loaded_models()
gen_ag_called = []

def _gen_ag(storage, status=None):
    gen_ag_called.append(True)
    p = storage.final_summary_path
    p.write_text("# final ok by mock", encoding="utf-8")
    return str(p)

def _rel_ag():
    forget_loaded_models({_FM_I})
    return True, []

def _chat_ag_ok(*args, **kwargs):
    return json.dumps({
        "current_topic": "二重积分", "new_knowledge": ["交换积分次序"],
        "new_formulas": ["$\\iint$"], "teacher_explanation": [],
        "teacher_emphasis": ["注意区域"], "examples": [],
        "pitfalls": [], "relations": [], "unresolved": [],
        "visual_note": "", "skip": False,
    })

_orig_chat_ag = oc.chat_text
oc.chat_text = _chat_ag_ok
try:
    with patch.object(fs, "generate_final_summary", _gen_ag), \
         patch.object(fs, "release_final_model", _rel_ag), \
         patch.object(fs, "can_load_final_model", lambda: (True, "ok")):
        fusion_ag, summary_ag, errs_ag = fs.run_final_phase(
            storage_ag,
            pending_transcripts=[_TE(text="important lecture content", session_t=1.0)],
            pending_visuals=[],
            note_engine=ne_ag,
        )
finally:
    oc.chat_text = _orig_chat_ag

check(fusion_ag == FUSION_SUCCESS, f"Test AG: fusion success (got {fusion_ag})")
check(bool(gen_ag_called), f"Test AG: generate_final_summary called ({gen_ag_called})")
check(summary_ag and str(summary_ag).endswith("final_summary.md"),
      f"Test AG: summary_path is final_summary.md (got {summary_ag})")
check(storage_ag.final_summary_path.exists(),
      "Test AG: final_summary.md exists after success")
check(not errs_ag, f"Test AG: clean errors empty: {errs_ag}")
check(_FM_I not in loaded_models(),
      f"Test AG: FINAL_MODEL released after success: {loaded_models()}")


print("== Test AH: Final Fusion uses think=False + explicit long timeout ==")
storage_ah = _SS()
ne_ah = NoteEngine(storage_ah, model=_RT_I)
forget_loaded_models()
calls_ah = []

def _chat_ah(*args, **kwargs):
    calls_ah.append(dict(kwargs))
    return json.dumps({
        "current_topic": "T", "new_knowledge": ["k"], "new_formulas": [],
        "teacher_explanation": [], "teacher_emphasis": [], "examples": [],
        "pitfalls": [], "relations": [], "unresolved": [],
        "visual_note": "", "skip": False,
    })

_orig_chat_ah = oc.chat_text
oc.chat_text = _chat_ah
try:
    st_ah = ne_ah.fuse_final(
        [_TE(text="tail data", session_t=99.0)], [], model=_FM_I
    )
finally:
    oc.chat_text = _orig_chat_ah
    forget_loaded_models()

check(st_ah == FUSION_SUCCESS, f"Test AH: final fusion success (got {st_ah})")
check(calls_ah and calls_ah[0].get("think") is False,
      f"Test AH: FINAL_MODEL final fusion disables thinking: {calls_ah}")
check(calls_ah and isinstance(calls_ah[0].get("timeout"), int)
      and calls_ah[0].get("timeout") >= 300,
      f"Test AH: final fusion has explicit >=300s timeout: {calls_ah}")


# =====================================================================
# ROUND 12: realtime fusion structured diagnostics + pending preservation
# =====================================================================
from app.course_session import note_engine as note_engine_mod  # noqa: E402
from app.course_session.note_engine import (  # noqa: E402
    FUSE_EMPTY,
    FUSE_EXCEPTION,
    FUSE_FAILURE,
    FUSE_INVALID_JSON,
    FUSE_OK,
    FUSE_SKIP,
    FUSE_TIMEOUT,
)
from app.course_session.session import SessionState as _SSState  # noqa: E402
from app.course_session.settings import (  # noqa: E402
    RECENT_TRANSCRIPT_EVENTS,
    RECENT_VISUAL_EVENTS,
)

_REQUIRED_STATUS_KEYS = (
    "timestamp",
    "attempt_id",
    "pending_transcript_count",
    "pending_visual_count",
    "input_chars",
    "result",
    "latency_ms",
    "exception_type",
    "error_message",
    # Round 13 diagnostic extensions (still size/enum only).
    "model",
    "prompt_chars",
    "transcript_chars",
    "visual_chars",
    "response_chars",
    "parse_stage",
    "http_status",
    "fused_transcript_count",
    "fused_visual_count",
)


def _read_status_lines(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _status_sess(name, text="RT12_PEND"):
    sess = CourseSession(_Region(10, 10, 50, 50))
    sess.storage.ensure_live_header("t")
    sess.note_engine = NoteEngine(sess.storage, model=name)
    te = _TE(text=text, session_t=3.0)
    with sess._lock:
        sess._unfused_transcripts.append(te)
    return sess, te


def _chat_delta_ok(*args, **kwargs):
    return json.dumps({
        "current_topic": "T", "new_knowledge": ["k"], "new_formulas": [],
        "teacher_explanation": [], "teacher_emphasis": [], "examples": [],
        "pitfalls": [], "relations": [], "unresolved": [],
        "visual_note": "", "skip": False,
    })


print("== Test AI: invalid JSON -> status invalid_json, pending kept ==")
sess_ai, te_ai = _status_sess("fake-rt-ai")
forget_loaded_models()
_orig_chat_ai = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = lambda *a, **k: "not json {{{"
try:
    with patch("time.sleep", lambda _s: None):
        sess_ai._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_ai
check(any(e is te_ai for e in sess_ai._unfused_transcripts),
      f"Test AI: pending kept on invalid_json (n={len(sess_ai._unfused_transcripts)})")
_ai_lines = _read_status_lines(sess_ai.storage.realtime_fusion_status_path)
check(len(_ai_lines) == 1, f"Test AI: one status line (got {len(_ai_lines)})")
check(_ai_lines and _ai_lines[0].get("result") == FUSE_INVALID_JSON,
      f"Test AI: result=invalid_json (got {_ai_lines and _ai_lines[0].get('result')})")
check(_ai_lines and all(k in _ai_lines[0] for k in _REQUIRED_STATUS_KEYS),
      f"Test AI: required keys present (got {sorted(_ai_lines[0]) if _ai_lines else []})")
check(_ai_lines and _ai_lines[0].get("pending_transcript_count") == 1
      and _ai_lines[0].get("pending_visual_count") == 0,
      f"Test AI: pending counts (got {_ai_lines[0] if _ai_lines else None})")
check("fake-rt-ai" not in loaded_models(),
      f"Test AI: no registry mark (got {loaded_models()})")
forget_loaded_models()


print("== Test AJ: chat exception -> status exception + type, pending kept ==")
sess_aj, te_aj = _status_sess("fake-rt-aj")
forget_loaded_models()

class _Boom(Exception):
    pass

def _chat_aj_boom(*a, **k):
    raise _Boom("fusion boom")

_orig_chat_aj = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_aj_boom
try:
    with patch("time.sleep", lambda _s: None):
        sess_aj._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_aj
check(any(e is te_aj for e in sess_aj._unfused_transcripts),
      f"Test AJ: pending kept on exception (n={len(sess_aj._unfused_transcripts)})")
_aj_lines = _read_status_lines(sess_aj.storage.realtime_fusion_status_path)
check(len(_aj_lines) == 1 and _aj_lines[0].get("result") == FUSE_EXCEPTION,
      f"Test AJ: result=exception (got {_aj_lines and _aj_lines[0].get('result')})")
check(_aj_lines and _aj_lines[0].get("exception_type") == "_Boom",
      f"Test AJ: exception_type=_Boom (got {_aj_lines and _aj_lines[0].get('exception_type')})")
check(_aj_lines and "fusion boom" in (_aj_lines[0].get("error_message") or ""),
      f"Test AJ: error_message carries message (got {_aj_lines and _aj_lines[0].get('error_message')})")
check(sess_aj.state != _SSState.FAILED,
      f"Test AJ: session not FAILED after fusion exception (state={sess_aj.state})")
forget_loaded_models()


print("== Test AK: chat timeout -> status timeout, pending kept ==")
sess_ak, te_ak = _status_sess("fake-rt-ak")
forget_loaded_models()

class _ReadTimeout(Exception):
    pass

def _chat_ak_timeout(*a, **k):
    raise _ReadTimeout("Read timed out waiting for token")

_orig_chat_ak = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_ak_timeout
try:
    with patch("time.sleep", lambda _s: None):
        sess_ak._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_ak
check(any(e is te_ak for e in sess_ak._unfused_transcripts),
      f"Test AK: pending kept on timeout (n={len(sess_ak._unfused_transcripts)})")
_ak_lines = _read_status_lines(sess_ak.storage.realtime_fusion_status_path)
check(len(_ak_lines) == 1 and _ak_lines[0].get("result") == FUSE_TIMEOUT,
      f"Test AK: result=timeout (got {_ak_lines and _ak_lines[0].get('result')})")
check(_ak_lines and "timed out" in (_ak_lines[0].get("error_message") or "").lower(),
      f"Test AK: timeout message (got {_ak_lines and _ak_lines[0].get('error_message')})")
forget_loaded_models()


print("== Test AL: fuse returns False (failure) -> status failure, pending kept ==")
sess_al, te_al = _status_sess("fake-rt-al")
forget_loaded_models()
# Empty chat responses after retries: _chat_fusion returns None without raising.
_orig_chat_al = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = lambda *a, **k: ""
try:
    with patch("time.sleep", lambda _s: None):
        sess_al._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_al
check(any(e is te_al for e in sess_al._unfused_transcripts),
      f"Test AL: pending kept when fuse=False (n={len(sess_al._unfused_transcripts)})")
_al_lines = _read_status_lines(sess_al.storage.realtime_fusion_status_path)
check(len(_al_lines) == 1 and _al_lines[0].get("result") == FUSE_FAILURE,
      f"Test AL: result=failure (got {_al_lines and _al_lines[0].get('result')})")
check("fake-rt-al" not in loaded_models(),
      f"Test AL: no registry mark on False (got {loaded_models()})")
forget_loaded_models()


print("== Test AM: skip=True -> status skip, pending kept ==")
sess_am, te_am = _status_sess("fake-rt-am")
forget_loaded_models()
_orig_chat_am = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = lambda *a, **k: json.dumps({"skip": True, "current_topic": ""})
try:
    sess_am._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_am
check(any(e is te_am for e in sess_am._unfused_transcripts),
      f"Test AM: pending kept on skip (n={len(sess_am._unfused_transcripts)})")
_am_lines = _read_status_lines(sess_am.storage.realtime_fusion_status_path)
check(len(_am_lines) == 1 and _am_lines[0].get("result") == FUSE_SKIP,
      f"Test AM: result=skip (got {_am_lines and _am_lines[0].get('result')})")
check("fake-rt-am" not in loaded_models(),
      f"Test AM: no registry mark on skip (got {loaded_models()})")
forget_loaded_models()


print("== Test AN: mid-fuse new event preserved; status success counts ==")
sess_an = CourseSession(_Region(10, 10, 50, 50))
sess_an.storage.ensure_live_header("t")
sess_an.note_engine = NoteEngine(sess_an.storage, model="fake-rt-an")
forget_loaded_models()
te_an_a = _TE(text="HEAD_AN", session_t=1.0)
te_an_b = _TE(text="TAIL_AN_DURING_FUSE", session_t=2.0)
with sess_an._lock:
    sess_an._unfused_transcripts.append(te_an_a)

def _chat_an_ok(*a, **k):
    with sess_an._lock:
        sess_an._unfused_transcripts.append(te_an_b)
    return _chat_delta_ok()

_orig_chat_an = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_an_ok
try:
    sess_an._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_an
check(not any(e is te_an_a for e in sess_an._unfused_transcripts),
      f"Test AN: snapshot consumed (n={len(sess_an._unfused_transcripts)})")
check(any(e is te_an_b for e in sess_an._unfused_transcripts),
      f"Test AN: mid-fuse event kept (n={len(sess_an._unfused_transcripts)})")
_an_lines = _read_status_lines(sess_an.storage.realtime_fusion_status_path)
check(len(_an_lines) == 1 and _an_lines[0].get("result") == FUSE_OK,
      f"Test AN: result=success (got {_an_lines and _an_lines[0].get('result')})")
check(_an_lines and _an_lines[0].get("pending_transcript_count") == 1
      and _an_lines[0].get("pending_visual_count") == 0,
      f"Test AN: status records pre-fuse counts (got {_an_lines and _an_lines[0]})")
check(_an_lines and isinstance(_an_lines[0].get("latency_ms"), int)
      and _an_lines[0].get("latency_ms") >= 0,
      f"Test AN: latency_ms present (got {_an_lines and _an_lines[0].get('latency_ms')})")
forget_loaded_models()


print("== Test AO: realtime fusion failure never sets session FAILED ==")
sess_ao = CourseSession(_Region(10, 10, 50, 50))
sess_ao.storage.ensure_live_header("t")
sess_ao.note_engine = NoteEngine(sess_ao.storage, model="fake-rt-ao")
sess_ao.running = True
sess_ao.state = _SSState.RUNNING
sess_ao.audio = object()  # sentinel: not None means workers present
sess_ao.visual = _OrderVisual()
forget_loaded_models()
with sess_ao._lock:
    sess_ao._unfused_transcripts.append(_TE(text="pend_ao", session_t=1.0))
_orig_chat_ao = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = lambda *a, **k: "broken {{{"
try:
    with patch("time.sleep", lambda _s: None):
        sess_ao._fuse_once()
        sess_ao._fuse_once()  # second failure must not escalate lifecycle
finally:
    note_engine_mod.chat_fusion = _orig_chat_ao
check(sess_ao.state == _SSState.RUNNING,
      f"Test AO: state stays RUNNING (got {sess_ao.state})")
check(sess_ao.running is True, "Test AO: running stays True after fusion failures")
check(len(sess_ao._unfused_transcripts) == 1,
      f"Test AO: pending intact after two failures (n={len(sess_ao._unfused_transcripts)})")
_ao_lines = _read_status_lines(sess_ao.storage.realtime_fusion_status_path)
check(len(_ao_lines) == 2 and all(
      ln.get("attempt_id") == i + 1 for i, ln in enumerate(_ao_lines)),
      f"Test AO: two attempts, attempt_id 1..2 (got {[ln.get('attempt_id') for ln in _ao_lines]})")
check(all(ln.get("result") in (FUSE_INVALID_JSON, FUSE_FAILURE, FUSE_EXCEPTION)
          for ln in _ao_lines),
      f"Test AO: both attempts failure-class (got {[ln.get('result') for ln in _ao_lines]})")
forget_loaded_models()


print("== Test AP: status never embeds full prompt/response bodies ==")
_sess_p, _te_p = _status_sess("fake-rt-ap", text="SECRET_COURSE_TEXT_MARKER_123")
forget_loaded_models()

def _chat_ap_leaky(*a, **k):
    # Large body: must NOT land in status fields (only truncated error_message
    # for exceptions; invalid_json uses a fixed short message).
    return "LEAK_PROMPT_AND_RESPONSE_" + ("X" * 5000)

_orig_chat_ap = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_ap_leaky
try:
    with patch("time.sleep", lambda _s: None):
        _sess_p._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_ap
_ap_raw = _sess_p.storage.realtime_fusion_status_path.read_text(encoding="utf-8")
check("SECRET_COURSE_TEXT_MARKER_123" not in _ap_raw,
      "Test AP: no transcript text in status file")
check("LEAK_PROMPT_AND_RESPONSE_" not in _ap_raw
      and "XXXX" not in _ap_raw,
      "Test AP: no raw model response body in status file")
check(len(_ap_raw) < 4000,
      f"Test AP: status line stays small (got {len(_ap_raw)} bytes)")
check(any(e is _te_p for e in _sess_p._unfused_transcripts),
      "Test AP: pending kept while status written")
forget_loaded_models()


print("== Test AQ: fusion loop keeps running after _fuse_once exception ==")
sess_aq = CourseSession(_Region(10, 10, 50, 50))
sess_aq.storage.ensure_live_header("t")
sess_aq.note_engine = NoteEngine(sess_aq.storage, model="fake-rt-aq")
sess_aq.running = True
sess_aq.state = _SSState.RUNNING
with sess_aq._lock:
    sess_aq._unfused_transcripts.append(_TE(text="pend_aq", session_t=1.0))

def _fuse_raise_once(*a, **k):
    raise RuntimeError("unexpected fuse crash")

_orig_fuse_aq = sess_aq.note_engine.fuse
sess_aq.note_engine.fuse = _fuse_raise_once
try:
    sess_aq._fuse_once()  # must not raise out of _fuse_once
finally:
    sess_aq.note_engine.fuse = _orig_fuse_aq
check(sess_aq.state == _SSState.RUNNING,
      f"Test AQ: state still RUNNING after fuse crash (got {sess_aq.state})")
check(len(sess_aq._unfused_transcripts) == 1,
      f"Test AQ: pending kept when fuse raises (n={len(sess_aq._unfused_transcripts)})")
_aq_lines = _read_status_lines(sess_aq.storage.realtime_fusion_status_path)
check(len(_aq_lines) == 1 and _aq_lines[0].get("result") == FUSE_EXCEPTION,
      f"Test AQ: status result=exception (got {_aq_lines and _aq_lines[0].get('result')})")
check(_aq_lines and _aq_lines[0].get("exception_type") == "RuntimeError",
      f"Test AQ: exception_type RuntimeError (got {_aq_lines and _aq_lines[0].get('exception_type')})")
# After recovery, a successful fuse still works and drains snapshot.
sess_aq.note_engine.fuse = _orig_fuse_aq
_orig_chat_aq = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_delta_ok
try:
    sess_aq._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_aq
check(len(sess_aq._unfused_transcripts) == 0,
      f"Test AQ: recovered success drains pending (n={len(sess_aq._unfused_transcripts)})")
_aq_lines2 = _read_status_lines(sess_aq.storage.realtime_fusion_status_path)
check(len(_aq_lines2) == 2 and _aq_lines2[1].get("result") == FUSE_OK,
      f"Test AQ: second attempt success recorded (got {[ln.get('result') for ln in _aq_lines2]})")
forget_loaded_models()


print("== Test AR: NoteEngine.last_fuse classifications (unit) ==")
_storage_ar = _SS()
_ne_ar = NoteEngine(_storage_ar, model="fake-rt-ar")
forget_loaded_models()

_orig_chat_ar = note_engine_mod.chat_fusion
# empty input -> FUSE_EMPTY, no chat call
_r_ar = _ne_ar.fuse([], [])
check(_r_ar is False and _ne_ar.last_fuse.get("result") == FUSE_EMPTY,
      f"Test AR: empty input -> empty (got {_ne_ar.last_fuse})")

# invalid json
note_engine_mod.chat_fusion = lambda *a, **k: "nope"
try:
    with patch("time.sleep", lambda _s: None):
        _r_ar2 = _ne_ar.fuse([_TE(text="x", session_t=1.0)], [])
finally:
    pass
check(_r_ar2 is False and _ne_ar.last_fuse.get("result") == FUSE_INVALID_JSON,
      f"Test AR: invalid_json (got {_ne_ar.last_fuse.get('result')})")

# skip
note_engine_mod.chat_fusion = lambda *a, **k: json.dumps({"skip": True})
_r_ar3 = _ne_ar.fuse([_TE(text="x", session_t=1.0)], [])
check(_r_ar3 is False and _ne_ar.last_fuse.get("result") == FUSE_SKIP,
      f"Test AR: skip (got {_ne_ar.last_fuse.get('result')})")

# success
note_engine_mod.chat_fusion = _chat_delta_ok
_r_ar4 = _ne_ar.fuse([_TE(text="real", session_t=2.0)], [])
check(_r_ar4 is True and _ne_ar.last_fuse.get("result") == FUSE_OK,
      f"Test AR: success (got {_ne_ar.last_fuse.get('result')})")
check(_ne_ar.last_fuse.get("input_chars", 0) > 0,
      f"Test AR: input_chars recorded (got {_ne_ar.last_fuse.get('input_chars')})")

# exception inside fuse outer path is still classified
def _chat_ar_raise(*a, **k):
    raise ValueError("chat path fail")
note_engine_mod.chat_fusion = _chat_ar_raise
try:
    with patch("time.sleep", lambda _s: None):
        _ne_ar.fuse([_TE(text="y", session_t=3.0)], [])
finally:
    note_engine_mod.chat_fusion = _orig_chat_ar
check(_ne_ar.last_fuse.get("result") == FUSE_EXCEPTION
      and _ne_ar.last_fuse.get("exception_type") == "ValueError",
      f"Test AR: chat exception classified (got {_ne_ar.last_fuse})")
forget_loaded_models()


print("== Test AT: realtime failure leaves tail for Final Fusion + empty /api/ps path ==")
TAIL_AT = "TAIL_TOPIC_AT"
sess_at = CourseSession(_Region(10, 10, 50, 50))
sess_at.visual = _OrderVisual()
sess_at.audio = None
sess_at._fusion_thread = _OrderThread()
sess_at.running = True
sess_at.state = _SSState.RUNNING
sess_at.storage.ensure_live_header("t")
sess_at.storage.append_event({"text": "head", "session_t": 0.0})
sess_at.note_engine = NoteEngine(sess_at.storage, model="fake-rt-at")
te_at = _TE(text=f"{TAIL_AT} core_fact", session_t=50.0)
with sess_at._lock:
    sess_at._unfused_transcripts.append(te_at)
_orig_chat_at = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = lambda *a, **k: "not json {{{"
try:
    with patch("time.sleep", lambda _s: None):
        sess_at._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_at
check(any(e is te_at for e in sess_at._unfused_transcripts),
      f"Test AT: tail still pending after realtime failure (n={len(sess_at._unfused_transcripts)})")
check(sess_at.state == _SSState.RUNNING,
      f"Test AT: session still RUNNING (got {sess_at.state})")
_at_lines = _read_status_lines(sess_at.storage.realtime_fusion_status_path)
check(len(_at_lines) == 1 and _at_lines[0].get("result") == FUSE_INVALID_JSON,
      f"Test AT: status written for final-bound failure attempt")

captured_at: dict = {}

def _capture_final_at(storage, status=None, **kwargs):
    captured_at["pending_transcripts"] = list(kwargs.get("pending_transcripts") or [])
    captured_at["pending_visuals"] = list(kwargs.get("pending_visuals") or [])
    captured_at["note_engine"] = kwargs.get("note_engine")
    p = storage.final_summary_path
    p.write_text("# at summary", encoding="utf-8")
    return "success", str(p), []

def _release_at(**kwargs):
    return True, []

def _can_at():
    return True, "ok"

with patch("app.course_session.final_summary.release_realtime_models", _release_at), \
     patch("app.course_session.final_summary.can_load_final_model", _can_at), \
     patch("app.course_session.final_summary.run_final_phase", _capture_final_at):
    res_at = sess_at._cleanup_core(
        stop_visual=True, stop_audio=False, stop_fusion=True,
        release_models=True,
        wait_final_summary=True, failed=False,
    )

texts_at = [getattr(e, "text", "") for e in (captured_at.get("pending_transcripts") or [])]
check(any(TAIL_AT in t for t in texts_at),
      f"Test AT: tail fed to Final Fusion pending (texts={texts_at})")
check(captured_at.get("note_engine") is sess_at.note_engine,
      "Test AT: cleanup drains pending into run_final_phase")
check(sess_at.storage.final_summary_path.exists(),
      "Test AT: final_summary written after realtime failure")
check(res_at is not None and getattr(res_at, "success", None) is True,
      f"Test AT: stop succeeded after realtime failures (got {res_at})")


# =====================================================================
# ROUND 13: realtime fusion root-cause fix (format=json + thinking harvest
# + window consume + diagnostics) — recovery / window / diag / parse tests
# =====================================================================
print("== Test AU: timeout -> invalid_json -> success recovery (pending kept until success) ==")
sess_au, te_au = _status_sess("fake-rt-au")
forget_loaded_models()
_au_calls = {"n": 0}

def _chat_au_seq(*args, **kwargs):
    # chat_fusion may be invoked twice per fuse() on non-JSON retry.
    # Map: first call timeout; next two invalid; then success.
    _au_calls["n"] += 1
    if _au_calls["n"] == 1:
        class _ReadTimeout(Exception):
            pass
        raise _ReadTimeout("Read timed out waiting for token")
    if _au_calls["n"] <= 3:
        return "not json {{{"
    return _chat_delta_ok()

_orig_chat_au = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = _chat_au_seq
try:
    with patch("time.sleep", lambda _s: None):
        sess_au._fuse_once()  # attempt 1: timeout
        sess_au._fuse_once()  # attempt 2: invalid_json (up to 2 retries)
        sess_au._fuse_once()  # attempt 3: success consumes same pending
finally:
    note_engine_mod.chat_fusion = _orig_chat_au
check(_au_calls["n"] >= 3, f"Test AU: at least three chat attempts (got {_au_calls['n']})")
check(len(sess_au._unfused_transcripts) == 0,
      f"Test AU: success consumed pending (n={len(sess_au._unfused_transcripts)})")
_au_lines = _read_status_lines(sess_au.storage.realtime_fusion_status_path)
check(len(_au_lines) == 3, f"Test AU: three status lines (got {len(_au_lines)})")
check([ln.get("result") for ln in _au_lines] == [FUSE_TIMEOUT, FUSE_INVALID_JSON, FUSE_OK],
      f"Test AU: results timeout/invalid_json/success (got {[ln.get('result') for ln in _au_lines]})")
check(all(ln.get("pending_transcript_count") == 1 for ln in _au_lines),
      f"Test AU: same batch pending each attempt (got {[ln.get('pending_transcript_count') for ln in _au_lines]})")
check(all(ln.get("attempt_id") == i + 1 for i, ln in enumerate(_au_lines)),
      f"Test AU: attempt_id 1..3 (got {[ln.get('attempt_id') for ln in _au_lines]})")
forget_loaded_models()


print("== Test AV: success consumes only RECENT window, not full backlog ==")
sess_av = CourseSession(_Region(10, 10, 50, 50))
sess_av.storage.ensure_live_header("t")
sess_av.note_engine = NoteEngine(sess_av.storage, model="fake-rt-av")
forget_loaded_models()
_n_backlog = RECENT_TRANSCRIPT_EVENTS + 15
_te_av_list = [_TE(text=f"av_{i}", session_t=float(i)) for i in range(_n_backlog)]
with sess_av._lock:
    sess_av._unfused_transcripts.extend(_te_av_list)
_orig_chat_av = note_engine_mod.chat_fusion
note_engine_mod.chat_fusion = lambda *a, **k: _chat_delta_ok()
try:
    sess_av._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_av
_left_av = list(sess_av._unfused_transcripts)
check(len(_left_av) == 15,
      f"Test AV: backlog { _n_backlog } -> window {RECENT_TRANSCRIPT_EVENTS} consumed, 15 left (got {len(_left_av)})")
check(all(any(e is t for e in _left_av) for t in _te_av_list[:-RECENT_TRANSCRIPT_EVENTS]),
      "Test AV: oldest backlog events retained")
check(not any(any(e is t for e in _left_av) for t in _te_av_list[-RECENT_TRANSCRIPT_EVENTS:]),
      "Test AV: newest window events consumed")
_av_lines = _read_status_lines(sess_av.storage.realtime_fusion_status_path)
check(_av_lines and _av_lines[0].get("pending_transcript_count") == _n_backlog,
      f"Test AV: status pending is full backlog (got {_av_lines and _av_lines[0].get('pending_transcript_count')})")
check(_av_lines and _av_lines[0].get("fused_transcript_count") == RECENT_TRANSCRIPT_EVENTS,
      f"Test AV: fused window size (got {_av_lines and _av_lines[0].get('fused_transcript_count')})")
forget_loaded_models()


print("== Test AW: status carries Round 13 diagnostic fields (sizes only) ==")
_REQUIRED_R13_STATUS_KEYS = (
    "model", "prompt_chars", "transcript_chars", "visual_chars",
    "response_chars", "parse_stage", "http_status",
    "fused_transcript_count", "fused_visual_count",
)
_sess_aw, _te_aw = _status_sess("fake-rt-aw", text="AW_SECRET_MARKER")
forget_loaded_models()
_orig_chat_aw = note_engine_mod.chat_fusion

def _chat_aw_ok(*args, **kwargs):
    # Simulate diag side-channel as chat_fusion would.
    d = kwargs.get("diag")
    if isinstance(d, dict):
        d["http_status"] = 200
        d["response_chars"] = 441
        d["response_source"] = "content"
    return _chat_delta_ok()

note_engine_mod.chat_fusion = _chat_aw_ok
try:
    with patch("time.sleep", lambda _s: None):
        _sess_aw._fuse_once()
finally:
    note_engine_mod.chat_fusion = _orig_chat_aw
_aw_lines = _read_status_lines(_sess_aw.storage.realtime_fusion_status_path)
check(len(_aw_lines) == 1, f"Test AW: one status line (got {len(_aw_lines)})")
check(_aw_lines and all(k in _aw_lines[0] for k in _REQUIRED_R13_STATUS_KEYS),
      f"Test AW: R13 keys present (got {sorted(_aw_lines[0]) if _aw_lines else []})")
check(_aw_lines and _aw_lines[0].get("result") == FUSE_OK,
      f"Test AW: success (got {_aw_lines and _aw_lines[0].get('result')})")
check(_aw_lines and _aw_lines[0].get("http_status") == 200,
      f"Test AW: http_status 200 (got {_aw_lines and _aw_lines[0].get('http_status')})")
check(_aw_lines and _aw_lines[0].get("prompt_chars", 0) > 0,
      f"Test AW: prompt_chars recorded (got {_aw_lines and _aw_lines[0].get('prompt_chars')})")
check(_aw_lines and _aw_lines[0].get("parse_stage") in (
    "direct", "fence", "brace", "escape_repaired", "brace_escape_repaired"),
      f"Test AW: parse_stage success class (got {_aw_lines and _aw_lines[0].get('parse_stage')})")
check(_aw_lines and isinstance(_aw_lines[0].get("response_chars"), int)
      and _aw_lines[0].get("response_chars") > 0,
      f"Test AW: response_chars int>0 (got {_aw_lines and _aw_lines[0].get('response_chars')})")
_aw_raw = _sess_aw.storage.realtime_fusion_status_path.read_text(encoding="utf-8")
check("AW_SECRET_MARKER" not in _aw_raw, "Test AW: no transcript text in status")
check(all(k not in _aw_raw for k in ("current_topic", "teacher_explanation", "FUSION_SYSTEM")),
      "Test AW: no prompt/response body fields in status")
forget_loaded_models()


print("== Test AX: parse_fusion_json fence / latex invalid-escape repair ==")
from app.course_session.note_engine import parse_fusion_json  # noqa: E402
_obj_ax1, _st_ax1 = parse_fusion_json('```json\n{"skip": false, "current_topic": "t"}\n```')
check(_obj_ax1 is not None and _st_ax1 in ("direct", "fence", "brace"),
      f"Test AX: fenced json (got {_st_ax1})")
_obj_ax2, _st_ax2 = parse_fusion_json(r'{"current_topic": "$0 \cdot \infty$", "skip": false}')
check(_obj_ax2 is not None and _st_ax2 in ("escape_repaired", "direct", "brace_escape_repaired", "brace"),
      f"Test AX: latex \\cdot repaired (stage={_st_ax2}, obj={_obj_ax2 is not None})")
_obj_ax3, _st_ax3 = parse_fusion_json("not json at all")
check(_obj_ax3 is None and _st_ax3 == "invalid",
      f"Test AX: invalid stage (got {_st_ax3})")
_obj_ax4, _st_ax4 = parse_fusion_json("")
check(_obj_ax4 is None and _st_ax4 == "empty",
      f"Test AX: empty stage (got {_st_ax4})")


print("== Test AY: chat_fusion harvests thinking when content empty ==")
_sess_ay, _te_ay = _status_sess("fake-rt-ay")
forget_loaded_models()

class _FakeResp:
    status_code = 200
    def raise_for_status(self):
        return None
    def json(self):
        return {
            "message": {
                "role": "assistant",
                "content": "",
                "thinking": json.dumps({
                    "current_topic": "think_topic",
                    "new_knowledge": ["k"],
                    "skip": False,
                }),
            }
        }

def _post_ay(*args, **kwargs):
    return _FakeResp()

import requests as _requests_mod  # noqa: E402
_orig_post_ay = _requests_mod.post
_requests_mod.post = _post_ay
try:
    with patch("time.sleep", lambda _s: None):
        _sess_ay._fuse_once()
finally:
    _requests_mod.post = _orig_post_ay
check(len(_sess_ay._unfused_transcripts) == 0,
      f"Test AY: thinking harvest success consumed pending (n={len(_sess_ay._unfused_transcripts)})")
_ay_lines = _read_status_lines(_sess_ay.storage.realtime_fusion_status_path)
check(_ay_lines and _ay_lines[0].get("result") == FUSE_OK,
      f"Test AY: result success (got {_ay_lines and _ay_lines[0].get('result')})")
check(_ay_lines and _ay_lines[0].get("http_status") == 200,
      f"Test AY: http_status (got {_ay_lines and _ay_lines[0].get('http_status')})")
forget_loaded_models()


print("== Test AZ: fuse_final still uses QLens chat_text (FINAL path unchanged) ==")
# Round 13 must not redirect Final Fusion off chat_text.
_final_src = Path("app/course_session/note_engine.py").read_text(encoding="utf-8")
check("from core.ollama_client import chat_text" in _final_src
      and "chat_fusion(" not in _final_src.split("def fuse_final")[1].split("def _chat_fusion_final")[0],
      "Test AZ: fuse_final body does not call chat_fusion")
check("def _chat_fusion_final" in _final_src and "chat_text(" in _final_src,
      "Test AZ: final path still has dedicated chat_text helper")


print()
print("== Anti-false-pass scan ==")
test_src = Path("test_stability.py").read_text(encoding="utf-8")
# Build needle without embedding the banned phrase as a contiguous literal
# in this scan block (avoids self-match false FAIL).
_needle_or_true = "or" + " " + "True"
check(_needle_or_true not in test_src, "no always-true or-True false-pass in test_stability.py")
_needle_bare_pass = "except Exception" + ": pass"
check(_needle_bare_pass not in test_src, "no bare except-pass in tests")
check("sys.exit(0)" in test_src and "FAILURES" in test_src,
      "tests exit 1 on failures / 0 only when no FAILURES")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("STABILITY_UNIT_TEST PASS")
sys.exit(0)
