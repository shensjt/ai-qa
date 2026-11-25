from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ai_qa.artifacts.store import FileArtifactStore
from ai_qa.domain.errors import AiQaError, ErrorCode


@pytest.fixture
def store(tmp_path: Path) -> FileArtifactStore:
    return FileArtifactStore(tmp_path / "artifacts")


def test_write_text_returns_metadata(store: FileArtifactStore) -> None:
    text = "hello artifact\n"

    ref = store.write_text("run_abc", "cases/http_get_index.txt", text, "case_log")

    assert ref.artifact_id.startswith("art_")
    assert ref.run_id == "run_abc"
    assert ref.name == "cases/http_get_index.txt"
    assert ref.kind == "case_log"
    assert ref.uri == "run_abc/cases/http_get_index.txt"
    assert ref.size_bytes == len(text.encode("utf-8"))
    assert ref.sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert ref.mime.startswith("text/plain")
    assert (store._root / ref.uri).read_text(encoding="utf-8") == text


def test_write_bytes_nested_name(store: FileArtifactStore) -> None:
    ref = store.write_bytes("run_abc", "cases/raw.bin", b"\x00\x01", "raw")

    assert ref.size_bytes == 2
    assert (store._root / ref.uri).read_bytes() == b"\x00\x01"
    assert ref.mime == "application/octet-stream"


def test_overwrite_replaces_content(store: FileArtifactStore) -> None:
    first = store.write_text("run_abc", "report.md", "one", "report")
    second = store.write_text("run_abc", "report.md", "two", "report")

    assert first.sha256 != second.sha256
    assert (store._root / "run_abc" / "report.md").read_text(encoding="utf-8") == "two"
    assert not list((store._root / "run_abc").glob("*.tmp"))


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "sub/../../escape.txt",
        "/absolute.txt",
        "..",
        ".",
        "a\x00b",
    ],
)
def test_name_traversal_rejected(store: FileArtifactStore, name: str) -> None:
    with pytest.raises(AiQaError) as excinfo:
        store.write_bytes("run_abc", name, b"x", "raw")

    assert excinfo.value.code is ErrorCode.ARTIFACT_WRITE_FAILED


@pytest.mark.parametrize(
    "run_id",
    ["../run", "run/../x", "run/sub", "", ".hidden", "a" * 65],
)
def test_run_id_rejected(store: FileArtifactStore, run_id: str) -> None:
    with pytest.raises(AiQaError) as excinfo:
        store.write_bytes(run_id, "a.txt", b"x", "raw")

    assert excinfo.value.code is ErrorCode.ARTIFACT_WRITE_FAILED


def test_symlink_escape_rejected(store: FileArtifactStore, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    run_dir = store._root / "run_abc"
    run_dir.mkdir()
    (run_dir / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AiQaError) as excinfo:
        store.write_bytes("run_abc", "link/evil.txt", b"x", "raw")

    assert excinfo.value.code is ErrorCode.ARTIFACT_WRITE_FAILED
    assert not (outside / "evil.txt").exists()


def test_write_failure_is_wrapped(store: FileArtifactStore) -> None:
    (store._root / "run_file").write_text("not a directory", encoding="utf-8")

    with pytest.raises(AiQaError) as excinfo:
        store.write_bytes("run_file", "a.txt", b"x", "raw")

    assert excinfo.value.code is ErrorCode.ARTIFACT_WRITE_FAILED
