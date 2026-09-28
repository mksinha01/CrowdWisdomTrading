r"""Scrub secrets, tokens, and authorization credentials from fixture files.

Spec:
  - walk fixtures/http/**/*.json (excluding *.raw.json)
  - apply token regex: ([?&]token=)[^&\s"']+ -> \\1<redacted>
  - redact prefixes: apify_api_, sk-or-v1-, nvapi-, tvly- -> <redacted>
  - redact Authorization and x-api-key header values in headers dict
  - rewrite in place with newline="\\n" and ensure_ascii=False (LF-only)
  - print per-file diff count; exit 1 if anything found (gates CI)
  - --check mode: report only, change nothing, exit 1 if secrets found, 0 if clean
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Regexes matching credentials and tokens (Rules R1, S10, S08)
TOKEN_RE = re.compile(r"([?&]token=)([^&\s\"']+)", re.I)
PREFIX_RE = re.compile(r"(?:apify_api_|sk-or-v1-|nvapi-|tvly-)[a-zA-Z0-9_-]+", re.I)
SENSITIVE_HEADERS = frozenset({
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
})


def scrub_text(text: str) -> tuple[str, int]:
    """Scrub query tokens and secret prefixes from a string.
    
    Returns (scrubbed_text, diff_count).
    Does not count already-redacted occurrences (<redacted>).
    """
    count = 0

    def _token_sub(m: re.Match) -> str:
        nonlocal count
        val = m.group(2)
        if val == "<redacted>":
            return m.group(0)
        count += 1
        return m.group(1) + "<redacted>"

    def _prefix_sub(m: re.Match) -> str:
        nonlocal count
        val = m.group(0)
        if val == "<redacted>":
            return val
        count += 1
        return "<redacted>"

    s1 = TOKEN_RE.sub(_token_sub, text)
    s2 = PREFIX_RE.sub(_prefix_sub, s1)
    return s2, count


def scrub_fixture_data(data: Any) -> tuple[Any, int]:
    """Recursively scrub a fixture data structure (dict/list/str).
    
    Redacts Authorization and x-api-key headers in headers dict.
    Redacts query tokens and secret prefixes everywhere.
    Returns (scrubbed_data, diff_count).
    """
    count = 0
    if isinstance(data, dict):
        new_dict: dict[str, Any] = {}
        for k, v in data.items():
            if str(k).lower() == "headers" and isinstance(v, dict):
                new_headers: dict[str, Any] = {}
                for hk, hv in v.items():
                    if str(hk).lower() in SENSITIVE_HEADERS:
                        if hv != "<redacted>":
                            count += 1
                            new_headers[hk] = "<redacted>"
                        else:
                            new_headers[hk] = hv
                    elif isinstance(hv, str):
                        scrubbed_hv, c = scrub_text(hv)
                        count += c
                        new_headers[hk] = scrubbed_hv
                    else:
                        new_headers[hk] = hv
                new_dict[k] = new_headers
            else:
                sub_val, c = scrub_fixture_data(v)
                count += c
                new_dict[k] = sub_val
        return new_dict, count
    elif isinstance(data, list):
        new_list: list[Any] = []
        for item in data:
            sub_val, c = scrub_fixture_data(item)
            count += c
            new_list.append(sub_val)
        return new_list, count
    elif isinstance(data, str):
        return scrub_text(data)
    else:
        return data, 0


def scrub_file(path: Path, check_only: bool = False) -> int:
    """Scrub a single file. Returns diff_count (secrets found/redacted)."""
    raw_bytes = path.read_bytes()
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw_text = raw_bytes.decode("latin-1")

    diff_count = 0
    try:
        data = json.loads(raw_text)
        scrubbed_data, diff_count = scrub_fixture_data(data)
        if diff_count > 0 and not check_only:
            text = json.dumps(scrubbed_data, indent=2, ensure_ascii=False) + "\n"
            # Ensure LF-only on disk (Rule W8)
            path.write_bytes(text.encode("utf-8"))
    except json.JSONDecodeError:
        scrubbed_text, diff_count = scrub_text(raw_text)
        if diff_count > 0 and not check_only:
            scrubbed_lf = scrubbed_text.replace("\r\n", "\n")
            path.write_bytes(scrubbed_lf.encode("utf-8"))

    return diff_count


def find_fixture_files(root: Path) -> list[Path]:
    """Find all committed fixture files (excluding *.raw.json)."""
    if root.is_file():
        if root.name.endswith(".raw.json") or not root.name.endswith(".json"):
            return []
        return [root]

    files: list[Path] = []
    for p in sorted(root.rglob("*.json")):
        if p.name.endswith(".raw.json"):
            continue
        files.append(p)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrub credentials and tokens from fixture files.")
    parser.add_argument("paths", nargs="*", default=["fixtures/http"], help="Path(s) or directories to scrub")
    parser.add_argument("--check", action="store_true", help="Report only, change nothing, exit 1 if secrets found")
    args = parser.parse_args(argv)

    total_secrets = 0
    dirty_files = 0
    scanned_files = 0

    for path_str in args.paths:
        p = Path(path_str)
        if not p.exists():
            continue
        target_files = find_fixture_files(p)
        for tf in target_files:
            scanned_files += 1
            diff_count = scrub_file(tf, check_only=args.check)
            if diff_count > 0:
                dirty_files += 1
                total_secrets += diff_count
                action = "FOUND" if args.check else "SCRUBBED"
                print(f"{tf}: {diff_count} secret(s) {action}")

    if total_secrets > 0:
        mode = "check failed" if args.check else "rewritten"
        print(f"\n[FAIL] {total_secrets} secret(s) in {dirty_files} file(s) ({mode}).", file=sys.stderr)
        return 1
    else:
        print(f"[OK] Clean: {scanned_files} fixture file(s) scanned, 0 secrets.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
