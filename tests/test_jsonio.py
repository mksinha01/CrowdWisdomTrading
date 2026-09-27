"""Tests for JSON and JSONL I/O utilities (Story S02, Rule W8)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from cwt.util.jsonio import append_jsonl, now_iso, read_json, write_json


def test_write_and_read_json_utf8_and_lf(tmp_path):
    """Rule W8: write_json enforces utf-8 and LF without CRLF translation."""
    target = tmp_path / "test.json"
    data = {
        "dash": "—",  # em dash
        "quote": "“smart quotes”",  # curly / smart quotes
        "symbol": "🚀",
        "nested": {"key": "value"},
    }

    write_json(target, data)

    raw_bytes = target.read_bytes()
    # LF only: MUST NOT contain CRLF (\r\n)
    assert b"\r\n" not in raw_bytes
    assert raw_bytes.endswith(b"\n")
    # ensure_ascii=False: em dash is encoded as utf-8 bytes (0xe2 0x80 0x94)
    assert "—".encode("utf-8") in raw_bytes
    assert "“smart quotes”".encode("utf-8") in raw_bytes

    # Round trip via read_json
    loaded = read_json(target)
    assert loaded == data


def test_write_json_creates_parent_directories(tmp_path):
    """write_json automatically creates nested parent directories."""
    nested = tmp_path / "deeply" / "nested" / "dir" / "out.json"
    write_json(nested, {"status": "ok"})
    assert nested.exists()
    assert read_json(nested) == {"status": "ok"}


def test_append_jsonl(tmp_path):
    """append_jsonl appends LF-delimited json lines with utf-8 encoding."""
    target = tmp_path / "deep" / "records.jsonl"
    record1 = {"id": 1, "text": "first — line"}
    record2 = {"id": 2, "text": "second “quote” line"}

    append_jsonl(target, record1)
    append_jsonl(target, record2)

    raw_bytes = target.read_bytes()
    assert b"\r\n" not in raw_bytes

    lines = [
        read_line for read_line in target.read_text(encoding="utf-8").splitlines() if read_line
    ]
    assert len(lines) == 2
    assert "—" in lines[0]
    assert "“quote”" in lines[1]


def test_now_iso_format():
    """now_iso returns UTC ISO timestamp with seconds precision and Z suffix."""
    iso = now_iso()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", iso)
    # Parsable back to UTC datetime
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    assert abs((now - dt).total_seconds()) < 5
