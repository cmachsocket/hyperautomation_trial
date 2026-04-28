from __future__ import annotations

from pathlib import Path

import pytest

from server.ai import ai_controller as http_controller
from server.ai import ai_controller_fastmcp as fastmcp_controller


def _prepare_controller_root(monkeypatch: pytest.MonkeyPatch, module, root: Path) -> None:
    monkeypatch.setattr(module, "PROJECT_ROOT", root)
    monkeypatch.setattr(module, "RESTRICTED_READ_PATHS", set())


def _write_sample_file(root: Path) -> Path:
    sample = root / "sample.txt"
    sample.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    return sample


@pytest.mark.asyncio
async def test_http_tool_read_file_and_chunk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_controller_root(monkeypatch, http_controller, tmp_path)
    sample = _write_sample_file(tmp_path)

    full = await http_controller.tool_read_file(sample.name)
    assert full == "one\ntwo\nthree\nfour\nfive\n"

    chunk = await http_controller.tool_read_file_chunk(sample.name, 2, 4)
    assert chunk == "two\nthree\nfour"


@pytest.mark.asyncio
async def test_http_tool_read_file_rejects_large_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_controller_root(monkeypatch, http_controller, tmp_path)
    monkeypatch.setattr(http_controller, "MAX_READ_FILE_SIZE_BYTES", 4)
    sample = tmp_path / "large.txt"
    sample.write_text("12345", encoding="utf-8")

    with pytest.raises(ValueError, match="file too large"):
        await http_controller.tool_read_file(sample.name)


def test_fastmcp_read_file_and_chunk(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_controller_root(monkeypatch, fastmcp_controller, tmp_path)
    sample = _write_sample_file(tmp_path)

    full = fastmcp_controller.read_file(sample.name)
    assert full == "one\ntwo\nthree\nfour\nfive\n"

    chunk = fastmcp_controller.read_file_chunk(sample.name, 3, 5)
    assert chunk == "three\nfour\nfive"


def test_fastmcp_read_file_rejects_large_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _prepare_controller_root(monkeypatch, fastmcp_controller, tmp_path)
    monkeypatch.setattr(fastmcp_controller, "MAX_READ_FILE_SIZE_BYTES", 4)
    sample = tmp_path / "large.txt"
    sample.write_text("12345", encoding="utf-8")

    with pytest.raises(ValueError, match="file too large"):
        fastmcp_controller.read_file(sample.name)