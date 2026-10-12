from __future__ import annotations

import sys
import math
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QAbstractSpinBox, QCheckBox,
    QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from conversion_page import ConversionPage
from video_engine import (
    FFmpegRunner, MediaInfo, Segment, VIDEO_EXTENSIONS, even_dimensions,
    probe_media, retained_segments, split_at, set_deleted, default_output_path,
)
from video_widgets import PlayerPanel, format_time


class BackgroundTask(QThread):
    progress = Signal(float, str)

    def __init__(self, operation, cancel_event: threading.Event):
        super().__init__()
        self.operation = operation
        self.cancel_event = cancel_event
        self.result = None
        self.error: Exception | None = None

    def run(self):
        try:
            self.result = self.operation(self.cancel_event, self.progress.emit)
        except Exception as exc:
            self.error = exc


class ProbeTask(QThread):
    completed = Signal(object, str)

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            self.completed.emit(probe_media(self.path), "")
        except Exception as exc:
            self.completed.emit(None, str(exc))


class CutPage(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.window = window
        self.info: MediaInfo | None = None
        self.segments: list[Segment] = []
        self.selected_index = -1
        self.history: list[tuple[list[Segment], int]] = []
        self.history_index = -1

        self.player = PlayerPanel(self)
        self.player.open_button.clicked.connect(self.open_video)
        self.player.play_button.setToolTip("空格播放/暂停；← / → 前进或后退一帧。")
        self.player.pause_button.setToolTip("空格播放/暂停；← / → 前进或后退一帧。")
        self.skip_deleted = QCheckBox("播放时跳过删除片段")
        self.segment_list = QListWidget(self)
        self.segment_list.itemClicked.connect(self._select_item)
        self.status_label = QLabel("请打开一个视频。")
        self.status_label.setWordWrap(True)
        self.time_input = QDoubleSpinBox(self)
        self.time_input.setDecimals(3)
        self.time_input.setRange(0, 24 * 60 * 60)
        self.time_input.setSingleStep(0.1)
        self.time_input.setSuffix(" 秒")
        self.split_playhead_button = QPushButton("在当前位置分割")
        self.split_time_button = QPushButton("按输入时间分割")
        self.delete_button = QPushButton("标记为删除")
        self.restore_button = QPushButton("恢复片段")
        self.reset_button = QPushButton("重置分割")
        self.export_button = QPushButton("导出保留片段")
        self._build_ui()

        self.player.play_button.clicked.disconnect()
        self.player.play_button.clicked.connect(self._play)
        self.split_playhead_button.clicked.connect(self.split_playhead)
        self.split_time_button.clicked.connect(lambda: self._split(self.time_input.value()))
        self.delete_button.clicked.connect(lambda: self._mark(True))
        self.restore_button.clicked.connect(lambda: self._mark(False))
        self.reset_button.clicked.connect(self.reset)
        self.export_button.clicked.connect(self.export)
        self.player.player.positionChanged.connect(self._playback_position)
        self.skip_deleted.toggled.connect(lambda _checked: None)
        QShortcut(QKeySequence(Qt.Key.Key_S), self, activated=self.split_playhead)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self.undo)
        delete_key = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.segment_list)
        delete_key.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete_key.activated.connect(lambda: self._mark(True))
        self._setup_playback_shortcuts()
        self._refresh()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.player, 1)
        left_layout.addWidget(self.skip_deleted)
        splitter.addWidget(left)

        right = QWidget(self)
        right.setMinimumWidth(330)
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("分割剪辑"))
        right_layout.addWidget(self.status_label)
        right_layout.addWidget(QLabel("时间轴：蓝色保留，红色删除，黄框为选中片段"))
        right_layout.addWidget(self.segment_list, 1)
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("分割时间"))
        time_row.addWidget(self.time_input, 1)
        right_layout.addLayout(time_row)
        for button in (self.split_playhead_button, self.split_time_button, self.delete_button,
                       self.restore_button, self.reset_button, self.export_button):
            right_layout.addWidget(button)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter)

    def open_video(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, "打开视频", "",
            "视频文件 (*.mp4 *.mkv *.mov *.m4v *.avi *.webm *.ts *.wmv *.mts *.m2ts);;所有文件 (*)",
        )
        if not filename:
            return
        path = Path(filename).resolve()
        self.info = None
        self.segments = []
        self.selected_index = -1
        self.history.clear()
        self.history_index = -1
        self.player.set_source(path)
        self.status_label.setText(f"正在读取：{path.name}")
        self.window.analyze(path, self._media_loaded, self._media_failed)
        self._refresh()

    def _media_loaded(self, info: MediaInfo):
        self.info = info
        self.segments = [Segment(0.0, info.duration, False)]
        self.selected_index = 0
        self.history = [(list(self.segments), self.selected_index)]
        self.history_index = 0
        self.time_input.setRange(0.0, info.duration)
        self.status_label.setText(
            f"{info.path.name}\n{info.display_width}×{info.display_height} · "
            f"{float(info.fps):.3g} fps · {format_time(info.duration)}"
        )
        self._refresh()

    def _media_failed(self, message: str):
        self.status_label.setText("媒体信息读取失败：" + message)
        QMessageBox.warning(self, "无法读取视频", message)
        self._refresh()

    def _push_history(self):
        self.history = self.history[:self.history_index + 1]
        self.history.append((list(self.segments), self.selected_index))
        self.history_index = len(self.history) - 1

    def undo(self):
        if self.history_index <= 0:
            return
        self.history_index -= 1
        segments, selected = self.history[self.history_index]
        self.segments = list(segments)
        self.selected_index = selected
        self._refresh()

    def _commit(self, segments: list[Segment], selected: int):
        if segments == self.segments:
            return
        self.segments = segments
        self.selected_index = selected
        self._push_history()
        self._refresh()

    def split_playhead(self):
        if self.info:
            self._split(self.player.player.position() / 1000.0)

    def _split(self, seconds: float):
        if not self.info:
            return
        updated, index = split_at(self.segments, seconds, self.info.fps, self.info.duration)
        if index is not None:
            self._commit(updated, index)

    def _mark(self, deleted: bool):
        if 0 <= self.selected_index < len(self.segments):
            self._commit(set_deleted(self.segments, self.selected_index, deleted), self.selected_index)

    def reset(self):
        if self.info:
            self._commit([Segment(0.0, self.info.duration, False)], 0)

    def _select_item(self, item: QListWidgetItem):
        index = int(item.data(Qt.ItemDataRole.UserRole))
        if not 0 <= index < len(self.segments):
            return
        self.selected_index = index
        if 0 <= self.history_index < len(self.history):
            self.history[self.history_index] = (list(self.segments), self.selected_index)
        selected = self.segments[index]
        self.player.player.pause()
        self.player.player.setPosition(round(selected.start * 1000))
        self._refresh()

    def _refresh(self):
        self.segment_list.clear()
        for index, segment in enumerate(self.segments):
            state = "删除" if segment.deleted else "保留"
            item = QListWidgetItem(
                f"{index + 1:02d}  {state}  {format_time(segment.start)} – "
                f"{format_time(segment.end)}  ·  {segment.duration:.3f} 秒"
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setForeground(QColor("#c63847" if segment.deleted else "#225f9f"))
            self.segment_list.addItem(item)
        if 0 <= self.selected_index < self.segment_list.count():
            self.segment_list.setCurrentRow(self.selected_index)
        duration = self.info.duration if self.info else 0.0
        self.player.timeline.set_timeline(self.segments, duration, self.selected_index)
        editing = self.info is not None and not self.window.task_active
        for control in (self.segment_list, self.time_input, self.split_playhead_button, self.split_time_button,
                        self.delete_button, self.restore_button, self.reset_button, self.export_button,
                        self.skip_deleted):
            control.setEnabled(editing)
        self.player.open_button.setEnabled(not self.window.task_active)
        self.export_button.setEnabled(editing)
        self._update_playback_shortcuts(None, QApplication.focusWidget())

    def _play(self):
        if self.segments and all(part.deleted for part in self.segments):
            QMessageBox.information(self, "没有可预览的内容", "所有片段都已标记为删除。请恢复至少一个片段后再预览或导出。")
            return
        self.player.player.play()

    def _setup_playback_shortcuts(self):
        self.play_pause_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.play_pause_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.play_pause_shortcut.activated.connect(self._toggle_play_pause)

        self.frame_back_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self.frame_back_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.frame_back_shortcut.activated.connect(lambda: self._step_frame(-1))

        self.frame_forward_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self.frame_forward_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.frame_forward_shortcut.activated.connect(lambda: self._step_frame(1))

        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._update_playback_shortcuts)
        self._update_playback_shortcuts(None, QApplication.focusWidget())

    def _update_playback_shortcuts(self, _old_focus, new_focus):
        inside_page = new_focus is self or (new_focus is not None and self.isAncestorOf(new_focus))
        can_use = inside_page and self.info is not None and not self.window.task_active
        text_input = isinstance(
            new_focus, (QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit),
        )
        list_focus = isinstance(new_focus, QAbstractItemView)
        self.play_pause_shortcut.setEnabled(
            can_use and not text_input and new_focus is not self.skip_deleted
        )
        can_step = can_use and not text_input and not list_focus
        self.frame_back_shortcut.setEnabled(can_step)
        self.frame_forward_shortcut.setEnabled(can_step)

    def _toggle_play_pause(self):
        focus = QApplication.focusWidget()
        if focus is self.player.play_button:
            self._play()
        elif focus is self.player.pause_button:
            self.player.player.pause()
        elif self.player.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.player.pause()
        else:
            self._play()

    def _step_frame(self, direction: int):
        if not self.info or direction == 0:
            return
        fps = float(self.info.fps)
        if fps <= 0:
            return
        player = self.player.player
        current_frame = math.floor(player.position() * fps / 1000 + 0.5)
        frame_count = max(1, math.ceil(self.info.video_duration * fps - 1e-6))
        target_frame = min(frame_count - 1, max(0, current_frame + direction))
        player.pause()
        player.setPosition(round(target_frame * 1000 / fps))

    def _playback_position(self, position_ms: int):
        if not self.info or not self.skip_deleted.isChecked():
            return
        if self.player.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        seconds = position_ms / 1000.0
        for index, segment in enumerate(self.segments):
            if not segment.deleted or not segment.start <= seconds < segment.end:
                continue
            end = segment.end
            cursor = index + 1
            while cursor < len(self.segments) and self.segments[cursor].deleted and abs(self.segments[cursor].start - end) < 1e-6:
                end = self.segments[cursor].end
                cursor += 1
            if end >= self.info.duration - 1.0 / max(float(self.info.fps), 1.0):
                self.player.player.pause()
            else:
                self.player.player.setPosition(round(end * 1000))
            return

    def export(self):
        if not self.info:
            return
        segments = retained_segments(self.segments)
        if not segments:
            QMessageBox.information(self, "没有可导出的内容", "所有片段都已标记为删除。")
            return
        initial = default_output_path(self.info.path, "MP4", "剪辑")
        filename, _ = QFileDialog.getSaveFileName(self, "导出剪辑", str(initial), "MP4 视频 (*.mp4)")
        if not filename:
            return
        destination = Path(filename).with_suffix(".mp4")
        ranges = [(self.info.path, part.start, part.end) for part in segments]
        output_size = even_dimensions(self.info.display_width, self.info.display_height)
        output_fps = self.info.fps
        self.window.run_task(
            "正在导出剪辑…",
            lambda cancel, progress: self.window.runner.export_ranges(
                ranges, destination, cancel, progress, output_size, output_fps,
            ),
        )

    def set_busy(self, _busy: bool):
        self._refresh()

    def pause(self):
        self.player.player.pause()


class MergePage(QWidget):
    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.window = window
        self.file_list = QListWidget(self)
        self.add_files_button = QPushButton("添加视频文件…")
        self.add_folder_button = QPushButton("添加文件夹…")
        self.up_button = QPushButton("上移")
        self.down_button = QPushButton("下移")
        self.remove_button = QPushButton("移除")
        self.merge_button = QPushButton("按列表顺序合并")
        self.hint = QLabel("至少添加两个视频。输出使用第一个视频的尺寸和帧率；原视频保持不变。")
        self.hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(self.file_list, 1)
        buttons = QHBoxLayout()
        for button in (self.add_files_button, self.add_folder_button, self.up_button,
                       self.down_button, self.remove_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(self.merge_button)
        self.add_files_button.clicked.connect(self.add_files)
        self.add_folder_button.clicked.connect(self.add_folder)
        self.up_button.clicked.connect(lambda: self.move(-1))
        self.down_button.clicked.connect(lambda: self.move(1))
        self.remove_button.clicked.connect(self.remove)
        self.merge_button.clicked.connect(self.merge)
        self._refresh()

    def add_files(self):
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "添加视频", "",
            "视频文件 (*.mp4 *.mkv *.mov *.m4v *.avi *.webm *.ts *.wmv *.mts *.m2ts);;所有文件 (*)",
        )
        self._append([Path(name) for name in filenames])

    def add_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "添加文件夹中的视频")
        if directory:
            videos = sorted(p for p in Path(directory).rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
            self._append(videos)

    def _append(self, paths: list[Path]):
        existing = {self.file_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.file_list.count())}
        for path in paths:
            absolute = str(path.resolve())
            if absolute in existing:
                continue
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, absolute)
            item.setToolTip(absolute)
            self.file_list.addItem(item)
            existing.add(absolute)
        self._refresh()

    def move(self, offset: int):
        row = self.file_list.currentRow()
        target = row + offset
        if 0 <= row < self.file_list.count() and 0 <= target < self.file_list.count():
            item = self.file_list.takeItem(row)
            self.file_list.insertItem(target, item)
            self.file_list.setCurrentRow(target)

    def remove(self):
        row = self.file_list.currentRow()
        if row >= 0:
            self.file_list.takeItem(row)
        self._refresh()

    def merge(self):
        if self.file_list.count() < 2:
            return
        first = Path(self.file_list.item(0).data(Qt.ItemDataRole.UserRole))
        initial = default_output_path(first, "MP4", "合并")
        filename, _ = QFileDialog.getSaveFileName(self, "保存合并视频", str(initial), "MP4 视频 (*.mp4)")
        if not filename:
            return
        destination = Path(filename).with_suffix(".mp4")
        paths = [Path(self.file_list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.file_list.count())]

        def operation(cancel, progress):
            ranges = []
            infos: list[MediaInfo] = []
            for index, path in enumerate(paths):
                if cancel.is_set():
                    raise RuntimeError("已取消")
                progress(0.03 * (index + 1) / len(paths), f"正在读取第 {index + 1}/{len(paths)} 个视频")
                info = probe_media(path)
                infos.append(info)
                ranges.append((path, 0.0, info.duration))
            def merge_progress(fraction: float, stage: str):
                progress(0.03 + 0.97 * fraction, stage)
            return self.window.runner.export_ranges(
                ranges, destination, cancel, merge_progress,
                even_dimensions(infos[0].display_width, infos[0].display_height), infos[0].fps,
            )

        self.window.run_task("正在合并视频…", operation)

    def _refresh(self):
        busy = self.window.task_active
        self.file_list.setEnabled(not busy)
        for button in (self.add_files_button, self.add_folder_button, self.up_button,
                       self.down_button, self.remove_button):
            button.setEnabled(not busy)
        self.merge_button.setEnabled(not busy and self.file_list.count() >= 2)

    def set_busy(self, _busy: bool):
        self._refresh()

    def pause(self):
        pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Easy Video Editor")
        self.setMinimumSize(1040, 700)
        self.task_active = False
        self.cancel_event: threading.Event | None = None
        self.worker: BackgroundTask | None = None
        self.success_callback = None
        self.close_after_task = False
        self.analysis_threads: set[ProbeTask] = set()
        self.analysis_callbacks: dict[ProbeTask, tuple[object, object]] = {}
        try:
            self.runner = FFmpegRunner()
            self.tool_error = ""
        except Exception as exc:
            self.runner = None
            self.tool_error = str(exc)

        self.tabs = QTabWidget(self)
        self.cut_page = CutPage(self)
        self.merge_page = MergePage(self)
        self.conversion_page = ConversionPage(self)
        self.tabs.addTab(self.cut_page, "分割剪辑")
        self.tabs.addTab(self.merge_page, "合并视频")
        self.tabs.addTab(self.conversion_page, "格式转换")
        self.tabs.currentChanged.connect(self._tab_changed)

        self.status_label = QLabel("就绪：所有处理都在本地运行。" if not self.tool_error else "缺少 FFmpeg / FFprobe；处理功能暂不可用。")
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setEnabled(False)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        bottom = QWidget(self)
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(12, 4, 12, 10)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.cancel_button)
        bottom_layout.addLayout(status_row)
        bottom_layout.addWidget(self.progress_bar)
        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(8, 8, 8, 0)
        central_layout.addWidget(self.tabs, 1)
        central_layout.addWidget(bottom)
        self.setCentralWidget(central)
        self.cancel_button.clicked.connect(self.cancel_task)
        self.setStyleSheet("""
            QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 10pt; }
            QTabBar::tab { padding: 10px 24px; }
            QPushButton { padding: 7px 12px; }
            QListWidget { background: #fbfcfe; }
        """)

    def analyze(self, path: Path, success, failure):
        task = ProbeTask(path)
        self.analysis_threads.add(task)
        self.analysis_callbacks[task] = (success, failure)
        task.completed.connect(self._probe_completed)
        task.finished.connect(self._probe_finished)
        task.finished.connect(task.deleteLater)
        task.start()

    def _probe_completed(self, info, error: str):
        task = self.sender()
        callbacks = self.analysis_callbacks.get(task)
        if not callbacks:
            return
        success, failure = callbacks
        if info is not None:
            success(info)
        else:
            failure(error)

    def _probe_finished(self):
        task = self.sender()
        self.analysis_threads.discard(task)
        self.analysis_callbacks.pop(task, None)
        if self.close_after_task and not self.task_active and not self.analysis_threads:
            QTimer.singleShot(0, self.close)

    def run_task(self, title: str, operation, success_callback=None):
        if self.task_active:
            return
        if self.runner is None:
            QMessageBox.warning(self, "缺少媒体工具", self.tool_error)
            return
        self.task_active = True
        self.cut_page.pause()
        self.conversion_page.pause()
        self.success_callback = success_callback
        self.cancel_event = threading.Event()
        self.progress_bar.setValue(0)
        self.status_label.setText(title)
        self.cancel_button.setEnabled(True)
        self.tabs.setEnabled(False)
        self.cut_page.set_busy(True)
        self.merge_page.set_busy(True)
        self.conversion_page.set_busy(True)
        self.worker = BackgroundTask(operation, self.cancel_event)
        self.worker.progress.connect(self._update_progress)
        self.worker.finished.connect(self._task_finished)
        self.worker.start()

    def _update_progress(self, fraction: float, status: str):
        self.progress_bar.setValue(round(min(1.0, max(0.0, fraction)) * 1000))
        if status:
            self.status_label.setText(status)

    def cancel_task(self):
        if self.cancel_event:
            self.cancel_event.set()
            if self.runner:
                self.runner.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("正在停止任务并清理临时文件…")

    def _task_finished(self):
        worker = self.worker
        self.task_active = False
        self.worker = None
        self.cancel_event = None
        self.cancel_button.setEnabled(False)
        self.tabs.setEnabled(True)
        self.cut_page.set_busy(False)
        self.merge_page.set_busy(False)
        self.conversion_page.set_busy(False)
        if worker and worker.error:
            message = str(worker.error)
            if message == "已取消":
                self.status_label.setText("任务已取消，临时文件已清理。")
            elif self.close_after_task:
                self.status_label.setText("任务已停止，正在关闭窗口。")
            else:
                self.status_label.setText("任务失败：" + message.splitlines()[-1][:220])
                QMessageBox.warning(self, "任务失败", message[-2400:])
        else:
            self.progress_bar.setValue(1000)
            self.status_label.setText("任务完成，输出已通过校验。")
            if self.success_callback:
                self.success_callback(worker.result if worker else None)
        self.success_callback = None
        if self.close_after_task:
            QTimer.singleShot(0, self.close)
        if worker:
            worker.deleteLater()

    def _tab_changed(self, selected: int):
        for index, page in enumerate((self.cut_page, self.merge_page, self.conversion_page)):
            if index != selected:
                page.pause()

    def closeEvent(self, event):
        if self.task_active:
            self.close_after_task = True
            self.cancel_task()
            event.ignore()
            return
        if self.analysis_threads:
            self.close_after_task = True
            self.status_label.setText("正在结束媒体信息读取…")
            event.ignore()
            return
        self.cut_page.pause()
        self.conversion_page.pause()
        self.cut_page.player.close_player()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Easy Video Editor")
    window = MainWindow()
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

