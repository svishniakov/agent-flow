# ADR-001: Локальный CodeGraph для AgentFlow

## Status

Proposed

## Date

2026-06-15

## Context

AgentFlow уже умеет держать project memory, проверять trace artifacts, валидировать lanes и останавливать работу на архитектурных gate-проверках. Слабое место осталось ниже: чтобы понять связи в коде, агент каждый раз читает файлы через `rg`, `sed`, `cat` и затем сам восстанавливает карту зависимостей в контексте LLM.

Это дорого и ненадёжно. LLM тратит токены на повторное чтение кода, может пропустить связь между файлами, переоценить влияние diff или дать уверенный вывод без фактической карты imports, symbols, tests и ownership boundaries.

Нужен локальный машинный слой, который отвечает на структурные вопросы:

- какие файлы и symbols связаны с задачей;
- какие tests относятся к изменяемой зоне;
- пересекаются ли две активные задачи по коду, API, типам или tests;
- выходит ли diff за allowed/forbidden boundaries;
- какие файлы агенту действительно нужно прочитать перед правкой.

Ограничение принципиальное: AgentFlow остаётся локальным. Внешняя graph DB, облачный индекс, Obsidian mirror, UI и отдельный daemon не входят в первую версию.

## Decision Drivers

- Локальный запуск без внешней инфраструктуры.
- Минимум токенов на сбор контекста для агента.
- Машинно проверяемые facts вместо пересказа LLM.
- Усиление Dependency Gate и Boundary Evidence Gate.
- Возможность инкрементального обновления индекса.
- Простая установка в текущий Python-oriented repo.
- Явные failure modes: если граф нельзя обновить, он не должен молча отдавать старые данные.

## Considered Options

### Option 1: Ephemeral graph из `rg` и разового parsing

Граф строится на лету под каждый запрос и не сохраняется.

Плюсы:

- быстро сделать прототип;
- нет storage migration;
- почти нет lifecycle.

Минусы:

- нет долговременной карты проекта;
- повторная стоимость на каждый запрос;
- слабая база для gates;
- невозможно нормально отслеживать freshness, history и run links.

Решение: отклонено. Это не решает главную проблему AgentFlow.

### Option 2: Embedded SQLite graph с локальным индексатором

Индексатор пишет `.agent-work/codegraph/codegraph.sqlite`. AgentFlow обращается к нему через CLI и получает JSON.

Плюсы:

- полностью локально;
- не нужен daemon;
- SQLite уже доступен через Python stdlib;
- удобно хранить typed tables и generic edges;
- легко проверять freshness и schema version;
- подходит для gate-проверок и tests.

Минусы:

- нужно написать индексатор;
- нужно поддерживать schema migrations;
- точность зависит от language adapters.

Решение: принято для v1.

### Option 3: LSP/LSIF как основной источник связей

Индекс строится поверх language servers или LSIF.

Плюсы:

- выше точность для definitions/references в зрелых языках;
- можно учитывать project config, path aliases, type checker.

Минусы:

- больше движущихся частей;
- разные языки требуют разной установки;
- сложнее поддерживать в локальном portable skill;
- повышается риск «у меня не работает» на чужих машинах.

Решение: отложено. Можно добавить позже как adapter enrichment.

### Option 4: CodeQL/CPG

Использовать code property graph, data flow и security-style queries.

Плюсы:

- силён для security review и dataflow;
- умеет глубже анализировать код, чем обычный import/call graph.

Минусы:

- тяжёлый runtime;
- меняет характер проекта;
- быстро расширяет scope до security analyzer.

Решение: не входит в v1. Может быть отдельным release/security gate позже.

### Option 5: External graph DB

Neo4j, ArangoDB или похожая graph DB.

Плюсы:

- богатые graph queries;
- удобная визуализация;
- хороша для больших графов.

Минусы:

- внешняя инфраструктура;
- сложнее установка;
- противоречит локальной природе AgentFlow.

Решение: отклонено.

### Option 6: Hybrid graph + embeddings

Структурный граф дополняется semantic search.

Плюсы:

- полезно для поиска похожих паттернов и старых решений;
- может улучшить context pack.

Минусы:

- embeddings не являются source of truth для фактов;
- больше зависимостей;
- риск смешать точные связи и смысловые догадки.

Решение: не входит в v1.

## Decision

Строим локальный CodeGraph v1 как embedded модуль AgentFlow.

Canonical index:

```text
.agent-work/codegraph/codegraph.sqlite
```

Config:

```text
.agent-work/codegraph/config.json
```

Public entrypoint:

```bash
python3 scripts/codegraph.py <subcommand>
```

Основной storage: SQLite.

Parser layer: Tree-sitter с language-specific adapters для Python, TypeScript и JavaScript. Python adapter может использовать stdlib `ast` для дополнительной точности, если это не ломает общий adapter contract.

Output: только JSON.

Success envelope:

```json
{
  "ok": true,
  "meta": {
    "schema_version": 1,
    "repo_root": "...",
    "index_path": ".agent-work/codegraph/codegraph.sqlite",
    "fresh": true,
    "indexed_at": "..."
  },
  "data": {}
}
```

Error envelope:

```json
{
  "ok": false,
  "error": {
    "code": "codegraph_index_failed",
    "message": "Index rebuild failed",
    "details": {}
  }
}
```

## Scope v1

CodeGraph v1 индексирует рабочее дерево как оно есть: tracked, dirty и untracked relevant files, если они не исключены ignore-правилами.

Поддерживаемые языки:

- Python;
- TypeScript;
- JavaScript;
- TSX/JSX как часть TypeScript/JavaScript adapter, если grammar поддерживает файл.

Обязательные entities:

- files;
- symbols;
- imports;
- calls;
- type usages;
- tests;
- snapshots;
- file hashes;
- generic edges.

Обязательные queries:

- `index`;
- `status`;
- `impact`;
- `tests`;
- `context`;
- `boundary`;
- `deps`;
- `doctor`.

Обязательная интеграция:

- Dependency Gate получает graph-backed overlap evidence;
- Boundary Gate получает graph-backed affected surface;
- context pack помогает агенту читать меньше файлов.

## Precision Contract

«Точность» v1 означает детерминированное извлечение структурных фактов из AST и language-specific правил. Это не обещание полного semantic type resolution.

Обязательные правила:

- каждый факт получает `confidence`: `exact`, `inferred` или `unknown`;
- unresolved calls/imports/type usages не превращаются в уверенные edges;
- query output должен явно показывать gaps;
- AgentFlow не должен считать graph output единственным источником истины для release/security выводов;
- при stale index query сначала запускает auto-reindex.

Для Python ожидается высокая точность по modules, imports, functions, classes, methods, decorators и calls.

Для TypeScript/JavaScript v1 покрывает синтаксические symbols, imports, exports, relative import resolution, calls и type usages. Path aliases, re-exports, dynamic imports, generated code и type checker-level facts могут остаться `unknown`.

## Storage Model

Выбран hybrid schema:

- typed tables для частых сущностей;
- generic `edges` для расширения без немедленной migration под каждый новый relation type.

Базовые таблицы:

- `schema_meta`;
- `snapshots`;
- `files`;
- `symbols`;
- `imports`;
- `calls`;
- `type_usages`;
- `tests`;
- `file_hashes`;
- `edges`.

Удалённые файлы при reindex удаляются из графа hard delete. История остаётся в git и trace artifacts; v1 не хранит historical graph snapshots как queryable timeline.

## Freshness

Индекс stale, если:

- database отсутствует;
- schema version не совпадает;
- config hash изменился;
- relevant file появился, удалился или изменил content hash;
- snapshot не соответствует текущему repo state;
- индексатор завершился ошибкой на прошлом запуске.

Перед каждым graph query запускается `ensure_fresh()`. Если индекс stale, CodeGraph делает auto-reindex. Если reindex не удался, query возвращает JSON error и non-zero exit code.

## CLI Contract

`index` строит или обновляет индекс.

`status` возвращает freshness, размер индекса, количество files/symbols/edges, последнюю ошибку.

`impact` возвращает affected files, symbols, tests и edges для path или symbol.

`tests` возвращает tests для path/symbol и объясняет strategy: naming convention, import link или оба признака.

`context` возвращает bounded context pack для агента: файлы, symbols, tests, risks, unread gaps.

`boundary` проверяет changed paths и affected graph surface против allowed/forbidden patterns.

`deps` сравнивает активную задачу и новую задачу по graph surfaces и возвращает `clear`, `uncertain` или `dependent`.

`doctor` проверяет зависимости, schema, доступность parsers, ignore behavior, SQLite features и basic self-test.

## Consequences

### Positive

- AgentFlow получает локальную карту кода без внешних сервисов.
- Dependency Gate и Boundary Gate становятся более фактологичными.
- Агент читает меньше файлов и тратит меньше токенов.
- Trace/review/QA lanes получают одинаковый machine-readable context.
- Ошибки индексации становятся явными JSON errors.

### Negative

- Появляется новый dependency set для Tree-sitter grammars.
- Нужно поддерживать schema и migrations.
- TypeScript/JavaScript precision без TypeScript compiler API будет ограничена на path aliases, re-exports и type-level facts.
- Auto-reindex перед query может дать задержку на больших repos.

### Risks

- Stale graph может дать уверенно неправильный context.
  - Mitigation: auto-reindex, freshness metadata, non-zero exit on failed reindex.
- Parser gaps могут выглядеть как реальные отсутствующие связи.
  - Mitigation: `confidence` и `unknown`, запрет на silent inference.
- Scope creep в security/dataflow analyzer.
  - Mitigation: security/dataflow явно out of scope для v1.
- Dependency bloat.
  - Mitigation: отдельный `requirements-codegraph.txt`, `doctor`, pinned versions after compatibility smoke.

## Non-goals

- Obsidian integration.
- UI/visual graph.
- External graph DB.
- MCP tool API.
- Embeddings/semantic search.
- Security/dataflow analyzer.
- Full TypeScript type checker integration.
- Cross-repo graph.
- Historical graph timeline.

## Implementation Boundary

Первый implementation должен ограничиться docs, Python CLI, SQLite schema, parsers, queries, tests и минимальной AgentFlow integration plan/update. Он не должен менять subagent topology, lane-map schema или Architecture Matrix.

## Acceptance

Перед реализацией пользователь читает:

- `docs/adr/adr-001-codegraph.md`;
- `docs/implementation/impl-001-codegraph-v1.md`.

Кодовая реализация начинается только после явного approval в чате.

## References

- Tree-sitter: https://tree-sitter.github.io/tree-sitter/
- py-tree-sitter: https://tree-sitter.github.io/py-tree-sitter/
- Python `ast`: https://docs.python.org/3/library/ast.html
- TypeScript Compiler API: https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API
- SQLite FTS5: https://sqlite.org/fts5.html
- SQLite JSON functions: https://sqlite.org/json1.html
- Git ignore patterns: https://git-scm.com/docs/gitignore
- Git status porcelain format: https://git-scm.com/docs/git-status
