"""Tests for rules.py — channel patterns, title rules, overrides."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from rules import Rules, _compile_channel_patterns, _compile_title_rules


def _rules(data: dict) -> Rules:
    return Rules(data)


# ---------------------------------------------------------------------------
# Channel matching — glob
# ---------------------------------------------------------------------------

def test_glob_whitelist():
    r = _rules({"channels": {"whitelist": ["*VEVO"]}})
    assert r.is_whitelisted("vid1", "TaylorSwiftVEVO")
    assert not r.is_whitelisted("vid1", "freeCodeCamp.org")


def test_glob_blacklist():
    r = _rules({"channels": {"blacklist": ["freeCodeCamp.org"]}})
    assert r.is_blacklisted("vid1", "freeCodeCamp.org")
    assert not r.is_blacklisted("vid1", "Radiohead")


# ---------------------------------------------------------------------------
# Channel matching — regex (patterns wrapped in /…/)
# ---------------------------------------------------------------------------

def test_regex_whitelist_basic():
    r = _rules({"channels": {"whitelist": ["/Records/"]}})
    assert r.is_whitelisted("vid1", "Island Records")       # exact word
    assert r.is_whitelisted("vid1", "Atlantic Records UK")  # substring
    assert not r.is_whitelisted("vid1", "XL Recordings")   # "Recordings" ≠ "Records"
    assert not r.is_whitelisted("vid1", "freeCodeCamp.org")


def test_regex_whitelist_case_insensitive():
    r = _rules({"channels": {"whitelist": ["/\\bmusic\\b/i"]}})
    assert r.is_whitelisted("vid1", "Universal Music")
    assert r.is_whitelisted("vid1", "MUSIC CHANNEL")


def test_regex_blacklist():
    r = _rules({"channels": {"blacklist": ["/^(?:Kurzgesagt|TED)$/i"]}})
    assert r.is_blacklisted("vid1", "Kurzgesagt")
    assert r.is_blacklisted("vid1", "TED")
    assert not r.is_blacklisted("vid1", "TEDx Talks")  # ^ anchored


def test_invalid_regex_pattern_skipped(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        r = _rules({"channels": {"whitelist": ["/[invalid/"]}})
    assert "Invalid regex" in caplog.text
    # Should still load without crashing
    assert not r.is_whitelisted("vid1", "anything")


def test_glob_and_regex_mixed():
    r = _rules({"channels": {"whitelist": ["*VEVO", "/Records/"]}})
    assert r.is_whitelisted("vid1", "TaylorSwiftVEVO")    # glob
    assert r.is_whitelisted("vid1", "Island Records")     # regex


# ---------------------------------------------------------------------------
# Video override always wins over channel rules
# ---------------------------------------------------------------------------

def test_override_beats_blacklist():
    r = _rules({
        "channels": {"blacklist": ["BadChannel"]},
        "video_overrides": {"vid1": {"artist": "A", "track": "T"}},
    })
    assert not r.is_blacklisted("vid1", "BadChannel")
    assert r.is_whitelisted("vid1", "BadChannel")


# ---------------------------------------------------------------------------
# Title pattern — skip
# ---------------------------------------------------------------------------

def test_title_skip():
    r = _rules({"title_patterns": [{"match": "hour mix|full album", "action": "skip"}]})
    assert r.title_should_skip("1 Hour Mix - Chill Beats")
    assert r.title_should_skip("Artist - Full Album")
    assert not r.title_should_skip("Artist - Normal Track")


def test_title_skip_case_insensitive():
    r = _rules({"title_patterns": [{"match": "HOUR MIX", "action": "skip"}]})
    assert r.title_should_skip("2 hour mix")


# ---------------------------------------------------------------------------
# Title pattern — strip
# ---------------------------------------------------------------------------

def test_title_strip():
    r = _rules({"title_patterns": [{"match": "\\s*\\|\\s*Suno AI$", "action": "strip"}]})
    cleaned, artist, track = r.apply_title_rules("Cool Song | Suno AI")
    assert cleaned == "Cool Song"
    assert artist is None
    assert track is None


def test_title_strip_multiple():
    r = _rules({"title_patterns": [
        {"match": "\\s*\\|\\s*Suno AI", "action": "strip"},
        {"match": "\\s*\\(AI\\)", "action": "strip"},
    ]})
    cleaned, _, _ = r.apply_title_rules("Cool Song (AI) | Suno AI")
    assert "Suno AI" not in cleaned
    assert "(AI)" not in cleaned


# ---------------------------------------------------------------------------
# Title pattern — extract
# ---------------------------------------------------------------------------

def test_title_extract():
    r = _rules({"title_patterns": [
        {"match": "^(.+?)\\s+-\\s+(.+)$", "action": "extract", "artist": "\\2", "track": "\\1"},
    ]})
    cleaned, artist, track = r.apply_title_rules("Skinny Love - Bon Iver")
    assert artist == "Bon Iver"
    assert track == "Skinny Love"


def test_title_extract_first_match_wins():
    r = _rules({"title_patterns": [
        {"match": "Suno", "action": "extract", "artist": "Suno", "track": "Generated"},
        {"match": ".*", "action": "extract", "artist": "Fallback", "track": "Fallback"},
    ]})
    _, artist, track = r.apply_title_rules("A Suno track")
    assert artist == "Suno"   # first match, not second


def test_title_no_match_returns_original():
    r = _rules({"title_patterns": [{"match": "NOMATCH", "action": "skip"}]})
    cleaned, artist, track = r.apply_title_rules("Some Title")
    assert cleaned == "Some Title"
    assert artist is None
    assert track is None
