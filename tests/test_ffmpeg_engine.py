import subprocess
import tempfile
import threading
import unittest
from fractions import Fraction
from pathlib import Path

from video_engine import ExportSettings, FFmpegRunner, find_ffmpeg_tools


class FFmpegSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg, cls.ffprobe = find_ffmpeg_tools()
        except FileNotFoundError as exc:
            raise unittest.SkipTest(str(exc))
        cls.runner = FFmpegRunner(cls.ffmpeg, cls.ffprobe)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="easy-video-editor-test-")
        self.folder = Path(self.temp.name)
        self.source = self.folder / "sample.mp4"
        command = [
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(self.source),
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(command, check=True, capture_output=True, creationflags=flags)

    def tearDown(self):
        self.temp.cleanup()

    def test_quality_conversion_produces_h264_and_aac(self):
        destination = self.folder / "converted.mp4"
        result = self.runner.convert(
            self.source, destination,
            ExportSettings(container="MP4", resolution="原始", mode="画质优先"),
            threading.Event(),
        )
        self.assertEqual(result.video_codec, "h264")
        self.assertEqual((result.width, result.height), (320, 240))
        self.assertTrue(result.has_audio)
        self.assertEqual(result.audio_codec, "aac")
        self.assertLess(abs(result.duration - 3.0), 0.15)

    def test_kept_ranges_are_joined_with_aac_audio(self):
        destination = self.folder / "cut.mp4"
        result = self.runner.export_ranges(
            [(self.source, 0.0, 1.0), (self.source, 2.0, 3.0)],
            destination, threading.Event(), output_size=(320, 240), output_fps=Fraction(25, 1),
        )
        self.assertEqual(result.video_codec, "h264")
        self.assertEqual(result.audio_codec, "aac")
        self.assertTrue(result.has_audio)
        self.assertLess(abs(result.duration - 2.0), 0.2)


if __name__ == "__main__":
    unittest.main()

