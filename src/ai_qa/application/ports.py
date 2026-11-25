"""应用层 Port 协议（实现规格 §7）。

Phase 1 只定义已经实现并由 Repository/ArtifactStore 满足的接口。
"""

from __future__ import annotations

from typing import Protocol

from ai_qa.domain.models import TestRun
from ai_qa.domain.plans import StoredPlan
from ai_qa.domain.results import ArtifactRef, CaseResult


class PlanRepository(Protocol):
    def save_plan(self, plan: StoredPlan) -> None: ...

    def get_plan(self, plan_id: str) -> StoredPlan: ...


class RunRepository(Protocol):
    def create_run(self, run: TestRun) -> None: ...

    def update_run(self, run: TestRun) -> None: ...

    def get_run(self, run_id: str) -> TestRun: ...


class ResultRepository(Protocol):
    def save_case_result(self, run_id: str, result: CaseResult) -> None: ...

    def list_case_results(self, run_id: str) -> list[CaseResult]: ...


class ArtifactRepository(Protocol):
    def save_artifact(self, artifact: ArtifactRef, case_result_id: str | None = None) -> None: ...

    def list_artifacts(self, run_id: str) -> list[ArtifactRef]: ...


class ArtifactStore(Protocol):
    def write_bytes(
        self,
        run_id: str,
        name: str,
        data: bytes,
        kind: str,
        *,
        mime: str | None = None,
    ) -> ArtifactRef: ...

    def write_text(
        self,
        run_id: str,
        name: str,
        text: str,
        kind: str,
        *,
        mime: str | None = None,
    ) -> ArtifactRef: ...
