# Quick VRAM bench: baseline -> +qwen3-vl -> release -> +qwen38 -> release
import subprocess, sys, threading, time, os
sys.path.insert(0, ".")
from app.course_session import settings
from core.ollama_client import chat_text

LOG = "vram_bench.log"
samples = []
stop = False

def sample_loop():
    while not stop:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
        samples.append((time.time(), out))
        time.sleep(2)

with open(LOG, "w", encoding="utf-8") as f:
    f.write("start bench\n")

t = threading.Thread(target=sample_loop, daemon=True)
t.start()
time.sleep(3)

def peak(label, t0):
    seg = [s for s in samples if s[0] >= t0]
    vals = []
    for _, s in seg:
        try:
            vals.append(int(s.split(",")[0]))
        except Exception:
            pass
    mx = max(vals) if vals else -1
    print(f"{label}: peak={mx} MiB over {len(seg)} samples")
    return mx

results = {}
t0 = time.time()
r = chat_text("只输出JSON: {\"ok\":true}", "确认", model="qwen3-vl:8b", num_ctx=8192,
              num_predict=64, temperature=0.1, timeout=300)
print("vl:", r[:80])
results["qwen3-vl:8b"] = peak("qwen3-vl:8b live", t0)

# release vlm, then qwen38 (small gen to force load)
subprocess.run(["ollama", "stop", "qwen3-vl:8b"], capture_output=True, timeout=60)
time.sleep(3)
t1 = time.time()
r2 = chat_text("只输出JSON: {\"ok\":true}", "确认", model="qwen38-27b-main:latest",
               num_ctx=16384, num_predict=64, temperature=0.1, think=False, timeout=600)
print("q38:", r2[:80])
results["qwen38-27b"] = peak("qwen38-27b final", t1)
subprocess.run(["ollama", "stop", "qwen38-27b-main:latest"], capture_output=True, timeout=60)

stop = True
time.sleep(1)
with open(LOG, "a", encoding="utf-8") as f:
    for k, v in results.items():
        f.write(f"{k} peak_MiB={v}\n")
print("VRAM_BENCH done", results)
