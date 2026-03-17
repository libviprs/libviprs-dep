"""Tests for fmt_time and make_bar helper functions."""

import build_pdfium as bp


class TestFmtTime:
    def test_zero(self):
        assert bp.fmt_time(0) == "0:00"

    def test_seconds_only(self):
        assert bp.fmt_time(5) == "0:05"
        assert bp.fmt_time(59) == "0:59"

    def test_minutes(self):
        assert bp.fmt_time(60) == "1:00"
        assert bp.fmt_time(125) == "2:05"

    def test_hours(self):
        assert bp.fmt_time(3600) == "1:00:00"
        assert bp.fmt_time(3661) == "1:01:01"
        assert bp.fmt_time(7384) == "2:03:04"


class TestMakeBar:
    def test_empty(self):
        bar = bp.make_bar(0.0, 10)
        assert bar == "░" * 10

    def test_full(self):
        bar = bp.make_bar(1.0, 10)
        assert bar == "█" * 10

    def test_half(self):
        bar = bp.make_bar(0.5, 10)
        assert bar == "█" * 5 + "░" * 5

    def test_length_matches_width(self):
        for w in [5, 20, 80]:
            bar = bp.make_bar(0.33, w)
            assert len(bar) == w
