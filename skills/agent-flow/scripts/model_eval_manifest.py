#!/usr/bin/env python3
"""Load and validate reproducible Agent Flow model-evaluation corpora."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from task_facts import TaskFactsError, normalize_task_facts


MANIFEST_SCHEMA_VERSION = 1
EVALUATOR_SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_CRITICALITY = {"normal", "critical"}
ALLOWED_REPEAT_POLICIES = {"paired-adaptive"}
ALLOWED_PREDICTABILITY_CLASSES = {"exact-spec", "ambiguity", "scope-trap", "contract"}
ALLOWED_DEVIATION_SEVERITIES = {"hard", "soft"}
EVALUATOR_OUTPUT_ROOT = "agent_flow_eval_output"
GIT_EXECUTABLE = shutil.which("git")
GIT_TIMEOUT_SECONDS = 30
GIT_ENV_ALLOWLIST = (
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TMPDIR",
    "TEMP",
    "TMP",
)


class ManifestError(ValueError):
    """Raised when an eval corpus cannot be executed safely or reproducibly."""


@dataclass(frozen=True)
class CorpusManifest:
    root: Path
    data: dict[str, Any]
    repositories: dict[str, Path]

    @property
    def tasks(self) -> list[dict[str, Any]]:
        return self.data["tasks"]


def corpus_fingerprint(corpus: CorpusManifest) -> str:
    """Hash every portable corpus file that can affect a model or evaluator run."""
    corpus_root = corpus.root.expanduser().resolve()
    relative_paths = {"manifest.json"}
    for task in corpus.tasks:
        relative_paths.add(task["prompt"])
        relative_paths.add(task["evaluator"])
        predictability = task.get("predictability")
        if predictability and predictability["ambiguity"] is not None:
            relative_paths.add(predictability["ambiguity"]["architect_response"])
        for revision in task["revisions"].values():
            gold_patch = revision.get("gold_patch")
            if gold_patch is not None:
                relative_paths.add(gold_patch)
        evaluator_path = corpus_root / task["evaluator"]
        evaluator = _load_json(evaluator_path, f"evaluator for {task['id']}")
        for injection in evaluator.get("injections", []):
            source = injection.get("source")
            if source is None:
                continue
            source_path = (evaluator_path.parent / source).resolve()
            relative_paths.add(source_path.relative_to(corpus_root).as_posix())

    digest = hashlib.sha256()
    for relative_path in sorted(relative_paths):
        path = (corpus_root / relative_path).resolve()
        try:
            path.relative_to(corpus_root)
        except ValueError as exc:
            raise ManifestError(f"fingerprinted corpus path escapes root: {relative_path}") from exc
        payload = path.read_bytes()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON in {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a JSON object: {path}")
    return value


def _strict_fields(data: dict[str, Any], required: set[str], optional: set[str], label: str) -> None:
    missing = sorted(required - data.keys())
    if missing:
        raise ManifestError(f"{label} missing fields: {', '.join(missing)}")
    unknown = sorted(set(data) - required - optional)
    if unknown:
        raise ManifestError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ManifestError(f"{label} must be a lowercase id")
    return value


def _string_array(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{label} must be a non-empty array")
    if not all(isinstance(item, str) and item for item in value):
        raise ManifestError(f"{label} must contain non-empty strings")
    if len(value) != len(set(value)):
        raise ManifestError(f"{label} must not contain duplicates")
    return list(value)


def _portable_relative(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ManifestError(f"{label} must be a portable relative path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ManifestError(f"{label} must be a portable relative path")
    return value


def _corpus_file(root: Path, value: object, label: str) -> Path:
    relative = _portable_relative(value, label)
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ManifestError(f"{label} escapes corpus root") from exc
    if not path.is_file():
        raise ManifestError(f"{label} does not exist: {relative}")
    return path


def _git_environment() -> dict[str, str]:
    if GIT_EXECUTABLE is None:
        raise ManifestError("git executable was not found")
    environment = {
        key: os.environ[key] for key in GIT_ENV_ALLOWLIST if key in os.environ
    }
    environment.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C",
            "LC_ALL": "C",
            "PAGER": "",
            "PATH": os.pathsep.join(
                dict.fromkeys(
                    [
                        str(Path(GIT_EXECUTABLE).resolve().parent),
                        "/usr/bin",
                        "/bin",
                        "/usr/sbin",
                        "/sbin",
                    ]
                )
            ),
        }
    )
    return environment


def _git_command(repo: Path, args: list[str]) -> list[str]:
    if GIT_EXECUTABLE is None:
        raise ManifestError("git executable was not found")
    return [GIT_EXECUTABLE, "--no-pager", "-C", str(repo), *args]


def _run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            _git_command(repo, args),
            env=_git_environment(),
            text=True,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ManifestError("git repository inspection timed out") from exc


def read_git_blob(repo: Path, revision: str, path: str) -> subprocess.CompletedProcess[bytes]:
    """Read a pinned blob without inheriting host Git state or replacement refs."""
    try:
        return subprocess.run(
            _git_command(repo, ["show", f"{revision}:{path}"]),
            env=_git_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ManifestError("git blob read timed out") from exc


def _verify_repository(path: Path, repository_id: str) -> Path:
    if not path.is_dir():
        raise ManifestError(f"repository {repository_id} does not exist: {path}")
    result = _run_git(path, ["rev-parse", "--show-toplevel"])
    if result.returncode:
        raise ManifestError(f"repository {repository_id} is not a git checkout")
    root = Path(result.stdout.strip()).resolve()
    if root != path.resolve():
        raise ManifestError(f"repository {repository_id} path must point to its git root")
    return root


def _verify_revision(repo: Path, revision: object, label: str) -> str:
    if not isinstance(revision, str) or not SHA_PATTERN.fullmatch(revision):
        raise ManifestError(f"{label} must be a full lowercase commit SHA")
    result = _run_git(repo, ["cat-file", "-e", f"{revision}^{{commit}}"])
    if result.returncode:
        raise ManifestError(f"{label} is not a commit in {repo}")
    return revision


def _tree_risks(repo: Path, revision: str, label: str) -> None:
    tree = _run_git(repo, ["ls-tree", "-r", revision])
    if tree.returncode:
        raise ManifestError(f"cannot inspect tree for {label}")
    if any(line.startswith("160000 ") for line in tree.stdout.splitlines()):
        raise ManifestError(f"{label} contains git submodules")

    lfs = _run_git(
        repo,
        ["grep", "-I", "-l", "-e", "version https://git-lfs.github.com/spec/v1", revision, "--", "."],
    )
    if lfs.returncode == 0:
        raise ManifestError(f"{label} contains Git LFS pointers")
    if lfs.returncode not in {0, 1}:
        raise ManifestError(f"cannot inspect Git LFS pointers for {label}")


def _validate_command(command: object, label: str) -> list[str]:
    if not isinstance(command, list) or not command:
        raise ManifestError(f"{label} must be a non-empty argv array")
    if not all(isinstance(item, str) and item for item in command):
        raise ManifestError(f"{label} must contain non-empty strings")
    for argument in command:
        if argument.startswith("/") or "\\" in argument or ".." in PurePosixPath(argument).parts:
            raise ManifestError(f"{label} contains a non-portable argument: {argument}")
    return list(command)


def _validate_evaluator(
    path: Path,
    task_id: str,
    repository_ids: set[str],
    revisions: dict[str, dict[str, Any]],
    resolved_repositories: dict[str, Path],
    verify_repositories: bool,
) -> set[str]:
    data = _load_json(path, f"evaluator for {task_id}")
    _strict_fields(
        data,
        {"schema_version", "commands", "positive_checks", "negative_checks"},
        {"setup_commands", "injections"},
        f"evaluator {task_id}",
    )
    if data["schema_version"] != EVALUATOR_SCHEMA_VERSION:
        raise ManifestError(f"evaluator {task_id} has unsupported schema_version")
    commands = data["commands"]
    if not isinstance(commands, list) or not commands:
        raise ManifestError(f"evaluator {task_id} commands must be a non-empty array")
    command_ids: set[str] = set()
    for index, command in enumerate(commands):
        _validate_evaluator_command(
            command,
            f"evaluator {task_id} command {index}",
            repository_ids,
            require_scope=True,
        )
        command_id = command["id"]
        if command_id in command_ids:
            raise ManifestError(f"evaluator {task_id} command ids must be unique")
        command_ids.add(command_id)
    for index, command in enumerate(data.get("setup_commands", [])):
        _validate_evaluator_command(
            command,
            f"evaluator {task_id} setup command {index}",
            repository_ids,
            require_scope=False,
        )
    injections = data.get("injections", [])
    if not isinstance(injections, list):
        raise ManifestError(f"evaluator {task_id} injections must be an array")
    injection_targets: set[tuple[str, str]] = set()
    for index, injection in enumerate(injections):
        if not isinstance(injection, dict):
            raise ManifestError(f"evaluator {task_id} injection {index} must be an object")
        _strict_fields(
            injection,
            {"repository_id", "target"},
            {"source", "gold_path", "replace", "replace_or_create"},
            f"evaluator {task_id} injection {index}",
        )
        repository_id = injection["repository_id"]
        if repository_id not in repository_ids:
            raise ManifestError(f"evaluator {task_id} injection {index} uses unknown repository")
        target = _portable_relative(injection["target"], f"evaluator {task_id} injection {index} target")
        if PurePosixPath(target).parts[0] == EVALUATOR_OUTPUT_ROOT:
            raise ManifestError(
                f"evaluator {task_id} injection {index} uses the reserved evaluator output root"
            )
        source = injection.get("source")
        gold_path = injection.get("gold_path")
        if (source is None) == (gold_path is None):
            raise ManifestError(
                f"evaluator {task_id} injection {index} requires exactly one of source or gold_path"
            )
        if source is not None:
            source = _portable_relative(source, f"evaluator {task_id} injection {index} source")
            source_path = (path.parent / source).resolve()
            try:
                source_path.relative_to(path.parent.resolve())
            except ValueError as exc:
                raise ManifestError(f"evaluator {task_id} injection {index} source escapes evaluator") from exc
            if not source_path.is_file():
                raise ManifestError(f"evaluator {task_id} injection {index} source does not exist")
        else:
            gold_path = _portable_relative(
                gold_path,
                f"evaluator {task_id} injection {index} gold_path",
            )
            if ":" in gold_path:
                raise ManifestError(f"evaluator {task_id} injection {index} gold_path contains ':'")
            if verify_repositories:
                repo = resolved_repositories[repository_id]
                gold = revisions[repository_id].get("gold")
                if gold is None:
                    raise ManifestError(
                        f"evaluator {task_id} injection {index} gold_path requires a gold revision"
                    )
                result = _run_git(repo, ["cat-file", "-t", f"{gold}:{gold_path}"])
                if result.returncode or result.stdout.strip() != "blob":
                    raise ManifestError(f"evaluator {task_id} injection {index} gold_path is not a blob")
        replace = injection.get("replace", False)
        if not isinstance(replace, bool):
            raise ManifestError(f"evaluator {task_id} injection {index} replace must be a boolean")
        replace_or_create = injection.get("replace_or_create", False)
        if not isinstance(replace_or_create, bool):
            raise ManifestError(
                f"evaluator {task_id} injection {index} replace_or_create must be a boolean"
            )
        if replace and replace_or_create:
            raise ManifestError(
                f"evaluator {task_id} injection {index} cannot enable both replacement modes"
            )
        target_key = (repository_id, target)
        if target_key in injection_targets:
            raise ManifestError(f"evaluator {task_id} injection targets must be unique")
        injection_targets.add(target_key)
    for index, command in enumerate(commands):
        repository_id = command["repository_id"]
        for write_path in command.get("write_paths", []):
            write_parts = PurePosixPath(write_path).parts
            for injected_repository, injected_path in injection_targets:
                injected_parts = PurePosixPath(injected_path).parts
                if (
                    injected_repository == repository_id
                    and injected_parts[: len(write_parts)] == write_parts
                ):
                    raise ManifestError(
                        f"evaluator {task_id} command {index} write path overlaps an injection"
                    )
    _string_array(data["positive_checks"], f"evaluator {task_id} positive_checks")
    _string_array(data["negative_checks"], f"evaluator {task_id} negative_checks")
    return command_ids


def _validate_evaluator_command(
    command: object,
    label: str,
    repository_ids: set[str],
    require_scope: bool,
) -> None:
    if not isinstance(command, dict):
        raise ManifestError(f"{label} must be an object")
    required = {"id", "repository_id", "argv"}
    if require_scope:
        required.add("scope")
    _strict_fields(command, required, {"write_paths"} if require_scope else set(), label)
    _identifier(command["id"], f"{label} id")
    if command["repository_id"] not in repository_ids:
        raise ManifestError(f"{label} uses unknown repository")
    _validate_command(command["argv"], f"{label} argv")
    if "write_paths" in command:
        write_paths = _string_array(command["write_paths"], f"{label} write_paths")
        for index, write_path in enumerate(write_paths):
            relative = _portable_relative(write_path, f"{label} write path {index}")
            parts = PurePosixPath(relative).parts
            if len(parts) < 2 or parts[0] != EVALUATOR_OUTPUT_ROOT:
                raise ManifestError(
                    f"{label} write path {index} must be under {EVALUATOR_OUTPUT_ROOT}/"
                )
    if require_scope and command["scope"] not in {"lane", "integration"}:
        raise ManifestError(f"{label} scope must be lane or integration")


def _known_roles() -> set[str]:
    agents_dir = Path(__file__).resolve().parents[1] / "agents"
    return {path.stem for path in agents_dir.glob("*.md")}


def _resolve_repositories(
    repositories: object,
    environment: dict[str, str],
    verify_repositories: bool,
) -> tuple[dict[str, dict[str, str]], dict[str, Path]]:
    if not isinstance(repositories, dict) or not repositories:
        raise ManifestError("repositories must be a non-empty object")
    normalized: dict[str, dict[str, str]] = {}
    resolved: dict[str, Path] = {}
    for raw_id, raw_config in repositories.items():
        repository_id = _identifier(raw_id, "repository id")
        if not isinstance(raw_config, dict):
            raise ManifestError(f"repository {repository_id} config must be an object")
        _strict_fields(raw_config, {"path_env"}, set(), f"repository {repository_id}")
        path_env = raw_config["path_env"]
        if not isinstance(path_env, str) or not ENV_PATTERN.fullmatch(path_env):
            raise ManifestError(f"repository {repository_id} path_env is invalid")
        normalized[repository_id] = {"path_env": path_env}
        if verify_repositories:
            raw_path = environment.get(path_env)
            if not raw_path:
                raise ManifestError(f"repository {repository_id} requires environment variable {path_env}")
            resolved[repository_id] = _verify_repository(Path(raw_path).expanduser().resolve(), repository_id)
    return normalized, resolved


def _validate_paths_by_repository(
    value: object,
    repository_ids: set[str],
    label: str,
    allow_empty: bool,
) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be an object")
    if set(value) != repository_ids:
        raise ManifestError(f"{label} must contain exactly the task repository ids")
    normalized: dict[str, list[str]] = {}
    for repository_id, raw_paths in value.items():
        if not isinstance(raw_paths, list) or (not raw_paths and not allow_empty):
            raise ManifestError(f"{label}.{repository_id} must be an array")
        paths = [_portable_relative(path, f"{label}.{repository_id}") for path in raw_paths]
        if len(paths) != len(set(paths)):
            raise ManifestError(f"{label}.{repository_id} must not contain duplicates")
        normalized[repository_id] = paths
    return normalized


def _verify_gold_patch(repo: Path, base: str, patch: Path, label: str) -> None:
    if not patch.read_bytes():
        raise ManifestError(f"{label} must be non-empty")
    with tempfile.TemporaryDirectory(prefix="agent-flow-gold-patch-") as raw_temp:
        index = Path(raw_temp) / "index"
        environment = _git_environment()
        environment["GIT_INDEX_FILE"] = str(index)
        read_tree = subprocess.run(
            _git_command(repo, ["read-tree", base]),
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if read_tree.returncode:
            raise ManifestError(f"cannot prepare {label} validation index")
        apply = subprocess.run(
            _git_command(repo, ["apply", "--cached", "--check", str(patch)]),
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if apply.returncode:
            detail = (apply.stderr or apply.stdout).strip()
            raise ManifestError(f"{label} does not apply to base: {detail}")


def _validate_predictability(
    root: Path,
    task_id: str,
    value: object,
    evaluator_check_ids: set[str],
    lane_count: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"task {task_id} predictability must be an object")
    _strict_fields(
        value,
        {
            "class",
            "required_behaviors",
            "forbidden_behaviors",
            "ambiguity",
            "claim_check_ids",
        },
        set(),
        f"task {task_id} predictability",
    )
    task_class = value["class"]
    if task_class not in ALLOWED_PREDICTABILITY_CLASSES:
        raise ManifestError(f"task {task_id} predictability class is invalid")
    if lane_count != 1:
        raise ManifestError(f"task {task_id} predictability v1 requires exactly one lane")

    raw_required = value["required_behaviors"]
    if not isinstance(raw_required, list) or not raw_required:
        raise ManifestError(f"task {task_id} required_behaviors must be a non-empty array")
    required: list[dict[str, Any]] = []
    required_ids: set[str] = set()
    required_points = 0
    for index, item in enumerate(raw_required):
        label = f"task {task_id} required behavior {index}"
        if not isinstance(item, dict):
            raise ManifestError(f"{label} must be an object")
        _strict_fields(item, {"id", "points", "check_ids"}, set(), label)
        behavior_id = _identifier(item["id"], f"{label} id")
        if behavior_id in required_ids:
            raise ManifestError(f"task {task_id} required behavior ids must be unique")
        points = item["points"]
        if isinstance(points, bool) or not isinstance(points, int) or points < 1:
            raise ManifestError(f"{label} points must be a positive integer")
        check_ids = _string_array(item["check_ids"], f"{label} check_ids")
        unknown = sorted(set(check_ids) - evaluator_check_ids)
        if unknown:
            raise ManifestError(f"{label} uses unknown evaluator checks: {', '.join(unknown)}")
        required_ids.add(behavior_id)
        required_points += points
        required.append({"id": behavior_id, "points": points, "check_ids": check_ids})
    if required_points != 40:
        raise ManifestError(f"task {task_id} required behavior points must total 40")

    raw_forbidden = value["forbidden_behaviors"]
    if not isinstance(raw_forbidden, list) or not raw_forbidden:
        raise ManifestError(f"task {task_id} forbidden_behaviors must be a non-empty array")
    forbidden: list[dict[str, Any]] = []
    forbidden_ids: set[str] = set()
    deductions = 0
    for index, item in enumerate(raw_forbidden):
        label = f"task {task_id} forbidden behavior {index}"
        if not isinstance(item, dict):
            raise ManifestError(f"{label} must be an object")
        _strict_fields(item, {"id", "severity", "deduction", "check_ids"}, set(), label)
        behavior_id = _identifier(item["id"], f"{label} id")
        if behavior_id in forbidden_ids or behavior_id in required_ids:
            raise ManifestError(f"task {task_id} behavior ids must be unique")
        severity = item["severity"]
        if severity not in ALLOWED_DEVIATION_SEVERITIES:
            raise ManifestError(f"{label} severity is invalid")
        deduction = item["deduction"]
        if isinstance(deduction, bool) or not isinstance(deduction, int) or deduction < 0:
            raise ManifestError(f"{label} deduction must be a non-negative integer")
        check_ids = _string_array(item["check_ids"], f"{label} check_ids")
        unknown = sorted(set(check_ids) - evaluator_check_ids)
        if unknown:
            raise ManifestError(f"{label} uses unknown evaluator checks: {', '.join(unknown)}")
        forbidden_ids.add(behavior_id)
        deductions += deduction
        forbidden.append(
            {
                "id": behavior_id,
                "severity": severity,
                "deduction": deduction,
                "check_ids": check_ids,
            }
        )
    if deductions > 35:
        raise ManifestError(f"task {task_id} forbidden deductions cannot exceed 35")

    claim_check_ids = _string_array(
        value["claim_check_ids"], f"task {task_id} predictability claim_check_ids"
    )
    unknown_claims = sorted(set(claim_check_ids) - evaluator_check_ids)
    if unknown_claims:
        raise ManifestError(
            f"task {task_id} claim checks are unknown: {', '.join(unknown_claims)}"
        )

    raw_ambiguity = value["ambiguity"]
    ambiguity: dict[str, Any] | None = None
    if task_class == "ambiguity":
        if not isinstance(raw_ambiguity, dict):
            raise ManifestError(f"task {task_id} ambiguity contract is required")
        _strict_fields(
            raw_ambiguity,
            {"decision_id", "affected_requirements", "architect_response"},
            set(),
            f"task {task_id} ambiguity",
        )
        decision_id = _identifier(
            raw_ambiguity["decision_id"], f"task {task_id} ambiguity decision_id"
        )
        affected = _string_array(
            raw_ambiguity["affected_requirements"],
            f"task {task_id} ambiguity affected_requirements",
        )
        unknown_requirements = sorted(set(affected) - required_ids)
        if unknown_requirements:
            raise ManifestError(
                f"task {task_id} ambiguity uses unknown requirements: "
                + ", ".join(unknown_requirements)
            )
        response = _corpus_file(
            root,
            raw_ambiguity["architect_response"],
            f"task {task_id} architect_response",
        )
        if decision_id not in response.read_text(encoding="utf-8"):
            raise ManifestError(
                f"task {task_id} architect_response must contain decision_id {decision_id}"
            )
        ambiguity = {
            "decision_id": decision_id,
            "affected_requirements": affected,
            "architect_response": response.relative_to(root).as_posix(),
        }
    elif raw_ambiguity is not None:
        raise ManifestError(f"task {task_id} non-ambiguity class requires ambiguity null")

    return {
        "class": task_class,
        "required_behaviors": required,
        "forbidden_behaviors": forbidden,
        "ambiguity": ambiguity,
        "claim_check_ids": claim_check_ids,
    }


def _validate_task(
    root: Path,
    task: object,
    repository_configs: dict[str, dict[str, str]],
    resolved_repositories: dict[str, Path],
    known_roles: set[str],
    verify_repositories: bool,
) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise ManifestError("task entries must be objects")
    _strict_fields(
        task,
        {
            "id",
            "product_id",
            "repositories",
            "revisions",
            "lanes",
            "prompt",
            "evaluator",
            "allowed_paths",
            "forbidden_paths",
            "timeout_seconds",
            "repeat_policy",
            "criticality",
        },
        {"predictability"},
        "task",
    )
    task_id = _identifier(task["id"], "task id")
    product_id = _identifier(task["product_id"], f"task {task_id} product_id")
    repository_ids = set(_string_array(task["repositories"], f"task {task_id} repositories"))
    unknown_repositories = sorted(repository_ids - repository_configs.keys())
    if unknown_repositories:
        raise ManifestError(f"task {task_id} uses unknown repositories: {', '.join(unknown_repositories)}")

    revisions = task["revisions"]
    if not isinstance(revisions, dict) or set(revisions) != repository_ids:
        raise ManifestError(f"task {task_id} revisions must contain exactly its repositories")
    normalized_revisions: dict[str, dict[str, Any]] = {}
    for repository_id, raw_revisions in revisions.items():
        if not isinstance(raw_revisions, dict):
            raise ManifestError(f"task {task_id} revisions.{repository_id} must be an object")
        _strict_fields(
            raw_revisions,
            {"base"},
            {"gold", "gold_patch"},
            f"task {task_id} revisions.{repository_id}",
        )
        if ("gold" in raw_revisions) == ("gold_patch" in raw_revisions):
            raise ManifestError(
                f"task {task_id} revisions.{repository_id} requires exactly one gold source"
            )
        if verify_repositories:
            repo = resolved_repositories[repository_id]
            base = _verify_revision(repo, raw_revisions["base"], f"task {task_id} {repository_id} base")
            gold: str | None = None
            gold_patch: str | None = None
            if "gold" in raw_revisions:
                gold = _verify_revision(
                    repo, raw_revisions["gold"], f"task {task_id} {repository_id} gold"
                )
                if base == gold:
                    raise ManifestError(f"task {task_id} {repository_id} base and gold must differ")
            else:
                patch = _corpus_file(
                    root,
                    raw_revisions["gold_patch"],
                    f"task {task_id} {repository_id} gold_patch",
                )
                _verify_gold_patch(repo, base, patch, f"task {task_id} {repository_id} gold_patch")
                gold_patch = patch.relative_to(root).as_posix()
            _tree_risks(repo, base, f"task {task_id} {repository_id} base")
        else:
            base = _verify_revision_shape(raw_revisions["base"], f"task {task_id} {repository_id} base")
            gold = None
            gold_patch = None
            if "gold" in raw_revisions:
                gold = _verify_revision_shape(
                    raw_revisions["gold"], f"task {task_id} {repository_id} gold"
                )
                if base == gold:
                    raise ManifestError(f"task {task_id} {repository_id} base and gold must differ")
            else:
                patch = _corpus_file(
                    root,
                    raw_revisions["gold_patch"],
                    f"task {task_id} {repository_id} gold_patch",
                )
                if not patch.read_bytes():
                    raise ManifestError(
                        f"task {task_id} {repository_id} gold_patch must be non-empty"
                    )
                gold_patch = patch.relative_to(root).as_posix()
        normalized_revision: dict[str, Any] = {"base": base}
        if gold is not None:
            normalized_revision["gold"] = gold
        else:
            normalized_revision["gold_patch"] = gold_patch
        normalized_revisions[repository_id] = normalized_revision

    lanes = task["lanes"]
    if not isinstance(lanes, list) or not lanes:
        raise ManifestError(f"task {task_id} lanes must be a non-empty array")
    normalized_lanes: list[dict[str, Any]] = []
    lane_ids: set[str] = set()
    for index, lane in enumerate(lanes):
        if not isinstance(lane, dict):
            raise ManifestError(f"task {task_id} lane {index} must be an object")
        _strict_fields(
            lane,
            {"id", "role", "repository_ids", "primary_repository", "task_facts"},
            set(),
            f"task {task_id} lane {index}",
        )
        lane_id = _identifier(lane["id"], f"task {task_id} lane id")
        if lane_id in lane_ids:
            raise ManifestError(f"task {task_id} lane ids must be unique")
        lane_ids.add(lane_id)
        role = _identifier(lane["role"], f"task {task_id} lane {lane_id} role")
        if role not in known_roles:
            raise ManifestError(f"task {task_id} lane {lane_id} uses unknown role: {role}")
        lane_repositories = set(
            _string_array(lane["repository_ids"], f"task {task_id} lane {lane_id} repository_ids")
        )
        if not lane_repositories or not lane_repositories <= repository_ids:
            raise ManifestError(f"task {task_id} lane {lane_id} repository_ids are outside task scope")
        primary_repository = lane["primary_repository"]
        if primary_repository not in lane_repositories:
            raise ManifestError(f"task {task_id} lane {lane_id} primary_repository is outside lane scope")
        try:
            task_facts = normalize_task_facts(lane["task_facts"], known_roles)
        except TaskFactsError as exc:
            raise ManifestError(f"task {task_id} lane {lane_id}: {exc}") from exc
        if task_facts["role"] != role:
            raise ManifestError(f"task {task_id} lane {lane_id} role does not match Task Facts")
        if task_facts["repo_count"] != len(repository_ids):
            raise ManifestError(f"task {task_id} lane {lane_id} repo_count does not match task repositories")
        normalized_lanes.append(
            {
                "id": lane_id,
                "role": role,
                "repository_ids": sorted(lane_repositories),
                "primary_repository": primary_repository,
                "task_facts": task_facts,
            }
        )

    prompt = _corpus_file(root, task["prompt"], f"task {task_id} prompt")
    evaluator = _corpus_file(root, task["evaluator"], f"task {task_id} evaluator")
    evaluator_check_ids = _validate_evaluator(
        evaluator,
        task_id,
        repository_ids,
        normalized_revisions,
        resolved_repositories,
        verify_repositories,
    )
    allowed_paths = _validate_paths_by_repository(
        task["allowed_paths"], repository_ids, f"task {task_id} allowed_paths", allow_empty=False
    )
    forbidden_paths = _validate_paths_by_repository(
        task["forbidden_paths"], repository_ids, f"task {task_id} forbidden_paths", allow_empty=True
    )
    timeout_seconds = task["timeout_seconds"]
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ManifestError(f"task {task_id} timeout_seconds must be a positive integer")
    if task["repeat_policy"] not in ALLOWED_REPEAT_POLICIES:
        raise ManifestError(f"task {task_id} repeat_policy is invalid")
    if task["criticality"] not in ALLOWED_CRITICALITY:
        raise ManifestError(f"task {task_id} criticality is invalid")
    predictability = None
    if "predictability" in task:
        predictability = _validate_predictability(
            root,
            task_id,
            task["predictability"],
            evaluator_check_ids,
            len(normalized_lanes),
        )

    return {
        "id": task_id,
        "product_id": product_id,
        "repositories": sorted(repository_ids),
        "revisions": normalized_revisions,
        "lanes": normalized_lanes,
        "prompt": prompt.relative_to(root).as_posix(),
        "evaluator": evaluator.relative_to(root).as_posix(),
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "timeout_seconds": timeout_seconds,
        "repeat_policy": task["repeat_policy"],
        "criticality": task["criticality"],
        "predictability": predictability,
    }


def _verify_revision_shape(revision: object, label: str) -> str:
    if not isinstance(revision, str) or not SHA_PATTERN.fullmatch(revision):
        raise ManifestError(f"{label} must be a full lowercase commit SHA")
    return revision


def load_corpus(
    root: Path,
    environment: dict[str, str] | None = None,
    verify_repositories: bool = True,
) -> CorpusManifest:
    """Load a corpus and reject unsafe, non-reproducible, or inconsistent input."""
    corpus_root = root.expanduser().resolve()
    data = _load_json(corpus_root / "manifest.json", "corpus manifest")
    _strict_fields(data, {"schema_version", "corpus_id", "repositories", "tasks"}, set(), "manifest")
    if data["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ManifestError("manifest has unsupported schema_version")
    corpus_id = _identifier(data["corpus_id"], "corpus_id")
    repository_configs, repositories = _resolve_repositories(
        data["repositories"], environment or dict(os.environ), verify_repositories
    )
    tasks = data["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise ManifestError("tasks must be a non-empty array")
    known_roles = _known_roles()
    normalized_tasks = [
        _validate_task(
            corpus_root,
            task,
            repository_configs,
            repositories,
            known_roles,
            verify_repositories,
        )
        for task in tasks
    ]
    task_ids = [task["id"] for task in normalized_tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ManifestError("task ids must be unique")
    normalized = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "corpus_id": corpus_id,
        "repositories": repository_configs,
        "tasks": normalized_tasks,
    }
    return CorpusManifest(root=corpus_root, data=normalized, repositories=repositories)
