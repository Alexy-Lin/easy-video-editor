# Easy Video Editor

## 中文

Easy Video Editor 是一款本地运行的 Windows 桌面视频工具，提供分割剪辑、合并视频和格式转换。项目使用 Python、PySide6、Qt Multimedia、FFprobe 与 FFmpeg，不使用浏览器、云服务或数据库。

### 功能

- **分割剪辑**：预览源视频，使用预览下方的彩色时间轴标记保留和删除片段；支持按播放位置或输入时间分割、标记删除、恢复、撤销和重置。空格控制播放/暂停，左右键逐帧移动。
- **合并视频**：按列表顺序合并两个或更多视频；输出尺寸和帧率取第一个视频，其他视频按比例缩放并补黑边。
- **格式转换**：支持 MP4/MKV 和原始尺寸、480P、720P、1080P、2K/1440P；默认使用 Intel QSV 硬件编码，也可手动选用 libx264 软件编码。
- **批量转换**：支持队列转换，也可递归转换当前文件夹下的支持视频，并将结果保存到上级目录新建的“压缩后”文件夹。显示总进度和当前视频进度。
- **视频分析与体积预计**：按当前编码方式抽样比较可用分辨率，显示样片 VMAF 和预计体积；预计大于源文件的选项会标红并禁用。转换日志记录结果、实际体积和占源文件比例。
- **编码回退与文件保护**：硬件编码失败时先询问是否回退至 CPU 软件编码。输出经过校验后才发布；可选择自动删除大于源文件的结果，源视频始终保留。

转换视频默认使用 H.264 Intel QSV ICQ 23；软件编码使用 H.264 libx264 CRF 23。有音轨时编码为 AAC。ICQ 23 与 CRF 23 是各编码器自己的质量参数，不代表相同画质；实际体积取决于编码器、画面复杂度和分辨率，预估值不等于最终体积。

### 环境与启动

- Windows 10 或更高版本
- Python 3.10 或更高版本
- PySide6 `>=6.8,<7`
- FFmpeg 与 FFprobe

创建虚拟环境并安装 Python 依赖：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

从 [gyan.dev FFmpeg Builds](https://www.gyan.dev/ffmpeg/builds/) 下载 Windows x64 Essentials build，并将 `ffmpeg.exe` 和 `ffprobe.exe` 放进项目的 `tools` 文件夹；也可以把它们加入系统 `PATH`。硬件编码需要兼容的 Intel 显卡和驱动。程序也会查找本机 LosslessCut 的 `resources` 目录。

仓库源码不托管 FFmpeg 可执行文件，因为它们超过 GitHub 单文件大小限制；克隆项目后需按上述步骤自行下载。Windows 便携版可从 [GitHub Releases](https://github.com/Alexy-Lin/easy-video-editor/releases) 下载，压缩包包含应用文件、FFmpeg 9.0.2 和对应许可证/构建说明。当前开发环境使用的构建信息也保存在 `tools/FFmpeg-README.txt` 与 `tools/FFmpeg-LICENSE.txt`。

运行源码：

```powershell
.\.venv\Scripts\python.exe video_cut.py
```

也可双击 `Easy Video Editor.bat`，使用项目虚拟环境启动。

### 处理说明

- 分割时间按源视频帧率对齐。剪辑标记只更新片段列表，导出时才重新编码。
- 合并时，视频统一到第一个视频的尺寸和帧率；无音轨输入会补静音以便统一拼接。
- 转换按所选尺寸限制输出，不会放大源视频。即使有损压缩，输出也可能大于源文件。
- 视频分析会抽取最多三段样片，按当前编码器和质量参数估算各可用分辨率的输出体积。
- HDR 色调映射、字幕、滤镜、转场、多轨编辑、项目保存和无损换容器不在当前范围内。
- Qt 预览用于日常定位，不是严格逐帧解码器；实际导出由 FFmpeg 处理。

### 许可证

本仓库中的 Easy Video Editor 应用源码采用 [Apache License 2.0](LICENSE)。FFmpeg 是独立的第三方程序，许可证取决于所下载的构建；本地开发环境随附的 FFmpeg 9.0.2 构建启用了 GPL 组件，适用 GPLv3，详情见 `tools/FFmpeg-LICENSE.txt`。本仓库不分发 FFmpeg 可执行文件。重新分发 FFmpeg 时，请一并遵守对应构建的许可证和源代码提供要求。

### 开发验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

单元测试覆盖片段分割与保留、尺寸计算和压缩预算。真实视频处理需要本机 FFmpeg/FFprobe；Qt 原生预览需在 Windows 桌面环境验收。

---

## English

Easy Video Editor is a local Windows desktop app for cutting, merging, and converting videos. It is built with Python, PySide6, Qt Multimedia, FFprobe, and FFmpeg. It does not use a browser, cloud service, or database.

### Features

- **Cut and edit**: Preview a video and mark retained or deleted segments on the color-coded timeline. Split at the playhead or an entered time; mark, restore, undo, and reset segments. Use Space to play or pause and the arrow keys to step one frame at a time.
- **Merge videos**: Join two or more videos in list order. The first video determines the output size and frame rate; other videos are scaled proportionally and padded with black bars.
- **Convert videos**: Supports MP4/MKV and original size, 480p, 720p, 1080p, and 2K/1440p. Intel QSV hardware encoding is the default; libx264 software encoding is also available.
- **Batch conversion**: Convert a queue, or recursively convert supported videos in the current folder into a new `Compressed` folder in its parent. The UI shows both overall and current-video progress.
- **Analysis and size estimates**: Sample the video using the selected encoder, compare available resolutions, and show sample VMAF and estimated output size. Options estimated to exceed the source are marked red and disabled. The conversion log records the result, actual size, and source-size ratio.
- **Encoder fallback and file safety**: If hardware encoding fails, the app asks before falling back to CPU encoding. Outputs are validated before publication. You can choose to delete results larger than their sources; source videos are kept.

The default video settings are H.264 Intel QSV ICQ 23 or, in software mode, H.264 libx264 CRF 23. Audio is encoded as AAC when present. ICQ 23 and CRF 23 are encoder-specific quality settings and do not guarantee identical quality. Actual output size depends on the encoder, content, and resolution; estimates are not guarantees.

### Requirements and setup

- Windows 10 or later
- Python 3.10 or later
- PySide6 `>=6.8,<7`
- FFmpeg and FFprobe

Create a virtual environment and install the Python dependencies:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Download a Windows x64 Essentials build from [gyan.dev FFmpeg Builds](https://www.gyan.dev/ffmpeg/builds/) and place `ffmpeg.exe` and `ffprobe.exe` in the project's `tools` folder, or add them to `PATH`. Hardware encoding requires a compatible Intel GPU and driver. The app also checks LosslessCut's local `resources` directory.

The source repository does not host FFmpeg executables because they exceed GitHub's per-file size limit. Download them as described above when building from source. A Windows portable package is available from [GitHub Releases](https://github.com/Alexy-Lin/easy-video-editor/releases); it includes the app files, FFmpeg 9.0.2, and the corresponding license/build notices. Build details for the version used in the current development environment are also in `tools/FFmpeg-README.txt` and `tools/FFmpeg-LICENSE.txt`.

Run from source:

```powershell
.\.venv\Scripts\python.exe video_cut.py
```

You can also double-click `Easy Video Editor.bat` to launch the app with the project virtual environment.

### Processing notes

- Split times are aligned to the source frame rate. Editing markers only update the segment list; export re-encodes the selected segments.
- During merging, video is normalized to the first video's size and frame rate. Silent audio is added when needed for consistent concatenation.
- Conversion limits output to the selected size and does not upscale. A lossy encode can still be larger than its source.
- Analysis samples up to three sections and estimates output sizes for available resolutions using the selected encoder and quality settings.
- HDR tone mapping, subtitles, filters, transitions, multi-track editing, project saving, and lossless container remuxing are outside the current scope.
- Qt preview is for everyday navigation, not frame-accurate decoding. FFmpeg performs the actual export.

### License

The Easy Video Editor application source in this repository is licensed under the [Apache License 2.0](LICENSE). FFmpeg is a separate third-party program, and its license depends on the build you download. The FFmpeg 9.0.2 build used in the local development environment enables GPL components and is covered by GPLv3; see `tools/FFmpeg-LICENSE.txt`. This repository does not distribute FFmpeg executables. If you redistribute FFmpeg, comply with the applicable license and corresponding-source requirements for that build.

### Development checks

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Unit tests cover segment splitting and retention, dimension calculations, and compression budgeting. Actual media processing requires local FFmpeg/FFprobe; native Qt preview should be checked in a Windows desktop session.
