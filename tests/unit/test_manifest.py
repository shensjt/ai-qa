from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.knowledge.eval import load_eval_cases
from ai_qa.knowledge.ingest import load_manifest


def _manifest_data() -> dict[str, object]:
    return {
        "project_id": "cpp-webserver",
        "commit": "abc123",
        "documents": [
            {
                "source_path": "knowledge/project/runtime-contract.md",
                "source_type": "project_doc",
                "priority": "P0",
            }
        ],
    }


def _write(tmp_path: Path, data: dict[str, object]) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_loads_repository_manifest(repo_root: Path) -> None:
    manifest = load_manifest(repo_root / "knowledge" / "manifests" / "cpp_webserver.yaml")

    assert manifest.project_id == "cpp-webserver"
    assert len(manifest.documents) == 6
    assert manifest.documents[0].priority == "P0"


def test_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(AiQaError) as excinfo:
        load_manifest(tmp_path / "missing.yaml")

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID


def test_duplicate_source_paths_rejected(tmp_path: Path) -> None:
    data = _manifest_data()
    data["documents"].append(dict(data["documents"][0]))  # type: ignore[index]

    with pytest.raises(AiQaError) as excinfo:
        load_manifest(_write(tmp_path, data))

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID
    assert "duplicates" in excinfo.value.details


def test_invalid_priority_rejected(tmp_path: Path) -> None:
    data = _manifest_data()
    data["documents"][0]["priority"] = "P9"  # type: ignore[index]

    with pytest.raises(AiQaError) as excinfo:
        load_manifest(_write(tmp_path, data))

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID


def test_eval_cases_have_at_least_fifteen(repo_root: Path) -> None:
    cases = load_eval_cases(repo_root / "tests" / "evals" / "rag_cases.yaml")

    assert len(cases) >= 15
    assert all(case.expected_sources for case in cases)
