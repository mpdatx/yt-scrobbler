from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_RULES_PATH = Path(__file__).parent / "rules.yaml"


_REGEX_PAT = re.compile(r"^/(.+)/([gimsux]*)$")

_FLAG_MAP = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


def _parse_regex_pattern(pat: str) -> re.Pattern | None:
    """Return compiled regex if pat is /pattern/ or /pattern/flags, else None."""
    m = _REGEX_PAT.match(pat)
    if not m:
        return None
    body, flags_str = m.group(1), m.group(2)
    flags = re.IGNORECASE  # always case-insensitive for channel matching
    for f in flags_str:
        flags |= _FLAG_MAP.get(f, 0)
    try:
        return re.compile(body, flags)
    except re.error as exc:
        log.warning("Invalid regex channel pattern %r: %s", pat, exc)
        return None


def _compile_channel_patterns(patterns: list[str]) -> list[tuple[str, re.Pattern | None]]:
    """Return list of (raw_pattern, compiled_regex_or_None)."""
    compiled = []
    for pat in patterns:
        rx = _parse_regex_pattern(pat)
        if rx is not None or pat.startswith("/"):
            compiled.append((pat, rx))  # rx=None means bad regex, skip silently
        else:
            compiled.append((pat, None))  # glob
    return compiled


@dataclass
class TitleRule:
    raw: str
    pattern: re.Pattern
    action: str          # "skip" | "strip" | "extract"
    artist_tpl: str      # only used for action="extract"
    track_tpl: str       # only used for action="extract"


def _compile_title_rules(raw_rules: list[dict]) -> list[TitleRule]:
    compiled = []
    for r in raw_rules:
        match_str = r.get("match", "")
        if not match_str:
            log.warning("title_pattern entry missing 'match' key: %r", r)
            continue
        try:
            pattern = re.compile(match_str, re.IGNORECASE)
        except re.error as exc:
            log.warning("Invalid title pattern %r: %s", match_str, exc)
            continue
        action = r.get("action", "extract" if ("artist" in r or "track" in r) else "skip")
        compiled.append(TitleRule(
            raw=match_str,
            pattern=pattern,
            action=action,
            artist_tpl=r.get("artist", ""),
            track_tpl=r.get("track", ""),
        ))
    return compiled


class Rules:
    def __init__(self, data: dict):
        ch = data.get("channels") or {}
        self._whitelist = _compile_channel_patterns(ch.get("whitelist") or [])
        self._blacklist = _compile_channel_patterns(ch.get("blacklist") or [])
        self._video_overrides: dict[str, dict] = data.get("video_overrides") or {}
        self._channel_artist_map: dict[str, Optional[str]] = (
            data.get("channel_artist_map") or {}
        )
        self._title_rules: list[TitleRule] = _compile_title_rules(
            data.get("title_patterns") or []
        )

    # ------------------------------------------------------------------
    # Channel matching
    # ------------------------------------------------------------------

    def _channel_matches(
        self, patterns: list[tuple[str, re.Pattern | None]], channel: str
    ) -> bool:
        for raw, rx in patterns:
            if rx is not None:
                if rx.search(channel):
                    return True
            else:
                if fnmatch.fnmatch(channel, raw):
                    return True
        return False

    def is_whitelisted(self, video_id: str, channel: str) -> bool:
        if video_id in self._video_overrides:
            return True
        return self._channel_matches(self._whitelist, channel)

    def is_blacklisted(self, video_id: str, channel: str) -> bool:
        if video_id in self._video_overrides:
            return False  # explicit override always wins
        return self._channel_matches(self._blacklist, channel)

    # ------------------------------------------------------------------
    # Video / channel overrides
    # ------------------------------------------------------------------

    def get_video_override(self, video_id: str) -> Optional[dict]:
        return self._video_overrides.get(video_id)

    def get_channel_artist(self, channel: str) -> Optional[str]:
        """Return mapped artist name, or None if no mapping."""
        return self._channel_artist_map.get(channel)

    # ------------------------------------------------------------------
    # Title pattern rules
    # ------------------------------------------------------------------

    def title_should_skip(self, title: str) -> bool:
        """Return True if any skip rule matches the title."""
        for rule in self._title_rules:
            if rule.action == "skip" and rule.pattern.search(title):
                return True
        return False

    def apply_title_rules(self, title: str) -> tuple[str, Optional[str], Optional[str]]:
        """Apply strip/extract rules to a title.

        Returns (cleaned_title, artist_override_or_None, track_override_or_None).
        First matching extract rule wins. All strip rules are applied.
        """
        artist_override: Optional[str] = None
        track_override: Optional[str] = None

        for rule in self._title_rules:
            if rule.action == "skip":
                continue

            if rule.action == "strip":
                title = rule.pattern.sub("", title).strip()

            elif rule.action == "extract":
                m = rule.pattern.search(title)
                if m:
                    try:
                        artist_override = m.expand(rule.artist_tpl) if rule.artist_tpl else None
                        track_override = m.expand(rule.track_tpl) if rule.track_tpl else None
                    except re.error as exc:
                        log.warning("expand failed for pattern %r: %s", rule.raw, exc)
                    break  # first extract match wins

        return title, artist_override, track_override


def load_rules(path: Path = _RULES_PATH) -> Rules:
    try:
        import yaml  # type: ignore
    except ImportError:
        log.warning("PyYAML not installed; rules.yaml will be ignored.")
        return Rules({})

    if not path.exists():
        log.debug("No rules.yaml found at %s; using empty rules", path)
        return Rules({})

    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    ch = data.get("channels") or {}
    log.info(
        "Loaded rules: %d channel patterns (whitelist), %d channel patterns (blacklist), "
        "%d video overrides, %d channel artist mappings, %d title patterns",
        len(ch.get("whitelist") or []),
        len(ch.get("blacklist") or []),
        len(data.get("video_overrides") or {}),
        len(data.get("channel_artist_map") or {}),
        len(data.get("title_patterns") or []),
    )
    return Rules(data)
