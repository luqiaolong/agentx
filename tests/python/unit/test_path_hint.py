"""Unit tests for :func:`app.security.path_hint.format_unauthorized_hint`."""

from __future__ import annotations

from pathlib import Path

from app.security.path_hint import format_unauthorized_hint


def test_format_lists_writable_dirs() -> None:
    out = format_unauthorized_hint(
        rejected_path="D:\\Users\\me\\foo.py",
        action="read",
        authorized_paths=[(Path("/data/workspace"), True), (Path("/data/uploads"), False)],
        scratch_path=Path("/data/workspace/.scratch"),
    )
    assert "/data/workspace" in out
    # read-only dirs are not listed (the LLM can't write there).
    assert "/data/uploads" not in out


def test_format_recommends_scratch_with_filename() -> None:
    out = format_unauthorized_hint(
        rejected_path="C:\\Users\\luqia\\Desktop\\memory_analyze.py",
        action="write",
        authorized_paths=[],
        scratch_path=Path("/data/workspace/.scratch"),
    )
    assert "data/workspace/.scratch/memory_analyze.py" in out


def test_format_scratch_recommendation_read() -> None:
    out = format_unauthorized_hint(
        rejected_path="C:\\secret\\file.txt",
        action="read",
        authorized_paths=[],
        scratch_path=Path("/data/workspace/.scratch"),
    )
    # read action: scratch recommendation is not appropriate (scratch is write-only).
    # Still must list scratch as a fallback location for the user's *next* write.
    assert "data/workspace/.scratch" in out
    assert "未授权读取" in out


def test_format_write_action_dialog_hint() -> None:
    out = format_unauthorized_hint(
        rejected_path="D:\\x\\y.py",
        action="write",
        authorized_paths=[],
        scratch_path=Path("/.scratch"),
    )
    assert "dialog" in out or "授权" in out


def test_format_read_action_dialog_hint() -> None:
    out = format_unauthorized_hint(
        rejected_path="D:\\x\\y.py",
        action="read",
        authorized_paths=[],
        scratch_path=Path("/.scratch"),
    )
    assert "dialog" in out or "授权" in out


def test_format_truncates_to_5_dirs() -> None:
    paths = [(Path(f"/data/workspace/d{i}"), True) for i in range(20)]
    out = format_unauthorized_hint(
        rejected_path="D:\\foo.py",
        action="write",
        authorized_paths=paths,
        scratch_path=Path("/.scratch"),
    )
    # Only the first 5 should be mentioned.
    assert "/d0" in out
    assert "/d4" in out
    assert "/d5" not in out
    assert "/d19" not in out


def test_format_no_authorized_falls_back_to_scratch() -> None:
    out = format_unauthorized_hint(
        rejected_path="D:\\foo.py",
        action="write",
        authorized_paths=[],
        scratch_path=Path("/data/workspace/.scratch"),
    )
    # No "已授权目录" header should be present.
    assert "已授权" not in out
    assert "data/workspace/.scratch/foo.py" in out
