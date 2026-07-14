#!/usr/bin/env python3
"""Fixture tests for model-evaluation corpus validation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from model_eval_manifest import ManifestError, corpus_fingerprint, load_corpus, read_git_blob


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def create_repository(root: Path) -> tuple[str, str]:
    root.mkdir()
    run(["git", "init", "--quiet"], root)
    run(["git", "config", "user.name", "Agent Flow Test"], root)
    run(["git", "config", "user.email", "agent-flow@example.invalid"], root)
    (root / "src").mkdir()
    (root / "src" / "value.txt").write_text("base\n", encoding="utf-8")
    run(["git", "add", "."], root)
    run(["git", "commit", "-qm", "base"], root)
    base = run(["git", "rev-parse", "HEAD"], root)
    (root / "src" / "value.txt").write_text("gold\n", encoding="utf-8")
    run(["git", "commit", "-qam", "gold"], root)
    gold = run(["git", "rev-parse", "HEAD"], root)
    return base, gold


def task_facts() -> dict:
    return {
        "role": "bun-worker",
        "changes_files": True,
        "repo_count": 1,
        "surfaces": ["backend"],
        "task_classes": ["local"],
        "public_contract": False,
        "migration": False,
        "external_write": False,
        "production_risk": "normal",
    }


def create_corpus(root: Path, base: str, gold: str) -> dict:
    (root / "tasks" / "fixture").mkdir(parents=True)
    (root / "tasks" / "fixture" / "prompt.md").write_text("Fix the fixture.\n", encoding="utf-8")
    write_json(
        root / "evaluators" / "fixture" / "evaluator.json",
        {
            "schema_version": 1,
            "commands": [
                {
                    "id": "behavior",
                    "repository_id": "fixture",
                    "scope": "lane",
                    "argv": ["python3", "checks/run.py"],
                }
            ],
            "positive_checks": ["updated behavior"],
            "negative_checks": ["old behavior rejected"],
        },
    )
    manifest = {
        "schema_version": 1,
        "corpus_id": "fixture-corpus",
        "repositories": {"fixture": {"path_env": "FIXTURE_EVAL_REPO"}},
        "tasks": [
            {
                "id": "fixture_task",
                "product_id": "fixture-product",
                "repositories": ["fixture"],
                "revisions": {"fixture": {"base": base, "gold": gold}},
                "lanes": [
                    {
                        "id": "worker",
                        "role": "bun-worker",
                        "repository_ids": ["fixture"],
                        "primary_repository": "fixture",
                        "task_facts": task_facts(),
                    }
                ],
                "prompt": "tasks/fixture/prompt.md",
                "evaluator": "evaluators/fixture/evaluator.json",
                "allowed_paths": {"fixture": ["src/**"]},
                "forbidden_paths": {"fixture": []},
                "timeout_seconds": 600,
                "repeat_policy": "paired-adaptive",
                "criticality": "normal",
            }
        ],
    }
    write_json(root / "manifest.json", manifest)
    return manifest


def expect_error(name: str, corpus: Path, environment: dict[str, str], needle: str) -> None:
    try:
        load_corpus(corpus, environment)
    except ManifestError as exc:
        message = str(exc)
    else:
        raise AssertionError(f"{name}: expected ManifestError")
    if needle not in message:
        raise AssertionError(f"{name}: expected {needle!r} in {message!r}")


def test_valid_corpus(root: Path) -> None:
    repo = root / "repo"
    base, gold = create_repository(repo)
    corpus = root / "corpus"
    create_corpus(corpus, base, gold)
    evaluator_path = corpus / "evaluators" / "fixture" / "evaluator.json"
    (evaluator_path.parent / "stub.py").write_text("VALUE = 'stub'\n", encoding="utf-8")
    evaluator = json.loads(evaluator_path.read_text(encoding="utf-8"))
    evaluator["commands"][0]["write_paths"] = ["agent_flow_eval_output/build"]
    evaluator["injections"] = [
        {
            "repository_id": "fixture",
            "source": "stub.py",
            "target": "node_modules/example/stub.py",
            "replace_or_create": True,
        }
    ]
    write_json(evaluator_path, evaluator)
    loaded = load_corpus(corpus, {"FIXTURE_EVAL_REPO": str(repo)})
    task = loaded.tasks[0]
    if task["lanes"][0]["task_facts"]["primary_task_class"] != "local":
        raise AssertionError("Task Facts were not normalized")
    if loaded.repositories["fixture"] != repo.resolve():
        raise AssertionError("repository path was not resolved")
    fingerprint = corpus_fingerprint(loaded)
    (corpus / "tasks" / "fixture" / "prompt.md").write_text(
        "Fix the changed fixture.\n",
        encoding="utf-8",
    )
    changed = corpus_fingerprint(load_corpus(corpus, {"FIXTURE_EVAL_REPO": str(repo)}))
    if fingerprint == changed:
        raise AssertionError("corpus fingerprint ignored a prompt change")


def test_rejects_unsafe_or_inconsistent_input(root: Path) -> None:
    repo = root / "repo-invalid"
    base, gold = create_repository(repo)
    corpus = root / "corpus-invalid"
    manifest = create_corpus(corpus, base, gold)
    environment = {"FIXTURE_EVAL_REPO": str(repo)}

    manifest["tasks"][0]["revisions"]["fixture"]["gold"] = base
    write_json(corpus / "manifest.json", manifest)
    expect_error("identical revisions", corpus, environment, "must differ")

    manifest["tasks"][0]["revisions"]["fixture"]["gold"] = gold
    manifest["tasks"][0]["lanes"][0]["role"] = "ghost-worker"
    manifest["tasks"][0]["lanes"][0]["task_facts"]["role"] = "ghost-worker"
    write_json(corpus / "manifest.json", manifest)
    expect_error("unknown role", corpus, environment, "unknown role")

    manifest["tasks"][0]["lanes"][0]["role"] = "bun-worker"
    manifest["tasks"][0]["lanes"][0]["task_facts"] = task_facts()
    evaluator_path = corpus / "evaluators" / "fixture" / "evaluator.json"
    evaluator = json.loads(evaluator_path.read_text(encoding="utf-8"))
    evaluator["commands"] = "python3 checks/run.py"
    write_json(evaluator_path, evaluator)
    write_json(corpus / "manifest.json", manifest)
    expect_error("shell command", corpus, environment, "non-empty array")

    evaluator["commands"] = [
        {
            "id": "behavior",
            "repository_id": "fixture",
            "scope": "lane",
            "argv": ["python3", "/tmp/run.py"],
        }
    ]
    write_json(evaluator_path, evaluator)
    expect_error("absolute evaluator path", corpus, environment, "non-portable argument")

    (evaluator_path.parent / "stub.py").write_text("VALUE = 'stub'\n", encoding="utf-8")
    evaluator["commands"] = [
        {
            "id": "behavior",
            "repository_id": "fixture",
            "scope": "lane",
            "argv": ["python3", "checks/run.py"],
            "write_paths": ["../outside"],
        }
    ]
    write_json(evaluator_path, evaluator)
    expect_error("unsafe evaluator write path", corpus, environment, "portable relative path")

    evaluator["commands"][0]["write_paths"] = [
        "agent_flow_eval_output/build",
        "agent_flow_eval_output/build",
    ]
    write_json(evaluator_path, evaluator)
    expect_error("duplicate evaluator write path", corpus, environment, "duplicates")

    evaluator["commands"][0]["write_paths"] = ["node_modules/build"]
    write_json(evaluator_path, evaluator)
    expect_error("arbitrary evaluator write root", corpus, environment, "must be under")

    evaluator["commands"][0]["write_paths"] = ["agent_flow_eval_output/build"]
    evaluator["injections"] = [
        {
            "repository_id": "fixture",
            "source": "stub.py",
            "target": "agent_flow_eval_output/build/stub.py",
        }
    ]
    write_json(evaluator_path, evaluator)
    expect_error("injection uses output root", corpus, environment, "reserved evaluator output root")

    evaluator["commands"] = [
        {
            "id": "behavior",
            "repository_id": "fixture",
            "scope": "lane",
            "argv": ["python3", "checks/run.py"],
        }
    ]
    evaluator["injections"] = [
        {
            "repository_id": "fixture",
            "source": "stub.py",
            "target": "node_modules/example/stub.py",
            "replace": True,
            "replace_or_create": True,
        }
    ]
    write_json(evaluator_path, evaluator)
    expect_error("conflicting replacement", corpus, environment, "both replacement modes")


def test_rejects_lfs_pointer(root: Path) -> None:
    repo = root / "repo-lfs"
    base, _ = create_repository(repo)
    (repo / "asset.bin").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n",
        encoding="utf-8",
    )
    run(["git", "add", "asset.bin"], repo)
    run(["git", "commit", "-qm", "lfs pointer"], repo)
    lfs_commit = run(["git", "rev-parse", "HEAD"], repo)
    (repo / "src" / "value.txt").write_text("after-lfs\n", encoding="utf-8")
    run(["git", "commit", "-qam", "after lfs"], repo)
    gold = run(["git", "rev-parse", "HEAD"], repo)
    corpus = root / "corpus-lfs"
    create_corpus(corpus, lfs_commit, gold)
    expect_error(
        "lfs pointer",
        corpus,
        {"FIXTURE_EVAL_REPO": str(repo)},
        "Git LFS pointers",
    )
    if base == lfs_commit:
        raise AssertionError("LFS fixture did not create a new commit")


def predictability_contract(corpus: Path, task_class: str = "ambiguity") -> dict:
    response = corpus / "tasks" / "fixture" / "architect-response.md"
    response.write_text(
        "decision_id: event-contract\nUse the canonical event contract.\n",
        encoding="utf-8",
    )
    ambiguity = None
    if task_class == "ambiguity":
        ambiguity = {
            "decision_id": "event-contract",
            "affected_requirements": ["required-behavior"],
            "architect_response": "tasks/fixture/architect-response.md",
        }
    return {
        "class": task_class,
        "required_behaviors": [
            {
                "id": "required-behavior",
                "points": 40,
                "check_ids": ["behavior"],
            }
        ],
        "forbidden_behaviors": [
            {
                "id": "scope-creep",
                "severity": "hard",
                "deduction": 35,
                "check_ids": ["behavior"],
            }
        ],
        "ambiguity": ambiguity,
        "claim_check_ids": ["behavior"],
    }


def test_predictability_contract_and_gold_patch(root: Path) -> None:
    repo = root / "repo-predictability"
    base, gold = create_repository(repo)
    corpus = root / "corpus-predictability"
    manifest = create_corpus(corpus, base, gold)
    manifest["tasks"][0]["predictability"] = predictability_contract(corpus)
    patch_path = corpus / "tasks" / "fixture" / "gold" / "fixture.patch"
    patch_path.parent.mkdir(parents=True)
    patch_path.write_text(run(["git", "diff", base, gold], repo) + "\n", encoding="utf-8")
    manifest["tasks"][0]["revisions"]["fixture"] = {
        "base": base,
        "gold_patch": "tasks/fixture/gold/fixture.patch",
    }
    write_json(corpus / "manifest.json", manifest)

    loaded = load_corpus(corpus, {"FIXTURE_EVAL_REPO": str(repo)})
    task = loaded.tasks[0]
    if task["predictability"]["class"] != "ambiguity":
        raise AssertionError("predictability contract was not normalized")
    if task["revisions"]["fixture"].get("gold_patch") != "tasks/fixture/gold/fixture.patch":
        raise AssertionError("gold patch source was not normalized")
    first_fingerprint = corpus_fingerprint(loaded)
    (corpus / "tasks" / "fixture" / "architect-response.md").write_text(
        "decision_id: event-contract\nUse the revised canonical contract.\n",
        encoding="utf-8",
    )
    if corpus_fingerprint(load_corpus(corpus, {"FIXTURE_EVAL_REPO": str(repo)})) == first_fingerprint:
        raise AssertionError("architect response was omitted from corpus fingerprint")

    manifest["tasks"][0]["predictability"]["required_behaviors"][0]["points"] = 39
    write_json(corpus / "manifest.json", manifest)
    expect_error(
        "predictability points",
        corpus,
        {"FIXTURE_EVAL_REPO": str(repo)},
        "must total 40",
    )

    manifest["tasks"][0]["predictability"]["required_behaviors"][0]["points"] = 40
    manifest["tasks"][0]["predictability"]["claim_check_ids"] = ["missing-check"]
    write_json(corpus / "manifest.json", manifest)
    expect_error(
        "predictability unknown check",
        corpus,
        {"FIXTURE_EVAL_REPO": str(repo)},
        "claim checks are unknown",
    )


def test_gold_patch_must_apply(root: Path) -> None:
    repo = root / "repo-invalid-gold-patch"
    base, gold = create_repository(repo)
    corpus = root / "corpus-invalid-gold-patch"
    manifest = create_corpus(corpus, base, gold)
    patch = corpus / "tasks" / "fixture" / "gold.patch"
    patch.write_text(
        "diff --git a/src/missing.txt b/src/missing.txt\n"
        "--- a/src/missing.txt\n"
        "+++ b/src/missing.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    manifest["tasks"][0]["revisions"]["fixture"] = {
        "base": base,
        "gold_patch": "tasks/fixture/gold.patch",
    }
    write_json(corpus / "manifest.json", manifest)
    expect_error(
        "gold patch apply",
        corpus,
        {"FIXTURE_EVAL_REPO": str(repo)},
        "does not apply to base",
    )


def test_git_reads_ignore_host_state_and_replacement_refs(root: Path) -> None:
    repo = root / "repo-git-isolation"
    base, gold = create_repository(repo)
    run(["git", "replace", gold, base], repo)
    corpus = root / "corpus-git-isolation"
    create_corpus(corpus, base, gold)
    hostile = {
        "GIT_DIR": str(root / "wrong-git-dir"),
        "GIT_INDEX_FILE": str(root / "wrong-index"),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.pager",
        "GIT_CONFIG_VALUE_0": "false",
    }
    previous = {key: os.environ.get(key) for key in hostile}
    os.environ.update(hostile)
    try:
        load_corpus(corpus, {"FIXTURE_EVAL_REPO": str(repo)})
        blob = read_git_blob(repo, gold, "src/value.txt")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    if blob.returncode or blob.stdout != b"gold\n":
        raise AssertionError("pinned Git blob read inherited host state or replacement refs")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="model-eval-manifest-") as raw_root:
        root = Path(raw_root)
        test_valid_corpus(root)
        test_rejects_unsafe_or_inconsistent_input(root)
        test_rejects_lfs_pointer(root)
        test_predictability_contract_and_gold_patch(root)
        test_gold_patch_must_apply(root)
        test_git_reads_ignore_host_state_and_replacement_refs(root)
    print("PASS model eval manifest fixture tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
