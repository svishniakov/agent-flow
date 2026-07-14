# Implementation Plan: CodeGraph v1

## Status

Approved for implementation in branch `codex/codegraph-v1`.

## Goal

Добавить в AgentFlow локальный CodeGraph, который строит машинную карту кода и помогает gate-проверкам принимать решения на фактах, а не на повторном чтении файлов через LLM.

Главный продуктовый metric v1: Dependency Gate и Boundary Gate точнее определяют пересечения и нарушения границ.

## Inputs

Принятые решения:

- языки v1: Python + TypeScript/JavaScript;
- индексатор: Python CLI;
- индекс: `.agent-work/codegraph/codegraph.sqlite`;
- config: `.agent-work/codegraph/config.json`;
- CLI entrypoint: `python3 scripts/codegraph.py <subcommand>`;
- output: JSON only;
- stale behavior: auto-reindex перед query;
- storage: typed tables + generic edges;
- dirty worktree: индексировать рабочее дерево как есть;
- tests mapping: naming conventions + imports from tests to source;
- public subcommands: `index`, `status`, `impact`, `tests`, `context`, `boundary`, `deps`, `doctor`;
- v1 non-goal: security/dataflow analyzer.

## Deliverables

- `requirements-codegraph.txt`;
- `scripts/codegraph.py`;
- tests for CLI, schema, adapters, freshness and queries;
- docs updates that explain how AgentFlow should use CodeGraph;
- `.gitignore` update for `.agent-work/`;
- optional small fixtures under `testdata/codegraph/`.

## Architecture

### Public entrypoint

```bash
python3 scripts/codegraph.py index
python3 scripts/codegraph.py status
python3 scripts/codegraph.py impact --target scripts/validate-run.py
python3 scripts/codegraph.py tests --target scripts/validate-run.py
python3 scripts/codegraph.py context --task "tighten boundary evidence validation"
python3 scripts/codegraph.py boundary --changed-paths checks/changed-paths.json --allowed "scripts/**" --forbidden "agents/**"
python3 scripts/codegraph.py deps --active-task .agent-work/tasks/todo.md --new-task /tmp/task.txt
python3 scripts/codegraph.py doctor
```

Все команды возвращают JSON. Ошибки тоже возвращаются JSON object в stdout и завершаются non-zero exit code.

### Internal modules

Публично остаётся один command file: `scripts/codegraph.py`. Если файл начинает распухать, допускается вынести внутренние модули в `scripts/codegraph_lib/`, но без изменения CLI.

Рекомендуемая внутренняя структура:

- `RepoScanner`: находит relevant files;
- `Config`: создаёт и читает `.agent-work/codegraph/config.json`;
- `Storage`: SQLite connection, schema, migrations;
- `Indexer`: orchestrates scan, parse, normalize, write;
- `PythonAdapter`: symbols/imports/calls/type usages/tests;
- `TsJsAdapter`: symbols/imports/exports/calls/type usages/tests;
- `Freshness`: file hashes, config hash, schema version, previous error;
- `Queries`: impact/tests/context/boundary/deps;
- `JsonOutput`: success/error envelope.

### Data flow

```text
repo files
  -> RepoScanner
  -> language adapter
  -> normalized facts
  -> SQLite typed tables
  -> generic edges
  -> query JSON
  -> AgentFlow gates/context
```

## Storage schema

Use SQLite with explicit schema version.

### `schema_meta`

Stores schema and tool metadata.

Columns:

- `key text primary key`;
- `value text not null`.

Required keys:

- `schema_version`;
- `created_at`;
- `updated_at`;
- `tool_version`.

### `snapshots`

One row per successful index pass.

Columns:

- `id integer primary key`;
- `repo_root text not null`;
- `head_commit text`;
- `git_status_hash text not null`;
- `config_hash text not null`;
- `started_at text not null`;
- `finished_at text`;
- `status text not null`;
- `error_json text`.

### `files`

Current indexed file set.

Columns:

- `id integer primary key`;
- `path text not null unique`;
- `language text not null`;
- `content_hash text not null`;
- `size_bytes integer not null`;
- `mtime_ns integer`;
- `tracked integer not null`;
- `last_indexed_at text not null`.

### `symbols`

Definitions found in files.

Columns:

- `id integer primary key`;
- `file_id integer not null`;
- `language text not null`;
- `kind text not null`;
- `name text not null`;
- `qualname text not null`;
- `start_line integer not null`;
- `end_line integer not null`;
- `signature text`;
- `exported integer`;
- `metadata_json text not null default '{}'`.

Indexes:

- `(file_id, kind)`;
- `(qualname)`;
- `(name)`.

### `imports`

Import/export facts.

Columns:

- `id integer primary key`;
- `file_id integer not null`;
- `module text not null`;
- `imported_name text`;
- `alias text`;
- `kind text not null`;
- `is_relative integer not null`;
- `resolved_file_id integer`;
- `confidence text not null`;
- `start_line integer not null`;
- `metadata_json text not null default '{}'`.

### `calls`

Syntactic call facts.

Columns:

- `id integer primary key`;
- `file_id integer not null`;
- `caller_symbol_id integer`;
- `callee_name text not null`;
- `callee_symbol_id integer`;
- `start_line integer not null`;
- `confidence text not null`;
- `metadata_json text not null default '{}'`.

### `type_usages`

Syntactic type annotations and type references.

Columns:

- `id integer primary key`;
- `file_id integer not null`;
- `symbol_id integer`;
- `type_name text not null`;
- `resolved_symbol_id integer`;
- `start_line integer not null`;
- `confidence text not null`;
- `metadata_json text not null default '{}'`.

### `tests`

Links between test files and source files/symbols.

Columns:

- `id integer primary key`;
- `test_file_id integer not null`;
- `source_file_id integer`;
- `symbol_id integer`;
- `strategy text not null`;
- `confidence text not null`;
- `metadata_json text not null default '{}'`.

Strategies:

- `naming`;
- `import`;
- `naming+import`.

### `edges`

Generic graph extension table.

Columns:

- `id integer primary key`;
- `src_kind text not null`;
- `src_id integer not null`;
- `dst_kind text not null`;
- `dst_id integer not null`;
- `edge_kind text not null`;
- `confidence text not null`;
- `metadata_json text not null default '{}'`.

Edge kinds v1:

- `DEFINES`;
- `IMPORTS`;
- `CALLS`;
- `USES_TYPE`;
- `TESTED_BY`;
- `RELATED_TO`;
- `AFFECTS`;
- `DEPENDS_ON`.

## Config

Config path:

```text
.agent-work/codegraph/config.json
```

Generated default:

```json
{
  "schema_version": 1,
  "languages": ["python", "typescript", "javascript"],
  "include": ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx"],
  "exclude": [
    ".git/**",
    ".agent-work/**",
    "**/__pycache__/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/dist/**",
    "**/build/**"
  ],
  "test_patterns": {
    "python": ["test_*.py", "*_test.py", "tests/**/*.py"],
    "typescript": ["*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx"],
    "javascript": ["*.test.js", "*.spec.js", "*.test.jsx", "*.spec.jsx"]
  },
  "auto_reindex": true,
  "max_context_files": 12,
  "max_context_symbols": 40
}
```

## Dependencies

Add `requirements-codegraph.txt`.

Pinned after local smoke on Python 3.12.6:

```text
tree-sitter==0.25.2
tree-sitter-python==0.25.0
tree-sitter-javascript==0.25.0
tree-sitter-typescript==0.23.2
```

Smoke covered parser creation and basic parsing for Python, JavaScript, TypeScript and TSX.

Do not add Node/npm as a required v1 dependency. TypeScript Compiler API stays a v2 enrichment path.

## File discovery

Use Git when available:

```bash
git ls-files --cached --others --exclude-standard -z
```

This captures tracked files and untracked files that are not ignored. It matches the v1 decision to index the working tree as-is.

Fallback when Git is unavailable: recursive filesystem scan from repo root with config excludes. This fallback is only for `doctor` and local robustness; AgentFlow repo work should normally run inside git.

## Freshness algorithm

Every query calls `ensure_fresh()`:

1. If DB missing: run full index.
2. If schema mismatch: run migration or full rebuild if migration not implemented.
3. If config hash changed: full reindex.
4. Compare current relevant file set with `files`.
5. Hash changed/new/deleted files.
6. Reindex only changed/new files.
7. Hard delete removed files and their edges.
8. Write successful snapshot.
9. If any step fails, return JSON error and do not run query on stale data.

Use content hash, not only mtime. Mtime can be optimization input, not source of truth.

## Language adapters

### Python adapter

Extract:

- modules;
- imports and from-imports;
- functions;
- async functions;
- classes;
- methods;
- decorators;
- call expressions;
- annotations and base classes as type usages.

Implementation path:

- Tree-sitter extracts ranges and common shape.
- Python `ast` may enrich qualnames, decorators, calls and annotations when parsing succeeds.
- If Tree-sitter and `ast` disagree, adapter records conservative `unknown` or lower confidence instead of inventing a relation.

### TypeScript/JavaScript adapter

Extract:

- ES imports;
- CommonJS `require` where syntactically obvious;
- exports;
- functions;
- classes;
- methods;
- arrow functions assigned to names;
- variable declarations with function values;
- JSX/TSX component-like symbols when grammar exposes them;
- call expressions;
- type annotations/interfaces/type aliases for TS/TSX.

Implementation path:

- Tree-sitter grammars for JavaScript and TypeScript.
- Relative import resolution for `./x`, `../x`, with common extensions.
- Package imports stay package-level nodes.
- Path aliases, generated files, re-exports and dynamic imports may return `unknown`.

## Query contracts

### `status`

Returns:

```json
{
  "ok": true,
  "meta": { "fresh": true },
  "data": {
    "files": 120,
    "symbols": 830,
    "edges": 1440,
    "last_indexed_at": "...",
    "last_error": null
  }
}
```

### `impact`

Input:

```bash
python3 scripts/codegraph.py impact --target scripts/validate-run.py
```

Returns:

- direct file;
- imported/importing files;
- defined symbols;
- callers/callees when known;
- tests;
- confidence gaps.

### `tests`

Returns linked tests for path/symbol with `strategy` and `confidence`.

### `context`

Returns bounded context pack:

- files to read;
- symbols to inspect;
- tests to run;
- graph gaps;
- suggested commands;
- max limits applied.

This command is for agents. It must stay compact.

### `boundary`

Inputs:

- changed paths;
- allowed patterns;
- forbidden patterns;
- optional target lane id.

Returns:

- `pass`;
- `fail`;
- `unknown`;
- violating paths;
- affected graph nodes;
- suggested evidence payload for Boundary Evidence Gate.

### `deps`

Compares active task and new task.

Returns:

- `clear`;
- `uncertain`;
- `dependent`;
- reasons;
- shared files/symbols/tests;
- missing context.

The graph assists the gate; the orchestrator still makes the final user-facing call.

### `doctor`

Checks:

- Python version;
- SQLite available;
- SQLite JSON functions available;
- Tree-sitter imports;
- language grammars load;
- config readable;
- DB schema valid;
- self-test parse for Python/TS/JS snippets;
- Git candidate discovery works.

## AgentFlow integration points

### Dependency Gate

Before warning the user about an active task conflict, AgentFlow should call `deps` when CodeGraph is available. The warning can then cite concrete shared files/symbols/tests.

If CodeGraph fails, AgentFlow falls back to existing manual classification and records the graph failure as a gap.

### Boundary Evidence Gate

`boundary` becomes the graph-backed companion to `record-lane-boundary.py`.

`record-lane-boundary.py` answers: «какие paths изменились».

`codegraph.py boundary` answers: «какая graph surface затронута этими paths».

### Context pack

Before implementation/review/QA, AgentFlow can call `context` to reduce file reads. The pack should be evidence, not a replacement for reading source files when editing.

## Test plan

Add fixture tests:

- empty repo;
- Python imports/functions/classes/calls/tests;
- TS imports/functions/classes/type aliases/calls/tests;
- JS imports/CommonJS/basic calls/tests;
- deleted file hard delete;
- stale index auto-reindex;
- malformed source file returns partial graph with gaps;
- `status` JSON envelope;
- `impact` output;
- `tests` strategy output;
- `boundary` pass/fail/unknown;
- `deps` clear/uncertain/dependent;
- `doctor` success path.

Test command names:

```bash
python3 scripts/test-codegraph.py
python3 scripts/check-all.py
```

Wire `test-codegraph.py` into `scripts/check-all.py` after the direct test passes locally.

## Implementation phases

### Phase 1: Docs and dependency shell

- Add `requirements-codegraph.txt`.
- Add `.agent-work/` to `.gitignore`.
- Add `scripts/codegraph.py` with argparse skeleton.
- Implement JSON success/error envelope.
- Implement `doctor` dependency checks.

Validation:

```bash
python3 scripts/codegraph.py doctor
git diff --check
```

### Phase 2: SQLite schema

- Create `.agent-work/codegraph/`.
- Create DB on `index`.
- Add schema version.
- Add typed tables and `edges`.
- Add DB reset/rebuild path for schema mismatch.

Validation:

```bash
python3 scripts/codegraph.py index
python3 scripts/codegraph.py status
```

### Phase 3: Repo scanner and freshness

- Use `git ls-files --cached --others --exclude-standard -z`.
- Apply config include/exclude.
- Hash relevant files.
- Implement hard delete for removed files.
- Implement auto-reindex before query.

Validation:

```bash
python3 scripts/codegraph.py status
python3 scripts/codegraph.py impact --target scripts/check-all.py
```

### Phase 4: Python adapter

- Parse Python files.
- Extract imports, symbols, calls, type usages.
- Link tests through naming and imports.
- Add fixtures.

Validation:

```bash
python3 scripts/test-codegraph.py
```

### Phase 5: TypeScript/JavaScript adapter

- Parse TS/TSX/JS/JSX.
- Extract imports, exports, symbols, calls, type usages.
- Resolve relative imports.
- Link tests through naming and imports.
- Add fixtures.

Validation:

```bash
python3 scripts/test-codegraph.py
```

### Phase 6: Query commands

- Implement `impact`.
- Implement `tests`.
- Implement `context`.
- Implement `boundary`.
- Implement `deps`.

Validation:

```bash
python3 scripts/codegraph.py impact --target scripts/validate-run.py
python3 scripts/codegraph.py tests --target scripts/validate-run.py
python3 scripts/codegraph.py context --task "update Lane Boundary Evidence Gate"
python3 scripts/test-codegraph.py
```

### Phase 7: AgentFlow docs integration

- Update runtime docs to describe CodeGraph as optional local fact layer.
- Do not change lane-map schema.
- Do not add new role.
- Do not change Architecture Matrix.
- Add check-all guard text only after wording stabilizes.

Validation:

```bash
python3 scripts/check-all.py
git diff --check
```

## Acceptance criteria

- `python3 scripts/codegraph.py doctor` returns `ok: true`.
- `python3 scripts/codegraph.py index` creates SQLite index locally.
- `status` reports fresh index.
- `impact` returns related files, symbols and tests for at least one Python target and one TS/JS fixture.
- `tests` links source to tests through naming and import strategies.
- `boundary` detects allowed and forbidden path cases.
- `deps` returns all three classifications in fixtures.
- `context` returns bounded JSON suitable for an agent.
- `python3 scripts/test-codegraph.py` passes.
- `python3 scripts/check-all.py` passes.
- `git diff --check` passes.

## Rollback

If CodeGraph implementation causes instability:

- remove runtime integration calls first;
- keep docs and CLI isolated;
- leave existing AgentFlow gates on their current manual/file-based behavior;
- do not remove unrelated architecture gates.

## Open technical notes for implementation

- Confirm Tree-sitter package compatibility in the local Python runtime before pinning exact versions.
- Decide whether first implementation stays one large `scripts/codegraph.py` or splits internal helpers after tests are green.
- Keep JSON contracts stable once tests cover them.
- Avoid using LLM summaries as graph data. Graph facts must come from parser, file system, git status, config or AgentFlow trace artifacts.

## References

- Tree-sitter parser docs: https://tree-sitter.github.io/tree-sitter/using-parsers/
- Tree-sitter queries: https://tree-sitter.github.io/tree-sitter/using-parsers/queries/index.html
- py-tree-sitter docs: https://tree-sitter.github.io/py-tree-sitter/
- Python `ast`: https://docs.python.org/3/library/ast.html
- TypeScript Compiler API: https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API
- SQLite FTS5: https://sqlite.org/fts5.html
- SQLite JSON functions: https://sqlite.org/json1.html
- Git ignore patterns: https://git-scm.com/docs/gitignore
- Git status porcelain format: https://git-scm.com/docs/git-status
