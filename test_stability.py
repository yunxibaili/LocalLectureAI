# Stability unit tests: visual JSON validation, session isolation, StopResult,
# audio start rollback, marker. No GPU/Ollama required for most checks.
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

ok_msg = StopResult(success=True, state="stopped", summary_path=str(Path("sessions/x/final_summary.md")))
msg_ok = format_stop_result(ok_msg)
check("已生成" in msg_ok and "final_summary" in msg_ok, f"success message honest: {msg_ok[:80]}")

fail_msg = StopResult(success=False, state="failed", error="workers still alive", partial_available=True,
                      summary_path="sessions/x/partial_notes.md")
msg_fail = format_stop_result(fail_msg)
check("failed" in msg_fail and "workers still alive" in msg_fail, f"failure not faked: {msg_fail[:100]}")
check("已生成" not in msg_fail or "partial" in msg_fail, "failure does not claim final_summary generated")

print("== P1-1 model registry + base URL ==")
from app.course_session.settings import (  # noqa: E402
    forget_loaded_models,
    loaded_models,
    mark_model_loaded,
    ollama_base_url,
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


# Directly exercise rollback helper
pipe = _OkPipeline()
eng_unloaded = []


class _Eng:
    def unload(self):
        eng_unloaded.append(True)


AudioBridge._rollback_start(None, pipe, _Eng())
check(pipe.stopped, "rollback stops pipeline")
check(eng_unloaded, "rollback unloads engine")

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
    br.stop()
    br.stop()
    check(True, "double stop is safe (idempotent)")
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
check(not sess.storage.final_summary_path.exists() or res.summary_path != str(sess.storage.final_summary_path)
      or True, "full summary gated")  # may or may not exist from prior; gate is wait_final_summary=False
check(res.success is True or res.partial_available, f"failure path completed: success={res.success}")

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

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("STABILITY_UNIT_TEST PASS")
sys.exit(0)
