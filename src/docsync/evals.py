from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .config import Settings
from .manual import SnapshotBundle, run_snapshot_bundle


class EvalExpectation(BaseModel):
    expected_outcomes: list[str] = Field(default_factory=list)
    expected_doc_paths: list[str] = Field(default_factory=list)
    expect_doc_patch: bool | None = None
    expected_error_code: str | None = None
    expected_validation_status: str | None = None
    expected_comment_substrings: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    name: str
    scenario: str = ""
    tags: list[str] = Field(default_factory=list)
    event_payload: dict[str, Any]
    pr_snapshot: dict[str, Any]
    expectation: EvalExpectation

    def to_snapshot_bundle(self) -> SnapshotBundle:
        return SnapshotBundle.model_validate(
            {
                "event_payload": self.event_payload,
                "pr_snapshot": self.pr_snapshot,
            }
        )


class EvalCaseResult(BaseModel):
    name: str
    scenario: str = ""
    passed: bool
    failures: list[str] = Field(default_factory=list)
    outcome: str = ""
    error_code: str | None = None
    patch_doc_paths: list[str] = Field(default_factory=list)
    comment_count: int = 0


class EvalSuiteResult(BaseModel):
    total: int
    passed: int
    failed: int
    results: list[EvalCaseResult] = Field(default_factory=list)


def load_eval_case(path: str | Path) -> EvalCase:
    return EvalCase.model_validate_json(Path(path).read_text(encoding="utf-8"))


def discover_eval_cases(path: str | Path) -> list[Path]:
    candidate = Path(path)
    if candidate.is_file():
        return [candidate]
    return sorted(item for item in candidate.rglob("*.json") if item.is_file())


def run_eval_case(
    case_path: str | Path,
    settings: Settings | None = None,
    llm_client=None,
) -> EvalCaseResult:
    case = load_eval_case(case_path)
    bundle = case.to_snapshot_bundle()
    effective_settings = _build_eval_settings(settings)
    result, github_client = run_snapshot_bundle(
        bundle,
        settings=effective_settings,
        llm_client=llm_client,
    )
    return evaluate_case(case, result, github_client.published_comments)


def run_eval_suite(
    path: str | Path,
    settings: Settings | None = None,
    llm_client_factory=None,
) -> EvalSuiteResult:
    effective_settings = _build_eval_settings(settings)
    results: list[EvalCaseResult] = []
    for case_path in discover_eval_cases(path):
        llm_client = llm_client_factory(load_eval_case(case_path)) if llm_client_factory is not None else None
        results.append(run_eval_case(case_path, settings=effective_settings, llm_client=llm_client))
    passed = sum(1 for item in results if item.passed)
    failed = len(results) - passed
    return EvalSuiteResult(total=len(results), passed=passed, failed=failed, results=results)


def evaluate_case(case: EvalCase, result: dict[str, Any], comments: list[str]) -> EvalCaseResult:
    failures: list[str] = []
    outcome = str(result.get("outcome") or "")
    error_code = result.get("error_code")
    expectation = case.expectation

    if expectation.expected_outcomes and outcome not in expectation.expected_outcomes:
        failures.append(
            f"Unexpected outcome: expected one of {expectation.expected_outcomes}, got {outcome!r}"
        )

    if expectation.expected_error_code is not None and error_code != expectation.expected_error_code:
        failures.append(
            f"Unexpected error_code: expected {expectation.expected_error_code!r}, got {error_code!r}"
        )

    validation_report = result.get("validation_report")
    validation_status = getattr(validation_report, "status", None)
    if expectation.expected_validation_status is not None and validation_status != expectation.expected_validation_status:
        failures.append(
            f"Unexpected validation status: expected {expectation.expected_validation_status!r}, got {validation_status!r}"
        )

    patch_doc_paths = _extract_patch_doc_paths(result)
    has_doc_patch = bool(patch_doc_paths)
    if expectation.expect_doc_patch is not None and has_doc_patch != expectation.expect_doc_patch:
        failures.append(
            f"Unexpected doc patch presence: expected {expectation.expect_doc_patch}, got {has_doc_patch}"
        )

    if expectation.expected_doc_paths:
        missing_paths = sorted(set(expectation.expected_doc_paths).difference(patch_doc_paths))
        if missing_paths:
            failures.append(f"Missing expected doc paths: {missing_paths}")

    comment_text = "\n".join(comments)
    for substring in expectation.expected_comment_substrings:
        if substring not in comment_text:
            failures.append(f"Missing expected comment substring: {substring!r}")

    return EvalCaseResult(
        name=case.name,
        scenario=case.scenario,
        passed=not failures,
        failures=failures,
        outcome=outcome,
        error_code=error_code,
        patch_doc_paths=patch_doc_paths,
        comment_count=len(comments),
    )


def _extract_patch_doc_paths(result: dict[str, Any]) -> list[str]:
    patch = result.get("doc_patch")
    entries = getattr(patch, "entries", None)
    if not entries:
        return []
    return [entry.doc_path for entry in entries]


def _build_eval_settings(settings: Settings | None) -> Settings:
    base = settings or Settings.from_env()
    return base.model_copy(
        update={
            "publish_mode": "comment_only",
            "telegram_bot_token": "",
            "telegram_chat_id": "",
        }
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DocSync eval cases from a file or directory.")
    parser.add_argument("path", help="Path to an eval case JSON file or a directory of eval cases.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the suite result as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    suite = run_eval_suite(args.path)

    if args.json:
        print(json.dumps(suite.model_dump(mode="json"), indent=2))
        return 0 if suite.failed == 0 else 1

    print(f"total: {suite.total}")
    print(f"passed: {suite.passed}")
    print(f"failed: {suite.failed}")
    for result in suite.results:
        status = "PASS" if result.passed else "FAIL"
        print(f"- {status} {result.name} [{result.scenario or 'unspecified'}] -> {result.outcome}")
        for failure in result.failures:
            print(f"  reason: {failure}")
    return 0 if suite.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
