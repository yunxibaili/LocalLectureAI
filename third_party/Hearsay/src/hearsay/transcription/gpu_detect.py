"""Detect CUDA GPU availability and recommend model/compute_type."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from hearsay.constants import (
    DEFAULT_CPU_COMPUTE,
    DEFAULT_CPU_MODEL,
    DEFAULT_GPU_COMPUTE,
    DEFAULT_GPU_MODEL,
)

log = logging.getLogger(__name__)


@dataclass
class GPUInfo:
    """GPU detection result."""

    cuda_available: bool
    gpu_name: str
    vram_gb: float
    recommended_model: str
    recommended_compute: str
    recommended_device: str


def detect_gpu() -> GPUInfo:
    """Detect a CUDA GPU and recommend a model / compute type.

    Two detection paths, in order:
      1. PyTorch — only if it already happens to be installed (it is *not* a
         Hearsay dependency); simplest, exposes both name and VRAM.
      2. ctranslate2 + nvidia-smi — ctranslate2 ships with faster-whisper, so
         this needs no extra dependency and covers the common case of a GPU
         user who doesn't have the heavy torch package. Without this, such
         users were always recommended CPU despite a capable GPU.

    Falls back to a CPU recommendation when neither path finds a CUDA device.

    The torch-free ctranslate2/nvidia-smi fallback was contributed by
    Kauã Amado (@kauaamado, #10).
    """
    # Method 1: PyTorch, if present (not a project dependency, so may be absent).
    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram_gb = torch.cuda.get_device_properties(0).total_mem / (1024**3)
            return _gpu_info(name, vram_gb)
    except ImportError:
        pass
    except Exception:
        log.warning("torch GPU detection failed", exc_info=True)

    # Method 2: ctranslate2 (bundled with faster-whisper) + nvidia-smi.
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            name, vram_gb = _nvidia_smi_gpu()
            return _gpu_info(name or "CUDA GPU", vram_gb)
    except ImportError:
        log.info("ctranslate2 unavailable for GPU detection")
    except Exception:
        log.warning("ctranslate2 GPU detection failed", exc_info=True)

    log.info("No CUDA GPU detected; recommending CPU")
    return GPUInfo(
        cuda_available=False,
        gpu_name="",
        vram_gb=0,
        recommended_model=DEFAULT_CPU_MODEL,
        recommended_compute=DEFAULT_CPU_COMPUTE,
        recommended_device="cpu",
    )


def _nvidia_smi_gpu() -> tuple[str, float]:
    """Return (name, vram_gb) from nvidia-smi, or ("", 0.0) if unavailable."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            encoding="utf-8", timeout=5,
        ).strip().splitlines()
        if out:
            name, mib = out[0].split(",")
            return name.strip(), round(float(mib) / 1024, 1)
    except Exception:
        log.debug("nvidia-smi query failed", exc_info=True)
    return "", 0.0


def _gpu_info(name: str, vram_gb: float) -> GPUInfo:
    """Build a GPUInfo with a model recommendation scaled to available VRAM."""
    log.info("CUDA GPU found: %s (%.1f GB VRAM)", name, vram_gb)
    if vram_gb >= 6:
        model = DEFAULT_GPU_MODEL
    elif vram_gb >= 2:
        model = "small.en"
    else:
        # Known CUDA device but little/unknown VRAM — stay conservative.
        model = "tiny.en"
    return GPUInfo(
        cuda_available=True,
        gpu_name=name,
        vram_gb=round(vram_gb, 1),
        recommended_model=model,
        recommended_compute=DEFAULT_GPU_COMPUTE,
        recommended_device="cuda",
    )
