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

storage_e = _SS()
ne = NoteEngine(storage_e, model="fake-fusion-test")
forget_loaded_models()

# Monkeypatch chat_text to return invalid JSON
import core.ollama_client as oc  # noqa: E402

_orig_chat_text = oc.chat_text

def _fake_chat_text_bad(*args, **kwargs):
    return "not valid json at all {{{"

oc.chat_text = _fake_chat_text_bad
try:
    tr = [_TE(text="hello", session_t=1.0)]
    updated = ne.fuse(tr, [])
    check(updated is False, f"Test E: fuse returns False on bad JSON (got {updated})")
    check("fake-fusion-test" not in loaded_models(),
          f"Test E: model NOT registered on invalid response: {loaded_models()}")
finally:
    oc.chat_text = _orig_chat_text


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


print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("STABILITY_UNIT_TEST PASS")
sys.exit(0)
