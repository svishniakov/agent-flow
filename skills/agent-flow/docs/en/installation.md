# Installing Agent Flow

Document status: done

Choose the skill installed through npx or the Codex plugin ZIP. Python 3.11+ is required for
setup scripts. Node.js and Skills CLI are needed only for `npx skills add`.

## Standalone skill

For a new global installation from GitHub:

```sh
npx skills add https://github.com/svishniakov/agent-flow -a codex -g &&
python3 ~/.agents/skills/agent-flow/scripts/check-agent-deps.py --post-install &&
python3 ~/.agents/skills/agent-flow/scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents
```

Start a new Codex task after setup. To install only in one project, omit `-g`
and synchronize roles into that project's `.codex/agents` directory.

## Codex plugin

Download `agent-flow-X.Y.Z-codex-plugin.zip` from the
[latest release](https://github.com/svishniakov/agent-flow/releases/latest).
It is the only uploaded release file. The two automatic Source code links
contain repository snapshots. Build JSON and SHA256SUMS stay inside Actions.

Compare the ZIP's SHA-256 with the digest in the release notes or beside the
asset on GitHub:

```sh
shasum -a 256 agent-flow-X.Y.Z-codex-plugin.zip
```

Extract it into a separate permanent directory. The extracted
`agent-flow/agent-flow-build.json` records `version`, `release_tag`, and
`commit_sha`; compare them with the release and its tag. Then install:

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

### Updating the skill installed through npx

Before updating, back up your entire installed skill, Codex settings and roles
outside their installation directories. Skills CLI can replace the target;
keep local additions in the backup and compare them before restoring any files.
Repeat the npx installation and role setup above, resolve reported conflicts,
and start a new task after the package check succeeds.

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
commands above with the new location. Local builds use a deterministic
`+codex.<snapshot hash>` suffix; GitHub builds use `X.Y.Z-dev.RUN.ATTEMPT`.
A new version selects a new Codex cache entry.
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
do not prove real QA/reviewer sessions or Desktop invocation. For development builds, use the Actions instructions below.

## Development builds after push

Open [GitHub Actions](https://github.com/svishniakov/agent-flow/actions/workflows/push-build.yml),
select a successful `Push checks and distributions` run, and confirm its commit SHA.
Download `agent-flow-<version>-<full SHA>-run<RUN>-attempt<ATTEMPT>` from Artifacts.
Retention is 14 days, subject to repository policy. Failed checks do not publish
installable archives.

Extract the downloaded artifact into an empty directory. It contains two ZIPs,
build metadata JSON, and a file ending in `-SHA256SUMS.txt`. Verify them there:

```sh
shasum -a 256 -c *-SHA256SUMS.txt
```

Stop on a checksum mismatch. Confirm the metadata commit SHA and
`X.Y.Z-dev.RUN.ATTEMPT` version against the selected run, then extract the chosen
ZIP into a new permanent directory and follow the installation instructions below.
Both archives contain the same shared package.

These are prerelease builds, lower than stable `X.Y.Z` under SemVer. Installing one
over a stable version requires explicit reinstallation from the chosen archive;
automatic stable-to-prerelease updates are not promised. For each subsequent build,
follow the update procedure: replace the plugin marketplace source, synchronize
roles, verify the installed package, and start a new Codex task.
