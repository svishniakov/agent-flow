# Installing Agent Flow

Both distributions contain the same skill bytes. Python 3.11+ is required for
setup scripts. The standalone installer also needs Node.js and Skills CLI.

## Standalone skill

For a new global installation from GitHub:

```sh
npx skills add https://github.com/svishniakov/agent-flow -a codex -g &&
python3 ~/.agents/skills/agent-flow/scripts/check-agent-deps.py --post-install &&
python3 ~/.agents/skills/agent-flow/scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents
```

Verify `-skill.zip` against `SHA256SUMS.txt` and extract it into a permanent
directory. From your project root, install a project copy into an absent target:

```sh
test ! -e .agents/skills/agent-flow &&
test ! -L .agents/skills/agent-flow &&
npx skills add "/permanent/directory/agent-flow" --skill agent-flow --agent codex --copy --yes &&
AF_PACKAGE="$PWD/.agents/skills/agent-flow" &&
python3 "$AF_PACKAGE/scripts/check-agent-deps.py" --post-install &&
python3 "$AF_PACKAGE/scripts/sync-codex-agent-config.py" --output-dir "$PWD/.codex/agents" &&
python3 "$AF_PACKAGE/scripts/check-installed-package.py" --dependencies
```

The chain stops before the next step if a command fails. Existing installations
use the update procedure below. These commands leave global skills and roles
unchanged. Start a new task and invoke `Agent Flow` or select the installed
`$agent-flow` copy.

## Codex plugin

Verify the ZIP against its `SHA256SUMS.txt`, then extract `-codex-plugin.zip` into
a separate permanent directory:

```sh
codex plugin marketplace add "/permanent/directory/agent-flow"
AF_PACKAGE="$(codex plugin add agent-flow@agent-flow --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["installedPath"] + "/skills/agent-flow")')"
codex plugin list --json
```

The command extracts `installedPath` from Codex's JSON response and sets
`AF_PACKAGE` without hardcoding a versioned cache path.

```sh
python3 "$AF_PACKAGE/scripts/check-agent-deps.py" --post-install
python3 "$AF_PACKAGE/scripts/sync-codex-agent-config.py" --output-dir "$HOME/.codex/agents"
python3 "$AF_PACKAGE/scripts/check-installed-package.py" --dependencies
```

Start a new Desktop task, type `@`, select Agent Flow, and send the task. An
explicit plugin selection invokes its main skill. Plain messages without that
selection or a textual invocation must not start Agent Flow.

## Roles, active source, and updates

The readiness check also rejects explicit Agent Flow role declarations in
`config.toml`, including named profiles (`$CODEX_HOME/<name>.config.toml`)
and parent project directories. For
example, `[agents.qa-verifier]` with `config_file` selects another role source.
Back up these settings and remove duplicate declarations before checking again;
the script never edits them. Do not apply additional CLI `-c` role overrides
after the readiness check.

The check also reads `/etc/codex/config.toml` when present. It cannot attest
cloud-managed organization settings; when those apply, verify the effective
roles in the client before delegation.

The sync command stores previous file hashes in `.agent-flow-baseline.json`
beside the role TOML files. It checks every conflict before writing. Custom files,
edited managed files, malformed baselines, and symlinks cause an error with exact
paths. Preserve each conflicting file, compare it with the new role, and move
only files you explicitly choose to replace before retrying. Do not delete the
whole agents directory. Identical legacy files can be adopted without rewriting
their bytes; a managed header alone never grants overwrite permission.

`--check` is read-only. Repeating unchanged setup is safe. Project role overrides
must match the selected package. Extra skills and external services are separate
setup; the dependency checker reports concrete missing items without installing them.

With both forms installed, select the plugin through `@`, or disable the plugin
in the client and select the standalone `$agent-flow`. Confirm `package_root` in
the readiness output. Never select both forms in one request. If the client cannot
identify one source, disable the extra form before delegation.

### Updating a standalone archive installation

Skills CLI clears the target directory during reinstallation. Preserve the
entire old copy in a unique backup outside `.agents/skills` before updating.
The commands below apply to the `--copy` installation above and refuse symlinks.
Extract the new archive into a new permanent location, then run from the project:

```sh
test -d .agents/skills/agent-flow &&
test ! -L .agents/skills/agent-flow &&
AF_BACKUP="$(mktemp -d "$PWD/agent-flow-backup.XXXXXX")" &&
mv .agents/skills/agent-flow "$AF_BACKUP/agent-flow" &&
npx skills add "/new/permanent/directory/agent-flow" --skill agent-flow --agent codex --copy --yes
```

Continue only if this chain succeeds. Repeat package checks and project role
setup from the installation section. User files remain in `$AF_BACKUP/agent-flow`;
compare them before moving anything over the new package. Resolve role conflicts
before delegation. Keep the backup and old archive until verification succeeds
in a new task. The Git updater does not update a ZIP-installed copy.

### Updating the plugin

Before updating, locate the actual installed directory with
`codex plugin list --json`. Copy the entire cache directory for this plugin,
including its stored versions and local additions, to a separate backup outside
the Codex cache. Verify file contents,
symbolic links, and permissions. Also back up Codex settings and user roles.
Stop if the backup is incomplete: native installation replaces the plugin
directory and can remove older cached versions. The old ZIP does not contain
user additions.

Extract the new archive into a new permanent location, run
`codex plugin marketplace remove agent-flow`, then repeat the add and setup
commands above with the new location. The builder adds a deterministic
`+codex.<snapshot hash>` version suffix so Codex selects a new cache entry.
Create a new task after setup. Keep the previous archive and backup until
verification passes in that task. Compare local additions before transferring
them, without overwriting files from the new package.
The legacy Git updater refuses plugin installations.
`codex plugin remove agent-flow@agent-flow` removes the plugin; standalone skills
and user role files must remain intact.

## Build and validation

From the source Git checkout:

```sh
python3 scripts/check-all.py
python3 scripts/build-distributions.py --output "/directory/outside/source"
```

The explicit `skills/agent-flow/package-files.txt` inventory excludes local
additions, backups, caches, and historical implementation/ADR documents. Add new
runtime resources to this list before building. ZIP output is reproducible from
identical inputs. Check an extracted installation using its own script:

```sh
python3 "$AF_PACKAGE/scripts/check-installed-package.py" --package-only
```

The complete repository suite requires its source Git checkout. Archive checks
do not prove real QA/reviewer sessions or Desktop invocation. Stage 1 requires
all A1–A6; hooks, GitHub Actions, and publication are outside this stage.
