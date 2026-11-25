from __future__ import annotations

from pathlib import Path

from ai_qa.settings import load_settings
from ai_qa.validation.policy import policy_from_settings


def test_policy_maps_settings(example_config_path: Path) -> None:
    loaded = load_settings(example_config_path)

    policy = policy_from_settings(loaded.settings)

    assert policy.expected_target_id == "cpp-webserver-local"
    assert policy.max_cases == 6
    assert policy.run_timeout_seconds == 120
    assert policy.benchmark_allowed is False
    assert policy.benchmark_max_duration_seconds == 30
    assert policy.benchmark_max_connections == 64
    assert policy.benchmark_max_threads == 4
    assert policy.max_plan_repairs == 2


def test_cli_authorization_enables_benchmark(example_config_path: Path) -> None:
    loaded = load_settings(example_config_path)

    policy = policy_from_settings(loaded.settings, allow_benchmark=True)

    assert policy.benchmark_allowed is True
