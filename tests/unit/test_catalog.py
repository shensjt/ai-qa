from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from ai_qa.domain.errors import AiQaError, ErrorCode
from ai_qa.validation.catalog import CaseCatalog, load_catalog


def _catalog_data() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "suite_id": "test_suite",
        "cases": [
            {
                "case_id": "http.get_index",
                "executor": "http",
                "description": "请求首页",
                "params": {
                    "path": {
                        "type": "http_path",
                        "default": "/",
                        "description": "请求路径",
                    }
                },
                "timeout_seconds": 5,
                "rule_set": "http.get_index",
                "artifact_policy": "http_exchange",
            }
        ],
    }


def _write_catalog(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_example_catalog_has_six_cases(http_catalog: CaseCatalog) -> None:
    assert http_catalog.suite_id == "http_smoke"
    assert http_catalog.case_ids() == (
        "http.get_index",
        "http.head",
        "http.keep_alive",
        "http.pipelined",
        "http.split_request",
        "http.path_traversal",
    )
    for case_id in http_catalog.case_ids():
        assert http_catalog.get(case_id) is not None


def test_catalog_marks_attack_policy(http_catalog: CaseCatalog) -> None:
    traversal = http_catalog.get("http.path_traversal")
    assert traversal is not None
    assert traversal.artifact_policy == "raw_http_exchange"
    assert traversal.params["path"].allowed_values


def test_loads_valid_single_case(tmp_path: Path) -> None:
    catalog = load_catalog(_write_catalog(tmp_path, _catalog_data()))

    case = catalog.get("http.get_index")
    assert case is not None
    assert case.params["path"].default == "/"


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AiQaError) as excinfo:
        load_catalog(tmp_path / "missing.yaml")

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID


def test_top_level_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "catalog.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(AiQaError) as excinfo:
        load_catalog(path)

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID


def test_duplicate_case_id_rejected(tmp_path: Path) -> None:
    data = _catalog_data()
    data["cases"].append(dict(data["cases"][0]))

    with pytest.raises(AiQaError) as excinfo:
        load_catalog(_write_catalog(tmp_path, data))

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID
    assert "http.get_index" in excinfo.value.details["duplicates"]


def test_attack_path_requires_allowed_values(tmp_path: Path) -> None:
    data = _catalog_data()
    data["cases"][0]["params"] = {"path": {"type": "attack_path", "default": "/../x"}}

    with pytest.raises(AiQaError) as excinfo:
        load_catalog(_write_catalog(tmp_path, data))

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID
    assert any("allowed_values" in issue["msg"] for issue in excinfo.value.details["issues"])


def test_attack_path_default_must_be_allowed(tmp_path: Path) -> None:
    data = _catalog_data()
    data["cases"][0]["params"] = {
        "path": {
            "type": "attack_path",
            "default": "/../x",
            "allowed_values": ["/../y"],
        }
    }

    with pytest.raises(AiQaError) as excinfo:
        load_catalog(_write_catalog(tmp_path, data))

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID


def test_param_requires_default(tmp_path: Path) -> None:
    data = _catalog_data()
    data["cases"][0]["params"]["path"].pop("default")

    with pytest.raises(AiQaError) as excinfo:
        load_catalog(_write_catalog(tmp_path, data))

    assert any("default" in issue["msg"] for issue in excinfo.value.details["issues"])


def test_allowed_values_only_for_attack_path(tmp_path: Path) -> None:
    data = _catalog_data()
    data["cases"][0]["params"]["path"]["allowed_values"] = ["/"]

    with pytest.raises(AiQaError) as excinfo:
        load_catalog(_write_catalog(tmp_path, data))

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID


def test_invalid_integer_list_default(tmp_path: Path) -> None:
    data = _catalog_data()
    data["cases"][0]["params"]["split_sizes"] = {
        "type": "integer_list",
        "default": ["a"],
        "min_items": 1,
        "max_items": 2,
        "min_value": 1,
        "max_value": 3,
    }

    with pytest.raises(AiQaError) as excinfo:
        load_catalog(_write_catalog(tmp_path, data))

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID


def test_extra_case_field_rejected(tmp_path: Path) -> None:
    data = _catalog_data()
    data["cases"][0]["command"] = "rm -rf /"

    with pytest.raises(AiQaError) as excinfo:
        load_catalog(_write_catalog(tmp_path, data))

    assert excinfo.value.code is ErrorCode.CONFIG_INVALID
