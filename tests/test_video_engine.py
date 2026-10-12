import unittest
from fractions import Fraction
from pathlib import Path

from video_engine import (
    ExportSettings, MediaInfo, Segment, calculate_target_bitrate, dimensions_for,
    fit_dimensions, output_dimensions, parse_fraction, recommend_resolution,
    retained_segments, split_at,
)


def sample_info(**overrides):
    values = dict(
        path=Path("sample.mp4"), duration=100.0, video_duration=100.0,
        audio_duration=100.0, file_size=200_000_000,
        video_stream_index=0, audio_stream_index=1,
        width=1920, height=1080, display_width=1920, display_height=1080,
        fps=Fraction(25, 1), video_codec="h264", audio_codec="aac",
        video_bitrate=4_000_000, audio_bitrate=192_000,
        container_bitrate=4_200_000, has_audio=True,
        sample_aspect_ratio=Fraction(1, 1), rotation=0,
    )
    values.update(overrides)
    return MediaInfo(**values)


class SegmentModelTests(unittest.TestCase):
    def test_split_aligns_to_frame_and_does_not_create_empty_parts(self):
        source = [Segment(0.0, 4.0)]
        parts, selected = split_at(source, 1.01, Fraction(25, 1), 4.0)
        self.assertEqual(parts, [Segment(0.0, 1.0), Segment(1.0, 4.0)])
        self.assertEqual(selected, 0)

        repeated, repeated_index = split_at(parts, 1.0, Fraction(25, 1), 4.0)
        self.assertEqual(repeated, parts)
        self.assertIsNone(repeated_index)
        for endpoint in (0.0, 4.0):
            unchanged, index = split_at(source, endpoint, Fraction(25, 1), 4.0)
            self.assertEqual(unchanged, source)
            self.assertIsNone(index)

    def test_split_inherits_deleted_state_and_retained_ranges_keep_order(self):
        source = [Segment(0.0, 3.0, True), Segment(3.0, 6.0, False)]
        parts, _ = split_at(source, 1.5, Fraction(30, 1), 6.0)
        self.assertEqual(parts[:2], [Segment(0.0, 1.5, True), Segment(1.5, 3.0, True)])
        self.assertEqual(retained_segments(parts), [Segment(3.0, 6.0, False)])


class ResolutionAndBudgetTests(unittest.TestCase):
    def test_size_math_uses_even_dimensions_without_upscaling(self):
        self.assertEqual(fit_dimensions(1920, 1080, 854, 480), (852, 480))
        self.assertEqual(dimensions_for(sample_info(width=1440, height=1080,
                                                   display_width=1440, display_height=1080), "1080P"),
                         (1440, 1080))
        portrait = sample_info(width=1080, height=1920, display_width=1080, display_height=1920)
        self.assertEqual(dimensions_for(portrait, "720P"), (720, 1280))
        small = sample_info(width=640, height=360, display_width=640, display_height=360)
        _label, size, _density, _low = recommend_resolution(small, 1_000_000)
        self.assertLessEqual(size[0], small.display_width)
        self.assertLessEqual(size[1], small.display_height)

    def test_budget_subtracts_audio_only_when_audio_exists(self):
        info = sample_info()
        target, bitrate = calculate_target_bitrate(info, 50)
        expected_target = 100_000_000
        expected_available = int(expected_target * 0.97) - 16_384
        self.assertEqual(target, expected_target)
        self.assertEqual(bitrate, expected_available * 8 // 100 - 192_000)

        silent = sample_info(has_audio=False, audio_stream_index=None, audio_codec=None,
                             audio_bitrate=None, audio_duration=None)
        _target, silent_bitrate = calculate_target_bitrate(silent, 50)
        self.assertEqual(silent_bitrate, expected_available * 8 // 100)

    def test_budget_below_minimum_is_rejected(self):
        tiny = sample_info(file_size=100_000, duration=3600.0)
        with self.assertRaises(ValueError):
            calculate_target_bitrate(tiny, 1)

    def test_probe_ratios_accept_ffprobe_colon_format(self):
        self.assertEqual(parse_fraction("8:9"), Fraction(8, 9))
        self.assertEqual(parse_fraction("30000/1001"), Fraction(30000, 1001))

    def test_low_density_recommends_smallest_size_without_exceeding_source(self):
        info = sample_info(width=3840, height=2160, display_width=3840, display_height=2160,
                           has_audio=False, audio_stream_index=None, audio_codec=None,
                           audio_bitrate=None, audio_duration=None, file_size=2_000_000,
                           duration=100.0)
        size = output_dimensions(info, ExportSettings(mode="体积优先", size_percent=50,
                                                     automatic_resolution=True))
        self.assertEqual(size, (852, 480))


if __name__ == "__main__":
    unittest.main()

