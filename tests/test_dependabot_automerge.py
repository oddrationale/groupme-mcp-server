from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dependabot_automerge.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dependabot_automerge", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = _load()


def _dep(
    name: str = "ruff",
    *,
    ecosystem: str = "uv",
    dependency_type: str = "direct:development",
    update_type: str = "version-update:semver-patch",
) -> dict[str, object]:
    return {
        "dependencyName": name,
        "packageEcosystem": ecosystem,
        "dependencyType": dependency_type,
        "updateType": update_type,
    }


@pytest.mark.parametrize(
    "dependency",
    [
        _dep("ruff", update_type="version-update:semver-patch"),
        _dep("ty", update_type="version-update:semver-minor"),
        _dep("fastmcp", dependency_type="direct:production"),
        _dep("joserfc", dependency_type="indirect", update_type="version-update:semver-minor"),
        _dep("astral-sh/setup-uv", ecosystem="github_actions", dependency_type="direct:production"),
        _dep(
            "github/codeql-action",
            ecosystem="github_actions",
            dependency_type="direct:production",
            update_type="version-update:semver-minor",
        ),
    ],
)
def test_low_risk_updates_auto_merge(dependency: dict[str, object]) -> None:
    assert policy.blockers([dependency]) == []


@pytest.mark.parametrize(
    ("dependency", "reason"),
    [
        (_dep("ruff", update_type="version-update:semver-major"), "semver-major update"),
        (
            _dep(
                "fastmcp",
                dependency_type="direct:production",
                update_type="version-update:semver-minor",
            ),
            "minor update of a runtime dependency",
        ),
        (
            _dep("pypa/gh-action-pypi-publish", ecosystem="github_actions"),
            "only runs during a release",
        ),
        (_dep("left-pad", ecosystem="npm_and_yarn"), "unsupported ecosystem 'npm_and_yarn'"),
        (
            _dep("ruff", dependency_type="direct:optional"),
            "unknown dependency type 'direct:optional'",
        ),
    ],
)
def test_risky_updates_need_review(dependency: dict[str, object], reason: str) -> None:
    (problem,) = policy.blockers([dependency])
    assert problem.startswith(f"{dependency['dependencyName']}: ")
    assert reason in problem


def test_missing_update_type_needs_review() -> None:
    dependency = _dep()
    del dependency["updateType"]
    assert policy.blockers([dependency]) == ["ruff: unknown update"]


def test_one_risky_dependency_blocks_the_whole_group() -> None:
    group = [
        _dep("ruff"),
        _dep(
            "fastmcp",
            dependency_type="direct:production",
            update_type="version-update:semver-minor",
        ),
    ]
    assert policy.blockers(group) == ["fastmcp: minor update of a runtime dependency"]


def test_no_metadata_needs_review() -> None:
    assert policy.blockers([]) == ["no dependency metadata"]


@pytest.mark.parametrize("raw", ["", "not json", '{"dependencyName": "ruff"}', "[1, 2]"])
def test_unreadable_metadata_fails_closed(raw: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert policy.main({"UPDATED_DEPENDENCIES": raw}) == 0
    captured = capsys.readouterr()
    assert captured.out == "automerge=false\n"
    assert "no dependency metadata" in captured.err


def test_main_writes_verdict_to_github_output(tmp_path: Path) -> None:
    output = tmp_path / "github_output"
    output.write_text("earlier=value\n", encoding="utf-8")
    raw = json.dumps([_dep("ruff")])
    assert policy.main({"UPDATED_DEPENDENCIES": raw, "GITHUB_OUTPUT": str(output)}) == 0
    assert output.read_text(encoding="utf-8") == "earlier=value\nautomerge=true\n"


def _yaml_list_after(text: str, key: str) -> set[str]:
    """Collect the ``- item`` lines of the block list that follows ``key``."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == key)
    items: set[str] = set()
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped.startswith("- "):
            break
        items.add(stripped.removeprefix("- "))
    return items


def test_dependabot_groups_match_release_only_actions() -> None:
    text = (_SCRIPT.parents[1] / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    actions_section = text[text.index("package-ecosystem: github-actions") :]
    excluded = _yaml_list_after(actions_section, "exclude-patterns:")
    release_group = _yaml_list_after(
        actions_section[actions_section.index("release-actions:") :], "patterns:"
    )
    assert excluded == policy.RELEASE_ONLY_ACTIONS
    assert release_group == policy.RELEASE_ONLY_ACTIONS
