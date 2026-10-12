from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from video_engine import Segment


def format_time(seconds: float, milliseconds: bool = True) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if milliseconds:
        millis = int((seconds - whole) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class SegmentedTimeline(QWidget):
    seekRequested = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(52)
        self.setMaximumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("蓝色为保留片段，红色为删除片段，黄色边框为当前选中片段；点击或拖动可定位。")
        self.setAccessibleName("分段视频时间轴")
        self.segments: list[Segment] = []
        self.total_duration = 0.0
        self.selected_segment = -1
        self.position = 0.0
        self._dragging = False

    def set_timeline(self, segments: list[Segment], duration: float, selected: int = -1):
        self.segments = list(segments)
        self.total_duration = max(0.0, duration)
        self.selected_segment = selected
        self.position = min(self.position, self.total_duration)
        self.update()

    def set_position(self, seconds: float):
        self.position = min(self.total_duration, max(0.0, seconds)) if self.total_duration else 0.0
        self.update()

    def _track_rect(self) -> QRectF:
        margin = 12.0
        height = 22.0
        return QRectF(margin, (self.height() - height) / 2, max(1.0, self.width() - 2 * margin), height)

    def _time_x(self, seconds: float) -> float:
        track = self._track_rect()
        fraction = min(1.0, max(0.0, seconds / self.total_duration)) if self.total_duration else 0.0
        return track.left() + fraction * track.width()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track_rect()
        painter.setPen(QPen(QColor("#aeb8c5"), 1))
        painter.setBrush(QColor("#e7edf4"))
        painter.drawRoundedRect(track, 7, 7)

        if self.total_duration > 0:
            for index, segment in enumerate(self.segments):
                x1, x2 = self._time_x(segment.start), self._time_x(segment.end)
                block = QRectF(x1 + 1, track.top() + 1, max(1.0, x2 - x1 - 2), track.height() - 2)
                color = QColor("#d94d5c" if segment.deleted else "#3183db")
                if index == self.selected_segment:
                    color = color.lighter(118)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(block, 4, 4)
                if index == self.selected_segment:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor("#f0a900"), 3))
                    painter.drawRoundedRect(block.adjusted(1.5, 1.5, -1.5, -1.5), 4, 4)

            painter.setPen(QPen(QColor("#26364a"), 1))
            for segment in self.segments[:-1]:
                boundary = round(self._time_x(segment.end))
                painter.drawLine(boundary, round(track.top() - 2), boundary, round(track.bottom() + 2))

            playhead = round(self._time_x(self.position))
            painter.setPen(QPen(QColor("#17212e"), 5))
            painter.drawLine(playhead, round(track.top() - 5), playhead, round(track.bottom() + 5))
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.drawLine(playhead, round(track.top() - 5), playhead, round(track.bottom() + 5))
        painter.end()

    def _seek_at(self, x: float):
        if self.total_duration <= 0:
            return
        track = self._track_rect()
        fraction = min(1.0, max(0.0, (x - track.left()) / track.width()))
        self.position = fraction * self.total_duration
        self.update()
        self.seekRequested.emit(self.position)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._seek_at(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._seek_at(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._seek_at(event.position().x())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PlayerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget(self)
        self.video.setMinimumSize(320, 180)
        self.video.setStyleSheet("background: #10151e;")
        self.player.setVideoOutput(self.video)
        self.timeline = SegmentedTimeline(self)
        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        self.message_label = QLabel("打开视频后预览")
        self.open_button = QPushButton("打开视频…")
        self.play_button = QPushButton("播放")
        self.pause_button = QPushButton("暂停")
        controls = QHBoxLayout()
        controls.addWidget(self.open_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.time_label, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video, 1)
        layout.addWidget(self.timeline)
        layout.addWidget(self.message_label)
        layout.addLayout(controls)
        self.duration_ms = 0
        self.timeline.seekRequested.connect(self._seek_to)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.errorOccurred.connect(self._media_error)
        self.play_button.clicked.connect(self.player.play)
        self.pause_button.clicked.connect(self.player.pause)

    def set_source(self, path: Path):
        self.player.pause()
        self.duration_ms = 0
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.message_label.setText("正在打开预览…")
        self.timeline.set_position(0)
        self._position_changed(0)

    def _duration_changed(self, duration: int):
        self.duration_ms = max(0, duration)
        self.message_label.setText("")
        self._position_changed(self.player.position())

    def _position_changed(self, position: int):
        self.timeline.set_position(position / 1000)
        self.time_label.setText(
            f"{format_time(position / 1000)} / {format_time(self.duration_ms / 1000)}"
        )

    def _seek_to(self, seconds: float):
        if self.duration_ms <= 0:
            return
        self.player.setPosition(round(seconds * 1000))

    def _media_error(self, _error, message: str):
        if message:
            self.message_label.setText(f"预览不可用：{message}")

    def close_player(self):
        self.player.stop()
        self.player.setSource(QUrl())

