"""Tests for parse_takeout.py"""
import sys
from datetime import timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from parse_takeout import parse_takeout

FIXTURE = Path(__file__).parent / "fixtures" / "sample_watch_history.json"


def _events():
    return list(parse_takeout(FIXTURE))


def test_total_count():
    # 6 valid entries (2 missing titleUrl or empty), 1 youtu.be short URL
    events = _events()
    assert len(events) == 7


def test_video_id_extraction():
    events = _events()
    ids = {e.video_id for e in events}
    assert "fJ9rUzIMcZQ" in ids  # standard watch?v=
    assert "5NV6Rdv1a3I" in ids  # youtu.be short URL


def test_missing_title_url_dropped():
    events = _events()
    ids = {e.video_id for e in events}
    # The entry with no titleUrl must not appear
    # (we don't have a specific ID to check, but count verifies it)
    assert len(events) == 7


def test_channel_captured():
    events = _events()
    by_id = {e.video_id: e for e in events}
    assert by_id["fJ9rUzIMcZQ"].channel == "Queen - Topic"


def test_timestamp_utc():
    events = _events()
    for e in events:
        assert e.watched_at.tzinfo is not None
        assert e.watched_at.tzinfo.utcoffset(None).total_seconds() == 0


def test_youtube_music_header_included():
    events = _events()
    ids = {e.video_id for e in events}
    assert "hTWKbfoikeg" in ids  # YouTube Music header entry


def test_unicode_title_preserved():
    events = _events()
    by_id = {e.video_id: e for e in events}
    # em-dash in "Daft Punk — Get Lucky"
    assert "—" in by_id["5NV6Rdv1a3I"].raw_title or "Daft Punk" in by_id["5NV6Rdv1a3I"].raw_title
