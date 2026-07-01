from __future__ import annotations

"""Interactive channel triage: review no_meta channels and bulk-add rules."""
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

_RULES_PATH = Path(__file__).parent / "rules.yaml"
_SAMPLE_SIZE = 5
_DEFAULT_TOP_N = 30


def _load_yaml(path: Path) -> dict:
    import yaml
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _save_yaml(path: Path, data: dict) -> None:
    import yaml
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _ensure_list(data: dict, *keys: str) -> list:
    """Navigate nested dict by keys, ensuring each level exists, returning the list."""
    node = data
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    last = keys[-1]
    if last not in node or node[last] is None:
        node[last] = []
    return node[last]


def _already_in_list(lst: list, value: str) -> bool:
    return value in lst


def _prompt(prompt_text: str) -> str:
    """Read a line from stdin, stripping whitespace. Raises EOFError on Ctrl-D."""
    try:
        return input(prompt_text).strip()
    except KeyboardInterrupt:
        print()
        raise


def _print_divider():
    print("\n" + "─" * 60)


def _strip_watched_prefix(title: str) -> str:
    import re
    _RE = re.compile(
        r"^(?:Watched|Regardé|Angesehen|Bekeken|Reproduzido|Reproducido|"
        r"Visualizzato|Просмотрено|観た|시청함|已觀看)\s+",
        re.IGNORECASE,
    )
    return _RE.sub("", title)


def run_triage(
    needs_metadata: list,
    rules_path: Path = _RULES_PATH,
    top_n: int = _DEFAULT_TOP_N,
    sample_size: int = _SAMPLE_SIZE,
) -> None:
    """Interactively triage the needs_metadata bucket by channel."""
    import random

    # Group events by channel; blank channel names get a placeholder
    by_channel: dict[str, list] = defaultdict(list)
    for e in needs_metadata:
        key = e.channel if e.channel else "(no channel)"
        by_channel[key].append(e)

    # Sort channels by event count descending
    ranked = sorted(by_channel.items(), key=lambda kv: len(kv[1]), reverse=True)
    if top_n:
        ranked = ranked[:top_n]

    if not ranked:
        print("No needs_metadata events to triage.")
        return

    data = _load_yaml(rules_path)
    whitelist = _ensure_list(data, "channels", "whitelist")
    blacklist = _ensure_list(data, "channels", "blacklist")

    pending_changes: list[str] = []
    total = len(ranked)

    print(f"\nTriaging {total} channels with most unclassified events.")
    print("Options per channel:")
    print("  [w] whitelist  — always treat as music (saved to rules.yaml)")
    print("  [b] blacklist  — never treat as music (saved to rules.yaml)")
    print("  [s] skip       — leave as-is, needs API metadata")
    print("  [q] quit       — stop and save changes so far\n")

    for idx, (channel, events) in enumerate(ranked, 1):
        count = len(events)
        _print_divider()
        print(f"[{idx}/{total}]  {channel}  ({count:,} events)")

        # Sample titles
        sample = random.sample(events, min(sample_size, count))
        print("  Sample titles:")
        for e in sample:
            print(f"    • {_strip_watched_prefix(e.raw_title)}")

        if channel == "(no channel)":
            print("  (no channel name — cannot add to rules; skipping)")
            continue

        # Check if already in a list
        already_white = channel in whitelist
        already_black = channel in blacklist
        if already_white:
            print("  (already in whitelist)")
        if already_black:
            print("  (already in blacklist)")

        while True:
            try:
                choice = _prompt("  → [w/b/s/q]: ").lower()
            except (EOFError, KeyboardInterrupt):
                print("\nInterrupted — saving changes so far.")
                _finish(data, rules_path, pending_changes)
                return

            if choice in ("q", "quit"):
                _finish(data, rules_path, pending_changes)
                return
            elif choice in ("s", "skip", ""):
                print("  Skipped.")
                break
            elif choice in ("w", "whitelist"):
                if already_white:
                    print("  Already whitelisted, skipping.")
                else:
                    whitelist.append(channel)
                    pending_changes.append(f"whitelist: {channel!r}")
                    print(f"  ✓ Added to whitelist.")
                break
            elif choice in ("b", "blacklist"):
                if already_black:
                    print("  Already blacklisted, skipping.")
                else:
                    blacklist.append(channel)
                    pending_changes.append(f"blacklist: {channel!r}")
                    print(f"  ✓ Added to blacklist.")
                break
            else:
                print("  Invalid choice — enter w, b, s, or q.")

    _finish(data, rules_path, pending_changes)


def _finish(data: dict, rules_path: Path, pending_changes: list[str]) -> None:
    if not pending_changes:
        print("\nNo changes made.")
        return

    _save_yaml(rules_path, data)
    print(f"\nSaved {len(pending_changes)} change(s) to {rules_path}:")
    for change in pending_changes:
        print(f"  + {change}")
    print("\nRe-run 'filter' or 'run' to apply the updated rules.")
