"""Tests for normalize.py — heavy table-driven coverage of title heuristics."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CONF_FALLBACK, CONF_PARSED, CONF_TOPIC
from models import VideoMeta, WatchEvent
from normalize import _clean_field, normalize_one

_NOW = datetime(2023, 1, 1, tzinfo=timezone.utc)


def _event(video_id="vid1", channel="Some Channel"):
    return WatchEvent(video_id=video_id, raw_title="", channel=channel, watched_at=_NOW)


def _meta(title: str, channel: str, category_id="10"):
    return VideoMeta(
        video_id="vid1",
        category_id=category_id,
        api_title=title,
        channel_title=channel,
        duration_s=200,
    )


# ---------------------------------------------------------------------------
# Topic channel cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("title,channel,exp_artist,exp_track", [
    (
        "Bohemian Rhapsody",
        "Queen - Topic",
        "Queen",
        "Bohemian Rhapsody",
    ),
    (
        "Smells Like Teen Spirit",
        "Nirvana - Topic",
        "Nirvana",
        "Smells Like Teen Spirit",
    ),
    (
        "Get Lucky (feat. Pharrell Williams)",
        "Daft Punk - Topic",
        "Daft Punk",
        "Get Lucky (feat. Pharrell Williams)",  # feat. preserved
    ),
])
def test_topic_channel(title, channel, exp_artist, exp_track):
    artist, track, conf = normalize_one(_event(channel=channel), _meta(title, channel))
    assert conf == CONF_TOPIC
    assert artist == exp_artist
    assert track == exp_track


# ---------------------------------------------------------------------------
# Artist - Track split cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("title,channel,exp_artist,exp_track", [
    (
        "Radiohead - Creep (Official Video)",
        "Radiohead",
        "Radiohead",
        "Creep",
    ),
    (
        "Kendrick Lamar – HUMBLE. (Official Music Video)",
        "KendrickLamarVEVO",
        "Kendrick Lamar",
        "HUMBLE.",
    ),
    (
        "Daft Punk — Get Lucky ft. Pharrell Williams (Official Audio)",
        "DaftPunkVEVO",
        "Daft Punk",
        "Get Lucky ft. Pharrell Williams",  # ft. preserved, official audio stripped
    ),
    (
        "Taylor Swift - Anti-Hero (Lyric Video)",
        "TaylorSwiftVEVO",
        "Taylor Swift",
        "Anti-Hero",
    ),
    (
        "Radiohead - Karma Police (Remastered 2016)",
        "Radiohead",
        "Radiohead",
        "Karma Police (Remastered 2016)",  # remaster year preserved
    ),
    (
        "Arcade Fire - Wake Up (Live at Sydney Opera House)",
        "ArcadeFireVEVO",
        "Arcade Fire",
        "Wake Up (Live at Sydney Opera House)",  # Live preserved
    ),
    (
        "Nirvana - Come As You Are (Acoustic)",
        "NirvanaVEVO",
        "Nirvana",
        "Come As You Are (Acoustic)",  # Acoustic preserved
    ),
    (
        "Billie Eilish - bad guy (Official Music Video) (HD)",
        "BillieEilishVEVO",
        "Billie Eilish",
        "bad guy",
    ),
])
def test_parsed_split(title, channel, exp_artist, exp_track):
    artist, track, conf = normalize_one(_event(channel=channel), _meta(title, channel))
    assert conf == CONF_PARSED, f"Expected CONF_PARSED, got {conf!r} for title={title!r}"
    assert artist == exp_artist
    assert track == exp_track


# ---------------------------------------------------------------------------
# Fallback cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("title,channel,exp_artist", [
    ("Wonderwall", "Oasis", "Oasis"),
    ("Stairway to Heaven [HD]", "Led Zeppelin", "Led Zeppelin"),
])
def test_fallback(title, channel, exp_artist):
    artist, track, conf = normalize_one(_event(channel=channel), _meta(title, channel))
    assert conf == CONF_FALLBACK
    assert artist == exp_artist


# ---------------------------------------------------------------------------
# Cleaning edge cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Song Title (Official Video)", "Song Title"),
    ("Song Title [Official Music Video]", "Song Title"),
    ("Song Title (Official Audio)", "Song Title"),
    ("Song Title (Lyric Video)", "Song Title"),
    ("Song Title (Lyrics)", "Song Title"),
    ("Song Title (Visualizer)", "Song Title"),
    ("Song Title (4K)", "Song Title"),
    ("Song Title (HD)", "Song Title"),
    ("Song Title (Remastered 2011)", "Song Title (Remastered 2011)"),
    ("Song Title (Remix)", "Song Title (Remix)"),
    ("Song Title feat. Artist B", "Song Title feat. Artist B"),
    ("Song Title  extra  spaces", "Song Title extra spaces"),  # interior whitespace is collapsed
])
def test_clean_field(raw, expected):
    assert _clean_field(raw) == expected


# ---------------------------------------------------------------------------
# Unicode dashes in title
# ---------------------------------------------------------------------------
def test_em_dash_split():
    title = "Bon Iver — Skinny Love (Official)"
    artist, track, conf = normalize_one(_event(channel="BonIverVEVO"), _meta(title, "BonIverVEVO"))
    assert conf == CONF_PARSED
    assert artist == "Bon Iver"
    assert track == "Skinny Love"


def test_en_dash_split():
    title = "Frank Ocean – Thinking Bout You"
    artist, track, conf = normalize_one(_event(channel="FrankOceanVEVO"), _meta(title, "FrankOceanVEVO"))
    assert conf == CONF_PARSED
    assert artist == "Frank Ocean"
    assert track == "Thinking Bout You"


# ---------------------------------------------------------------------------
# hashtags stripped
# ---------------------------------------------------------------------------
def test_hashtag_stripped():
    title = "Artist - Track #NewMusic #Pop"
    artist, track, conf = normalize_one(_event(channel="ArtistVEVO"), _meta(title, "ArtistVEVO"))
    assert conf == CONF_PARSED
    assert "NewMusic" not in track
    assert "#" not in track
