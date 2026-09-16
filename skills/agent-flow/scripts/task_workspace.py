"""Capture full product trees through pinned descriptors and retain task results."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import posixpath
import stat
import subprocess
import sys
import unicodedata
import uuid
import base64
import tempfile

from journal_io import JournalError, JournalSnapshot, sync_file, capture_file, operation_id, transact


WorkspaceError = JournalError
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def require(condition, message):
    if not condition:
        raise WorkspaceError(message)


def encode(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def manifest_digest(manifest):
    return hashlib.sha256(encode(manifest)).hexdigest()


def manifest_delta(baseline, candidate):
    old, new = baseline["entries"], candidate["entries"]
    return {name: {"before": old.get(name), "after": new.get(name)}
            for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name)}


def identity(metadata):
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def stability(metadata):
    return (*identity(metadata), metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def basename(name):
    require(isinstance(name, str) and name not in {"", ".", ".."} and "/" not in name and "\0" not in name,
            f"invalid descriptor basename: {name!r}")
    try:
        name.encode("utf-8")
    except UnicodeError as exc:
        raise WorkspaceError(f"unrepresentable filesystem name: {name!r}") from exc
    return name


def preflight():
    require(sys.platform in {"darwin", "linux"}, "workspace requires local Darwin/Linux descriptor support")
    for function in (os.open, os.stat, os.mkdir, os.readlink, os.symlink):
        require(function in os.supports_dir_fd, f"workspace dir_fd unavailable: {function.__name__}")
    require(os.scandir in os.supports_fd, "workspace scandir(fd) unavailable")


def notify(barrier, phase, **context):
    if barrier:
        barrier(phase, context)


@contextmanager
def root_descriptor(path, *, barrier=None, destination=False, expected=None):
    """Walk from /; a selected root cannot be replaced by resolving its name again."""
    path = Path(path).absolute()
    selected = path.lstat()
    require(stat.S_ISDIR(selected.st_mode), f"root must be a real directory: {path}")
    require(expected is None or list(identity(selected)[:2]) == list(expected), f"registered root identity changed: {path}")
    descriptor = os.open("/", DIRECTORY_FLAGS)
    chain = []
    try:
        for name in path.parts[1:]:
            name = basename(name)
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            require(stat.S_ISDIR(before.st_mode), f"root parent is not a real directory: {name}")
            notify(barrier, "root-before-open", path=str(path), name=name, parent_fd=descriptor, destination=destination)
            child = os.open(name, DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                require(identity(os.fstat(child)) == identity(before), f"root parent changed before open: {name}")
                require(identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) == identity(before),
                        f"root parent changed after open: {name}")
            except BaseException:
                os.close(child)
                raise
            chain.append((descriptor, name, identity(before)))
            descriptor = child
        require(identity(os.fstat(descriptor)) == identity(selected), f"selected root identity changed: {path}")
        notify(barrier, "root-opened", path=str(path), fd=descriptor, destination=destination)
        yield descriptor
        for parent, name, expected in chain:
            require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == expected,
                    f"selected root parent changed: {path}")
    finally:
        os.close(descriptor)
        for parent, _, _ in reversed(chain):
            os.close(parent)


def directory_names(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    with os.scandir(fd) as entries:
        names = sorted(entry.name for entry in entries)
    normalized = [unicodedata.normalize("NFC", name).casefold() for name in names]
    require(len(set(normalized)) == len(names), "directory contains colliding names")
    return names


def validate_link(path, target):
    try:
        target.encode("utf-8")
    except UnicodeError as exc:
        raise WorkspaceError(f"unrepresentable symlink target: {path!r}") from exc
    require(target and not posixpath.isabs(target), f"escaping symlink: {path!r} -> {target!r}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    require(resolved != ".." and not resolved.startswith("../"), f"escaping symlink: {path!r} -> {target!r}")


def validate_link_graph(entries):
    for name, entry in entries.items():
        if entry["type"] != "symlink":
            continue
        pending = name.split("/")
        resolved, expansions = [], 0
        while pending:
            part = pending.pop(0)
            if part in {"", "."}:
                continue
            if part == "..":
                require(bool(resolved), f"escaping symlink chain: {name!r}")
                resolved.pop()
                continue
            candidate = "/".join([*resolved, part])
            target = entries.get(candidate)
            if target is not None and target["type"] == "symlink":
                expansions += 1
                require(expansions <= 40, f"unsupported symlink cycle: {name!r}")
                pending = target["target"].split("/") + pending
            else:
                resolved.append(part)


def scan_tree(root, *, exclusions=(), copy_to=None, barrier=None, expected_source=None, expected_destination=None, sync_source=False, allow_symlinks=True):
    """Inventory every entry; optional copy reads and hashes each verified file FD once.

    Exclusions are exact paths selected by the workspace protocol, never ignore globs.
    The test barrier observes FD identities before reads/writes and can inject races.
    """
    preflight()
    root = Path(root).absolute()
    excluded = {Path(path).absolute() for path in exclusions}
    entries = {}

    def walk(source_fd, relative, destination_fd=None):
        directory_before = os.fstat(source_fd)
        require(directory_before.st_mode & 0o444 and directory_before.st_mode & 0o111,
                f"unreadable directory: {relative or '.'}")
        names = directory_names(source_fd)
        for name in names:
            basename(name)
            path = f"{relative}/{name}" if relative else name
            if root / path in excluded:
                continue
            require(name != ".git", f"unsupported nested repository: {path}")
            before = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            mode = stat.S_IMODE(before.st_mode)
            notify(barrier, "source-before-open", path=path, parent_fd=source_fd, metadata=before)
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, DIRECTORY_FLAGS, dir_fd=source_fd)
                target = None
                try:
                    require(identity(os.fstat(child)) == identity(before), f"directory changed before open: {path}")
                    notify(barrier, "source-directory-opened", path=path, fd=child, parent_fd=source_fd)
                    require(stability(os.fstat(child)) == stability(before), f"directory changed after open: {path}")
                    if destination_fd is not None:
                        notify(barrier, "destination-before-directory", path=path, parent_fd=destination_fd)
                        os.mkdir(name, 0o700, dir_fd=destination_fd)
                        named = os.stat(name, dir_fd=destination_fd, follow_symlinks=False)
                        target = os.open(name, DIRECTORY_FLAGS, dir_fd=destination_fd)
                        require(identity(os.fstat(target)) == identity(named), f"destination directory replaced: {path}")
                        opened = os.fstat(target)
                        notify(barrier, "destination-directory-opened", path=path, fd=target, parent_fd=destination_fd)
                        require(stability(os.fstat(target)) == stability(opened), f"destination directory changed: {path}")
                    entries[path] = {"type": "directory", "mode": mode}
                    walk(child, path, target)
                    require(stability(os.stat(name, dir_fd=source_fd, follow_symlinks=False)) == stability(before),
                            f"source directory entry changed: {path}")
                    if target is not None:
                        require(identity(os.stat(name, dir_fd=destination_fd, follow_symlinks=False)) == identity(os.fstat(target)),
                                f"destination directory entry changed: {path}")
                        os.fchmod(target, mode)
                        os.fsync(target)
                finally:
                    os.close(child)
                    if target is not None:
                        os.close(target)
            elif stat.S_ISREG(before.st_mode):
                require(mode & 0o444, f"unreadable file: {path}")
                descriptor = os.open(name, FILE_FLAGS, dir_fd=source_fd)
                output = None
                try:
                    opened = os.fstat(descriptor)
                    require(stat.S_ISREG(opened.st_mode) and stability(opened) == stability(before),
                            f"file changed before first read: {path}")
                    if destination_fd is not None:
                        notify(barrier, "destination-before-file", path=path, parent_fd=destination_fd)
                        output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                         0o600, dir_fd=destination_fd)
                        require(stat.S_ISREG(os.fstat(output).st_mode), f"destination is not a regular file: {path}")
                        notify(barrier, "destination-file-opened", path=path, fd=output, parent_fd=destination_fd)
                        require(identity(os.stat(name, dir_fd=destination_fd, follow_symlinks=False)) == identity(os.fstat(output)),
                                f"destination file entry changed: {path}")
                    checksum = hashlib.sha256()
                    while True:
                        require(stability(os.fstat(source_fd)) == stability(directory_before), f"source parent changed: {path}")
                        notify(barrier, "source-before-read", path=path, fd=descriptor, parent_fd=source_fd)
                        data = os.read(descriptor, 1024 * 1024)
                        if not data:
                            break
                        notify(barrier, "source-read-buffer", path=path, fd=descriptor, data=data)
                        checksum.update(data)
                        if output is not None:
                            view = memoryview(data)
                            while view:
                                written = os.write(output, view)
                                require(written > 0, f"destination write did not advance: {path}")
                                view = view[written:]
                    require(stability(os.fstat(descriptor)) == stability(before)
                            and stability(os.stat(name, dir_fd=source_fd, follow_symlinks=False)) == stability(before),
                            f"source file changed during read: {path}")
                    if sync_source:
                        sync_file(descriptor)
                    entries[path] = {"type": "file", "mode": mode, "sha256": checksum.hexdigest()}
                    if output is not None:
                        os.fchmod(output, mode)
                        sync_file(output)
                        require(identity(os.stat(name, dir_fd=destination_fd, follow_symlinks=False)) == identity(os.fstat(output)),
                                f"destination file entry changed: {path}")
                finally:
                    os.close(descriptor)
                    if output is not None:
                        os.close(output)
            elif stat.S_ISLNK(before.st_mode):
                require(allow_symlinks, f"unsupported Git metadata symlink: {path}")
                target = os.readlink(name, dir_fd=source_fd)
                validate_link(path, target)
                require(stability(os.stat(name, dir_fd=source_fd, follow_symlinks=False)) == stability(before),
                        f"symlink changed while reading: {path}")
                entries[path] = {"type": "symlink", "mode": mode, "target": target}
                if destination_fd is not None:
                    notify(barrier, "destination-before-link", path=path, parent_fd=destination_fd)
                    os.symlink(target, name, dir_fd=destination_fd)
                    require(os.readlink(name, dir_fd=destination_fd) == target, f"destination link changed: {path}")
            else:
                raise WorkspaceError(f"unsupported special file: {path}")
        require(directory_names(source_fd) == names and stability(os.fstat(source_fd)) == stability(directory_before),
                f"source directory changed during scan: {relative or '.'}")
        if sync_source:
            os.fsync(source_fd)
        if destination_fd is not None:
            os.fsync(destination_fd)

    try:
        with root_descriptor(root, barrier=barrier, expected=expected_source) as source_fd:
            if copy_to is None:
                walk(source_fd, "")
            else:
                with root_descriptor(copy_to, barrier=barrier, destination=True, expected=expected_destination) as destination_fd:
                    require(identity(os.fstat(source_fd))[:2] != identity(os.fstat(destination_fd))[:2], "copy roots alias")
                    walk(source_fd, "", destination_fd)
    except OSError as exc:
        raise WorkspaceError(f"incomplete workspace inventory: {exc}") from exc
    validate_link_graph(entries)
    return {"version": 1, "entries": entries,
            "exclusions": sorted(str(path.relative_to(root)) for path in excluded if path.is_relative_to(root))}


def git_command(source, *arguments, check=True, input_data=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                        "GIT_ATTR_NOSYSTEM": "1"})
    command = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
               "-c", "core.attributesFile=" + os.devnull, "-c", "protocol.allow=never",
               "-c", "protocol.file.allow=never", "-c", "submodule.recurse=false"]
    filters = subprocess.run([*command, "config", "--null", "--name-only", "--get-regexp", r"^filter\."],
                             cwd=source, env=environment, capture_output=True)
    require(filters.returncode in {0, 1}, "cannot inspect Git filter configuration")
    drivers = {os.fsdecode(key).rsplit(".", 1)[0] for key in filters.stdout.split(b"\0") if key}
    for driver in sorted(drivers):
        for option, value in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false")):
            command.extend(["-c", f"{driver}.{option}={value}"])
    command.extend(arguments)
    result = subprocess.run(command, cwd=source, env=environment, capture_output=True, input=input_data)
    require(not check or result.returncode == 0, f"workspace git command failed: {arguments!r}: {os.fsdecode(result.stderr)}")
    return result


def discover_git(source, *, barrier=None):
    source = Path(source).absolute()
    with root_descriptor(source) as descriptor:
        root_identity = list(identity(os.fstat(descriptor))[:2])
        control = os.stat(".git", dir_fd=descriptor, follow_symlinks=False)
    gitfile = None
    if stat.S_ISDIR(control.st_mode):
        git_dir = source / ".git"
    else:
        require(stat.S_ISREG(control.st_mode), "source Git control entry must be a directory or regular gitfile")
        gitfile = read_owned(source, ".git", expected=root_identity, barrier=barrier)
        require(gitfile.startswith(b"gitdir: ") and gitfile.count(b"\n") <= 1, "unsupported Git control file")
        value = os.fsdecode(gitfile.removeprefix(b"gitdir: ").removesuffix(b"\n"))
        require(bool(value), "empty Git control path")
        git_dir = Path(os.path.normpath(source / value))
    with root_descriptor(git_dir, expected=list(identity(control)[:2]) if gitfile is None else None) as descriptor:
        git_identity = list(identity(os.fstat(descriptor))[:2])
        names = directory_names(descriptor)
    common_file = read_owned(git_dir, "commondir", expected=git_identity, barrier=barrier) if "commondir" in names else None
    common = git_dir
    if common_file is not None:
        require(common_file and common_file.count(b"\n") <= 1, "unsupported Git common directory file")
        common = Path(os.path.normpath(git_dir / os.fsdecode(common_file.removesuffix(b"\n"))))
    with root_descriptor(common) as descriptor:
        common_identity = list(identity(os.fstat(descriptor))[:2])
    return {"root": str(source), "root_identity": root_identity, "git_dir": str(git_dir), "git_identity": git_identity,
            "common_dir": str(common), "common_identity": common_identity,
            "gitfile_sha256": hashlib.sha256(gitfile).hexdigest() if gitfile is not None else None,
            "commondir_sha256": hashlib.sha256(common_file).hexdigest() if common_file is not None else None}


@contextmanager
def git_scratch():
    temporary_root = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
    with root_descriptor(temporary_root):
        with tempfile.TemporaryDirectory(prefix="agent-flow-git-", dir=temporary_root) as directory:
            yield Path(directory)


def capture_git(source, scratch, *, barrier=None):
    """Copy the bounded Git layout through FDs; Git sees only private scratch."""
    roots = discover_git(source, barrier=barrier)
    own, common = Path(roots["git_dir"]), Path(roots["common_dir"])
    destination = scratch / ".git"
    create_directory(scratch, ".git")
    captured = {}
    parent_identities = {str(common): roots["common_identity"], str(own): roots["git_identity"]}
    def optional_file(parent, name, output=None):
        expected = parent_identities.get(str(parent))
        with root_descriptor(parent, expected=expected) as descriptor:
            if name not in directory_names(descriptor):
                return None
        data = read_owned(parent, name, expected=expected, barrier=barrier)
        captured[str(parent) + "/" + name] = hashlib.sha256(data).hexdigest()
        if output is not None:
            write_owned(output, name, data)
        return data
    config = optional_file(common, "config")
    require(config is not None, "source Git config is missing")
    parsed = git_command(scratch, "config", "--file", "-", "--no-includes", "--null", "--list", input_data=config).stdout
    settings = {}
    for entry in parsed.split(b"\0"):
        if entry:
            key, _, value = entry.partition(b"\n")
            settings[os.fsdecode(key).lower()] = os.fsdecode(value)
    version = settings.get("core.repositoryformatversion", "0")
    require(version in {"0", "1"}, "unsupported Git repository format")
    extensions = {key: value for key, value in settings.items() if key.startswith("extensions.")}
    require(set(extensions) <= {"extensions.objectformat", "extensions.refstorage", "extensions.worktreeconfig"},
            "unsupported Git repository extension")
    object_format = extensions.get("extensions.objectformat", "sha1")
    require(object_format in {"sha1", "sha256"}, "unsupported Git object format")
    require(extensions.get("extensions.refstorage", "files") == "files", "unsupported Git ref storage")
    generated = "[core]\nrepositoryformatversion = " + ("1" if object_format == "sha256" else "0") + "\nbare = false\nfilemode = true\n"
    if object_format == "sha256":
        generated += "[extensions]\nobjectformat = sha256\n"
    write_owned(destination, "config", generated.encode())
    for directory in ("objects", "refs"):
        origin = common / directory
        target = destination / directory
        create_directory(destination, directory)
        excludes = [origin / name for name in ("bisect", "rewritten", "worktree")] if directory == "refs" and own != common else []
        with root_descriptor(common, expected=roots["common_identity"]) as descriptor:
            selected = os.stat(directory, dir_fd=descriptor, follow_symlinks=False)
            require(stat.S_ISDIR(selected.st_mode), "Git objects and refs must be real directories")
            captured[str(origin)] = scan_tree(origin, exclusions=excludes, copy_to=target, barrier=barrier,
                                               expected_source=list(identity(selected)[:2]), allow_symlinks=False)
        if directory == "objects":
            require(not any(name.endswith(".promisor") for name in captured[str(origin)]["entries"]),
                    "unsupported Git promisor object dependency")
    info = destination / "objects/info"
    for name in ("alternates", "http-alternates"):
        if info.exists():
            data = optional_file(info, name)
            require(not data, "source Git uses external object alternates")
            captured.pop(str(info) + "/" + name, None)
    for name in ("packed-refs", "shallow"):
        optional_file(common, name, destination)
    head = optional_file(own, "HEAD", destination)
    require(head is not None, "source Git HEAD is missing")
    index = optional_file(own, "index", destination)
    optional_file(own, "config.worktree")
    with root_descriptor(own, expected=roots["git_identity"]) as descriptor:
        names = directory_names(descriptor)
    for name in names:
        if name.startswith("sharedindex."):
            optional_file(own, name, destination)
    if own != common and "refs" in names:
        with root_descriptor(own, expected=roots["git_identity"]) as descriptor:
            own_refs_identity = list(identity(os.stat("refs", dir_fd=descriptor, follow_symlinks=False))[:2])
        with root_descriptor(own / "refs", expected=own_refs_identity) as descriptor:
            ref_names = directory_names(descriptor)
            for name in ("bisect", "rewritten", "worktree"):
                origin = own / "refs" / name
                if name in ref_names:
                    selected = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    create_directory(destination / "refs", name)
                    captured[str(origin)] = scan_tree(origin, copy_to=destination / "refs" / name, barrier=barrier,
                                                       expected_source=list(identity(selected)[:2]), allow_symlinks=False)
    require(discover_git(source, barrier=barrier) == roots, "source Git roots changed during capture")
    roots.update(index_sha256=hashlib.sha256(index).hexdigest() if index is not None else None,
                 metadata_digest=manifest_digest(captured), source_config_sha256=hashlib.sha256(config).hexdigest(), object_format=object_format,
                 metadata_profile="private-fd-snapshot-fixed-config-v2")
    return roots


def query_git(scratch, metadata, *, full=False):
    gitlinks = git_command(scratch, "ls-files", "--stage", "-z").stdout
    for entry in gitlinks.split(b"\0"):
        require(not entry.startswith(b"160000 "), "unsupported submodule")
    head = git_command(scratch, "rev-parse", "--verify", "HEAD", check=False)
    require(head.returncode in {0, 128}, "cannot read captured HEAD")
    refs = git_command(scratch, "show-ref", "--head", check=False)
    require(refs.returncode in {0, 1}, "cannot read captured refs")
    git_command(scratch, "fsck", "--connectivity-only", "--no-reflogs")
    result = {**metadata, "head": head.stdout.decode().strip() if head.returncode == 0 else None,
              "refs_base64": base64.b64encode(refs.stdout).decode()}
    if full:
        commands = {"status": ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored"),
                    "staged": ("diff", "--cached", "--raw", "-z", "--no-ext-diff", "--no-textconv"),
                    "unstaged": ("diff", "--raw", "-z", "--no-ext-diff", "--no-textconv")}
        for name, command in commands.items():
            result[name + "_base64"] = base64.b64encode(git_command(scratch, *command).stdout).decode()
    return result


def git_identity(source):
    """Metadata-only diagnostic; no product scan or live Git subprocess."""
    with git_scratch() as scratch:
        return query_git(scratch, capture_git(source, scratch))


def create_directory(parent, name, *, expected=None):
    with root_descriptor(parent, destination=True, expected=expected) as descriptor:
        os.mkdir(basename(name), 0o700, dir_fd=descriptor)
        created = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        os.fsync(descriptor)
        return list(identity(created)[:2])


def write_owned(directory, name, data, *, expected=None):
    with root_descriptor(directory, destination=True, expected=expected) as descriptor:
        output = os.open(basename(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=descriptor)
        try:
            view = memoryview(data)
            while view:
                written = os.write(output, view)
                require(written > 0, "owned metadata write did not advance")
                view = view[written:]
            sync_file(output)
        finally:
            os.close(output)
        os.fsync(descriptor)


def read_owned(directory, name, *, expected=None, barrier=None):
    with root_descriptor(directory, expected=expected) as parent:
        parent_before = stability(os.fstat(parent))
        before = os.stat(basename(name), dir_fd=parent, follow_symlinks=False)
        require(stat.S_ISREG(before.st_mode), "owned metadata is not a regular file")
        notify(barrier, "git-before-open", path=name, parent_fd=parent, metadata=before)
        descriptor = os.open(name, FILE_FLAGS, dir_fd=parent)
        try:
            require(stability(os.fstat(descriptor)) == stability(before), "owned metadata changed before read")
            notify(barrier, "git-file-opened", path=name, fd=descriptor, parent_fd=parent)
            chunks = []
            while True:
                require(stability(os.fstat(parent)) == parent_before, "owned metadata parent changed during read")
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                notify(barrier, "git-read-buffer", path=name, fd=descriptor, data=chunk)
                chunks.append(chunk)
            require(stability(os.fstat(parent)) == parent_before, "owned metadata parent changed during read")
            require(stability(os.fstat(descriptor)) == stability(before) ==
                    stability(os.stat(name, dir_fd=parent, follow_symlinks=False)), "owned metadata changed during read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)


def observed_identity(path, inputs=None):
    from validation_inputs import captured
    def read():
        with root_descriptor(path) as descriptor:
            return list(identity(os.fstat(descriptor))[:2])
    return captured(inputs, "directory-identity:" + str(path), read)


def observed_owned(root, name, expected, inputs=None):
    from validation_inputs import captured
    return captured(inputs, "owned:" + str(root) + "/" + name,
                    lambda: read_owned(root, name, expected=expected))


def observed_tree(root, manifest, expected, barrier=None, inputs=None):
    from validation_inputs import captured
    return captured(inputs, "tree:" + str(root), lambda: scan_tree(
        root, exclusions=[root / name for name in manifest["exclusions"]],
        expected_source=expected, barrier=barrier))


def verify_baseline(workspace, *, barrier=None, inputs=None):
    validate_owner(workspace, workspace["run_uuid"], inputs=inputs)
    baseline = Path(workspace["baseline_root"])
    expected = workspace["baseline_manifest"]
    actual = observed_tree(baseline, expected, workspace["baseline_identity"], barrier, inputs)
    same_tree(expected, actual, "retained baseline changed")
    require(manifest_digest(actual) == workspace["baseline_digest"], "baseline digest mismatch")
    require(observed_owned(baseline.parent, "baseline-manifest.json", workspace["attempt_identity"], inputs) == encode(expected), "retained baseline manifest changed")
    return actual


def checked_document(snapshot, result):
    raw = snapshot.read_bytes(result["document"])
    require(hashlib.sha256(raw).hexdigest() == result["sha256"], "registered workspace document changed")
    return json.loads(raw)


def registered_workspace(snapshot, *, require_sealed=False):
    receipt = snapshot.operation_receipt("workspace-prepare")
    if receipt is None:
        require(not require_sealed, "change requires registered and sealed workspace")
        return None
    workspace = checked_document(snapshot, receipt["result"])
    seals = [snapshot.operation_receipt(name) for name in snapshot.receipts if name.startswith("workspace-seal-")]
    if seals:
        latest = max(seals, key=lambda item: item["revision"])
        workspace["seal"] = checked_document(snapshot, latest["result"])
    require(not require_sealed or "seal" in workspace, "change requires sealed workspace candidate")
    return workspace


def validate_owner(workspace, run_uuid, *, inputs=None):
    validate_metadata_identity(workspace.get("metadata_scope"), inputs=inputs)
    bundle = Path(workspace["bundle"])
    require(observed_identity(bundle, inputs) == workspace["bundle_identity"], "workspace owner root changed")
    owner = json.loads(observed_owned(bundle, "owner.json", workspace["bundle_identity"], inputs))
    require(owner == {"run_uuid": run_uuid, "workspace_id": workspace["workspace_id"], "root_identity": workspace["bundle_identity"]},
            "foreign workspace ownership")
    return bundle


def validate_metadata_identity(binding, *, inputs=None):
    if binding is None:
        return
    for name in ("source", "metadata", "git", "common"):
        require(observed_identity(binding[name + "_root"], inputs) == binding[name + "_identity"],
                "metadata directory identity changed: " + name)


def capture_metadata_scope(snapshot, source, metadata, request, session_source):
    if request is None:
        return None
    from verification_evidence import (verify_reference, completed_turn, completion_follows,
                                       require_own_reviewer_handoff)
    require(set(request) == {"namespace", "authorized_source", "scope_ref", "qa_proof_ref", "reviewer_proof_ref"},
            "metadata scope requires namespace, authorized source and all three references")
    require(request["namespace"] == "agent-flow-runtime", "unsupported metadata namespace")
    require(request["authorized_source"] == str(source), "metadata authorized source differs from selected source")
    summary = json.loads(snapshot.read_text("delegation-summary.json"))
    require(isinstance(summary, dict) and isinstance(summary.get("verification"), dict), "metadata scope requires registered verification")
    verification = summary["verification"]
    require(isinstance(verification.get("root_thread_id"), str), "metadata scope requires registered root thread")
    root_id = verification["root_thread_id"]
    scope_digest = verify_reference(snapshot.artifact_root, request["scope_ref"], snapshot=snapshot)
    captured = []
    identities = set(verification.get("author_thread_ids", [])) | {root_id}
    for key, role in (("qa_proof_ref", "qa-verifier"), ("reviewer_proof_ref", "reviewer")):
        reference = request[key]
        verify_reference(snapshot.artifact_root, reference, snapshot=snapshot)
        proof = json.loads(snapshot.read_bytes(reference["path"]))
        require(isinstance(proof, dict) and isinstance(proof.get("thread_id"), str) and
                isinstance(proof.get("completion_turn_id"), str) and isinstance(proof.get("completion"), dict),
                "metadata proof requires source thread, turn and completion facts")
        thread, turn = proof["thread_id"], proof["completion_turn_id"]
        require(thread not in identities, "metadata scope requires independent QA and reviewer")
        identities.add(thread)
        completion = completed_turn(session_source, thread, turn, root_id, role)
        require(json.loads(json.dumps(completion, default=str)) == proof["completion"], "metadata proof differs from raw completion facts")
        answer = completion["answer"]
        require(answer.get("verdict") == "passed" and answer.get("reviewed_result_hash") == scope_digest,
                "metadata completion does not accept the exact scope")
        verify_reference(snapshot.artifact_root, {"path": answer["handoff"], "sha256": answer["handoff_sha256"]}, snapshot=snapshot)
        captured.append(completion)
    qa, reviewer = captured
    require_own_reviewer_handoff(snapshot.artifact_root, reviewer["answer"]["handoff"], qa["answer"]["handoff"], snapshot=snapshot)
    require(reviewer["answer"].get("qa_handoff_sha256") == qa["answer"]["handoff_sha256"] and
            completion_follows(reviewer, qa), "metadata reviewer must follow and reference accepted QA")
    metadata_root = source / ".agent-work"
    namespace_identity = observed_identity(metadata_root, snapshot.external_inputs)
    binding = {"request": request, "source_root": str(source), "source_identity": metadata["root_identity"],
               "metadata_root": str(metadata_root), "metadata_identity": namespace_identity,
               "reason": "explicit accepted Agent Flow runtime namespace"}
    for name, path in (("git", metadata["git_dir"]), ("common", metadata["common_dir"])):
        binding[name + "_root"] = path
        binding[name + "_identity"] = observed_identity(path, snapshot.external_inputs)
    return binding


def workspace_exclusions(source, snapshot, destination, metadata, binding=None):
    paths = {source / ".git"}
    if binding is not None:
        validate_metadata_identity(binding)
        paths.add(Path(binding["metadata_root"]))
    for path in (Path(metadata["git_dir"]), Path(metadata["common_dir"]), snapshot.artifact_root, destination):
        if path.is_relative_to(source) and path != source:
            paths.add(path)
    return tuple(sorted(paths))


def same_tree(expected, actual, message):
    require(expected == actual, message)


def prepare(run_dir, source, destination, *, metadata_scope=None, session_source=None, barrier=None):
    snapshot = JournalSnapshot.open(run_dir)
    require(snapshot.storage_version == 2 and not snapshot.closed, "workspace prepare requires open version 2 journal")
    require(snapshot.durable, "import journal before workspace prepare")
    source, destination = Path(source).absolute(), Path(destination).absolute()
    payload = {"source": str(source), "destination": str(destination)}
    existing = registered_workspace(snapshot)
    if existing is not None:
        require(existing["source"] == str(source) and existing["bundle"] == str(destination), "workspace root binding cannot change")
        existing_scope = existing.get("metadata_scope")
        require((existing_scope["request"] if existing_scope else None) == metadata_scope,
                "registered metadata scope cannot change")
        verify_baseline(existing)
        validate_metadata_identity(existing.get("metadata_scope"))
        if metadata_scope is not None:
            from verification_evidence import CodexSessionSource
            require(capture_metadata_scope(snapshot, source, existing["source_metadata"], metadata_scope,
                                           session_source or CodexSessionSource()) == existing["metadata_scope"],
                    "registered metadata evidence changed")
        return existing
    from journal_lifecycle import capture_validation, freeze_validation
    from dataclasses import replace
    snapshot, captured_source = capture_validation(snapshot, session_source=session_source)
    metadata = discover_git(source, barrier=barrier)
    binding = capture_metadata_scope(snapshot, source, metadata, metadata_scope, captured_source)
    if binding is not None:
        payload["metadata_scope"] = binding
    identifier = operation_id(run_dir, "workspace-prepare", payload, identifier="workspace-prepare")
    freeze_validation(snapshot, captured_source)
    workspace_id = str(uuid.uuid5(uuid.NAMESPACE_URL, snapshot.run_uuid + str(source) + str(destination)))
    owner = {"run_uuid": snapshot.run_uuid, "workspace_id": workspace_id}
    if destination.exists():
        owner["root_identity"] = list(identity(destination.lstat())[:2])
        require(json.loads(read_owned(destination, "owner.json", expected=owner["root_identity"])) == owner, "destination is not owned by this workspace")
    else:
        owner["root_identity"] = create_directory(destination.parent, destination.name)
        write_owned(destination, "owner.json", encode(owner), expected=owner["root_identity"])
    attempt = destination / ("prepare-" + str(uuid.uuid4()))
    attempt_identity = create_directory(destination, attempt.name, expected=owner["root_identity"])
    baseline, working = attempt / "baseline", attempt / "working"
    baseline_identity = create_directory(attempt, baseline.name, expected=attempt_identity)
    metadata = discover_git(source, barrier=barrier)
    exclusions = workspace_exclusions(source, snapshot, destination, metadata, binding)
    notify(barrier, "prepare-before-copy", source=str(source), attempt=str(attempt))
    first = scan_tree(source, exclusions=exclusions, copy_to=baseline, barrier=barrier,
                      expected_source=metadata["root_identity"], expected_destination=baseline_identity)
    relative_exclusions = first["exclusions"]
    same_tree(first, scan_tree(source, exclusions=exclusions, barrier=barrier), "source changed during prepare")
    copied = scan_tree(baseline, exclusions=[baseline / name for name in relative_exclusions], barrier=barrier)
    same_tree(first, copied, "baseline differs from full source inventory")
    notify(barrier, "prepare-before-git-copy", source=str(source), attempt=str(attempt), working=str(working))
    working_identity = create_directory(attempt, working.name, expected=attempt_identity)
    scan_tree(baseline, exclusions=[baseline / name for name in relative_exclusions], copy_to=working, barrier=barrier,
              expected_source=baseline_identity, expected_destination=working_identity)
    with git_scratch() as scratch:
        captured_metadata = capture_git(source, scratch, barrier=barrier)
        scan_tree(baseline, exclusions=[baseline / name for name in relative_exclusions], copy_to=scratch, barrier=barrier,
                  expected_source=baseline_identity)
        metadata = query_git(scratch, captured_metadata, full=True)
        git_destination_identity = create_directory(working, ".git", expected=working_identity)
        scan_tree(scratch / ".git", copy_to=working / ".git", barrier=barrier,
                  expected_destination=git_destination_identity)
        same_tree(scan_tree(scratch / ".git"), scan_tree(working / ".git", expected_source=git_destination_identity),
                  "retained Git metadata changed during copy")
    same_tree(first, scan_tree(working, exclusions=[working / name for name in relative_exclusions], barrier=barrier),
              "working copy differs from full baseline")
    same_tree(first, scan_tree(source, exclusions=exclusions, expected_source=metadata["root_identity"], barrier=barrier),
              "source changed before workspace publication")
    with git_scratch() as scratch:
        require(capture_git(source, scratch, barrier=barrier) == captured_metadata,
                "source Git metadata changed during prepare")
    write_owned(attempt, "baseline-manifest.json", encode(first), expected=attempt_identity)
    workspace = {"version": 1, "workspace_id": workspace_id, "run_uuid": snapshot.run_uuid,
                 "source": str(source), "bundle": str(destination), "bundle_identity": list(identity(destination.lstat())[:2]),
                 "baseline_root": str(baseline), "working_root": str(working),
                 "baseline_identity": baseline_identity, "working_identity": working_identity, "attempt_identity": attempt_identity,
                 "source_metadata": metadata, "metadata_scope": binding, "baseline_manifest": first, "baseline_digest": manifest_digest(first)}
    document = f"artifacts/workspaces/{workspace_id}/prepare.json"
    raw = encode(workspace)
    notify(barrier, "prepare-before-publication", workspace=workspace)
    def publish(current):
        require(current.revision == snapshot.revision, "workspace prepare preconditions changed")
        current = replace(current, external_inputs=snapshot.external_inputs)
        require(registered_workspace(current) is None, "workspace already registered")
        require(capture_metadata_scope(current, source, metadata, metadata_scope, captured_source) == binding,
                "metadata binding changed before prepare publication")
        return {document: raw}, {"document": document, "sha256": hashlib.sha256(raw).hexdigest()}
    transact(run_dir, identifier, payload, publish)
    notify(barrier, "prepare-after-publication", workspace=workspace)
    return workspace


def seal(run_dir, *, identifier=None, barrier=None):
    snapshot = JournalSnapshot.open(run_dir)
    require(snapshot.storage_version == 2 and not snapshot.closed, "workspace seal requires open version 2 journal")
    workspace = registered_workspace(snapshot)
    require(workspace is not None, "prepare workspace before seal")
    bundle = validate_owner(workspace, snapshot.run_uuid)
    verify_baseline(workspace, barrier=barrier)
    working = Path(workspace["working_root"])
    require(list(identity(working.lstat())[:2]) == workspace["working_identity"], "registered working root changed")
    relative_exclusions = workspace["baseline_manifest"]["exclusions"]
    exclusions = [working / name for name in relative_exclusions]
    current = scan_tree(working, exclusions=exclusions, barrier=barrier, expected_source=workspace["working_identity"])
    if "seal" in workspace and current == workspace["seal"]["candidate_manifest"] and identifier is None:
        verify_candidate(workspace, barrier=barrier)
        return workspace["seal"]
    identifier = identifier or str(uuid.uuid4())
    operation = identifier if identifier.startswith("workspace-seal-") else "workspace-seal-" + identifier
    payload = {"workspace_id": workspace["workspace_id"], "candidate_manifest_digest": manifest_digest(current)}
    operation_id(run_dir, "workspace-seal", payload, identifier=operation)
    old = snapshot.operation_receipt(operation)
    if old is not None:
        retained = checked_document(snapshot, old["result"])
        verify_candidate({**workspace, "seal": retained}, barrier=barrier)
        return retained
    candidate_id = str(uuid.uuid5(uuid.NAMESPACE_URL, snapshot.run_uuid + operation))
    container = bundle / ("candidate-" + candidate_id)
    if container.exists():
        require(json.loads(read_owned(container, "owner.json")) == {"run_uuid": snapshot.run_uuid, "candidate_id": candidate_id},
                "foreign candidate directory")
        container = bundle / ("candidate-" + candidate_id + "-" + str(uuid.uuid4()))
    container_identity = create_directory(bundle, container.name, expected=workspace["bundle_identity"])
    write_owned(container, "owner.json", encode({"run_uuid": snapshot.run_uuid, "candidate_id": candidate_id}), expected=container_identity)
    candidate = container / "tree"
    candidate_identity = create_directory(container, candidate.name, expected=container_identity)
    notify(barrier, "seal-before-copy", working=str(working), candidate=str(candidate))
    copied = scan_tree(working, exclusions=exclusions, copy_to=candidate, barrier=barrier,
                       expected_source=workspace["working_identity"], expected_destination=candidate_identity)
    same_tree(current, copied, "working tree changed before candidate copy")
    same_tree(current, scan_tree(working, exclusions=exclusions, barrier=barrier), "working tree changed during seal")
    captured = scan_tree(candidate, exclusions=[candidate / name for name in relative_exclusions], barrier=barrier)
    same_tree(current, captured, "retained candidate differs from working tree")
    delta = manifest_delta(workspace["baseline_manifest"], captured)
    sealed = {"version": 2, "workspace_id": workspace["workspace_id"], "candidate_id": candidate_id, "operation_id": operation,
              "candidate_root": str(candidate), "candidate_identity": candidate_identity, "container_identity": container_identity,
              "candidate_manifest": captured, "candidate_digest": manifest_digest(captured),
              "baseline_digest": workspace["baseline_digest"], "delta": delta, "changed_paths": sorted(delta)}
    write_owned(container, "manifest.json", encode(captured), expected=container_identity)
    write_owned(container, "delta.json", encode(delta), expected=container_identity)
    document = f"artifacts/workspaces/{workspace['workspace_id']}/seals/{candidate_id}.json"
    raw = encode(sealed)
    notify(barrier, "seal-before-publication", sealed=sealed)
    def publish(current_snapshot):
        registered = registered_workspace(current_snapshot)
        require(registered is not None and registered["workspace_id"] == workspace["workspace_id"], "workspace binding changed")
        return {document: raw}, {"document": document, "sha256": hashlib.sha256(raw).hexdigest()}
    transact(run_dir, operation, payload, publish)
    notify(barrier, "seal-after-publication", sealed=sealed)
    return sealed


def verify_candidate(workspace, *, barrier=None, inputs=None):
    verify_baseline(workspace, barrier=barrier, inputs=inputs)
    sealed = workspace.get("seal")
    require(sealed is not None, "workspace has no sealed candidate")
    candidate = Path(sealed["candidate_root"])
    require(observed_identity(candidate.parent, inputs) == sealed["container_identity"], "candidate container changed")
    require(json.loads(observed_owned(candidate.parent, "owner.json", sealed["container_identity"], inputs)) ==
            {"run_uuid": workspace["run_uuid"], "candidate_id": sealed["candidate_id"]}, "candidate ownership changed")
    require(observed_owned(candidate.parent, "manifest.json", sealed["container_identity"], inputs) == encode(sealed["candidate_manifest"]), "candidate manifest changed")
    require(observed_owned(candidate.parent, "delta.json", sealed["container_identity"], inputs) == encode(sealed["delta"]), "candidate delta changed")
    require(observed_identity(candidate, inputs) == sealed["candidate_identity"], "sealed candidate root changed")
    manifest = observed_tree(candidate, sealed["candidate_manifest"], sealed["candidate_identity"], barrier, inputs)
    same_tree(manifest, sealed["candidate_manifest"], "sealed candidate changed; obtain current acceptance")
    require(manifest_digest(manifest) == sealed["candidate_digest"], "sealed candidate digest mismatch")
    return sealed


def inspect(run_dir):
    snapshot = JournalSnapshot.open(run_dir)
    workspace = registered_workspace(snapshot)
    require(workspace is not None, "workspace is not prepared")
    validate_owner(workspace, snapshot.run_uuid)
    if "seal" in workspace:
        verify_candidate(workspace)
    else:
        verify_baseline(workspace)
    return workspace


def delivery(run_dir, *, identifier=None, session_source=None):
    """Publish locations of the retained, currently accepted full candidate."""
    import re
    from verification_evidence import result_hash
    from journal_lifecycle import capture_validation, freeze_validation, load_validator
    from journal_io import _transact
    from dataclasses import replace
    snapshot, source = capture_validation(JournalSnapshot.open(run_dir), session_source=session_source)
    validator_bytes = snapshot.external_inputs.file(Path(__file__).with_name("validate-run.py"))
    validator = load_validator(validator_bytes)
    def acceptance(current):
        verdicts = re.findall(r"^Verdict:\s*(.*?)\s*$", current.read_text("final.md"), re.MULTILINE)
        require(len(verdicts) == 1 and verdicts[0] in {"ship", "pass-with-risks"}, "delivery requires positive final verdict")
        errors = validator.validate_run(Path(run_dir), session_source=source, snapshot=current)
        require(not errors, "delivery validation failed: " + "; ".join(errors))
        registered = registered_workspace(current, require_sealed=True)
        verify_candidate(registered, inputs=current.external_inputs)
        summary = json.loads(current.read_text("delegation-summary.json"))
        return registered, result_hash(Path(run_dir), summary["verification"], snapshot=current)
    workspace, accepted_hash = acceptance(snapshot)
    freeze_validation(snapshot, source)
    sealed = workspace["seal"]
    lines = ["# Результат задачи", "", "Полная сохранённая копия: " + sealed["candidate_root"], "", "Изменённые пути:"]
    for name, change in sealed["delta"].items():
        kind = "добавлен" if change["before"] is None else "удалён" if change["after"] is None else "изменён"
        lines.append("- " + kind + ": " + json.dumps(name, ensure_ascii=True))
    text = "\n".join(lines) + "\n"
    container = Path(sealed["candidate_root"]).parent
    if (container / "delivery.md").exists():
        require(read_owned(container, "delivery.md", expected=sealed["container_identity"]) == text.encode(), "retained delivery description changed")
    else:
        write_owned(container, "delivery.md", text.encode(), expected=sealed["container_identity"])
    result = {"generation": snapshot.generation, "validator_version": 2, "validator_sha256": hashlib.sha256(validator_bytes).hexdigest(),
              "workspace_id": workspace["workspace_id"], "candidate_id": sealed["candidate_id"],
              "result_hash": accepted_hash, "candidate_root": sealed["candidate_root"],
              "baseline_root": workspace["baseline_root"], "baseline_manifest": str(Path(workspace["baseline_root"]).parent / "baseline-manifest.json"),
              "candidate_manifest": str(container / "manifest.json"), "delta": str(container / "delta.json"),
              "description": str(container / "delivery.md"), "changed_paths": sealed["changed_paths"]}
    payload = {"candidate_id": sealed["candidate_id"], "result_hash": accepted_hash}
    operation = operation_id(run_dir, "workspace-delivery", payload,
                             identifier=identifier or "workspace-delivery-" + sealed["candidate_id"])
    document = f"artifacts/workspaces/{workspace['workspace_id']}/delivery/{sealed['candidate_id']}.json"
    def publish(current):
        require((current.run_uuid, current.revision, current.generation) ==
                (snapshot.run_uuid, snapshot.revision, snapshot.generation), "delivery preconditions changed")
        current = replace(current, external_inputs=snapshot.external_inputs)
        registered, digest = acceptance(current)
        require(registered["seal"]["candidate_id"] == sealed["candidate_id"] and digest == accepted_hash,
                "acceptance changed before delivery publication")
        return {document: encode(result)}, result
    _transact(run_dir, operation, payload, publish, replay_guard=publish, lifecycle="delivery")
    return result
