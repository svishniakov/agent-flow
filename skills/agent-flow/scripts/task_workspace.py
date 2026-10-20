import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from journal_io import JournalError
from workspace_scope import canonical_path, path_allowed, selected_roots


WorkspaceError = JournalError
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if result.returncode:
        raise WorkspaceError(result.stderr.decode(errors="replace").strip() or "Git command failed")
    return result.stdout


def is_git_root(path):
    return path.is_dir() and (path / ".git").exists()


def stable(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def directory_descriptor(path):
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in Path(path).parts[1:]:
            child = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def observation(root, name):
    root = Path(root)
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise WorkspaceError(f"unsafe inventory path: {name}")
    base = root if root.is_dir() and not root.is_symlink() else root.parent
    parts = relative.parts if base == root else (root.name,)
    descriptors = [directory_descriptor(base)]
    try:
        for part in parts[:-1]:
            descriptors.append(os.open(part, DIRECTORY_FLAGS, dir_fd=descriptors[-1]))
        leaf = parts[-1] if parts else "."
        before = os.stat(leaf, dir_fd=descriptors[-1], follow_symlinks=False)
        entry = {"root": str(root), "path": relative.as_posix()}
        if stat.S_ISLNK(before.st_mode):
            entry.update(type="symlink", target=os.readlink(leaf, dir_fd=descriptors[-1]))
        elif stat.S_ISDIR(before.st_mode):
            entry.update(type="directory")
        elif stat.S_ISREG(before.st_mode):
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=descriptors[-1])
            try:
                if stable(os.fstat(fd)) != stable(before):
                    raise WorkspaceError(f"file changed before reading: {root / relative}")
                checksum = hashlib.sha256()
                while chunk := os.read(fd, 1024 * 1024):
                    checksum.update(chunk)
                if stable(os.fstat(fd)) != stable(before):
                    raise WorkspaceError(f"file changed while reading: {root / relative}")
                entry.update(type="file", sha256=checksum.hexdigest(), mode=stat.S_IMODE(before.st_mode))
            finally:
                os.close(fd)
        else:
            raise WorkspaceError(f"unsupported input type: {root / relative}")
        if stable(os.stat(leaf, dir_fd=descriptors[-1], follow_symlinks=False)) != stable(before):
            raise WorkspaceError(f"input changed during inventory: {root / relative}")
        return entry
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def excluded(path, roots, run_dir):
    return ".git" in path.parts or any(path == other or path.is_relative_to(other) for other in roots) or (
        run_dir is not None and (path == run_dir or path.is_relative_to(run_dir)))


def walk_names(root, start, nested, run_dir):
    path = root / start
    if excluded(path, nested, run_dir) or not os.path.lexists(path):
        return []
    if not path.is_symlink() and path.is_dir():
        if start != Path(".") and (path / ".git").exists():
            raise WorkspaceError(f"nested Git root must be explicitly selected: {path}")
        result = []
        fd = directory_descriptor(path)
        try:
            before = os.fstat(fd)
            with os.scandir(fd) as entries:
                children = sorted(entry.name for entry in entries)
            after = path.lstat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise WorkspaceError(f"directory changed during inventory: {path}")
        finally:
            os.close(fd)
        for child in children:
            result.extend(walk_names(root, start / child, nested, run_dir))
        return result
    return [start.as_posix()]


def root_inventory(root, all_roots, run_dir, excluded_files=()):
    base = Path(root["path"])
    nested = [Path(other["path"]) for other in all_roots
              if other["path"] != str(base) and Path(other["path"]).is_relative_to(base)]
    if base.is_symlink() or not base.is_dir():
        names = ["."] if os.path.lexists(base) else []
    else:
        names = ([os.fsdecode(name) for name in git(base, "ls-files", "--cached", "--others",
                                                    "--exclude-standard", "-z").split(b"\0") if name]
                 if is_git_root(base) else [])
        if "include" not in root and not is_git_root(base):
            names = walk_names(base, Path("."), nested, run_dir)
        for name in root.get("include", []):
            if not path_allowed({"roots": [root]}, base / name):
                raise WorkspaceError(f"included path traverses a symbolic parent: {base / name}")
            names.extend(walk_names(base, Path(name), nested, run_dir))
    result = []
    for name in sorted(set(names)):
        path = base / name
        if str(path) in excluded_files or excluded(path, nested, run_dir) or not os.path.lexists(path):
            continue
        if path != base and path.is_dir() and not path.is_symlink() and (path / ".git").exists():
            raise WorkspaceError(f"nested Git root must be explicitly selected: {path}")
        try:
            result.append(observation(base, name))
        except OSError as exc:
            raise WorkspaceError(f"cannot inventory {path}: {exc}") from exc
    return result


def current_inventory(scope, run_dir=None):
    run = canonical_path(run_dir) if run_dir is not None else None
    for path in scope.get("memory", []):
        if not Path(path).is_file() or Path(path).is_symlink():
            raise WorkspaceError(f"memory input must be an existing regular file: {path}")
    entries = [entry for root in scope["roots"]
               for entry in root_inventory(root, scope["roots"], run, scope.get("memory", []))]
    return sorted(entries, key=lambda entry: (entry["root"], entry["path"]))


def bind_workspace(source_root, roots=None, context=None, run_dir=None, memory=None):
    source = Path(source_root).resolve()
    if not is_git_root(source):
        raise WorkspaceError("source_root must be a Git checkout root")
    selected = selected_roots(source, roots)
    scope = {"source_root": str(source), "roots": selected,
             "context": sorted({str(canonical_path(path)) for path in context or []}), "git": {},
             "memory": sorted({str(canonical_path(path)) for path in memory or []})}
    for path in scope["memory"]:
        if not Path(path).is_file() or Path(path).is_symlink():
            raise WorkspaceError(f"memory input must be an existing regular file: {path}")
    for path in scope["context"]:
        if not os.path.lexists(path):
            raise WorkspaceError(f"context input does not exist: {path}")
    for root in selected:
        path = Path(root["path"])
        if is_git_root(path):
            scope["git"][str(path)] = {
                "head": git(path, "rev-parse", "--revs-only", "HEAD").decode().strip() or None,
                "status": git(path, "status", "--porcelain=v1", "-z").decode(errors="surrogateescape")}
    scope["initial"] = current_inventory(scope, run_dir)
    context_roots = [{"path": path} for path in scope["context"]]
    for root in context_roots:
        root_inventory(root, context_roots, None, scope["memory"])
    return scope


def expand_workspace(scope, roots=None, context=None, run_dir=None):
    expanded = json.loads(json.dumps(scope))
    merged = {root["path"]: root for root in scope["roots"]}
    for value in roots or []:
        root = selected_roots(value["path"], [value])[0]
        previous = merged.get(root["path"])
        if previous is not None and "include" in previous and "include" in root:
            root = {"path": root["path"], "include": sorted(set(previous["include"]) | set(root["include"]))}
        elif previous is not None and "include" in previous and is_git_root(Path(root["path"])):
            root = previous
        elif previous is not None and "include" not in previous and not is_git_root(Path(root["path"])):
            root = previous
        merged[root["path"]] = root
    expanded["roots"] = sorted(merged.values(), key=lambda root: root["path"])
    expanded["context"] = sorted(set(scope["context"]) | {str(canonical_path(path)) for path in context or []})
    for path in context or []:
        if not os.path.lexists(canonical_path(path)):
            raise WorkspaceError(f"context input does not exist: {path}")
    old = {}
    for entry in scope["initial"]:
        absolute = Path(entry["root"]) / entry["path"]
        owner = max((root["path"] for root in expanded["roots"]
                     if absolute == Path(root["path"]) or absolute.is_relative_to(root["path"])), key=len)
        moved = {**entry, "root": owner, "path": absolute.relative_to(owner).as_posix()}
        old[(owner, moved["path"])] = moved
    git_names = {root["path"]: {os.fsdecode(name) for name in git(Path(root["path"]), "ls-files", "--cached",
                                                                "--others", "--exclude-standard", "-z").split(b"\0") if name}
                 for root in scope["roots"] if is_git_root(Path(root["path"]))}
    for entry in current_inventory(expanded, run_dir):
        absolute = Path(entry["root"]) / entry["path"]
        previously_covered = False
        for root in scope["roots"]:
            base = Path(root["path"])
            if absolute != base and not absolute.is_relative_to(base):
                continue
            relative = absolute.relative_to(base)
            explicit = any(relative == Path(name) or relative.is_relative_to(name)
                           for name in root.get("include", []))
            automatic = (relative.as_posix() in git_names[root["path"]] if root["path"] in git_names
                         else "include" not in root)
            previously_covered = previously_covered or explicit or automatic
        if not previously_covered and (entry["root"], entry["path"]) not in old:
            old[(entry["root"], entry["path"])] = entry
    expanded["initial"] = sorted(old.values(), key=lambda entry: (entry["root"], entry["path"]))
    for root in expanded["roots"]:
        path = Path(root["path"])
        if str(path) not in expanded["git"] and is_git_root(path):
            expanded["git"][str(path)] = {
                "head": git(path, "rev-parse", "--revs-only", "HEAD").decode().strip() or None,
                "status": git(path, "status", "--porcelain=v1", "-z").decode(errors="surrogateescape")}
    context_roots = [{"path": path} for path in expanded["context"]]
    for root in context_roots:
        root_inventory(root, context_roots, None, expanded.get("memory", []))
    return expanded


def changed_paths(scope, run_dir=None):
    before = {(item["root"], item["path"]): item for item in scope["initial"]}
    after = {(item["root"], item["path"]): item for item in current_inventory(scope, run_dir)}
    return sorted({str(Path(root) / name) for root, name in before.keys() | after.keys()
                   if before.get((root, name)) != after.get((root, name))})


def workspace_fingerprint(scope, requirements_digest, run_dir=None):
    context = [{"path": path} for path in scope["context"]]
    observations = [entry for root in context
                    for entry in root_inventory(root, context, None, scope.get("memory", []))]
    return digest({"roots": scope["roots"], "context": context, "requirements": requirements_digest,
                   "memory": scope.get("memory", []), "inventory": current_inventory(scope, run_dir),
                   "context_inventory": observations})
