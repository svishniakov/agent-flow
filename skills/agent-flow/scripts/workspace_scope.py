import os
from pathlib import Path

from journal_io import JournalError


def canonical_path(path):
    path = Path(os.path.abspath(path))
    return path.parent.resolve() / path.name


def selected_roots(source_root, roots=None):
    source = canonical_path(source_root)
    values = list(roots or [])
    if not any(canonical_path(value["path"]) == source for value in values):
        values.insert(0, {"path": str(source)})
    result = {}
    for value in values:
        if not isinstance(value, dict) or set(value) - {"path", "include"}:
            raise JournalError("root requires path and optional include")
        path = canonical_path(value["path"])
        if not path.exists() and not path.is_symlink():
            raise JournalError(f"selected root does not exist: {path}")
        entry = {"path": str(path)}
        if "include" in value:
            includes = value["include"]
            if not isinstance(includes, list) or not includes:
                raise JournalError("include must be a nonempty list of relative paths")
            for name in includes:
                if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
                    raise JournalError(f"invalid included path: {name!r}")
            entry["include"] = sorted(set(includes))
        key = str(path)
        if key in result and result[key] != entry:
            raise JournalError(f"conflicting selected root: {path}")
        result[key] = entry
    return sorted(result.values(), key=lambda item: item["path"])


def path_allowed(scope, absolute):
    path = Path(os.path.abspath(absolute))
    for root in scope["roots"]:
        base = Path(root["path"])
        if path != base and not path.is_relative_to(base):
            continue
        if path != base and not path.parent.resolve().is_relative_to(base):
            continue
        relative = path.relative_to(base)
        if ".git" in relative.parts:
            return False
        if (base / ".git").exists() or "include" not in root or any(relative == Path(name) or relative.is_relative_to(name)
                                         for name in root["include"]):
            return True
    return False
