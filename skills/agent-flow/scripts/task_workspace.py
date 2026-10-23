from pathlib import Path

from journal_io import JournalError
from workspace_scope import canonical_path, path_allowed, selected_roots


WorkspaceError = JournalError


def validate_inputs(scope):
    for path in scope.get("memory", []):
        if not Path(path).is_file() or Path(path).is_symlink():
            raise WorkspaceError(f"memory input must be an existing regular file: {path}")
    for path in scope.get("context", []):
        if not Path(path).exists() and not Path(path).is_symlink():
            raise WorkspaceError(f"context input does not exist: {path}")


def bind_workspace(source_root, roots=None, context=None, run_dir=None, memory=None):
    source = Path(source_root).resolve()
    if not source.is_dir() or not (source / ".git").exists():
        raise WorkspaceError("source_root must be a Git checkout root")
    scope = {"source_root": str(source), "roots": selected_roots(source, roots),
             "context": sorted({str(canonical_path(path)) for path in context or []}),
             "memory": sorted({str(canonical_path(path)) for path in memory or []})}
    validate_inputs(scope)
    return scope


def expand_workspace(scope, roots=None, context=None, run_dir=None):
    merged = {root["path"]: dict(root) for root in scope["roots"]}
    for value in roots or []:
        root = selected_roots(value["path"], [value])[0]
        previous = merged.get(root["path"])
        if previous is not None:
            if "include" in previous and "include" in root:
                root = {"path": root["path"], "include": sorted(set(previous["include"]) | set(root["include"]))}
            elif "include" not in previous:
                root = previous
        merged[root["path"]] = root
    expanded = {"source_root": scope["source_root"], "roots": sorted(merged.values(), key=lambda root: root["path"]),
                "context": sorted(set(scope.get("context", [])) | {str(canonical_path(path)) for path in context or []}),
                "memory": list(scope.get("memory", []))}
    validate_inputs(expanded)
    return expanded
