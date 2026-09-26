"""Tests for CLI entrypoint (Story S01)."""
import pytest

from cwt.cli import main


def test_cli_help(capsys):
    """--help should exit with status 0 and show help text."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "usage: cwt" in captured.out
    assert "run" in captured.out
    assert "doctor" in captured.out


def test_cli_requires_command():
    """Calling without command raises SystemExit."""
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2
