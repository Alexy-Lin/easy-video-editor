from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QHeaderView,
    QVBoxLayout, QWidget,
)

from video_engine import (
    ExportSettings, HardwareEncoderError, MediaInfo, RESOLUTION_BOUNDS, VIDEO_CRF, VIDEO_EXTENSIONS,
    decimal_megabytes, default_output_path, dimensions_for, probe_media,
)


class ConversionPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.current_path: Path | None = None
        self.info: MediaInfo | None = None
        self.output_location: Path | None = None
        self.dialog_settings = QSettings("EasyVideoEditor", "EasyVideoEditor")
        legacy_settings = QSettings("JianJian", "JianJian")
        last_directory_key = "conversion/last_input_directory"
        if not self.dialog_settings.contains(last_directory_key):
            previous_directory = legacy_settings.value(last_directory_key)
            if previous_directory:
                self.dialog_settings.setValue(last_directory_key, previous_directory)
        self.estimated_sizes: list[dict[str, int | float | str]] | None = None
        self.estimated_sizes_path: Path | None = None

        self.file_list = QListWidget(self)
        self.file_list.currentRowChanged.connect(self._select_row)
        self.add_files_button = QPushButton("添加文件…")
        self.add_folder_button = QPushButton("添加文件夹…")
        self.remove_button = QPushButton("移除选中")
        self.clear_button = QPushButton("清空队列")
        self.container = QComboBox(self)
        self.container.addItems(("MP4", "MKV"))
        self.resolution = QComboBox(self)
        self.resolution.addItems(("原始", *RESOLUTION_BOUNDS.keys()))
        self._resolution_item_flags = [self.resolution.model().item(i).flags() for i in range(self.resolution.count())]
        self.resolution.setCurrentText("原始")
        self.encoding_mode = QComboBox(self)
        self.encoding_mode.addItem("硬件编码（Intel QSV，默认）", True)
        self.encoding_mode.addItem("软件编码（CPU）", False)
        self.crf_label = QLabel()
        self.estimated_use_hardware: bool | None = None
        self._update_quality_label()
        self.output_button = QPushButton("选择保存位置…")
        self.output_label = QLabel("开始转换时选择保存位置")
        self.output_label.setWordWrap(True)
        self.batch_convert_button = QPushButton("批量转换当前文件夹")
        self.batch_convert_button.setToolTip(
            "递归转换当前选中视频所在文件夹中的支持视频，输出到上级目录的新建“压缩后”文件夹。"
        )
        self.skip_existing = QCheckBox("跳过已存在的输出文件")
        self.skip_existing.setChecked(True)
        self.discard_if_larger = QCheckBox("如果输出大于源文件，自动删除结果")
        self.discard_if_larger.setChecked(False)
        self.discard_if_larger.setToolTip("每个文件分别比较；只删除尚未发布的本次转换结果，不会删除源文件。")
        self.convert_button = QPushButton("开始转换")
        self.media_info_label = QLabel("添加视频后显示文件信息。")
        self.media_info_label.setWordWrap(True)
        self.quick_analysis_button = QPushButton("分析视频并预计体积")
        self.quick_analysis_button.setToolTip("按当前选择的硬件或软件编码方式抽样分析，估算各分辨率的输出体积。")
        self.quick_analysis_note = QLabel("分析后会列出不同分辨率的预计体积。点击未标红的行即可采用该分辨率。")
        self.quick_analysis_note.setWordWrap(True)
        self.estimate_table = QTableWidget(0, 4, self)
        self.estimate_table.setHorizontalHeaderLabels(("分辨率", "输出尺寸", "样片 VMAF", "预计体积（占源）"))
        self.estimate_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.estimate_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.estimate_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.estimate_table.verticalHeader().setVisible(False)
        self.estimate_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.estimate_table.setMinimumHeight(105)
        self.estimate_table.setMaximumHeight(230)
        self.estimate_table.setToolTip("点击预计体积不大于源文件的行，设置目标分辨率。标红行不可选择。")
        self._fit_estimate_table_height()
        self.conversion_log = QTableWidget(0, 4, self)
        self.conversion_log.setHorizontalHeaderLabels(("文件", "结果", "实际体积", "占源文件"))
        self.conversion_log.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.conversion_log.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.conversion_log.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.conversion_log.verticalHeader().setVisible(False)
        self.conversion_log.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.conversion_log.setWordWrap(True)
        self.conversion_log.setMinimumHeight(140)

        self._build_ui()
        self.add_files_button.clicked.connect(self.add_files)
        self.add_folder_button.clicked.connect(self.add_folder)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_queue)
        self.resolution.currentTextChanged.connect(self._settings_changed)
        self.encoding_mode.currentIndexChanged.connect(self._encoding_mode_changed)
        self.discard_if_larger.toggled.connect(self._refresh_info)
        self.container.currentTextChanged.connect(self._container_changed)
        self.output_button.clicked.connect(self.choose_output)
        self.convert_button.clicked.connect(self.start_conversion)
        self.batch_convert_button.clicked.connect(self.start_batch_conversion)
        self.quick_analysis_button.clicked.connect(self.quick_analyze)
        self.estimate_table.cellClicked.connect(self._apply_estimated_resolution)
        self._refresh_info()
        self._update_controls()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("转换队列：批量添加后将按顺序使用同一组设置。"))
        left_layout.addWidget(self.file_list, 1)
        file_buttons = QHBoxLayout()
        for control in (self.add_files_button, self.add_folder_button, self.remove_button, self.clear_button):
            file_buttons.addWidget(control)
        left_layout.addLayout(file_buttons)
        splitter.addWidget(left)

        right_content = QWidget(self)
        right_layout = QHBoxLayout(right_content)
        media_group = QGroupBox("媒体信息", right_content)
        media_layout = QVBoxLayout(media_group)
        media_layout.addWidget(self.media_info_label)
        suggestions_group = QGroupBox("视频分析与体积预计", right_content)
        suggestions_group.setMinimumWidth(300)
        suggestions_layout = QVBoxLayout(suggestions_group)
        suggestions_layout.addWidget(self.quick_analysis_button)
        suggestions_layout.addWidget(self.quick_analysis_note)
        suggestions_layout.addWidget(self.estimate_table)
        log_group = QGroupBox("转换日志", suggestions_group)
        log_layout = QVBoxLayout(log_group)
        log_layout.addWidget(self.conversion_log)
        suggestions_layout.addWidget(log_group, 1)
        conversion_group = QGroupBox("格式转换", right_content)
        conversion_group.setMinimumWidth(275)
        conversion_layout = QVBoxLayout(conversion_group)
        conversion_layout.addWidget(QLabel("文件容器"))
        conversion_layout.addWidget(self.container)
        conversion_layout.addWidget(QLabel("分辨率"))
        conversion_layout.addWidget(self.resolution)
        conversion_layout.addWidget(QLabel("编码方式"))
        conversion_layout.addWidget(self.encoding_mode)
        conversion_layout.addWidget(self.crf_label)
        conversion_layout.addWidget(QLabel("保存位置"))
        conversion_layout.addWidget(self.output_button)
        conversion_layout.addWidget(self.output_label)
        conversion_layout.addWidget(self.skip_existing)
        conversion_layout.addWidget(self.discard_if_larger)
        conversion_layout.addWidget(self.convert_button)
        conversion_layout.addWidget(self.batch_convert_button)
        media_group.setMinimumWidth(220)

        info_analysis_column = QWidget(right_content)
        info_analysis_layout = QVBoxLayout(info_analysis_column)
        info_analysis_layout.setContentsMargins(0, 0, 0, 0)
        info_analysis_layout.addWidget(media_group)
        info_analysis_layout.addWidget(suggestions_group, 1)
        right_layout.addWidget(info_analysis_column, 1)
        right_layout.addWidget(conversion_group)
        right = QScrollArea(self)
        right.setWidgetResizable(True)
        right.setMinimumWidth(640)
        right.setWidget(right_content)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        outer.addWidget(splitter)

    def add_files(self):
        initial_directory = self._last_input_directory()
        files, _ = QFileDialog.getOpenFileNames(
            self, "添加转换视频", initial_directory,
            "视频文件 (*.mp4 *.mkv *.mov *.m4v *.avi *.webm *.ts *.wmv *.mts *.m2ts);;所有文件 (*)",
        )
        if files:
            self._remember_input_directory(Path(files[0]).parent)
        self._append_paths([Path(item) for item in files])

    def add_folder(self):
        selected = QFileDialog.getExistingDirectory(
            self, "选择视频文件夹", self._last_input_directory(),
        )
        if selected:
            self._remember_input_directory(Path(selected))
            files = sorted(p for p in Path(selected).rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
            self._append_paths(files)

    def _last_input_directory(self) -> str:
        saved = self.dialog_settings.value("conversion/last_input_directory", str(Path.home()))
        directory = Path(str(saved)).expanduser()
        if not directory.is_dir():
            directory = Path.home()
        return str(directory)

    def _remember_input_directory(self, directory: Path):
        self.dialog_settings.setValue("conversion/last_input_directory", str(directory.resolve()))

    def _append_paths(self, paths: list[Path]):
        present = {self.file_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.file_list.count())}
        for path in paths:
            try:
                absolute = str(path.resolve())
            except OSError:
                continue
            if absolute in present:
                continue
            row = QListWidgetItem(path.name)
            row.setData(Qt.ItemDataRole.UserRole, absolute)
            row.setToolTip(absolute)
            self.file_list.addItem(row)
            present.add(absolute)
        if self.file_list.count() and self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(0)
        self.output_location = None
        self.output_label.setText("开始转换时选择保存位置")
        self._update_controls()

    def _paths(self) -> list[Path]:
        return [Path(self.file_list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.file_list.count())]

    def _select_row(self, row: int):
        if row < 0 or row >= self.file_list.count():
            self.current_path = None
            self.info = None
            self.estimated_sizes = None
            self.estimated_sizes_path = None
            self.estimated_use_hardware = None
            self._update_resolution_item_states()
            self.media_info_label.setText("添加视频后显示文件信息。")
            self.quick_analysis_note.setText("分析后会列出不同分辨率的预计体积。点击未标红的行即可采用该分辨率。")
            self.estimate_table.setRowCount(0)
            self._fit_estimate_table_height()
            self._update_controls()
            return
        path = Path(self.file_list.item(row).data(Qt.ItemDataRole.UserRole))
        self.current_path = path
        self.info = None
        self.estimated_sizes = None
        self.estimated_sizes_path = None
        self.estimated_use_hardware = None
        self._update_resolution_item_states()
        self.media_info_label.setText("正在读取媒体信息…")
        self.quick_analysis_note.setText("正在读取媒体信息…")
        self.estimate_table.setRowCount(0)
        self._fit_estimate_table_height()
        self.window.analyze(path, self._analysis_done, self._analysis_failed)
        self._update_controls()

    def _analysis_done(self, info: MediaInfo):
        if info.path != self.current_path:
            return
        self.info = info
        if self.estimated_sizes_path != info.path:
            self.quick_analysis_note.setText(
                f"点击分析后，按 {self._quality_description()} 抽样编码并预计各分辨率的体积。"
            )
        self._refresh_info()
        self._update_controls()

    def _analysis_failed(self, message: str):
        if self.current_path:
            self.media_info_label.setText("媒体信息读取失败：\n" + message)
            self.quick_analysis_note.setText("读取失败，暂时无法分析和预计体积。")

    def _refresh_info(self, *_args):
        info = self.info
        if not info:
            return
        video_bitrate = f"{info.video_bitrate / 1000:.0f} kbps" if info.video_bitrate else "未提供"
        audio_bitrate = f"{info.audio_bitrate / 1000:.0f} kbps" if info.audio_bitrate else ("未提供" if info.has_audio else "无音轨")
        container_bitrate = f"{info.container_bitrate / 1000:.0f} kbps" if info.container_bitrate else "未提供"
        media_lines = [
            f"文件：{info.path.name}",
            f"显示尺寸：{info.display_width}×{info.display_height}，{float(info.fps):.3g} fps",
            f"视频：{info.video_codec}；轨道码率：{video_bitrate}",
            f"音频：{info.audio_codec or '无音轨'}；轨道码率：{audio_bitrate}",
            f"容器平均码率：{container_bitrate}",
            f"时长：{info.duration / 60:.2f} 分钟；文件：{decimal_megabytes(info.file_size)}",
            f"每分钟占用：{info.file_size / max(info.duration / 60, .001) / 1_000_000:.2f} MB",
        ]
        self.media_info_label.setText("\n".join(media_lines))
        if self.estimated_sizes_path == info.path and self.estimated_sizes is not None:
            self._refresh_estimate_table()

    def _refresh_estimate_table(self):
        info = self.info
        estimates = self.estimated_sizes if info and self.estimated_sizes_path == info.path else None
        self.estimate_table.setRowCount(0)
        if not info or estimates is None:
            self._fit_estimate_table_height()
            return

        estimates_match_mode = self.estimated_use_hardware == bool(self.encoding_mode.currentData())
        if estimates_match_mode:
            self.quick_analysis_note.setText(
                f"以下为 {self._quality_description(self.estimated_use_hardware)} 的样片估算，实际输出可能有差异。"
                "预计大于源文件的项目以红色标出并禁用；点击其他行即可设置目标分辨率。"
            )
        else:
            self.quick_analysis_note.setText(
                f"现有数据按 {self._quality_description(self.estimated_use_hardware)} 估算，当前为 "
                f"{self._quality_description()}；请重新分析以更新体积和超出标记。"
            )
        for row, estimate in enumerate(estimates):
            estimated_size = int(estimate["estimated_size"])
            oversized = estimates_match_mode and estimated_size > info.file_size
            ratio = estimated_size / max(info.file_size, 1) * 100
            values = (
                str(estimate["resolution"]),
                f"{int(estimate['width'])}×{int(estimate['height'])}",
                f"{float(estimate['vmaf']):.1f}",
                f"{decimal_megabytes(estimated_size)} ({ratio:.1f}%)",
            )
            self.estimate_table.insertRow(row)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(estimate["resolution"]))
                if oversized:
                    item.setForeground(QColor("#b42318"))
                    item.setToolTip("预计体积大于源文件，不能从分析结果中选择此分辨率。")
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                else:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.estimate_table.setItem(row, column, item)
            if str(estimate["resolution"]) == self.resolution.currentText() and not oversized:
                self.estimate_table.selectRow(row)
        self._fit_estimate_table_height()

    def _fit_estimate_table_height(self):
        self.estimate_table.resizeRowsToContents()
        if self.estimate_table.rowCount():
            rows_height = sum(
                self.estimate_table.rowHeight(row)
                for row in range(self.estimate_table.rowCount())
            )
            content_height = self.estimate_table.horizontalHeader().height() + rows_height
            content_height += 2 * self.estimate_table.frameWidth() + 4
            height = min(230, max(105, content_height))
        else:
            height = 105
        self.estimate_table.setFixedHeight(height)

    def _update_resolution_item_states(self):
        estimates = self.estimated_sizes if self.info and self.estimated_sizes_path == self.info.path else None
        if self.estimated_use_hardware != bool(self.encoding_mode.currentData()):
            estimates = None
        estimate_by_size = {}
        if self.info and estimates is not None:
            estimate_by_size = {
                (int(estimate["width"]), int(estimate["height"])): int(estimate["estimated_size"])
                for estimate in estimates
            }
        for index in range(self.resolution.count()):
            model_item = self.resolution.model().item(index)
            if model_item is None:
                continue
            model_item.setFlags(self._resolution_item_flags[index])
            model_item.setData(None, Qt.ItemDataRole.ForegroundRole)
            model_item.setToolTip("")
            if not self.info or estimates is None:
                continue
            size = dimensions_for(self.info, self.resolution.itemText(index))
            estimated_size = estimate_by_size.get(size)
            if estimated_size is not None and estimated_size > self.info.file_size:
                model_item.setFlags(self._resolution_item_flags[index] & ~Qt.ItemFlag.ItemIsEnabled)
                model_item.setForeground(QColor("#b42318"))
                model_item.setToolTip("预计输出体积大于源文件，已禁用此分辨率。")

    def _current_estimate_is_larger(self) -> bool:
        if (not self.info or self.estimated_sizes_path != self.info.path or self.estimated_sizes is None
                or self.estimated_use_hardware != bool(self.encoding_mode.currentData())):
            return False
        size = dimensions_for(self.info, self.resolution.currentText())
        estimate = next(
            (item for item in self.estimated_sizes
             if (int(item["width"]), int(item["height"])) == size),
            None,
        )
        return bool(estimate and int(estimate["estimated_size"]) > self.info.file_size)

    def _apply_estimated_resolution(self, row: int, _column: int):
        item = self.estimate_table.item(row, 0)
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        resolution = str(item.data(Qt.ItemDataRole.UserRole))
        self.resolution.setCurrentText(resolution)
        self.window.status_label.setText(f"已选择 {resolution} 分辨率。")

    def quick_analyze(self):
        if not self.info or self.info.path != self.current_path:
            return
        self._start_quick_analysis(self.info.path, bool(self.encoding_mode.currentData()))

    def _start_quick_analysis(self, source: Path, use_hardware: bool):

        def operation(cancel, report):
            try:
                estimates = self.window.runner.analyze_conversion_estimates(
                    source, cancel, report, use_hardware_encoding=use_hardware,
                )
            except HardwareEncoderError as exc:
                return {"hardware_error": str(exc)}
            return {"estimates": estimates, "use_hardware": use_hardware}

        def finished(result):
            if not result or self.current_path != source:
                return
            if result.get("hardware_error"):
                choice = QMessageBox.question(
                    self, "硬件编码失败",
                    "硬件编码无法完成样片分析。是否切换到软件编码后重新分析？\n\n"
                    + str(result["hardware_error"])[-700:],
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if choice == QMessageBox.StandardButton.Yes:
                    self._start_quick_analysis(source, False)
                else:
                    self.window.status_label.setText("样片分析已取消；编码方式仍为硬件编码。")
                return
            estimates = result.get("estimates")
            if estimates:
                self.estimated_sizes = estimates
                self.estimated_sizes_path = source
                self.estimated_use_hardware = bool(result.get("use_hardware"))
                self._update_resolution_item_states()
                self._refresh_estimate_table()
                self._update_controls()

        self.window.run_task("正在分析视频并预计体积…", operation, finished)

    def _settings_changed(self, _value: str):
        self._refresh_info()
        self._update_controls()

    def _quality_description(self, use_hardware: bool | None = None) -> str:
        if use_hardware is None:
            use_hardware = bool(self.encoding_mode.currentData())
        return f"Intel QSV ICQ {VIDEO_CRF}" if use_hardware else f"H.264 CRF {VIDEO_CRF}（CPU）"

    def _encoder_tag(self, use_hardware: bool | None = None) -> str:
        if use_hardware is None:
            use_hardware = bool(self.encoding_mode.currentData())
        return f"QSV{VIDEO_CRF}" if use_hardware else f"CRF{VIDEO_CRF}"

    def _update_quality_label(self):
        self.crf_label.setText(f"质量参数：{self._quality_description()}")

    def _encoding_mode_changed(self, _index: int):
        self._update_quality_label()
        self._refresh_estimate_table()
        self._update_resolution_item_states()
        self._update_controls()

    def _container_changed(self, _value: str):
        self.output_location = None
        self.output_label.setText("开始转换时选择保存位置")

    def choose_output(self):
        paths = self._paths()
        if not paths:
            return
        if len(paths) == 1:
            if self.info and self.info.path == paths[0]:
                try:
                    width, height = dimensions_for(self.info, self.resolution.currentText())
                    suffix = f"转换_{width}x{height}_{self._encoder_tag()}"
                except ValueError:
                    suffix = "转换"
                initial = default_output_path(paths[0], self.container.currentText(), suffix)
            else:
                initial = default_output_path(paths[0], self.container.currentText(), "转换")
            selected, _ = QFileDialog.getSaveFileName(
                self, "选择输出文件", str(initial),
                "MP4 视频 (*.mp4)" if self.container.currentText() == "MP4" else "MKV 视频 (*.mkv)",
            )
            if not selected:
                return
            suffix = ".mp4" if self.container.currentText() == "MP4" else ".mkv"
            self.output_location = Path(selected).with_suffix(suffix)
        else:
            selected = QFileDialog.getExistingDirectory(self, "选择输出文件夹", str(paths[0].parent))
            if not selected:
                return
            self.output_location = Path(selected)
        self.output_label.setText(str(self.output_location))

    def start_conversion(self):
        if self._current_estimate_is_larger():
            self.window.status_label.setText("当前分辨率预计体积大于源文件，请从分析结果中选择其他分辨率。")
            return
        paths = self._paths()
        if not paths:
            return
        if self.output_location is None:
            self.choose_output()
            if self.output_location is None:
                return
        self._launch_conversion(paths, self.output_location, batch=len(paths) > 1)

    def start_batch_conversion(self):
        if self._current_estimate_is_larger():
            self.window.status_label.setText("当前分辨率预计体积大于源文件，请从分析结果中选择其他分辨率。")
            return
        source_root = self.current_path.parent if self.current_path else None
        if source_root is None or not source_root.is_dir():
            self.window.status_label.setText("请先选择一个有效视频。")
            return

        paths = sorted(
            path for path in source_root.rglob("*")
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
        )
        if not paths:
            self.window.status_label.setText("所选视频文件夹中没有可转换的视频。")
            return

        output_base = source_root.parent / "压缩后"
        output_dir = output_base
        number = 2
        while output_dir.exists():
            output_dir = source_root.parent / f"压缩后_{number}"
            number += 1

        self.output_label.setText(f"批量输出：{output_dir}")
        self._launch_conversion(
            paths, output_dir, batch=True, source_root=source_root,
            create_output_directory=True,
        )

    def _launch_conversion(
        self,
        paths: list[Path],
        location: Path,
        *,
        batch: bool,
        source_root: Path | None = None,
        create_output_directory: bool = False,
        settings_override: ExportSettings | None = None,
        initial_summary: dict | None = None,
        initial_reserved: set[str] | None = None,
        progress_offset: int = 0,
        total_count: int | None = None,
    ):
        settings = settings_override or self._settings()
        skip_existing = self.skip_existing.isChecked()
        extension = ".mp4" if settings.container == "MP4" else ".mkv"
        total_count = total_count or (len(paths) + progress_offset)

        def operation(cancel, report):
            summary = dict(initial_summary) if initial_summary else {
                "converted": 0, "skipped": 0, "discarded": 0, "entries": [],
            }
            summary["entries"] = list(summary.get("entries", []))
            reserved: set[str] = set(initial_reserved or ())
            output_dir = location if batch else location.parent
            if create_output_directory:
                output_dir.mkdir(parents=True, exist_ok=False)
            for local_index, source in enumerate(paths):
                index = progress_offset + local_index
                if cancel.is_set():
                    raise RuntimeError("已取消")
                report(index / total_count, f"正在读取第 {index + 1}/{total_count} 个视频信息")
                source_info = probe_media(source, self.window.runner.ffprobe)
                if cancel.is_set():
                    raise RuntimeError("已取消")
                width, height = dimensions_for(source_info, settings.resolution)
                output_tag = f"{width}x{height}_{self._encoder_tag(settings.use_hardware_encoding)}"
                if batch:
                    destination_dir = output_dir
                    if source_root is not None:
                        destination_dir = output_dir / source.parent.relative_to(source_root)
                        destination_dir.mkdir(parents=True, exist_ok=True)
                    base = destination_dir / f"{source.stem}_转换_{output_tag}{extension}"
                    candidate = base
                    number = 2
                    while str(candidate.resolve()).lower() in reserved:
                        candidate = destination_dir / f"{source.stem}_转换_{output_tag}_{number}{extension}"
                        number += 1
                    if candidate.exists() and skip_existing:
                        reserved.add(str(candidate.resolve()).lower())
                        summary["skipped"] += 1
                        summary["entries"].append({
                            "path": candidate, "status": "跳过（已存在）",
                            "output_size": candidate.stat().st_size, "source_size": source_info.file_size,
                        })
                        report((index + 1) / total_count, f"跳过已存在：{candidate.name}")
                        continue
                    while candidate.exists():
                        candidate = destination_dir / f"{source.stem}_转换_{output_tag}_{number}{extension}"
                        number += 1
                else:
                    candidate = location.with_suffix(extension)
                    if candidate.exists() and skip_existing:
                        summary["skipped"] += 1
                        summary["entries"].append({
                            "path": candidate, "status": "跳过（已存在）",
                            "output_size": candidate.stat().st_size, "source_size": source_info.file_size,
                        })
                        report((index + 1) / total_count, "跳过已存在的输出文件")
                        continue
                    if candidate.exists():
                        raise FileExistsError(f"输出文件已存在：{candidate}")
                reserved.add(str(candidate.resolve()).lower())

                def one_progress(fraction: float, stage: str):
                    current_fraction = min(1.0, max(0.0, fraction))
                    total_fraction = (index + current_fraction) / total_count
                    if batch:
                        status = (
                            f"总进度 {int(total_fraction * 100)}% · 当前视频 {index + 1}/{total_count} "
                            f"（{int(current_fraction * 100)}%） · {stage or source.name}"
                        )
                    else:
                        status = f"{int(current_fraction * 100)}% · {stage or source.name}"
                    report(total_fraction, status)

                discarded_size = None

                def on_discarded(size: int):
                    nonlocal discarded_size
                    discarded_size = size

                try:
                    result = self.window.runner.convert(
                        source, candidate, settings, cancel, one_progress, on_discarded=on_discarded,
                    )
                except HardwareEncoderError as exc:
                    reserved.discard(str(candidate.resolve()).lower())
                    if create_output_directory:
                        summary["output_directory"] = str(output_dir)
                    summary["hardware_fallback"] = {
                        "error": str(exc),
                        "remaining_paths": paths[local_index:],
                        "location": location,
                        "batch": batch,
                        "source_root": source_root,
                        "reserved": reserved,
                        "progress_offset": index,
                        "total_count": total_count,
                        "settings": settings,
                    }
                    return summary
                if result is None:
                    summary["discarded"] += 1
                    summary["entries"].append({
                        "path": candidate,
                        "status": "已删除（大于源文件）" if discarded_size is not None else "已删除",
                        "output_size": discarded_size, "source_size": source_info.file_size,
                    })
                else:
                    summary["converted"] += 1
                    summary["entries"].append({
                        "path": candidate,
                        "status": "软件编码完成（确认回退）"
                        if summary.get("software_fallback_confirmed") else "转换完成",
                        "output_size": result.file_size, "source_size": source_info.file_size,
                    })
            if create_output_directory:
                summary["output_directory"] = str(output_dir)
            return summary

        title = "正在批量转换文件夹视频…" if create_output_directory else "正在转换视频…"
        self.window.run_task(title, operation, self._conversion_done)

    def _settings(self) -> ExportSettings:
        return ExportSettings(
            container=self.container.currentText(),
            resolution=self.resolution.currentText(),
            discard_if_larger=self.discard_if_larger.isChecked(),
            use_hardware_encoding=bool(self.encoding_mode.currentData()),
        )

    def _conversion_done(self, summary):
        if summary:
            self._append_conversion_log(summary.get("entries", []))
            hardware_fallback = summary.pop("hardware_fallback", None)
            output_note = f" 输出位置：{summary['output_directory']}" if summary.get("output_directory") else ""
            if hardware_fallback:
                choice = QMessageBox.question(
                    self, "硬件编码失败",
                    "硬件编码无法处理当前视频。是否切换到软件编码（CPU）并从此视频继续？\n"
                    "本次已完成的视频不会重新转换。\n\n"
                    + str(hardware_fallback["error"])[-700:],
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if choice == QMessageBox.StandardButton.Yes:
                    resume_summary = dict(summary)
                    resume_summary["entries"] = []
                    resume_summary["software_fallback_confirmed"] = True
                    self._launch_conversion(
                        hardware_fallback["remaining_paths"],
                        hardware_fallback["location"],
                        batch=hardware_fallback["batch"],
                        source_root=hardware_fallback["source_root"],
                        settings_override=replace(
                            hardware_fallback["settings"], use_hardware_encoding=False,
                        ),
                        initial_summary=resume_summary,
                        initial_reserved=set(hardware_fallback["reserved"]),
                        progress_offset=hardware_fallback["progress_offset"],
                        total_count=hardware_fallback["total_count"],
                    )
                else:
                    self.window.status_label.setText(
                        f"已取消软件编码回退；成功 {summary['converted']} 项，跳过 {summary['skipped']} 项。"
                        f"{output_note}"
                    )
                return
            self.window.status_label.setText(
                f"转换完成：成功 {summary['converted']} 项，跳过 {summary['skipped']} 项，"
                f"因大于源文件删除 {summary['discarded']} 项。"
                f"{'硬件失败后已按确认改用软件编码。' if summary.get('software_fallback_confirmed') else ''}"
                f"{output_note}"
            )

    def _append_conversion_log(self, entries):
        for entry in entries:
            row = self.conversion_log.rowCount()
            self.conversion_log.insertRow(row)
            output_size = entry.get("output_size")
            source_size = int(entry.get("source_size") or 0)
            if output_size is None:
                size_text = ratio_text = "—"
            else:
                output_size = int(output_size)
                size_text = decimal_megabytes(output_size)
                ratio_text = f"{output_size / max(source_size, 1) * 100:.1f}%"
            path = Path(entry["path"])
            values = (path.name, str(entry["status"]), size_text, ratio_text)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(path))
                self.conversion_log.setItem(row, column, item)
        if entries:
            self.conversion_log.scrollToBottom()

    def remove_selected(self):
        row = self.file_list.currentRow()
        if row >= 0:
            self.file_list.takeItem(row)
            self.output_location = None
            self.output_label.setText("开始转换时选择保存位置")
            if self.file_list.count():
                self.file_list.setCurrentRow(min(row, self.file_list.count() - 1))
            else:
                self._select_row(-1)
            self._update_controls()

    def clear_queue(self):
        self.file_list.clear()
        self.output_location = None
        self._select_row(-1)
        self.output_label.setText("开始转换时选择保存位置")

    def _update_controls(self):
        busy = self.window.task_active
        for control in (self.file_list, self.add_files_button, self.add_folder_button, self.remove_button,
                        self.clear_button, self.container, self.resolution,
                        self.encoding_mode, self.skip_existing, self.discard_if_larger, self.quick_analysis_button,
                        self.batch_convert_button, self.estimate_table):
            control.setEnabled(not busy)
        single_ready = self.file_list.count() != 1 or (
            self.info is not None and self.current_path == self.info.path
        )
        has_info = self.info is not None and self.current_path == self.info.path
        self.quick_analysis_button.setEnabled(not busy and has_info)
        has_selected_file = self.current_path is not None and self.current_path.is_file()
        can_use_resolution = not self._current_estimate_is_larger()
        self.output_button.setEnabled(not busy and single_ready and can_use_resolution)
        self.batch_convert_button.setEnabled(not busy and has_selected_file and can_use_resolution)
        self.convert_button.setEnabled(bool(self.file_list.count()) and not busy and single_ready and can_use_resolution)

    def set_busy(self, _busy: bool):
        self._update_controls()

    def pause(self):
        pass

