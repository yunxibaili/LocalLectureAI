"""Post-setup settings editor window."""

from __future__ import annotations

import logging
from tkinter import filedialog

import customtkinter as ctk

from hearsay.audio.devices import list_input_devices, list_loopback_devices
from hearsay.config import ConfigManager
from hearsay.constants import (
    APP_NAME,
    AUDIO_SOURCE_BOTH,
    AUDIO_SOURCE_MIC,
    AUDIO_SOURCE_SYSTEM,
    MODEL_TABLE,
)
from hearsay.ui.window_icon import apply_window_icon

log = logging.getLogger(__name__)

# Picker label for "let the app pick the system default device".
_AUTO_DEVICE = "Automatic (system default)"


def _device_choices(current: str, names: list[str]) -> tuple[dict[str, str], list[str], str]:
    """Build (label->stored-name map, ordered labels, initial label) for a picker.

    An empty stored name maps to the 'Automatic' label. A previously-saved
    device that is currently absent stays selectable (marked 'not connected')
    so opening Settings never silently discards the saved choice.
    """
    mapping: dict[str, str] = {_AUTO_DEVICE: ""}
    choices: list[str] = [_AUTO_DEVICE]
    for nm in names:
        if nm not in mapping:
            mapping[nm] = nm
            choices.append(nm)
    initial = _AUTO_DEVICE
    if current:
        match = next((lbl for lbl, val in mapping.items() if val == current), None)
        if match is None:
            match = f"{current} (not connected)"
            mapping[match] = current
            choices.append(match)
        initial = match
    return mapping, choices, initial


def _safe_device_names(list_fn) -> list[str]:
    """Enumerate device names, returning [] if the audio backend errors out."""
    try:
        return [d.name for d in list_fn()]
    except Exception:
        log.warning("Device enumeration failed", exc_info=True)
        return []


class SettingsWindow(ctk.CTkToplevel):
    """Settings editor window."""

    def __init__(self, master: ctk.CTk, config_manager: ConfigManager) -> None:
        super().__init__(master)
        self.title(f"{APP_NAME} Settings")
        self.geometry("550x520")
        self.resizable(False, False)
        apply_window_icon(self)

        self._config_manager = config_manager
        self._config = config_manager.config

        self._build_ui()
        self.grab_set()

    def _build_ui(self) -> None:
        # Title
        ctk.CTkLabel(
            self,
            text="Settings",
            font=("Segoe UI", 20, "bold"),
        ).pack(pady=(15, 10))

        # Scrollable content
        scroll = ctk.CTkScrollableFrame(self, width=490, height=360)
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        # ── Audio Source ──
        ctk.CTkLabel(scroll, text="Default Audio Source", font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(10, 5)
        )
        self._source_var = ctk.StringVar(value=self._config.audio_source)
        for value, label in [
            (AUDIO_SOURCE_SYSTEM, "System Audio"),
            (AUDIO_SOURCE_MIC, "Microphone"),
            (AUDIO_SOURCE_BOTH, "Both"),
        ]:
            ctk.CTkRadioButton(
                scroll, text=label, variable=self._source_var, value=value
            ).pack(anchor="w", padx=15, pady=2)

        # ── Microphone device ──
        ctk.CTkLabel(scroll, text="Microphone", font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(15, 5)
        )
        self._mic_map, mic_choices, mic_initial = _device_choices(
            self._config.mic_device_name, _safe_device_names(list_input_devices)
        )
        self._mic_var = ctk.StringVar(value=mic_initial)
        ctk.CTkOptionMenu(
            scroll, variable=self._mic_var, values=mic_choices, width=360
        ).pack(anchor="w", padx=15)

        # ── System audio device ──
        ctk.CTkLabel(scroll, text="System Audio Device", font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(15, 5)
        )
        self._sys_map, sys_choices, sys_initial = _device_choices(
            self._config.loopback_device_name, _safe_device_names(list_loopback_devices)
        )
        self._sys_var = ctk.StringVar(value=sys_initial)
        ctk.CTkOptionMenu(
            scroll, variable=self._sys_var, values=sys_choices, width=360
        ).pack(anchor="w", padx=15)

        # ── Model ──
        ctk.CTkLabel(scroll, text="Whisper Model", font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(15, 5)
        )
        self._model_var = ctk.StringVar(value=self._config.model_name)
        self._model_menu = ctk.CTkOptionMenu(
            scroll,
            variable=self._model_var,
            values=list(MODEL_TABLE.keys()),
            width=200,
        )
        self._model_menu.pack(anchor="w", padx=15)

        # ── Compute Type ──
        ctk.CTkLabel(scroll, text="Compute Type", font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(15, 5)
        )
        self._compute_var = ctk.StringVar(value=self._config.compute_type)
        self._compute_menu = ctk.CTkOptionMenu(
            scroll,
            variable=self._compute_var,
            values=["float16", "int8", "float32"],
            width=200,
        )
        self._compute_menu.pack(anchor="w", padx=15)

        # ── Device ──
        ctk.CTkLabel(scroll, text="Device", font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(15, 5)
        )
        self._device_var = ctk.StringVar(value=self._config.device)
        ctk.CTkRadioButton(
            scroll, text="CPU", variable=self._device_var, value="cpu"
        ).pack(anchor="w", padx=15, pady=2)
        ctk.CTkRadioButton(
            scroll, text="CUDA (GPU)", variable=self._device_var, value="cuda"
        ).pack(anchor="w", padx=15, pady=2)

        # ── Language ──
        ctk.CTkLabel(scroll, text="Language", font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(15, 5)
        )
        self._lang_var = ctk.StringVar(value=self._config.language)
        self._lang_entry = ctk.CTkEntry(scroll, textvariable=self._lang_var, width=100)
        self._lang_entry.pack(anchor="w", padx=15)
        ctk.CTkLabel(
            scroll, text="ISO 639-1 code (e.g., en, es, fr) or empty for auto-detect",
            font=("Segoe UI", 10), text_color="gray"
        ).pack(anchor="w", padx=15)

        # ── VAD ──
        self._vad_var = ctk.BooleanVar(value=self._config.vad_filter)
        ctk.CTkCheckBox(
            scroll, text="Enable VAD filter (recommended)", variable=self._vad_var
        ).pack(anchor="w", padx=15, pady=(15, 5))

        # ── Output Directory ──
        ctk.CTkLabel(scroll, text="Output Directory", font=("Segoe UI", 14, "bold")).pack(
            anchor="w", pady=(15, 5)
        )
        dir_frame = ctk.CTkFrame(scroll)
        dir_frame.pack(fill="x", padx=15, pady=2)

        self._dir_var = ctk.StringVar(value=self._config.output_dir)
        ctk.CTkEntry(
            dir_frame, textvariable=self._dir_var, width=350, font=("Consolas", 11)
        ).pack(side="left", padx=(0, 5))
        ctk.CTkButton(
            dir_frame, text="Browse", width=70, command=self._browse
        ).pack(side="left")

        # ── Buttons ──
        btn_frame = ctk.CTkFrame(self)
        btn_frame.pack(fill="x", padx=20, pady=(0, 15))

        ctk.CTkButton(
            btn_frame, text="Save", width=100, command=self._save
        ).pack(side="right", padx=5)
        ctk.CTkButton(
            btn_frame, text="Cancel", width=100, fg_color="gray",
            command=self._cancel
        ).pack(side="right", padx=5)

    def _browse(self) -> None:
        path = filedialog.askdirectory(
            initialdir=self._config.output_dir,
            title="Select Output Directory",
        )
        if path:
            self._dir_var.set(path)

    def _save(self) -> None:
        self._config.audio_source = self._source_var.get()
        self._config.mic_device_name = self._mic_map.get(self._mic_var.get(), "")
        self._config.loopback_device_name = self._sys_map.get(self._sys_var.get(), "")
        self._config.model_name = self._model_var.get()
        self._config.compute_type = self._compute_var.get()
        self._config.device = self._device_var.get()
        self._config.language = self._lang_var.get()
        self._config.vad_filter = self._vad_var.get()
        self._config.output_dir = self._dir_var.get()
        self._config_manager.save()
        log.info("Settings saved")
        self.grab_release()
        self.destroy()

    def _cancel(self) -> None:
        self.grab_release()
        self.destroy()
