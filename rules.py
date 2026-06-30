from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_RULES_PATH = Path(__file__).parent / "rules.yaml"


class Rules:
    def __init__(self, data: dict):
        ch = data.get("channels") or {}
        self._whitelist: list[str] = ch.get("whitelist") or []
        self._blacklist: list[str] = ch.get("blacklist") or []
        self._video_overrides: dict[str, dict] = data.get("video_overrides") or {}
        self._channel_artist_map: dict[str, Optional[str]] = (
            data.get("channel_artist_map") or {}
        )

    # ------------------------------------------------------------------
    def _channel_matches(self, patterns: list[str], channel: str) -> bool:
        for pat in patterns:
            if fnmatch.fnmatch(channel, pat):
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

    def get_video_override(self, video_id: str) -> Optional[dict]:
        return self._video_overrides.get(video_id)

    def get_channel_artist(self, channel: str) -> Optional[str]:
        """Return mapped artist name, empty string to skip, or None if no mapping."""
        if channel in self._channel_artist_map:
            return self._channel_artist_map[channel]
        return None


def load_rules(path: Path = _RULES_PATH) -> Rules:
    try:
        import yaml  # type: ignore
    except ImportError:
        log.warning("PyYAML not installed; rules.yaml will be ignored. Run: uv pip install pyyaml")
        return Rules({})

    if not path.exists():
        log.debug("No rules.yaml found at %s; using empty rules", path)
        return Rules({})

    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    log.info(
        "Loaded rules: %d channel whitelist, %d channel blacklist, "
        "%d video overrides, %d channel artist mappings",
        len((data.get("channels") or {}).get("whitelist") or []),
        len((data.get("channels") or {}).get("blacklist") or []),
        len(data.get("video_overrides") or {}),
        len(data.get("channel_artist_map") or {}),
    )
    return Rules(data)
