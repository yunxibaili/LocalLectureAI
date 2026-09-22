"""Enumerate loopback (system audio) and microphone devices.

Microphone capture uses the WASAPI host API throughout: it is the modern,
reliable Windows capture backend (the same one loopback and 'Both' mode use).
The legacy MME devices sounddevice also exposes are omitted from the picker so
it only offers devices that actually deliver audio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class AudioDevice:
    """Represents an audio device."""

    index: int
    name: str
    channels: int
    sample_rate: int
    is_loopback: bool


def match_device_by_name(
    name: str, devices: list[AudioDevice]
) -> AudioDevice | None:
    """Find the device whose name best matches *name*.

    Config stores device *names*, not indices: indices are unstable across
    reboots and differ between the sounddevice and PyAudioWPatch libraries.
    Matching is tolerant so small library-to-library name differences still
    resolve — exact, then prefix, then substring. Returns None for an empty
    name or no match, so callers fall back to the default device.
    """
    if not name:
        return None
    for dev in devices:
        if dev.name == name:
            return dev
    for dev in devices:
        if dev.name.startswith(name) or name.startswith(dev.name):
            return dev
    for dev in devices:
        if name in dev.name or dev.name in name:
            return dev
    return None


# ── Loopback (system audio) devices — via PyAudioWPatch / WASAPI ──────────

def list_loopback_devices() -> list[AudioDevice]:
    """Return WASAPI loopback devices (system audio capture)."""
    import pyaudiowpatch as pyaudio

    devices: list[AudioDevice] = []
    p = pyaudio.PyAudio()
    try:
        for i in range(p.get_device_count()):
            dev = p.get_device_info_by_index(i)
            if dev.get("isLoopbackDevice"):
                devices.append(AudioDevice(
                    index=dev["index"],
                    name=dev["name"],
                    channels=dev["maxInputChannels"],
                    sample_rate=int(dev["defaultSampleRate"]),
                    is_loopback=True,
                ))
    finally:
        p.terminate()
    log.debug("Found %d loopback devices", len(devices))
    return devices


def get_default_loopback() -> AudioDevice | None:
    """Return the default loopback device (speakers)."""
    import pyaudiowpatch as pyaudio

    p = pyaudio.PyAudio()
    try:
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = p.get_device_info_by_index(
            wasapi_info["defaultOutputDevice"]
        )
        for i in range(p.get_device_count()):
            dev = p.get_device_info_by_index(i)
            if (
                dev.get("isLoopbackDevice")
                and dev["name"].startswith(default_speakers["name"])
            ):
                return AudioDevice(
                    index=dev["index"],
                    name=dev["name"],
                    channels=dev["maxInputChannels"],
                    sample_rate=int(dev["defaultSampleRate"]),
                    is_loopback=True,
                )
    except Exception:
        log.warning("Could not find default loopback device", exc_info=True)
    finally:
        p.terminate()
    return None


def resolve_loopback(name: str) -> AudioDevice | None:
    """Resolve a configured system-audio device name to a loopback device."""
    if not name:
        return None
    try:
        return match_device_by_name(name, list_loopback_devices())
    except Exception:
        log.warning("Could not resolve loopback device %r", name, exc_info=True)
        return None


# ── Microphone / input devices — via sounddevice / WASAPI ─────────────────

def _wasapi_hostapi_index(sd) -> int | None:
    """Return sounddevice's host-API index for WASAPI, or None if absent."""
    try:
        for i, api in enumerate(sd.query_hostapis()):
            if "WASAPI" in api["name"]:
                return i
    except Exception:
        log.warning("Could not query host APIs", exc_info=True)
    return None


def list_input_devices() -> list[AudioDevice]:
    """Return WASAPI microphone/input devices (sounddevice indices)."""
    import sounddevice as sd

    devices: list[AudioDevice] = []
    wasapi = _wasapi_hostapi_index(sd)
    seen: set[str] = set()
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] <= 0:
            continue
        if wasapi is not None and dev["hostapi"] != wasapi:
            continue
        if dev["name"] in seen:
            continue
        seen.add(dev["name"])
        devices.append(AudioDevice(
            index=i,
            name=dev["name"],
            channels=dev["max_input_channels"],
            sample_rate=int(dev["default_samplerate"]),
            is_loopback=False,
        ))
    log.debug("Found %d WASAPI input devices", len(devices))
    return devices


def get_default_input_device() -> AudioDevice | None:
    """Return the default WASAPI input device (sounddevice index).

    Falls back to sounddevice's global default input (index -1 sentinel =
    'let PortAudio pick') if WASAPI can't be resolved.
    """
    import sounddevice as sd

    try:
        wasapi = _wasapi_hostapi_index(sd)
        if wasapi is not None:
            idx = sd.query_hostapis()[wasapi].get("default_input_device", -1)
            if idx is not None and idx >= 0:
                dev = sd.query_devices(idx)
                return AudioDevice(
                    index=idx,
                    name=dev["name"],
                    channels=dev["max_input_channels"],
                    sample_rate=int(dev["default_samplerate"]),
                    is_loopback=False,
                )
    except Exception:
        log.warning("Could not resolve default WASAPI input", exc_info=True)

    # Fall back to sounddevice's global default input (may be MME).
    try:
        dev = sd.query_devices(kind="input")
        idx = sd.default.device[0]
        return AudioDevice(
            index=idx if isinstance(idx, int) and idx >= 0 else -1,
            name=dev["name"],
            channels=dev["max_input_channels"],
            sample_rate=int(dev["default_samplerate"]),
            is_loopback=False,
        )
    except Exception:
        log.warning("Could not resolve any default input device", exc_info=True)
        return None


def resolve_input_device(name: str) -> AudioDevice | None:
    """Resolve a configured microphone name to a WASAPI input device."""
    if not name:
        return None
    try:
        return match_device_by_name(name, list_input_devices())
    except Exception:
        log.warning("Could not resolve input device %r", name, exc_info=True)
        return None
