# AgentFlow

[English](#english) | [Русский](#russian)

## English

AgentFlow is a Codex skill for scoped agent workflows. Put `Agent Flow`, `AgentFlow`, `$agent-flow`, or `agent-flow` anywhere in the prompt; after that, the orchestrator reads context, checks active project work, switches internal budgets under the hood, decides whether subagents are useful, and requires verification before a final answer.

Target: Codex with OpenAI models only. Claude Code, Cursor, Hermes, and other hosts are outside this package scope.

It ships 25 roles and tracks 138 role skill dependencies.

### Contract

AgentFlow runs only when the user request contains one of these invocation markers:

```text
Agent Flow <task>
AgentFlow <task>
$agent-flow <task>
agent-flow <task>
```

The marker can appear at the beginning, middle, or end of the prompt. Users do not choose budgets or explicitly ask for subagents. The orchestrator decides from context whether the task should stay solo, collect more evidence, create trace artifacts, or delegate work to subagents.

### Install

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

`--post-install` reports missing role skills and recommends the `core` set. It never installs anything silently.

### Update

```bash
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py --dry-run
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py
```

The updater fetches `origin`, reports dirty state, and only fast-forwards a clean checkout. If local edits or divergent commits should be discarded, pass `--overwrite` explicitly.

### Checks

Run from the repository root:

```bash
python3 scripts/check-all.py
python3 scripts/check-agent-deps.py --strict
```

Expected final line from `check-all.py`:

```text
PASS all Agent Flow checks
```

### What Is Inside

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Codex skill entrypoint and invocation contract |
| `agents/*.md` | 25 bundled role prompts |
| `agents/agent-identities.json` | stable role identities for traces and handoffs |
| `references/architecture-matrix.md` | reusable architecture matrix facets for product, surface, stack, risk, and verification context |
| `references/architecture-capability-router.md` | Architecture Capability Router and Soft Skill Binding contract |
| `references/role-catalog.md` | role lifecycle, use cases, exclusions, overlap notes |
| `registries/agent-skills.json` | metadata for role skill dependencies |
| `registries/architecture-capabilities.json` | capability routing from Matrix facets to soft skill bindings |
| `scripts/check-all.py` | repository validation suite |
| `scripts/analyze-evidence-records.py` | Evidence Records analyzer for local learning |
| `scripts/resolve-agent-config.py` | model/reasoning resolver for `spawn_agent` |
| `scripts/validate-run.py` | traceable run and lane-map validator |
| `scripts/validate-architecture-capabilities.py` | Architecture Capability Router registry validator |

### Key Rules

- AgentFlow is not a general preflight. Requests without an invocation marker stay outside this skill.
- Project `AGENTS.md` files cannot force AgentFlow on.
- Budget switching is internal; users do not choose workflow depth manually.
- Dependency Gate is mandatory before new feature work: if another active task may affect the new one, the orchestrator stops and recommends waiting or merging the work into one coordinated run.
- Task status is a hard completion signal: after successful verification or product commit, a completed current task must move from `in_progress` to `done`.
- Evidence Records in `implementation-notes.md` are structured local learning data, not free-form notes.
- Local Best Practice auto gate can reuse a learned approach only after analyzer confirmation, clear context match, no matching `Do not reuse when`, no external write, and fresh verification.
- A failed or regressed reuse demotes or freezes the practice until architecture review resolves it.
- Model/reasoning upgrade is not the default fix for a rejected approach; the orchestrator improves context, architecture, evidence, or verification first.
- Architecture Matrix makes the architecture contract context-specific when product type, application surface, stack, risk, or verification constraints matter.
- Subagents no longer need an explicit user request; the orchestrator turns them on when they add real review, QA, research, or parallel implementation value.
- `.agent-work/tasks/` is local project memory.
- `.agent-work/runs/` is used only for traceable work.
- AgentFlow artifacts should not be included in product commits unless explicitly requested.
- Role frontmatter uses one-line `key: value` entries and inline lists only. Full-line comments and YAML-like inline comments are allowed; multiline YAML is unsupported.

### Large Scopes

For large or risky scopes, the orchestrator can split work into implementation, integration, architecture, QA, and review lanes. For traceable runs, `lane-map.json` is the source of truth. `validate-run.py` blocks `Verdict: ship` if a critical lane lacks evidence or a valid replacement lane.

Schema v2 adds the Architecture Contract Gate and requires `budget`. `release` runs and `standard` runs with two or more worker lanes must set `architecture_contract_required=true`. A critical `architecture` lane must pass with handoff, evidence, and required contract sections before QA/review can close `ship`. If `architecture_contract_independent` is true, that lane must be a real subagent with spawned trace evidence; otherwise a scoped role-lane architecture check may be enough for standard multi-lane work.

Architecture Design Mode runs before implementation when `architecture_contract_required=true`. Every successful critical `architecture` lane must set `architecture_design_brief` to an Architecture Design Brief with `Selected Matrix Facets` and `Status: approved`; `validate-run.py` blocks `ship` and `pass-with-risks` without an approved brief, and worker lanes must run after it.

When the product or stack matters, the orchestrator writes `architecture_context` in `lane-map.json` with the six Architecture Matrix axes: `product_context`, `application_surface`, `architecture_pattern`, `stack_runtime`, `risk_gates`, and `verification_gates`. `validate-run.py` parses the allowed facet ids from `references/architecture-matrix.md` and checks that every selected facet appears in the architect's `Selected Architecture` section.

Architecture Context Propagation then carries that context through execution: workers declare covered `matrix_facets`, QA covers selected `risk_gates` and `verification_gates`, and reviewer checks every selected facet through `Architecture Matrix Mismatches` and `Contract Drift`.

### Dependency Gate

When a user starts a new feature while another feature is still `in_progress` or `blocked`, the orchestrator checks project memory before planning. If the active work may change the same files, API, data model, UI flow, tests, deploy path, or acceptance criteria, AgentFlow stops the new session.

Before blocking, AgentFlow checks for stale completed task sections. If an `in_progress` section has all checklist items checked, verification recorded, and no blocker, the orchestrator closes it as `done` first. If evidence is missing, the gate treats it as `uncertain` and asks to verify or close it before new work.

The warning names the active task, explains the risk in practical terms, and recommends waiting for the active feature to finish. The user can still continue by explicitly accepting the risk, or can merge both pieces of work into one coordinated AgentFlow run.

### Evidence Records

AgentFlow can learn from local project history through `## Evidence Records` in `implementation-notes.md`. Records are grouped by exact `Problem class + Approach`, then counted as `success`, `failure`, `regression`, `rejected`, or `unknown`. Architecture and orchestration attempts are counted separately so worker failures do not hide architecture failures.

Promotion is conservative:

- `1 success` -> `Observed`;
- `2 success` -> `Candidate Practice`;
- success count reaches the clean threshold, default `3`, -> `Local Best Practice`;
- repeated failures or rejects -> `Anti-pattern`.

Unknown outcomes are reported as incomplete, not as promotion evidence. Missing reuse boundaries block promotion. A Local Best Practice can be auto-applied only through the auto gate; the analyzer never edits the notes file.

Run the analyzer directly when needed:

```bash
python3 scripts/analyze-evidence-records.py --json
python3 scripts/analyze-evidence-records.py --fail-on-invalid
```

### Examples

Solo repository read:

```text
Agent Flow Read the repository and project memory. Return active work, blocked items, next actions, and risks. Do not change anything.
```

Bugfix:

```text
Agent Flow Investigate this bug: <description>. Find the cause, make the smallest fix, run checks, and return changed files plus residual risks.
```

Release review:

```text
Agent Flow Finish this feature for release. Run architecture, QA, and review gates, then return ship/pass-with-risks/blocker status with evidence.
```

### Useful Links

- [Role catalog](references/role-catalog.md)
- [Architecture Matrix](references/architecture-matrix.md)
- [Subagent policy](references/subagents.md)
- [Delegation rules](references/delegation.md)
- [Traceable runs](references/traceable-runs.md)
- [Definition of Done](references/definition-of-done.md)
- [Russian documentation](docs/ru/agent-flow.md)
- [English documentation](docs/en/agent-flow.md)
- [License](LICENSE)

## Russian

AgentFlow - Codex skill для задач, где агенту нужен управляемый workflow: контекст, проектная память, делегация и проверка результата. Добавьте `Agent Flow`, `AgentFlow`, `$agent-flow` или `agent-flow` в любую часть промпта. После этого оркестратор читает контекст, проверяет активную работу, выбирает глубину выполнения, решает, нужны ли субагенты, и не завершает задачу без verification.

Пакет рассчитан на Codex и модели OpenAI. Claude Code, Cursor, Hermes и другие hosts не поддерживаются.

В репозитории есть 25 ролей и 138 зависимостей role skills.

### Контракт

AgentFlow включается только если запрос содержит один из маркеров:

```text
Agent Flow <задача>
AgentFlow <задача>
$agent-flow <задача>
agent-flow <задача>
```

Маркер запуска может стоять в любой части промпта. Пользователю не нужно выбирать budgets или отдельно просить субагентов. Оркестратор по контексту решает: выполнить задачу solo, собрать evidence, создать trace artifacts или делегировать часть работы.

### Установка

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

`--post-install` показывает missing role skills и рекомендует набор `core`. Автоматической установки без подтверждения нет.

### Обновление

```bash
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py --dry-run
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py
```

Скрипт обновления выполняет `fetch` из `origin`, показывает состояние checkout и обновляет только clean checkout через fast-forward. Чтобы отбросить локальные правки или divergent commits, нужен явный `--overwrite`.

### Проверки

Запускать из корня репозитория:

```bash
python3 scripts/check-all.py
python3 scripts/check-agent-deps.py --strict
```

Ожидаемая последняя строка `check-all.py`:

```text
PASS all Agent Flow checks
```

### Что внутри

| Файл | Назначение |
| --- | --- |
| `SKILL.md` | точка входа skill и правило запуска |
| `agents/*.md` | 25 встроенных prompts ролей |
| `agents/agent-identities.json` | стабильные identities ролей для trace и handoff |
| `references/architecture-matrix.md` | переиспользуемая Architecture Matrix по типу продукта, приложению, стеку, рискам и проверкам |
| `references/architecture-capability-router.md` | Architecture Capability Router и Soft Skill Binding |
| `references/role-catalog.md` | сценарии ролей, ограничения и пересечения |
| `registries/agent-skills.json` | metadata зависимостей role skills |
| `registries/architecture-capabilities.json` | routing capabilities от Matrix facets к мягким skill-связям |
| `scripts/check-all.py` | полный набор проверок репозитория |
| `scripts/analyze-evidence-records.py` | analyzer для Evidence Records и локального обучения |
| `scripts/resolve-agent-config.py` | resolver model/reasoning для `spawn_agent` |
| `scripts/validate-run.py` | validator traceable runs и lane-map |
| `scripts/validate-architecture-capabilities.py` | validator Architecture Capability Router registry |

### Главные правила

- AgentFlow - не общий preflight для всех запросов.
- Проектный `AGENTS.md` не может включить AgentFlow без видимого пользователю маркера запуска в текущем запросе.
- Budget выбирает оркестратор; пользователь не управляет глубиной workflow вручную.
- Dependency Gate обязателен перед новой фичей: если другая активная задача может повлиять на неё, оркестратор останавливает старт и предлагает дождаться результата или объединить работы в один coordinated run.
- Статус задачи - обязательный сигнал завершения: после успешной проверки или product commit закрытая текущая задача должна перейти из `in_progress` в `done`.
- Evidence Records в `implementation-notes.md` - структурированные данные для локального обучения, а не свободные заметки.
- Local Best Practice auto gate переиспользует подход только после подтверждения analyzer, ясного совпадения контекста, отсутствия совпадения с `Do not reuse when`, отсутствия внешней записи и свежей проверки.
- Если переиспользованный подход дал failure или regression, practice демотируется или замораживается до архитектурного разбора.
- Rejected-подход не лечится автоматическим model/reasoning upgrade: сначала оркестратор улучшает контекст, архитектуру, evidence или verification.
- Architecture Matrix уточняет архитектурный контракт по типу продукта, приложению, стеку, рискам и проверкам.
- Субагенты больше не требуют явной просьбы пользователя; оркестратор включает их, когда они дают реальную пользу для review, QA, research или параллельной реализации.
- `.agent-work/tasks/` - локальная проектная память.
- `.agent-work/runs/` используется только для traceable work.
- AgentFlow artifacts не попадают в product commits без явной просьбы.
- Frontmatter ролей использует однострочные `key: value` и inline lists. Разрешены отдельные строки-комментарии и YAML-like inline comments; multiline YAML не поддерживается.

### Большие задачи

Для больших или рискованных задач оркестратор делит работу на implementation, integration, architecture, QA и review lanes. В traceable runs `lane-map.json` задаёт lanes и их статус. `validate-run.py` блокирует `Verdict: ship`, если critical lane не закрыта evidence или валидной replacement lane.

Schema v2 добавляет Architecture Contract Gate и требует `budget`. Для `release` и `standard` с двумя или более worker lanes нужно ставить `architecture_contract_required=true`. Критическая `architecture` lane должна пройти с handoff, evidence и обязательными секциями контракта до QA/review. Если `architecture_contract_independent` равен true, нужна реальная subagent lane со spawned trace evidence. Для standard multi-lane работы иногда достаточно scoped role-lane проверки.

Architecture Design Mode запускается до реализации, когда `architecture_contract_required=true`. Каждая успешная критическая `architecture` lane указывает `architecture_design_brief` на Architecture Design Brief с `Selected Matrix Facets` и `Status: approved`; `validate-run.py` блокирует `ship` и `pass-with-risks` без approved brief, а worker lanes стартуют только после него.

Если тип продукта или стек влияет на решение, оркестратор записывает `architecture_context` в `lane-map.json`: `product_context`, `application_surface`, `architecture_pattern`, `stack_runtime`, `risk_gates` и `verification_gates`. `validate-run.py` берёт допустимые facet ids из `references/architecture-matrix.md` и проверяет, что каждый выбранный facet попал в секцию `Selected Architecture`.

Architecture Context Propagation проводит этот контекст через исполнение: workers указывают покрытые `matrix_facets`, QA покрывает выбранные `risk_gates` и `verification_gates`, а reviewer проверяет все выбранные facets через `Architecture Matrix Mismatches` и `Contract Drift`.

### Dependency Gate

Когда пользователь запускает новую фичу, а в проекте уже есть задача со статусом `in_progress` или `blocked`, оркестратор сначала смотрит проектную память. Если активная работа затрагивает те же файлы, API, модель данных, UI flow, тесты, deploy path или критерии приёмки, AgentFlow останавливает новую сессию.

Перед блокировкой AgentFlow проверяет, нет ли stale завершённых секций. Если у секции `in_progress` отмечены все пункты checklist, записана проверка и нет blocker, оркестратор сначала закрывает её как `done`. Если evidence не хватает, gate считает секцию `uncertain` и просит проверить или закрыть её перед новой работой.

Предупреждение называет активную задачу, объясняет риск и предлагает дождаться её завершения. Пользователь может принять риск и продолжить или объединить обе работы в один coordinated AgentFlow run.

### Evidence Records

AgentFlow учится на локальной истории проекта через `## Evidence Records` в `implementation-notes.md`. Записи группируются по точной паре `Problem class + Approach`, затем считаются как `success`, `failure`, `regression`, `rejected` или `unknown`. Architecture и orchestration attempts считаются отдельно, чтобы worker failures не скрывали архитектурные failures.

Promotion работает осторожно:

- `1 success` -> `Observed`;
- `2 success` -> `Candidate Practice`;
- success достигает threshold без failures/regressions, по умолчанию `3`, -> `Local Best Practice`;
- повторные failures или rejects -> `Anti-pattern`.

Unknown outcomes попадают в incomplete, но не продвигают practice. Без reuse boundaries promotion блокируется. Local Best Practice применяется автоматически только через auto gate; analyzer не редактирует notes file.

Analyzer можно запустить напрямую:

```bash
python3 scripts/analyze-evidence-records.py --json
python3 scripts/analyze-evidence-records.py --fail-on-invalid
```

### Примеры

Solo чтение репозитория:

```text
Agent Flow Read the repository and project memory. Return active work, blocked items, next actions, and risks. Do not change anything.
```

Bugfix:

```text
Agent Flow Investigate this bug: <description>. Find the cause, make the smallest fix, run checks, and return changed files plus residual risks.
```

Release review:

```text
Agent Flow Finish this feature for release. Run architecture, QA, and review gates, then return ship/pass-with-risks/blocker status with evidence.
```

### Ссылки

- [Role catalog](references/role-catalog.md)
- [Architecture Matrix](references/architecture-matrix.md)
- [Subagent policy](references/subagents.md)
- [Delegation rules](references/delegation.md)
- [Traceable runs](references/traceable-runs.md)
- [Definition of Done](references/definition-of-done.md)
- [Документация на русском](docs/ru/agent-flow.md)
- [Documentation in English](docs/en/agent-flow.md)
- [License](LICENSE)
