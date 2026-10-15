"""Selected-root topology and canonical context for the workspace contract."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import posixpath
import stat
import uuid

import task_workspace as tree
from journal_io import JournalSnapshot, operation_id, transact


def absolute(path):
    return Path(os.path.abspath(path))


def selected_paths(paths):
    values = [absolute(path) for path in paths]
    tree.require(len(values) == len(set(values)), "duplicate selected root")
    return values


def contains(path, roots):
    return any(path.is_relative_to(root) for root in roots)


def outer_roots(roots):
    return [root for root in roots if not any(root != other and root.is_relative_to(other) for other in roots)]


def relative(path, anchor):
    value = path.relative_to(anchor).as_posix()
    return "" if value == "." else value


def raw_target(target, parent):
    return Path(target) if posixpath.isabs(target) else parent / target


def relative_target(target, parent):
    """Relocate an absolute link without folding '..' across symbolic components."""
    left, right, shared = parent.parts, target.parts, 0
    while shared < min(len(left), len(right)) and left[shared] == right[shared]:
        shared += 1
    return "/".join([*[".."] * (len(left) - shared), *right[shared:]]) or "."


def symbolic_product_parent(path, roots):
    """Leave ordered product expressions for the captured graph to decide."""
    selected = [root for root in roots if path.is_relative_to(root)]
    if not selected:
        return False
    root = max(selected, key=lambda value: len(value.parts))
    parts = path.relative_to(root).parts
    if ".." not in parts or not stat.S_ISDIR(root.lstat().st_mode):
        return False
    opened = []
    with tree.root_descriptor(root) as descriptor:
        try:
            for name in parts:
                if name == "..":
                    return False
                before = os.stat(tree.basename(name), dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISLNK(before.st_mode):
                    return True
                if not stat.S_ISDIR(before.st_mode):
                    return False
                child = os.open(name, tree.DIRECTORY_FLAGS, dir_fd=descriptor)
                opened.append(child)
                tree.require(tree.identity(os.fstat(child)) == tree.identity(before), "product link component changed")
                descriptor = child
        except FileNotFoundError:
            return False
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)
    return False


def context_capture(path, product_roots=()):
    """Read only the explicitly selected file/subtree, without following links."""
    metadata = path.lstat()
    if stat.S_ISDIR(metadata.st_mode):
        manifest = tree.scan_tree(path, exclusions=[root for root in product_roots if root.is_relative_to(path)],
                                  expected_source=list(tree.identity(metadata)[:2]))
        return {"canonical_path": str(path), "kind": "directory", "identity": list(tree.identity(metadata)[:2]),
                "manifest": manifest, "digest": tree.manifest_digest(manifest)}
    tree.require(stat.S_ISREG(metadata.st_mode), f"context input must be a canonical file or directory: {path}")
    with tree.root_descriptor(path.parent) as descriptor:
        parent_identity = list(tree.identity(os.fstat(descriptor))[:2])
    data = tree.read_owned(path.parent, path.name, expected=parent_identity,
                           expected_file=tree.stability(metadata))
    tree.require(tree.stability(path.lstat()) == tree.stability(metadata), f"context file changed during capture: {path}")
    manifest = {"type": "file", "mode": stat.S_IMODE(metadata.st_mode), "sha256": hashlib.sha256(data).hexdigest()}
    return {"canonical_path": str(path), "kind": "file", "identity": list(tree.identity(metadata)[:2]),
            "parent_identity": parent_identity, "manifest": manifest, "digest": tree.manifest_digest(manifest)}


def verify_context(workspace, *, inputs=None):
    from validation_inputs import captured
    for expected in workspace["task_scope"]["context_roots"]:
        path = Path(expected["canonical_path"])
        products = [Path(root["source"]) for root in workspace["task_scope"]["product_roots"]]
        actual = captured(inputs, "workspace-context:" + workspace["task_scope"]["digest"] + ":" + str(path),
                          lambda: context_capture(path, products))
        tree.require(all(actual.get(key) == expected.get(key) for key in
                         ("canonical_path", "kind", "manifest", "digest")),
                     f"canonical context changed: {path}; prepare a new workspace revision and obtain current QA")
    for alias in workspace["task_scope"]["context_exclusions"]:
        for expected in [alias, *alias.get("resolution_chain", [])]:
            path = Path(expected["source"])
            def read():
                with tree.root_descriptor(path.parent) as parent:
                    before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                    tree.require(stat.S_ISLNK(before.st_mode), f"context link chain replaced: {path}")
                    target = os.readlink(path.name, dir_fd=parent)
                    tree.require(tree.stability(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == tree.stability(before),
                                 f"context link changed during verification: {path}")
                    return {"source": str(path), "original_target": target, "descriptor": list(tree.stability(before))}
            actual = captured(inputs, "workspace-context-link:" + workspace["task_scope"]["digest"] + ":" + str(path), read)
            tree.require(actual["source"] == expected["source"] and
                         actual["original_target"] == expected["original_target"] and
                         stat.S_IMODE(actual["descriptor"][2]) == stat.S_IMODE(expected["descriptor"][2]),
                         f"context link chain changed: {path}; prepare a new workspace revision")


def context_target(path, context):
    for item in context:
        selected = Path(item["canonical_path"])
        if path == selected:
            return True
        if item["kind"] == "directory" and path.is_relative_to(selected):
            entry = item["manifest"]["entries"].get(path.relative_to(selected).as_posix())
            if entry is not None and entry["type"] in {"file", "directory"}:
                return True
    return False


def resolve_context(path, context):
    """Resolve named link descriptors, opening no final content or sibling trees."""
    if not context:
        return None
    pending, resolved, chain, links = list(path.parts[1:]), [], [], []
    descriptor = os.open("/", tree.DIRECTORY_FLAGS)
    def release():
        for parent, name, expected in chain:
            tree.require(tree.identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == expected,
                         f"context chain directory changed: {name}")
    try:
        while pending:
            name = pending.pop(0)
            if name in {"", "."}:
                continue
            if name == "..":
                if chain:
                    parent, selected, expected = chain.pop()
                    tree.require(tree.identity(os.stat(selected, dir_fd=parent, follow_symlinks=False)) == expected,
                                 f"context chain directory changed: {selected}")
                    os.close(descriptor)
                    descriptor = parent
                    resolved.pop()
                continue
            tree.basename(name)
            parent_before = tree.stability(os.fstat(descriptor))
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            location = Path("/", *resolved, name)
            if stat.S_ISLNK(before.st_mode):
                tree.require(len(links) < 40, f"unsupported context symlink cycle: {path}")
                target = os.readlink(name, dir_fd=descriptor)
                tree.require(tree.stability(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) == tree.stability(before) and
                             tree.stability(os.fstat(descriptor)) == parent_before, f"context symlink changed during resolution: {location}")
                links.append({"source": str(location), "original_target": target, "descriptor": list(tree.stability(before))})
                expanded = Path(target)
                if expanded.is_absolute():
                    pending = list(expanded.parts[1:]) + pending
                    release()
                    os.close(descriptor)
                    descriptor = None
                    for parent, _, _ in reversed(chain):
                        os.close(parent)
                    chain, resolved = [], []
                    descriptor = os.open("/", tree.DIRECTORY_FLAGS)
                else:
                    pending = list(expanded.parts) + pending
                continue
            if not pending:
                release()
                return (location, links) if context_target(location, context) else None
            if not stat.S_ISDIR(before.st_mode):
                return None
            child = os.open(name, tree.DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                tree.require(tree.identity(os.fstat(child)) == tree.identity(before) and
                             tree.stability(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) == tree.stability(before),
                             f"context chain directory changed before open: {location}")
            except BaseException:
                os.close(child)
                raise
            chain.append((descriptor, name, tree.identity(before)))
            descriptor = child
            resolved.append(name)
        return None
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise tree.WorkspaceError(f"cannot inspect declared context link chain: {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for parent, _, _ in reversed(chain):
            os.close(parent)


def link_policy(source, anchor, roots, context, aliases, normalized):
    def classify(name, target, metadata):
        location = source / name
        canonical = raw_target(target, location.parent)
        qualified = relative(location, anchor)
        if contains(absolute(canonical), roots) or symbolic_product_parent(canonical, roots):
            if posixpath.isabs(target):
                rewritten = relative_target(canonical, location.parent)
                normalized[qualified] = {"source": str(location), "original_target": target, "target": rewritten,
                                         "canonical_path": str(canonical)}
                return rewritten
            return target
        resolution = (canonical, []) if context_target(canonical, context) else resolve_context(canonical, context)
        if resolution is not None:
            canonical, chain = resolution
            alias = {"source": str(location), "relative_path": qualified, "original_target": target,
                     "canonical_path": str(canonical), "descriptor": list(tree.stability(metadata))}
            if chain:
                alias["resolution_chain"] = chain
            tree.require(qualified not in aliases or aliases[qualified] == alias,
                         f"context alias changed during capture: {location}")
            aliases[qualified] = alias
            return None
        raise tree.WorkspaceError(f"undeclared symlink target: {location} -> {target!r} ({canonical}); "
                                  "declare its product dependency with --source or its instructions with --context-root")
    return classify


def validate_graph(entries, roots, anchor, aliases):
    """Resolve captured link strings only; never access a target through the OS."""
    tree.validate_link_graph(entries)
    prefixes, targets = [relative(root, anchor) for root in roots], {}
    for name, entry in entries.items():
        if entry["type"] != "symlink":
            continue
        pending, resolved, count = name.split("/"), [], 0
        while pending:
            part = pending.pop(0)
            if part in {"", "."}:
                continue
            if part == "..":
                tree.require(bool(resolved), f"escaping symlink chain: {name}")
                resolved.pop()
                continue
            current = "/".join([*resolved, part])
            tree.require(current not in aliases, f"product link uses excluded context alias: {name} -> {current}; declare a product dependency")
            linked = entries.get(current)
            if linked is not None and linked["type"] == "symlink":
                count += 1
                tree.require(count <= 40, f"unsupported symlink cycle: {name}")
                pending = linked["target"].split("/") + pending
            else:
                resolved.append(part)
        final = "/".join(resolved)
        tree.require(any(not prefix or final == prefix or final.startswith(prefix + "/") for prefix in prefixes),
                     f"symlink chain leaves selected roots: {name}; declare its product dependency")
        targets[name] = final
    return targets


def directories(destination, path, *, expected):
    current = destination
    opened = []
    with tree.root_descriptor(destination, destination=True, expected=expected) as root:
        descriptor = root
        try:
            for name in path.parts:
                if name == ".":
                    continue
                name = tree.basename(name)
                try:
                    before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    os.mkdir(name, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                    before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                tree.require(stat.S_ISDIR(before.st_mode), "topology parent must be a real directory")
                child = os.open(name, tree.DIRECTORY_FLAGS, dir_fd=descriptor)
                opened.append((descriptor, name, tree.identity(before), child))
                tree.require(tree.identity(os.fstat(child)) == tree.identity(before) ==
                             tree.identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)),
                             "topology directory changed before open")
                descriptor = child
                current = current / name
            for parent, name, identity, child in opened:
                tree.require(tree.identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity,
                             "topology directory changed during creation")
            selected_identity = list(tree.identity(os.fstat(descriptor))[:2])
        finally:
            for _, _, _, child in reversed(opened):
                os.close(child)
    return current, selected_identity


def scoped_inventory(destination, exclusions, roots, anchor, aliases, *, barrier=None, expected=None):
    manifest = tree.scan_tree(destination, exclusions=[destination / name for name in exclusions],
                              barrier=barrier, expected_source=expected)
    validate_graph(manifest["entries"], roots, anchor, aliases)
    for name, entry in manifest["entries"].items():
        original = anchor / name
        tree.require(contains(original, roots) or entry["type"] == "directory" and
                     any(root.is_relative_to(original) for root in roots),
                     f"workspace entry outside selected product roots: {name}")
    for name in aliases:
        path = destination / name
        try:
            with tree.root_descriptor(path.parent) as parent:
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            continue
        tree.require(False, f"context alias must be absent from executable workspace: {name}")
    return manifest


def stopped_workers(snapshot):
    if not snapshot.exists("delegation-summary.json"):
        return
    summary = json.loads(snapshot.read_bytes("delegation-summary.json"))
    authors = set(summary.get("verification", {}).get("author_thread_ids", []))
    lanes = json.loads(snapshot.read_bytes("lane-map.json")) if snapshot.exists("lane-map.json") else {}
    planned = {lane.get("id", lane.get("lane_id")): lane for lane in lanes.get("lanes", [])}
    active = [record.get("lane_id") for record in summary.get("subagents", []) + summary.get("role_lanes", [])
              if record.get("status") in {"active", "spawned"} and
              (record.get("codex_thread_id") in authors or
               planned.get(record.get("lane_id"), {}).get("type") in {"implementation", "integration"})]
    tree.require(not active, "stop current product authors before workspace scope update: " + ", ".join(active))


def previous_roots(workspace):
    if workspace.get("version") == 2:
        return workspace["task_scope"]["product_roots"]
    return [{"source": workspace["source"], "relative_path": "", "source_metadata": workspace["source_metadata"]}]


def discover_source(path, *, primary=False, barrier=None):
    before = path.lstat()
    if stat.S_ISDIR(before.st_mode):
        with tree.root_descriptor(path) as descriptor:
            names = tree.directory_names(descriptor)
        if primary or ".git" in names:
            return tree.discover_git(path, barrier=barrier)
        return {"root": str(path), "root_identity": list(tree.identity(before)[:2]), "kind": "directory"}
    tree.require(not primary and stat.S_ISREG(before.st_mode), f"product dependency must be a real file or directory: {path}")
    with tree.root_descriptor(path.parent) as descriptor:
        parent_identity = list(tree.identity(os.fstat(descriptor))[:2])
    return {"root": str(path), "root_identity": list(tree.identity(before)[:2]), "parent_identity": parent_identity, "kind": "file"}


def copy_file(source, destination, *, expected=None, expected_parent=None, expected_destination=None, barrier=None):
    before = source.lstat()
    tree.require(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) & 0o444,
                 f"product dependency must be a readable regular file: {source}")
    tree.require(expected is None or list(tree.identity(before)[:2]) == expected, f"selected dependency identity changed: {source}")
    data = tree.read_owned(source.parent, source.name, expected=expected_parent,
                           expected_file=tree.stability(before), barrier=barrier)
    tree.require(tree.stability(source.lstat()) == tree.stability(before), f"product dependency changed during capture: {source}")
    if destination is not None:
        tree.require(expected_destination is not None, "copied dependency requires a pinned destination parent")
        created = tree.write_owned(destination.parent, destination.name, data, expected=expected_destination)
        with tree.root_descriptor(destination.parent, destination=True, expected=expected_destination) as parent:
            descriptor = os.open(destination.name, tree.FILE_FLAGS, dir_fd=parent)
            try:
                tree.require(list(tree.identity(os.fstat(descriptor))[:2]) == created and os.fstat(descriptor).st_size == len(data),
                             "copied dependency identity changed")
                os.fchmod(descriptor, stat.S_IMODE(before.st_mode))
                tree.sync_file(descriptor)
            finally:
                os.close(descriptor)
    return {"type": "file", "mode": stat.S_IMODE(before.st_mode), "sha256": hashlib.sha256(data).hexdigest()}


def prepare_scope(run_dir, sources, destination, *, context_roots=(), identifier=None, metadata_scope=None,
                  session_source=None, barrier=None):
    snapshot = JournalSnapshot.open(run_dir)
    sources, contexts, destination = selected_paths(sources), selected_paths(context_roots), absolute(destination)
    payload = {"sources": [str(path) for path in sources], "context_roots": [str(path) for path in contexts],
               "destination": str(destination), "metadata_scope": metadata_scope}
    operation = identifier or str(uuid.uuid4())
    if not operation.startswith("workspace-prepare-"):
        operation = "workspace-prepare-" + operation
    if identifier:
        operation_id(run_dir, "workspace-prepare", payload, identifier=operation)
        receipt = snapshot.operation_receipt(operation)
        if receipt is not None:
            retained = tree.checked_document(snapshot, receipt["result"])
            tree.verify_baseline(retained)
            return retained
    tree.require(snapshot.durable and not snapshot.closed,
                 "workspace prepare requires an open durable journal")
    existing = tree.registered_workspace(snapshot)
    if existing is not None:
        tree.require(existing["source"] == str(sources[0]) and existing["bundle"] == str(destination),
                     "primary source and workspace bundle cannot change")
        old_roots = previous_roots(existing)
        tree.require({root["source"] for root in old_roots} <= set(payload["sources"]), "workspace update cannot remove product roots")
        old_contexts = [root["canonical_path"] for root in existing.get("task_scope", {}).get("context_roots", [])]
        tree.require(set(old_contexts) <= set(payload["context_roots"]), "workspace update cannot remove context roots")
        old_request = existing.get("metadata_scope")
        tree.require((old_request["request"] if old_request else None) == metadata_scope, "registered metadata scope cannot change")
        tree.verify_baseline(existing)
        retained_working_identity = tree.observed_identity(existing["working_root"])
        retained_working = tree.workspace_inventory(Path(existing["working_root"]), existing["baseline_manifest"],
                                                  retained_working_identity, workspace=existing)
        if not identifier:
            tree.require(existing.get("prepare_request") == payload, "scope updates require a fresh --operation-id")
            tree.current_scope_digest(existing)
            return existing
        tree.require(not snapshot.archives, "recovered workspace identity is frozen; start a new run to change scope")
        stopped_workers(snapshot)
    else:
        old_roots = []
        tree.require(not snapshot.archives, "recovered workspace identity is frozen; start a new run to prepare workspace")
    from journal_lifecycle import capture_validation, freeze_validation
    snapshot, captured_source = capture_validation(snapshot, session_source=session_source)
    operation_id(run_dir, "workspace-prepare", payload, identifier=operation)
    anchor = Path(os.path.commonpath(sources))
    context = [context_capture(path, sources) for path in contexts if not contains(path, sources)]
    metadata = {source: discover_source(source, primary=source == sources[0], barrier=barrier)
                for source in sources}
    binding = existing.get("metadata_scope") if existing else tree.capture_metadata_scope(
        snapshot, sources[0], metadata[sources[0]], metadata_scope, captured_source)
    freeze_validation(snapshot, captured_source)
    workspace_id = existing["workspace_id"] if existing else str(uuid.uuid5(uuid.NAMESPACE_URL, snapshot.run_uuid + str(sources[0]) + str(destination)))
    owner = {"run_uuid": snapshot.run_uuid, "workspace_id": workspace_id}
    if destination.exists():
        owner["root_identity"] = list(tree.identity(destination.lstat())[:2])
        retained_owner = json.loads(tree.read_owned(destination, "owner.json", expected=owner["root_identity"]))
        tree.require({key: retained_owner.get(key) for key in ("run_uuid", "workspace_id")} ==
                     {key: owner[key] for key in ("run_uuid", "workspace_id")},
                     "destination is not owned by this workspace")
    else:
        owner["root_identity"] = tree.create_directory(destination.parent, destination.name)
        tree.write_owned(destination, "owner.json", tree.encode(owner), expected=owner["root_identity"])
    attempt = destination / ("prepare-" + str(uuid.uuid4()))
    attempt_identity = tree.create_directory(destination, attempt.name, expected=owner["root_identity"])
    baseline, working = attempt / "baseline", attempt / "working"
    baseline_identity = tree.create_directory(attempt, "baseline", expected=attempt_identity)
    working_identity = tree.create_directory(attempt, "working", expected=attempt_identity)
    old_anchor = Path(existing["task_scope"]["anchor"]) if existing and existing.get("version") == 2 else sources[0]
    aliases, normalized, exclusions, fresh = {}, {}, set(), []
    expected_baseline, expected_working = {}, {}
    for source in sources:
        path = Path(relative(source, anchor))
        directory = path.parent if metadata[source].get("kind") == "file" else path
        for parent in [*reversed(directory.parents), directory]:
            if parent.as_posix() != ".":
                expected_baseline[parent.as_posix()] = {"type": "directory", "mode": 0o700}
                expected_working[parent.as_posix()] = {"type": "directory", "mode": 0o700}
    if existing and existing.get("version") == 2:
        for field, output in (("context_exclusions", aliases), ("link_provenance", normalized)):
            for value in existing["task_scope"][field]:
                remapped = relative(Path(value["source"]), anchor)
                output[remapped] = {**value, **({"relative_path": remapped} if field == "context_exclusions" else {})}
        for alias in aliases.values():
            if contains(Path(alias["canonical_path"]), sources):
                continue
            target = raw_target(alias["original_target"], Path(alias["source"]).parent)
            if contains(absolute(target), sources):
                alias["canonical_path"] = str(target)
                continue
            resolution = (target, []) if context_target(target, context) else resolve_context(target, context)
            tree.require(resolution is not None, f"context alias now leaves declared context: {alias['source']}; declare its canonical context root")
            target, chain = resolution
            alias["canonical_path"] = str(target)
            alias.pop("resolution_chain", None)
            if chain:
                alias["resolution_chain"] = chain
    old_sources = [Path(root["source"]) for root in old_roots]
    for root in outer_roots(old_sources):
        old_relative, new_relative = relative(root, old_anchor), relative(root, anchor)
        for origin, target, expected_entries in ((Path(existing["baseline_root"]), baseline, expected_baseline),
                                                  (Path(existing["working_root"]), working, expected_working)):
            old_tree = origin / old_relative
            if metadata[root].get("kind") == "file":
                parent, parent_identity = directories(target, Path(new_relative).parent, expected=baseline_identity if target == baseline else working_identity)
                output = parent / Path(new_relative).name
                expected_entries[new_relative] = copy_file(old_tree, output, expected_destination=parent_identity, barrier=barrier)
                continue
            retained_exclusions = [origin / name for name in existing["baseline_manifest"]["exclusions"]]
            output, output_identity = directories(target, Path(new_relative), expected=baseline_identity if target == baseline else working_identity)
            copied = tree.scan_tree(old_tree, exclusions=retained_exclusions, copy_to=output, barrier=barrier,
                                    link_policy=lambda name, target, metadata: target, check_link_graph=False,
                                    expected_destination=output_identity)
            expected_entries.update({(new_relative + "/" if new_relative else "") + name: entry
                                     for name, entry in copied["entries"].items()})
        for name in existing["baseline_manifest"]["exclusions"]:
            original = old_anchor / name
            if original.is_relative_to(root):
                exclusions.add(relative(original, anchor))
    for source in outer_roots(sources):
        if contains(source, old_sources):
            continue
        if metadata[source].get("kind") == "file":
            name = relative(source, anchor)
            parent, parent_identity = directories(baseline, Path(name).parent, expected=baseline_identity)
            target = parent / Path(name).name
            entry = copy_file(source, target, expected=metadata[source]["root_identity"],
                              expected_parent=metadata[source]["parent_identity"], expected_destination=parent_identity, barrier=barrier)
            output_parent, output_identity = directories(working, Path(name).parent, expected=working_identity)
            output = output_parent / Path(name).name
            tree.same_tree(entry, copy_file(target, output, expected_parent=parent_identity,
                                           expected_destination=output_identity, barrier=barrier), "working dependency differs from baseline")
            expected_baseline[name] = expected_working[name] = entry
            fresh.append((source, None, entry, None))
            continue
        # A newly selected parent is captured around retained children; their bytes win.
        target, target_identity = directories(baseline, Path(relative(source, anchor)), expected=baseline_identity)
        omit = set(tree.workspace_exclusions(source, snapshot, destination, metadata[source], binding)) if metadata[source].get("git_dir") else {
            path for path in (snapshot.artifact_root, destination) if path.is_relative_to(source) and path != source}
        omit.update(root for root in outer_roots(old_sources) if root.is_relative_to(source))
        omit.update(root / ".git" for root in sources if root.is_relative_to(source) and metadata[root].get("git_dir"))
        for root in sources:
            for git_path in (metadata[root].get("git_dir"), metadata[root].get("common_dir")):
                if git_path is not None and Path(git_path).is_relative_to(source):
                    omit.add(Path(git_path))
        tree.notify(barrier, "prepare-before-copy", source=str(source), attempt=str(attempt))
        policy = link_policy(source, anchor, sources, context, aliases, normalized)
        first = tree.scan_tree(source, exclusions=omit, copy_to=target, barrier=barrier,
                               expected_source=metadata[source]["root_identity"], link_policy=policy, check_link_graph=False,
                               merge_directories=True, expected_destination=target_identity)
        again = tree.scan_tree(source, exclusions=omit, barrier=barrier, expected_source=metadata[source]["root_identity"],
                               link_policy=policy, check_link_graph=False)
        tree.same_tree(first, again, "source changed during scope prepare")
        prefix = relative(source, anchor)
        captured_entries = {(prefix + "/" if prefix else "") + name: entry for name, entry in first["entries"].items()}
        expected_baseline.update(captured_entries)
        expected_working.update(captured_entries)
        fresh.append((source, omit, first, policy))
        exclusions.update(relative(source / name, anchor) for name in first["exclusions"]
                          if source / name not in old_sources)
        # Copy only fresh bytes here; the retained working children are already present.
        fresh_omit = [baseline / relative(root, anchor) for root in outer_roots(old_sources) if root.is_relative_to(source)]
        output, output_identity = directories(working, Path(relative(source, anchor)), expected=working_identity)
        tree.scan_tree(target, exclusions=fresh_omit, copy_to=output, barrier=barrier,
                       link_policy=lambda name, target, metadata: target, check_link_graph=False, merge_directories=True,
                       expected_source=target_identity, expected_destination=output_identity)
    # Every selected repository gets its own private Git capture. Existing working Git
    # state comes from the retained working tree, never from a changed source repository.
    for source in sources:
        if not metadata[source].get("git_dir"):
            continue
        path = relative(source, anchor)
        exclusions.add((path + "/" if path else "") + ".git")
        output, output_identity = directories(working, Path(path), expected=working_identity)
        git_destination = output / ".git"
        git_destination_identity = tree.create_directory(output, ".git", expected=output_identity)
        old = next((root for root in old_roots if root["source"] == str(source)), None)
        if old is not None:
            tree.scan_tree(Path(existing["working_root"]) / old["relative_path"] / ".git", copy_to=git_destination,
                           barrier=barrier, allow_symlinks=False, expected_destination=git_destination_identity)
        else:
            with tree.git_scratch() as scratch:
                captured = tree.capture_git(source, scratch, barrier=barrier)
                local_omit = [baseline / name for name in exclusions]
                tree.scan_tree(baseline / path, exclusions=local_omit, copy_to=scratch, barrier=barrier,
                               link_policy=lambda name, target, metadata: target, check_link_graph=False)
                declared_gitlinks = {relative(root, source) for root in sources
                                     if root != source and root.is_relative_to(source) and metadata[root].get("git_dir")}
                metadata[source] = tree.query_git(scratch, captured, full=True, declared_gitlinks=declared_gitlinks)
                tree.scan_tree(scratch / ".git", copy_to=git_destination, barrier=barrier, allow_symlinks=False,
                               expected_destination=git_destination_identity)
                with tree.git_scratch() as check:
                    tree.require(tree.capture_git(source, check, barrier=barrier) == captured, "source Git changed during prepare")
    for source, omit, first, policy in fresh:
        final = copy_file(source, None, expected=metadata[source]["root_identity"],
                          expected_parent=metadata[source]["parent_identity"], barrier=barrier) if omit is None else tree.scan_tree(
            source, exclusions=omit, barrier=barrier, expected_source=metadata[source]["root_identity"],
            link_policy=policy, check_link_graph=False)
        tree.same_tree(first, final, "source changed before workspace publication")
    # Context aliases are exact omitted entries, including retained aliases remapped
    # after anchor expansion. Product classification takes precedence.
    for name, alias in list(aliases.items()):
        if contains(absolute(alias["canonical_path"]), sources):
            target = relative_target(Path(alias["canonical_path"]), Path(alias["source"]).parent)
            for root in (baseline, working):
                parent, parent_identity = directories(root, Path(name).parent, expected=baseline_identity if root == baseline else working_identity)
                with tree.root_descriptor(parent, destination=True, expected=parent_identity) as descriptor:
                    os.symlink(target, Path(name).name, dir_fd=descriptor)
                    os.fsync(descriptor)
            exclusions.discard(name)
            expected_baseline[name] = expected_working[name] = {"type": "symlink", "mode": stat.S_IMODE(alias["descriptor"][2]), "target": target}
            if target != alias["original_target"]:
                normalized[name] = {"source": alias["source"], "original_target": alias["original_target"],
                                    "target": target, "canonical_path": alias["canonical_path"]}
                if alias.get("resolution_chain"):
                    normalized[name]["resolution_chain"] = alias["resolution_chain"]
            del aliases[name]
        else:
            exclusions.add(name)
    # Excluded retained children are copied, so only metadata/context exceptions remain.
    exclusions.difference_update(relative(root, anchor) for root in old_sources)
    baseline_manifest = scoped_inventory(baseline, exclusions, sources, anchor, aliases, barrier=barrier, expected=baseline_identity)
    working_manifest = scoped_inventory(working, exclusions, sources, anchor, aliases, barrier=barrier, expected=working_identity)
    tree.same_tree(expected_baseline, baseline_manifest["entries"], "baseline differs from selected full source inventories")
    tree.same_tree(expected_working, working_manifest["entries"], "working copy differs from selected retained and new inventories")
    resolved_targets = validate_graph(baseline_manifest["entries"], sources, anchor, aliases)
    for name, provenance in normalized.items():
        provenance["canonical_path"] = str(anchor / resolved_targets[name])
    if existing is not None:
        tree.verify_baseline(existing, barrier=barrier)
        tree.same_tree(retained_working, tree.workspace_inventory(Path(existing["working_root"]), existing["baseline_manifest"],
                                                                 retained_working_identity, workspace=existing, barrier=barrier),
                       "retained working tree changed during scope repack")
        for root in outer_roots(old_sources):
            old_entries = existing["baseline_manifest"]["entries"]
            for name, entry in old_entries.items():
                original = old_anchor / name
                if original.is_relative_to(root):
                    tree.require(baseline_manifest["entries"].get(relative(original, anchor)) == entry,
                                 f"retained baseline content changed during repack: {name}")
    scope = {"version": 2, "anchor": str(anchor),
             "product_roots": [{"root_id": hashlib.sha256(str(source).encode()).hexdigest()[:16], "source": str(source),
                                "relative_path": relative(source, anchor), "source_metadata": metadata[source]} for source in sources],
             "context_roots": context, "context_exclusions": [aliases[name] for name in sorted(aliases)],
             "link_provenance": [normalized[name] for name in sorted(normalized)]}
    scope["digest"] = tree.manifest_digest(scope)
    workspace = {"version": 2, "workspace_revision": existing.get("workspace_revision", 0) + 1 if existing else 1,
                 "workspace_id": workspace_id, "run_uuid": snapshot.run_uuid, "source": str(sources[0]),
                 "bundle": str(destination), "bundle_identity": owner["root_identity"],
                 "baseline_root": str(baseline), "working_root": str(working), "baseline_identity": baseline_identity,
                 "working_identity": working_identity, "attempt_identity": attempt_identity,
                 "source_metadata": metadata[sources[0]], "metadata_scope": binding, "task_scope": scope,
                 "prepare_request": payload, "baseline_manifest": baseline_manifest,
                 "baseline_digest": tree.manifest_digest(baseline_manifest)}
    verify_context(workspace)
    tree.write_owned(attempt, "baseline-manifest.json", tree.encode(baseline_manifest), expected=attempt_identity)
    document = f"artifacts/workspaces/{workspace_id}/revisions/{workspace['workspace_revision']}.json"
    raw = tree.encode(workspace)
    tree.notify(barrier, "prepare-before-publication", workspace=workspace)
    def publish(current):
        tree.require(current.revision == snapshot.revision, "workspace prepare preconditions changed")
        current = replace(current, external_inputs=snapshot.external_inputs)
        if existing is not None:
            stopped_workers(current)
        verify_context(workspace)
        return {document: raw}, {"document": document, "sha256": hashlib.sha256(raw).hexdigest()}
    transact(run_dir, operation, payload, publish)
    tree.notify(barrier, "prepare-after-publication", workspace=workspace)
    return workspace
