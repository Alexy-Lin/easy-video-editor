"""Local media probing, editing models, and FFmpeg processing for Easy Video Editor."""
from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable, Sequence


VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm", ".ts", ".mts", ".m2ts"}
RESOLUTION_BOUNDS = {
    "480P": (854, 480),
    "720P": (1280, 720),
    "1080P": (1920, 1080),
    "2K / 1440P": (2560, 1440),
}
VIDEO_CRF = 23
ProgressCallback = Callable[[float, str], None]


class HardwareEncoderError(RuntimeError):
    """Raised when the selected hardware encoder cannot process the video."""


class HardwareDecoderError(RuntimeError):
    """Raised when QSV hardware decoding cannot process the video."""


class LosslessExportError(RuntimeError):
    """Raised when lossless stream-copy export is not compatible with the inputs."""


@dataclass(frozen=True)
class HardwareEncoderProfile:
    encoder_id: str
    label: str
    vendor_id: str
    gpu_name: str
    arguments: tuple[str, ...]


_HARDWARE_ENCODERS = {
    "h264_qsv": ("Intel QSV", ("-preset", "fast", "-global_quality", str(VIDEO_CRF), "-pix_fmt", "nv12")),
    "h264_nvenc": ("NVIDIA NVENC", ("-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(VIDEO_CRF), "-b:v", "0", "-pix_fmt", "nv12")),
    "h264_amf": ("AMD AMF", ("-quality", "balanced", "-rc", "cqp", "-qp_i", str(VIDEO_CRF), "-qp_p", str(VIDEO_CRF), "-pix_fmt", "nv12")),
}
_VENDOR_ENCODERS = {"8086": "h264_qsv", "10DE": "h264_nvenc", "1002": "h264_amf", "1022": "h264_amf"}


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    deleted: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration: float
    video_duration: float
    audio_duration: float | None
    file_size: int
    video_stream_index: int
    audio_stream_index: int | None
    width: int
    height: int
    display_width: int
    display_height: int
    fps: Fraction
    video_codec: str
    audio_codec: str | None
    video_bitrate: int | None
    audio_bitrate: int | None
    container_bitrate: int | None
    has_audio: bool
    sample_aspect_ratio: Fraction
    rotation: int
    start_time: float = 0.0
    video_profile: str | None = None
    video_pixel_format: str | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    audio_channel_layout: str | None = None


@dataclass(frozen=True)
class ExportSettings:
    container: str = "MP4"
    resolution: str = "原始"
    discard_if_larger: bool = False
    use_hardware_encoding: bool = True
    # Kept for callers of the earlier size-target API. Conversion no longer
    # uses a target byte percentage or automatic resolution selection.
    mode: str = "画质优先"
    size_percent: int = 50
    automatic_resolution: bool = True


def parse_fraction(value: object, default: Fraction = Fraction(0, 1)) -> Fraction:
    try:
        if value in (None, "", "N/A"):
            return default
        result = Fraction(str(value).replace(":", "/"))
        return result if result.denominator else default
    except (ValueError, ZeroDivisionError, TypeError):
        return default


def optional_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
        return number if number > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def find_ffmpeg_tools() -> tuple[str, str]:
    """Search project tools, LosslessCut resources, then PATH."""
    roots = [Path(__file__).resolve().parent]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)
    ffmpeg_names = ("ffmpeg.exe", "ffmpeg") if os.name == "nt" else ("ffmpeg",)
    ffprobe_names = ("ffprobe.exe", "ffprobe") if os.name == "nt" else ("ffprobe",)
    directories = [
        folder
        for root in roots
        for folder in (root / "tools", root / "tools" / "ffmpeg", root)
    ]
    for env_name in ("LOSSLESSCUT_PATH", "LOSSLESS_CUT_PATH"):
        configured = os.environ.get(env_name)
        if configured:
            candidate = Path(configured)
            if candidate.is_file():
                candidate = candidate.parent
            directories.extend((candidate, candidate / "resources", candidate / "resources" / "app.asar.unpacked" / "resources"))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_app = os.environ.get("LOCALAPPDATA")
    directories.extend((
        program_files / "LosslessCut" / "resources",
        program_files / "LosslessCut" / "resources" / "app.asar.unpacked" / "resources",
    ))
    if local_app:
        install = Path(local_app) / "Programs" / "LosslessCut"
        directories.extend((install / "resources", install / "resources" / "app.asar.unpacked" / "resources"))

    local_ffmpeg = next((str(folder / name) for folder in directories for name in ffmpeg_names if (folder / name).is_file()), None)
    local_ffprobe = next((str(folder / name) for folder in directories for name in ffprobe_names if (folder / name).is_file()), None)
    ffmpeg = local_ffmpeg or shutil.which("ffmpeg")
    ffprobe = local_ffprobe or shutil.which("ffprobe")
    missing = [name for name, found in (("FFmpeg", ffmpeg), ("FFprobe", ffprobe)) if not found]
    if missing:
        raise FileNotFoundError("找不到" + "和".join(missing) + "。请放入项目 tools 目录，或安装 LosslessCut / FFmpeg。")
    return str(ffmpeg), str(ffprobe)


def probe_media(path: str | Path, ffprobe: str | None = None) -> MediaInfo:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到视频文件：{path}")
    if ffprobe is None:
        _, ffprobe = find_ffmpeg_tools()
    command = [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or "无法读取媒体信息")[-1800:])
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    if not videos:
        raise ValueError("文件中没有可用的视频轨道。")
    video = videos[0]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    audio = audios[0] if audios else None
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("视频尺寸信息无效。")
    sar = parse_fraction(video.get("sample_aspect_ratio"), Fraction(1, 1)) or Fraction(1, 1)
    rotation = 0
    for side_data in video.get("side_data_list", []):
        try:
            rotation = int(round(float(side_data.get("rotation", 0)))) % 360
        except (TypeError, ValueError):
            continue
    if not rotation:
        try:
            rotation = int(round(float(video.get("tags", {}).get("rotate", 0)))) % 360
        except (TypeError, ValueError):
            pass
    display_width, display_height = max(1, round(width * float(sar))), height
    if rotation in (90, 270):
        display_width, display_height = display_height, display_width
    fmt = payload.get("format", {})
    media_start_time = None
    try:
        candidate = float(fmt.get("start_time"))
        if math.isfinite(candidate):
            media_start_time = candidate
    except (ValueError, TypeError):
        pass
    if media_start_time is None:
        stream_starts = []
        for stream in streams:
            try:
                stream_start = float(stream.get("start_time"))
                if math.isfinite(stream_start):
                    stream_starts.append(stream_start)
            except (ValueError, TypeError):
                continue
        media_start_time = min(stream_starts, default=0.0)
    try:
        video_duration = float(video.get("duration") or 0)
    except (ValueError, TypeError):
        video_duration = 0.0
    try:
        audio_duration = float(audio.get("duration") or 0) if audio else None
    except (ValueError, TypeError):
        audio_duration = 0.0 if audio else None
    durations = []
    for value in (fmt.get("duration"), video_duration, audio_duration):
        try:
            number = float(value) if value is not None else 0.0
            if number > 0:
                durations.append(number)
        except (ValueError, TypeError):
            pass
    duration = max(durations, default=0.0)
    if duration <= 0:
        raise ValueError("无法读取视频时长。")
    fps = parse_fraction(video.get("avg_frame_rate"), parse_fraction(video.get("r_frame_rate"), Fraction(25, 1)))
    if fps <= 0 or float(fps) > 1000:
        fps = Fraction(25, 1)
    return MediaInfo(
        path=path, duration=duration, video_duration=video_duration or duration,
        audio_duration=audio_duration, file_size=path.stat().st_size,
        video_stream_index=int(video.get("index", 0)),
        audio_stream_index=int(audio["index"]) if audio else None,
        width=width, height=height, display_width=display_width, display_height=display_height,
        fps=fps, video_codec=str(video.get("codec_name", "未知")),
        audio_codec=str(audio.get("codec_name")) if audio else None,
        video_bitrate=optional_int(video.get("bit_rate")),
        audio_bitrate=optional_int(audio.get("bit_rate")) if audio else None,
        container_bitrate=optional_int(fmt.get("bit_rate")), has_audio=audio is not None,
        sample_aspect_ratio=sar, rotation=rotation, start_time=media_start_time,
        video_profile=str(video.get("profile")) if video.get("profile") else None,
        video_pixel_format=str(video.get("pix_fmt")) if video.get("pix_fmt") else None,
        audio_sample_rate=optional_int(audio.get("sample_rate")) if audio else None,
        audio_channels=optional_int(audio.get("channels")) if audio else None,
        audio_channel_layout=str(audio.get("channel_layout")) if audio and audio.get("channel_layout") else None,
    )


def probe_keyframe_times(path: str | Path, ffprobe: str | None = None,
                         media_start_time: float = 0.0,
                         cancel: threading.Event | None = None) -> list[float]:
    """Return video keyframe times relative to the media's start without decoding frames."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到视频文件：{path}")
    if ffprobe is None:
        _, ffprobe = find_ffmpeg_tools()
    command = [
        ffprobe, "-v", "error", "-select_streams", "V:0", "-show_packets",
        "-show_entries", "packet=pts_time,dts_time,flags", "-of", "compact=p=0:nk=0", str(path),
    ]
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        startupinfo=startupinfo, creationflags=creationflags,
    )
    timestamps: set[float] = set()
    try:
        if process.stdout is None:
            raise RuntimeError("无法读取 FFprobe 的关键帧扫描结果。")
        for line in process.stdout:
            if cancel and cancel.is_set():
                process.terminate()
                raise RuntimeError("已取消")
            fields = {}
            for field in line.strip().split("|"):
                if "=" in field:
                    key, value = field.split("=", 1)
                    fields[key] = value
            if "K" not in fields.get("flags", ""):
                continue
            value = fields.get("pts_time") or fields.get("dts_time")
            try:
                timestamp = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(timestamp):
                timestamps.add(max(0.0, timestamp - media_start_time))
        code = process.wait()
        if cancel and cancel.is_set():
            raise RuntimeError("已取消")
        if code:
            raise RuntimeError(f"FFprobe 关键帧扫描失败（退出码 {code}）。")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()
    keyframes = sorted(timestamps)
    if not keyframes:
        raise ValueError("视频中没有检测到可用关键帧。")
    return keyframes


def align_time_to_frame(seconds: float, fps: Fraction) -> float:
    rate = float(fps) if fps else 25.0
    return round(seconds * rate) / rate


def split_at(segments: Sequence[Segment], seconds: float, fps: Fraction, duration: float,
             align_to_frame: bool = True) -> tuple[list[Segment], int | None]:
    aligned = align_time_to_frame(seconds, fps) if align_to_frame else seconds
    point = min(duration, max(0.0, aligned))
    tolerance = 0.5 / max(float(fps), 1.0)
    result: list[Segment] = []
    split_index: int | None = None
    for segment in segments:
        if segment.start + tolerance < point < segment.end - tolerance:
            split_index = len(result)
            result.extend((Segment(segment.start, point, segment.deleted), Segment(point, segment.end, segment.deleted)))
        else:
            result.append(segment)
    return result, split_index


def nearest_keyframe_time(seconds: float, keyframes: Sequence[float], start: float, end: float,
                          fps: Fraction) -> float | None:
    """Snap to the closest keyframe that can create a non-empty split in this segment."""
    tolerance = 0.5 / max(float(fps), 1.0)
    candidates = [point for point in keyframes if start + tolerance < point < end - tolerance]
    if not candidates:
        return None
    return min(candidates, key=lambda point: (abs(point - seconds), point))


def set_deleted(segments: Sequence[Segment], index: int, deleted: bool) -> list[Segment]:
    result = list(segments)
    if 0 <= index < len(result):
        old = result[index]
        result[index] = Segment(old.start, old.end, deleted)
    return result


def retained_segments(segments: Sequence[Segment]) -> list[Segment]:
    return [segment for segment in segments if not segment.deleted and segment.duration > 0]


def calculate_target_bitrate(info: MediaInfo, percent: int) -> tuple[int, int]:
    """Return the output byte limit and video bit/s using decimal-byte math."""
    target_bytes = math.floor(info.file_size * min(100, max(1, percent)) / 100)
    available_bytes = math.floor(target_bytes * 0.97) - 16384
    video_bitrate = math.floor(available_bytes * 8 / info.duration) - (192_000 if info.has_audio else 0)
    if video_bitrate < 16_000:
        raise ValueError("目标体积预算过低（视频码率不足 16 kbps），请提高百分比。")
    return target_bytes, video_bitrate


def even_dimensions(width: int, height: int) -> tuple[int, int]:
    return max(2, int(width) // 2 * 2), max(2, int(height) // 2 * 2)


def fit_dimensions(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("视频尺寸无效。")
    scale = min(max_width / width, max_height / height, 1.0)
    return even_dimensions(round(width * scale), round(height * scale))


def dimensions_for(info: MediaInfo, resolution: str) -> tuple[int, int]:
    if resolution == "原始":
        return even_dimensions(info.display_width, info.display_height)
    bounds = RESOLUTION_BOUNDS.get(resolution)
    if bounds is None:
        return even_dimensions(info.display_width, info.display_height)
    if info.display_height > info.display_width:
        bounds = bounds[1], bounds[0]
    return fit_dimensions(info.display_width, info.display_height, *bounds)


def analysis_resolutions(info: MediaInfo) -> list[tuple[str, tuple[int, int]]]:
    """Return every distinct, non-upscaled conversion resolution, largest first."""
    candidates = [("原始", dimensions_for(info, "原始"))]
    candidates.extend((label, dimensions_for(info, label)) for label in RESOLUTION_BOUNDS)
    distinct: dict[tuple[int, int], str] = {}
    for label, size in candidates:
        distinct.setdefault(size, label)
    ordered = sorted(distinct.items(), key=lambda item: item[0][0] * item[0][1], reverse=True)
    return [(label, size) for size, label in ordered]


def recommend_resolution(info: MediaInfo, target_video_bitrate: int) -> tuple[str, tuple[int, int], float, bool]:
    candidates: dict[tuple[int, int], str] = {even_dimensions(info.display_width, info.display_height): "原始"}
    for label, bounds in RESOLUTION_BOUNDS.items():
        if info.display_height > info.display_width:
            bounds = bounds[1], bounds[0]
        size = fit_dimensions(info.display_width, info.display_height, *bounds)
        if size[0] <= info.display_width and size[1] <= info.display_height:
            candidates.setdefault(size, label)
    ordered = sorted(candidates.items(), key=lambda row: row[0][0] * row[0][1], reverse=True)
    for size, label in ordered:
        density = target_video_bitrate / (size[0] * size[1] * float(info.fps))
        if density >= 0.08:
            return label, size, density, False
    size, label = ordered[-1]
    density = target_video_bitrate / (size[0] * size[1] * float(info.fps))
    return label, size, density, True


def output_dimensions(info: MediaInfo, settings: ExportSettings) -> tuple[int, int]:
    if settings.mode == "体积优先" and settings.automatic_resolution:
        _target_bytes, bitrate = calculate_target_bitrate(info, settings.size_percent)
        label, _size, _density, _low = recommend_resolution(info, bitrate)
        return dimensions_for(info, label)
    return dimensions_for(info, settings.resolution)


def decimal_megabytes(size: int) -> str:
    return f"{size / 1_000_000:.2f} MB"


def recommendations(info: MediaInfo, target_video_bitrate: int | None = None) -> list[str]:
    result: list[str] = []
    if info.display_width < 854 or info.display_height < 480:
        result.append("放大不会增加细节，可以保留原始分辨率。")
    if max(info.display_width, info.display_height) >= 2560:
        result.append("源视频分辨率较高，可以考虑 1080P。")
    if info.video_bitrate is not None and info.video_bitrate < 500_000:
        result.append("源视频码率较低，继续压缩可能损失画质。")
    if info.video_codec.lower() in {"hevc", "h265", "av1", "vp9"}:
        result.append("HEVC、AV1 或 VP9 转为 H.264 不一定更省空间。")
    if target_video_bitrate is not None:
        density = target_video_bitrate / (info.display_width * info.display_height * float(info.fps))
        if density < 0.08:
            result.append("目标码率预算偏低，建议降低分辨率或提高目标体积百分比。")
    if not result:
        result.append("建议依据元信息生成；未分析抽样画面或运动复杂度。")
    return result


def default_output_path(source: Path | str, container: str, suffix: str) -> Path:
    source = Path(source).resolve()
    extension = ".mp4" if container.upper() == "MP4" else ".mkv"
    candidate = source.with_name(f"{source.stem}_{suffix}{extension}")
    number = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}_{suffix}_{number}{extension}")
        number += 1
    return candidate


def _concat_file_line(path: Path) -> str:
    normalized = str(path.resolve()).replace("\\", "/")
    return "file '" + normalized.replace("'", "'\\''") + "'\n"


def _publish_no_overwrite(temp_path: Path, destination: Path) -> None:
    """Publish on the destination volume and fail if an output appeared meanwhile."""
    if destination.exists():
        raise FileExistsError(f"输出文件已存在：{destination}")
    try:
        os.link(temp_path, destination)
    except OSError:
        # Reserve the name atomically as a portable fallback, then replace only
        # our own empty reservation with the validated file.
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        os.close(descriptor)
        try:
            os.replace(temp_path, destination)
        except Exception:
            try:
                if destination.stat().st_size == 0:
                    destination.unlink()
            except OSError:
                pass
            raise


class FFmpegRunner:
    def __init__(self, ffmpeg: str | None = None, ffprobe: str | None = None):
        found_ffmpeg, found_ffprobe = find_ffmpeg_tools() if not (ffmpeg and ffprobe) else (ffmpeg, ffprobe)
        self.ffmpeg = ffmpeg or found_ffmpeg
        self.ffprobe = ffprobe or found_ffprobe
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._hardware_encoder: HardwareEncoderProfile | None = None
        self._hardware_encoder_candidates: list[HardwareEncoderProfile] | None = None
        self._hardware_preference_path = self._get_hardware_preference_path()
        self._load_hardware_encoder_preference()

    @staticmethod
    def _get_hardware_preference_path() -> Path:
        app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if app_data:
            root = Path(app_data)
        else:
            root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return root / "EasyVideoEditor" / "encoder-preference.json"

    def _load_hardware_encoder_preference(self):
        try:
            saved = json.loads(self._hardware_preference_path.read_text(encoding="utf-8"))
            encoder_id = str(saved.get("encoder_id", ""))
            encoder = _HARDWARE_ENCODERS.get(encoder_id)
            if encoder:
                self._hardware_encoder = HardwareEncoderProfile(
                    encoder_id=encoder_id,
                    label=encoder[0],
                    vendor_id=str(saved.get("vendor_id", "")),
                    gpu_name=str(saved.get("gpu_name", "")),
                    arguments=encoder[1],
                )
        except (OSError, ValueError, TypeError, AttributeError):
            self._hardware_encoder = None

    def _save_hardware_encoder_preference(self, profile: HardwareEncoderProfile):
        data = {
            "encoder_id": profile.encoder_id,
            "vendor_id": profile.vendor_id,
            "gpu_name": profile.gpu_name,
        }
        try:
            self._hardware_preference_path.parent.mkdir(parents=True, exist_ok=True)
            self._hardware_preference_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except OSError:
            # The chosen encoder remains cached for this run if the profile cannot be saved.
            pass

    def clear_hardware_encoder_preference(self):
        """Forget a saved encoder after it fails on the current machine."""
        self._hardware_encoder = None
        self._hardware_encoder_candidates = None
        try:
            self._hardware_preference_path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _hidden_subprocess_options() -> dict:
        if os.name != "nt":
            return {}
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}

    def _installed_hardware_encoder_ids(self) -> set[str]:
        try:
            result = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=15, **self._hidden_subprocess_options(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise HardwareEncoderError(f"读取 FFmpeg 硬件编码器列表失败：{exc}") from exc
        if result.returncode:
            raise HardwareEncoderError((result.stderr or result.stdout or "无法读取 FFmpeg 编码器列表")[-1500:])
        encoder_output = result.stdout + "\n" + result.stderr
        return set(re.findall(r"\b(h264_(?:qsv|nvenc|amf))\b", encoder_output))

    @staticmethod
    def _installed_gpus() -> list[tuple[str, str]]:
        """Return (vendor id, model name) pairs from Windows video-controller devices."""
        if os.name != "nt":
            return []
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not powershell:
            powershell = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) /
                             "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
            if not Path(powershell).is_file():
                return []
        if not powershell:
            return []
        script = (
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "Get-CimInstance -ClassName Win32_VideoController | "
            "Select-Object @{n='name';e={$_.Name}},@{n='pnp';e={$_.PNPDeviceID}} | "
            "ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=12, **FFmpegRunner._hidden_subprocess_options(),
            )
            if result.returncode or not result.stdout.strip():
                return []
            raw = json.loads(result.stdout)
            devices = raw if isinstance(raw, list) else [raw]
            adapters: list[tuple[str, str]] = []
            for device in devices:
                if not isinstance(device, dict):
                    continue
                pnp_id = str(device.get("pnp", ""))
                match = re.search(r"VEN_([0-9A-F]{4})", pnp_id, re.IGNORECASE)
                if match:
                    adapters.append((match.group(1).upper(), str(device.get("name", "显卡"))))
            return adapters
        except (OSError, ValueError, subprocess.SubprocessError):
            return []

    def _discover_hardware_encoder_candidates(self) -> list[HardwareEncoderProfile]:
        available = self._installed_hardware_encoder_ids()
        adapters = self._installed_gpus()
        # Prefer discrete adapters when a machine exposes more than one vendor.
        vendor_priority = {"10DE": 0, "1002": 1, "1022": 1, "8086": 2}
        adapters.sort(key=lambda item: vendor_priority.get(item[0], 3))
        candidates: list[HardwareEncoderProfile] = []
        seen: set[str] = set()
        for vendor_id, gpu_name in adapters:
            encoder_id = _VENDOR_ENCODERS.get(vendor_id)
            encoder = _HARDWARE_ENCODERS.get(encoder_id or "")
            if encoder_id and encoder_id in available and encoder and encoder_id not in seen:
                seen.add(encoder_id)
                candidates.append(HardwareEncoderProfile(encoder_id, encoder[0], vendor_id, gpu_name, encoder[1]))
        if not candidates:
            # If Windows did not expose a model name, let FFmpeg's runtime probe
            # determine which compiled hardware backend can actually open a device.
            fallback_order = ("h264_nvenc", "h264_amf", "h264_qsv")
            for encoder_id in fallback_order:
                encoder = _HARDWARE_ENCODERS[encoder_id]
                if encoder_id in available:
                    candidates.append(HardwareEncoderProfile(
                        encoder_id, encoder[0], "", "未识别显卡", encoder[1],
                    ))
        return candidates

    def _probe_hardware_encoder(self, profile: HardwareEncoderProfile,
                                cancel: threading.Event, progress: ProgressCallback | None) -> None:
        args = [
            "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=128x128:r=1:d=1",
            "-frames:v", "1", "-an", *profile.arguments, "-f", "null", "-",
        ]
        self._run(args, cancel, None, 1.0, f"检测硬件编码器 · {profile.label}")
        if progress:
            progress(0.0, f"已选择 {profile.gpu_name} · {profile.label}")

    def get_hardware_encoder(self, cancel: threading.Event | None = None,
                             progress: ProgressCallback | None = None) -> HardwareEncoderProfile:
        """Reuse the saved encoder, or detect and probe an encoder once on this PC."""
        if self._hardware_encoder is not None:
            return self._hardware_encoder
        cancel = cancel or threading.Event()
        if self._hardware_encoder_candidates is None:
            self._hardware_encoder_candidates = self._discover_hardware_encoder_candidates()
        failures: list[str] = []
        for profile in self._hardware_encoder_candidates:
            if cancel.is_set():
                raise RuntimeError("已取消")
            try:
                self._probe_hardware_encoder(profile, cancel, progress)
            except (RuntimeError, OSError, ValueError) as exc:
                if cancel.is_set() or str(exc) == "已取消":
                    raise
                failures.append(f"{profile.label}：{str(exc).splitlines()[-1]}")
                continue
            self._hardware_encoder = profile
            # Cache the result for this run. Persist it after a real media job succeeds.
            return profile
        details = "\n".join(failures)
        raise HardwareEncoderError(
            "没有可用的硬件 H.264 编码器。"
            + (f"\n{details}" if details else " 请确认显卡驱动正常，且 FFmpeg 构建包含 QSV、NVENC 或 AMF。")
        )

    def cancel(self):
        with self._process_lock:
            process = self._process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _run(self, args: list[str], cancel: threading.Event, progress: ProgressCallback | None,
             duration: float, stage: str = "", cwd: Path | str | None = None) -> None:
        if cancel.is_set():
            raise RuntimeError("已取消")
        command = [self.ffmpeg, "-hide_banner", "-nostdin", "-progress", "pipe:1", "-nostats", *args]
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1,
                                   startupinfo=startupinfo, creationflags=creationflags,
                                   cwd=str(cwd) if cwd is not None else None)
        with self._process_lock:
            self._process = process
        messages: queue.Queue[tuple[str, str]] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=120)

        def drain(stream, name: str):
            try:
                for line in stream:
                    messages.put((name, line.rstrip("\r\n")))
            finally:
                messages.put((name + "_eof", ""))

        stdout_thread = threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True)
        stderr_thread = threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        stdout_eof = False
        values: dict[str, str] = {}
        last_update = 0.0
        termination_started = False
        termination_time = 0.0
        try:
            while not stdout_eof or process.poll() is None:
                if cancel.is_set() and process.poll() is None and not termination_started:
                    termination_started = True
                    termination_time = time.monotonic()
                    try:
                        process.terminate()
                    except OSError:
                        pass
                elif termination_started and process.poll() is None and time.monotonic() - termination_time > 3.0:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    termination_time = float("inf")
                try:
                    channel, line = messages.get(timeout=0.1)
                except queue.Empty:
                    continue
                if channel == "stdout_eof":
                    stdout_eof = True
                elif channel == "stderr":
                    stderr_tail.append(line)
                elif channel == "stdout" and "=" in line:
                    key, value = line.split("=", 1)
                    values[key] = value
                    if key == "progress":
                        try:
                            timestamp_us = int(values.get("out_time_us", "0") or 0)
                        except ValueError:
                            timestamp_us = 0
                        fraction = min(1.0, timestamp_us / 1_000_000 / duration) if duration > 0 else 0.0
                        if progress and (time.monotonic() - last_update > 0.2 or value == "end"):
                            progress(fraction, stage)
                            last_update = time.monotonic()
            code = process.wait()
            stderr_thread.join(timeout=2)
            if cancel.is_set():
                raise RuntimeError("已取消")
            if code:
                diagnostic = "\n".join(stderr_tail).strip() or f"FFmpeg 退出码：{code}"
                raise RuntimeError(diagnostic[-5000:])
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait()
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            for stream in (process.stdout, process.stderr):
                if stream:
                    stream.close()

    def _probe(self, path: Path) -> MediaInfo:
        return probe_media(path, self.ffprobe)

    @staticmethod
    def _vf(size: tuple[int, int]) -> str:
        return f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"

    def _video_encoder_args(self, use_hardware_encoding: bool,
                            hardware_encoder: HardwareEncoderProfile | None = None) -> list[str]:
        if use_hardware_encoding:
            profile = hardware_encoder or self.get_hardware_encoder()
            return ["-c:v", profile.encoder_id, *profile.arguments]
        return [
            "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-crf", str(VIDEO_CRF),
        ]

    def _conversion_args(self, source: Path, output: Path, info: MediaInfo,
                         size: tuple[int, int], use_hardware_encoding: bool,
                         hardware_encoder: HardwareEncoderProfile | None = None) -> list[str]:
        args = ["-y", "-i", str(source), "-map", f"0:{info.video_stream_index}"]
        if info.has_audio and info.audio_stream_index is not None:
            args += ["-map", f"0:{info.audio_stream_index}"]
        args += ["-vf", self._vf(size), *self._video_encoder_args(use_hardware_encoding, hardware_encoder)]
        if info.has_audio:
            args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        args += ["-map_metadata", "0", "-metadata:s:v:0", "rotate=0"]
        if output.suffix.lower() == ".mp4":
            args += ["-movflags", "+faststart", "-f", "mp4"]
        else:
            args += ["-f", "matroska"]
        args.append(str(output))
        return args

    def convert(self, source: Path | str, destination: Path | str, settings: ExportSettings,
                cancel: threading.Event, progress: ProgressCallback | None = None,
                on_discarded: Callable[[int], None] | None = None) -> MediaInfo | None:
        if cancel.is_set():
            raise RuntimeError("已取消")
        source, destination = Path(source).resolve(), Path(destination).resolve()
        if source == destination:
            raise ValueError("输出文件不能覆盖源视频。")
        extension = ".mp4" if settings.container.upper() == "MP4" else ".mkv"
        destination = destination.with_suffix(extension)
        if destination.exists():
            raise FileExistsError(f"输出文件已存在：{destination}")
        info = self._probe(source)
        if cancel.is_set():
            raise RuntimeError("已取消")
        output_size = dimensions_for(info, settings.resolution)
        destination.parent.mkdir(parents=True, exist_ok=True)
        working = Path(tempfile.mkdtemp(prefix=".easy-video-editor-", dir=destination.parent))
        temporary_output = working / ("result" + extension)
        hardware_encoder = None
        try:
            if settings.use_hardware_encoding:
                hardware_encoder = self.get_hardware_encoder(cancel, progress)
            try:
                self._run(
                    self._conversion_args(
                        source, temporary_output, info, output_size, settings.use_hardware_encoding,
                        hardware_encoder,
                    ),
                    cancel, progress, info.duration,
                    f"正在硬件编码 · {hardware_encoder.label}" if hardware_encoder else f"正在软件编码 · CRF {VIDEO_CRF}",
                )
                checked = self._probe(temporary_output)
                if checked.video_codec != "h264":
                    raise RuntimeError(f"输出视频编码校验失败：{checked.video_codec}")
                if (checked.width, checked.height) != output_size:
                    raise RuntimeError(f"输出尺寸校验失败：{checked.width}×{checked.height}，期望 {output_size[0]}×{output_size[1]}")
                expected_audio_codec = "aac"
                if checked.has_audio != info.has_audio or (checked.has_audio and checked.audio_codec != expected_audio_codec):
                    raise RuntimeError("输出音轨校验失败。")
                if abs(checked.duration - info.duration) > max(1.0, 3 / float(info.fps)):
                    raise RuntimeError(f"输出时长校验失败：源 {info.duration:.2f} 秒，输出 {checked.duration:.2f} 秒。")
                actual_output_size = temporary_output.stat().st_size
            except (RuntimeError, OSError, ValueError) as exc:
                if settings.use_hardware_encoding and not cancel.is_set() and str(exc) != "已取消":
                    self.clear_hardware_encoder_preference()
                    raise HardwareEncoderError(str(exc)) from exc
                raise
            if hardware_encoder:
                self._save_hardware_encoder_preference(hardware_encoder)
            if settings.discard_if_larger and actual_output_size > info.file_size:
                if on_discarded:
                    on_discarded(actual_output_size)
                if progress:
                    progress(1.0, "输出大于源文件，已按设置删除压缩结果")
                return None
            _publish_no_overwrite(temporary_output, destination)
            return self._probe(destination)
        finally:
            shutil.rmtree(working, ignore_errors=True)

    def analyze_conversion_estimates(
        self, source: Path | str, cancel: threading.Event,
        progress: ProgressCallback | None = None,
        use_hardware_encoding: bool = True,
    ) -> list[dict[str, int | float | str]]:
        """Sample representative clips using the selected encoder and estimate size and VMAF."""
        source = Path(source).resolve()
        info = self._probe(source)
        resolutions = analysis_resolutions(info)
        hardware_encoder = self.get_hardware_encoder(cancel, progress) if use_hardware_encoding else None
        encoder_profile = (
            f"{hardware_encoder.label} quality {VIDEO_CRF}" if hardware_encoder else f"CRF{VIDEO_CRF}"
        )
        profiles = ((encoder_profile, VIDEO_CRF),)
        if info.duration <= 6:
            sample_duration = max(0.1, info.duration)
            windows = [(0.0, sample_duration)]
        else:
            sample_duration = 1.5
            windows = []
            for ratio in (0.2, 0.5, 0.8):
                start = min(max(0.0, info.duration * ratio - sample_duration / 2), info.duration - sample_duration)
                windows.append((start, sample_duration))

        candidate_count = len(resolutions) * len(profiles)
        total_work = max(1, candidate_count * 2)
        working = Path(tempfile.mkdtemp(prefix=".easy-video-editor-analysis-"))
        candidates: list[dict[str, int | float | str]] = []
        try:
            for index, (resolution, size) in enumerate(resolutions):
                for compression, crf in profiles:
                    if cancel.is_set():
                        raise RuntimeError("已取消")
                    candidate_index = len(candidates)
                    sample_path = working / f"sample-{candidate_index:02d}.mp4"
                    encode_filters = [
                        f"[{window_index}:{info.video_stream_index}]trim=duration={length:.6f},setpts=PTS-STARTPTS[s{window_index}]"
                        for window_index, (_start, length) in enumerate(windows)
                    ]
                    sample_labels = "".join(f"[s{window_index}]" for window_index in range(len(windows)))
                    encode_filters.append(
                        f"{sample_labels}concat=n={len(windows)}:v=1:a=0,{self._vf(size)}[sample]"
                    )
                    encode_args = ["-y"]
                    for start, _length in windows:
                        encode_args += ["-ss", f"{start:.6f}", "-i", str(source)]
                    encode_args += [
                        "-filter_complex", ";".join(encode_filters), "-map", "[sample]", "-an",
                        *self._video_encoder_args(use_hardware_encoding, hardware_encoder),
                        "-movflags", "+faststart", "-f", "mp4", str(sample_path),
                    ]
                    sample_total = sum(length for _start, length in windows)

                    def encode_progress(fraction: float, _stage: str, candidate_index=candidate_index):
                        if progress:
                            progress((candidate_index * 2 + min(1.0, fraction)) / total_work,
                                     f"视频分析：样片编码 {candidate_index + 1}/{candidate_count}")

                    try:
                        self._run(encode_args, cancel, encode_progress, sample_total, "编码样片")
                        sample_info = self._probe(sample_path)
                    except (RuntimeError, OSError, ValueError) as exc:
                        if use_hardware_encoding and not cancel.is_set() and str(exc) != "已取消":
                            self.clear_hardware_encoder_preference()
                            raise HardwareEncoderError(str(exc)) from exc
                        raise
                    sample_video_bitrate = sample_info.video_bitrate or round(
                        sample_path.stat().st_size * 8 / max(sample_total, 0.1)
                    )
                    estimated_size = round(
                        (sample_video_bitrate + (192_000 if info.has_audio else 0)) * info.duration / 8 + 32_768
                    )

                    score_log = f"vmaf-{candidate_index:02d}.json"
                    score_filters = [
                        f"[{window_index}:{info.video_stream_index}]trim=duration={length:.6f},setpts=PTS-STARTPTS[r{window_index}]"
                        for window_index, (_start, length) in enumerate(windows)
                    ]
                    ref_labels = "".join(f"[r{window_index}]" for window_index in range(len(windows)))
                    ref_input = len(windows)
                    reference_size = dimensions_for(info, "原始")
                    score_filters.append(
                        f"{ref_labels}concat=n={len(windows)}:v=1:a=0,{self._vf(reference_size)},format=yuv420p[reference]"
                    )
                    score_filters.append(
                        f"[{ref_input}:v:0]setpts=PTS-STARTPTS,"
                        f"scale={reference_size[0]}:{reference_size[1]}:flags=bicubic,setsar=1,"
                        f"format=yuv420p[distorted]"
                    )
                    score_filters.append(
                        f"[distorted][reference]libvmaf=log_fmt=json:log_path={score_log}:n_threads=2[score]"
                    )
                    score_args = ["-y"]
                    for start, _length in windows:
                        score_args += ["-ss", f"{start:.6f}", "-i", str(source)]
                    score_args += ["-i", str(sample_path), "-filter_complex", ";".join(score_filters),
                                   "-map", "[score]", "-shortest", "-f", "null", os.devnull]

                    def score_progress(fraction: float, _stage: str, candidate_index=candidate_index):
                        if progress:
                            progress((candidate_index * 2 + 1 + min(1.0, fraction)) / total_work,
                                     f"视频分析：画质评分 {candidate_index + 1}/{candidate_count}")

                    self._run(score_args, cancel, score_progress, sample_total, "VMAF 画质评分", cwd=working)
                    with (working / score_log).open("r", encoding="utf-8") as stream:
                        score_data = json.load(stream)
                    try:
                        vmaf = float(score_data["pooled_metrics"]["vmaf"]["mean"])
                    except (KeyError, TypeError, ValueError):
                        frame_scores = [
                            float(frame["metrics"]["vmaf"])
                            for frame in score_data.get("frames", [])
                            if frame.get("metrics", {}).get("vmaf") is not None
                        ]
                        if not frame_scores:
                            raise RuntimeError("视频分析没有得到有效的 VMAF 画质评分。")
                        vmaf = sum(frame_scores) / len(frame_scores)
                    candidates.append({
                        "resolution": resolution,
                        "width": size[0],
                        "height": size[1],
                        "compression": compression,
                        "vmaf": vmaf,
                        "estimated_size": estimated_size,
                    })

            if progress:
                progress(1.0, "视频分析完成")
            return candidates
        finally:
            shutil.rmtree(working, ignore_errors=True)

    def export_ranges(self, sources: Sequence[tuple[Path | str, float, float]], destination: Path | str,
                      cancel: threading.Event, progress: ProgressCallback | None = None,
                      output_size: tuple[int, int] | None = None, output_fps: Fraction | None = None,
                      lossless: bool = False, use_hardware_decoder: bool = False,
                      use_hardware_encoding: bool = False) -> MediaInfo:
        destination = Path(destination).resolve()
        if destination.suffix.lower() != ".mp4":
            destination = destination.with_suffix(".mp4")
        paths = [Path(path).resolve() for path, _, _ in sources]
        if not sources:
            raise ValueError("没有保留的片段可导出。")
        if destination in paths:
            raise ValueError("输出文件不能覆盖源视频。")
        if destination.exists():
            raise FileExistsError(f"输出文件已存在：{destination}")
        infos: list[MediaInfo] = []
        for path in paths:
            if cancel.is_set():
                raise RuntimeError("已取消")
            infos.append(self._probe(path))
        if lossless:
            return self._export_ranges_lossless(sources, infos, destination, cancel, progress)
        output_size = output_size or even_dimensions(infos[0].display_width, infos[0].display_height)
        output_fps = output_fps or infos[0].fps
        hardware_encoder = self.get_hardware_encoder(cancel, progress) if use_hardware_encoding else None
        fps_number = float(output_fps)
        fps_text = f"{output_fps.numerator}/{output_fps.denominator}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        working = Path(tempfile.mkdtemp(prefix=".easy-video-editor-", dir=destination.parent))
        parts: list[Path] = []
        part_durations: list[float] = []
        total_source_duration = sum(max(0.0, end - start) for _, start, end in sources)
        completed_source_duration = 0.0
        try:
            for index, (source, start, end) in enumerate(sources):
                if cancel.is_set():
                    raise RuntimeError("已取消")
                duration = end - start
                if duration <= 0:
                    continue
                info = infos[index]
                frame_count = max(1, round(duration * fps_number))
                output_duration = frame_count / fps_number
                part = working / f"part-{index:05d}.mov"
                video_filter = (
                    f"trim=end={duration:.9f},"
                    f"tpad=stop_mode=clone:stop_duration={1 / fps_number:.9f},"
                    f"fps=fps={fps_text}:start_time=0,trim=end_frame={frame_count},"
                    f"setpts=N/({fps_number:.12f}*TB),"
                    f"scale={output_size[0]}:{output_size[1]}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                    f"pad={output_size[0]}:{output_size[1]}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
                )
                # Keep FFmpeg's accurate input seeking enabled so frames from
                # the preceding keyframe do not leak into the exported range.
                args = ["-y", "-ss", f"{start:.9f}"]
                if use_hardware_decoder:
                    args += ["-hwaccel", "qsv"]
                video_encoder_args = (
                    self._video_encoder_args(True, hardware_encoder) if use_hardware_encoding else
                    ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
                )
                args += ["-i", str(source)]
                if not info.has_audio or info.audio_stream_index is None:
                    # Add every input before output options such as -map. FFmpeg
                    # otherwise treats -map as an option for this lavfi input.
                    args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                args += ["-map", f"0:{info.video_stream_index}", "-vf", video_filter,
                         *video_encoder_args, "-r", fps_text]
                audio_filter = (
                    "aresample=48000:async=1:first_pts=0,"
                    f"atrim=duration={duration:.9f},apad=whole_dur={output_duration:.9f},"
                    f"atrim=duration={output_duration:.9f},asetpts=PTS-STARTPTS"
                )
                if info.has_audio and info.audio_stream_index is not None:
                    args += ["-map", f"0:{info.audio_stream_index}", "-af", audio_filter,
                             "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
                else:
                    args += ["-map", "1:a:0", "-af", f"atrim=duration={output_duration:.9f},asetpts=PTS-STARTPTS",
                             "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
                args += ["-t", f"{output_duration:.9f}", "-f", "mov", str(part)]
                def part_progress(fraction: float, _stage: str):
                    if progress and total_source_duration:
                        progress(0.9 * (completed_source_duration + duration * fraction) / total_source_duration,
                                 f"正在处理片段 {index + 1}/{len(sources)}")
                try:
                    stage = f"片段 {index + 1} · {hardware_encoder.label}" if hardware_encoder else f"片段 {index + 1}"
                    self._run(args, cancel, part_progress, output_duration, stage)
                except (RuntimeError, OSError, ValueError) as exc:
                    if cancel.is_set() or str(exc) == "已取消":
                        raise
                    if use_hardware_decoder:
                        raise HardwareDecoderError(str(exc)) from exc
                    if use_hardware_encoding:
                        self.clear_hardware_encoder_preference()
                        raise HardwareEncoderError(str(exc)) from exc
                    raise
                parts.append(part)
                part_durations.append(output_duration)
                completed_source_duration += duration
            if not parts:
                raise ValueError("没有可导出的有效片段。")
            concat_list = working / "concat.txt"
            concat_list.write_text("".join(_concat_file_line(p) for p in parts), encoding="utf-8")
            temp_output = working / "result.mp4"
            concat_args = ["-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                           "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "aac",
                           "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(temp_output)]
            try:
                self._run(concat_args, cancel, progress, sum(part_durations), "正在拼接")
                checked = self._probe(temp_output)
            except (RuntimeError, OSError, ValueError) as exc:
                if cancel.is_set() or str(exc) == "已取消":
                    raise
                if use_hardware_decoder:
                    raise HardwareDecoderError(str(exc)) from exc
                if use_hardware_encoding:
                    self.clear_hardware_encoder_preference()
                    raise HardwareEncoderError(str(exc)) from exc
                raise

            def fail_reencoded_export(message: str):
                if use_hardware_decoder:
                    raise HardwareDecoderError(message)
                if use_hardware_encoding:
                    self.clear_hardware_encoder_preference()
                    raise HardwareEncoderError(message)
                raise RuntimeError(message)

            if checked.video_codec != "h264" or (checked.width, checked.height) != output_size:
                fail_reencoded_export("导出结果的视频编码或尺寸校验失败。")
            if not checked.has_audio or checked.audio_codec != "aac":
                fail_reencoded_export("导出结果的 AAC 音轨校验失败。")
            expected_duration = sum(part_durations)
            if abs(checked.duration - expected_duration) > max(1.0, len(parts) * 0.08):
                fail_reencoded_export(
                    f"导出时长校验失败：期望约 {expected_duration:.2f} 秒，实际 {checked.duration:.2f} 秒。"
                )
            _publish_no_overwrite(temp_output, destination)
            if hardware_encoder:
                self._save_hardware_encoder_preference(hardware_encoder)
            if progress:
                progress(1.0, "完成")
            return self._probe(destination)
        finally:
            shutil.rmtree(working, ignore_errors=True)

    def _export_ranges_lossless(self, sources: Sequence[tuple[Path | str, float, float]],
                                infos: Sequence[MediaInfo],
                                destination: Path, cancel: threading.Event,
                                progress: ProgressCallback | None) -> MediaInfo:
        """Stream-copy keyframe-aligned ranges into one MP4 without re-encoding."""
        if any(end <= start for _, start, end in sources):
            raise ValueError("无损裁剪包含无效的空片段。")
        first = infos[0]
        for info in infos[1:]:
            if (info.video_codec, info.video_profile, info.video_pixel_format,
                    info.width, info.height, info.sample_aspect_ratio, info.rotation,
                    info.has_audio, info.audio_codec, info.audio_sample_rate,
                    info.audio_channels, info.audio_channel_layout) != (
                    first.video_codec, first.video_profile, first.video_pixel_format,
                    first.width, first.height, first.sample_aspect_ratio, first.rotation,
                    first.has_audio, first.audio_codec, first.audio_sample_rate,
                    first.audio_channels, first.audio_channel_layout):
                raise LosslessExportError(
                    "输入视频的编码、尺寸或音轨参数不一致，无法直接无损拼接。"
                )

        destination.parent.mkdir(parents=True, exist_ok=True)
        working = Path(tempfile.mkdtemp(prefix=".easy-video-editor-lossless-", dir=destination.parent))
        parts: list[Path] = []
        expected_duration = sum(end - start for _, start, end in sources)
        completed_duration = 0.0
        try:
            for index, ((source, start, end), info) in enumerate(zip(sources, infos)):
                if cancel.is_set():
                    raise RuntimeError("已取消")
                duration = end - start
                part = working / f"part-{index:05d}.mp4"
                args = ["-y", "-ss", f"{start:.9f}", "-i", str(source),
                        "-t", f"{duration:.9f}", "-map", f"0:{info.video_stream_index}",
                        "-map", "0:a?", "-map", "0:s?"]
                args += ["-map_metadata", "0", "-c", "copy", "-avoid_negative_ts", "make_zero",
                         "-movflags", "+faststart", "-f", "mp4", str(part)]

                def part_progress(fraction: float, _stage: str):
                    if progress and expected_duration:
                        progress(0.82 * (completed_duration + duration * fraction) / expected_duration,
                                 f"无损裁剪片段 {index + 1}/{len(sources)}")

                try:
                    self._run(args, cancel, part_progress, duration, f"无损裁剪片段 {index + 1}")
                except RuntimeError as exc:
                    if str(exc) == "已取消":
                        raise
                    raise LosslessExportError(
                        f"无损流复制失败：{str(exc).splitlines()[-1]}。"
                    ) from exc
                try:
                    checked_part = self._probe(part)
                except (RuntimeError, OSError, ValueError) as exc:
                    raise LosslessExportError(f"无损流复制片段校验失败：{exc}") from exc
                if checked_part.video_codec != info.video_codec:
                    raise LosslessExportError("片段视频编码与源文件不一致。")
                if info.has_audio and checked_part.audio_codec != info.audio_codec:
                    raise LosslessExportError("片段音频编码与源文件不一致。")
                if not info.has_audio and checked_part.has_audio:
                    raise LosslessExportError("无损流复制结果意外包含音轨。")
                parts.append(part)
                completed_duration += duration

            if not parts:
                raise ValueError("没有可导出的有效片段。")
            concat_list = working / "concat.txt"
            concat_list.write_text("".join(_concat_file_line(part) for part in parts), encoding="utf-8")
            temp_output = working / "result.mp4"
            concat_args = ["-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                           "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?", "-c", "copy",
                           "-movflags", "+faststart", str(temp_output)]
            def concat_progress(fraction: float, stage: str):
                if progress:
                    progress(0.82 + 0.18 * fraction, stage)
            try:
                self._run(concat_args, cancel, concat_progress, expected_duration, "正在无损拼接")
            except RuntimeError as exc:
                if str(exc) == "已取消":
                    raise
                raise LosslessExportError(f"无损拼接失败：{str(exc).splitlines()[-1]}。") from exc
            try:
                checked = self._probe(temp_output)
            except (RuntimeError, OSError, ValueError) as exc:
                raise LosslessExportError(f"无损拼接结果校验失败：{exc}") from exc
            if checked.video_codec != infos[0].video_codec:
                raise LosslessExportError("最终视频编码与源文件不一致。")
            if infos[0].has_audio and checked.audio_codec != infos[0].audio_codec:
                raise LosslessExportError("最终音频编码与源文件不一致。")
            if not infos[0].has_audio and checked.has_audio:
                raise LosslessExportError("无损流复制结果意外包含音轨。")
            if abs(checked.duration - expected_duration) > max(1.0, len(parts) * 0.08):
                raise LosslessExportError(
                    f"无损拼接时长校验失败：期望约 {expected_duration:.2f} 秒，"
                    f"实际 {checked.duration:.2f} 秒。"
                )
            _publish_no_overwrite(temp_output, destination)
            if progress:
                progress(1.0, "无损裁剪完成")
            return self._probe(destination)
        finally:
            shutil.rmtree(working, ignore_errors=True)

