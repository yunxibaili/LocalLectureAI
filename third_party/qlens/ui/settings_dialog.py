from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QSpinBox, QDoubleSpinBox, QDialogButtonBox, QLabel,
)


@dataclass
class AppSettings:
    max_objects: int
    video_interval_s: float
    video_max_frames: int


class SettingsDialog(QDialog):
    def __init__(self, parent, settings: AppSettings):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        form = QFormLayout(self)

        self.max_objects = QSpinBox()
        self.max_objects.setRange(1, 20)
        self.max_objects.setValue(settings.max_objects)
        form.addRow("Max objects per detection:", self.max_objects)
        form.addRow(QLabel("  (1 = single bbox; >1 enables multi-bbox mode)"))

        self.video_interval = QDoubleSpinBox()
        self.video_interval.setRange(0.0, 30.0)
        self.video_interval.setSingleStep(0.5)
        self.video_interval.setDecimals(1)
        self.video_interval.setSuffix(" s")
        self.video_interval.setValue(settings.video_interval_s)
        form.addRow("Video frame interval:", self.video_interval)
        form.addRow(QLabel("  (0 = continuous: grab next frame immediately after the previous one is processed)"))

        self.video_max_frames = QSpinBox()
        self.video_max_frames.setRange(1, 1000)
        self.video_max_frames.setValue(settings.video_max_frames)
        form.addRow("Video max frames:", self.video_max_frames)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def values(self) -> AppSettings:
        return AppSettings(
            max_objects=self.max_objects.value(),
            video_interval_s=self.video_interval.value(),
            video_max_frames=self.video_max_frames.value(),
        )
