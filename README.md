# AgentFlow

[English](#english) | [Русский](#russian)

## English

AgentFlow is a Codex skill for scoped agent workflows. Put `Agent Flow` first in the prompt; after that, the orchestrator reads context, checks active project work, switches internal budgets under the hood, decides whether subagents are useful, and requires verification before a final answer.

Target: Codex with OpenAI models only. Claude Code, Cursor, Hermes, and other hosts are outside this package scope.

It ships 25 roles and tracks 138 role skill dependencies.

### Contract

AgentFlow runs only when the user request starts with one of these prefixes:

```text
Agent Flow <task>
$agent-flow <task>
agent-flow <task>
```

The prefix must be first, and it is enough. Users do not choose budgets or explicitly ask for subagents. The orchestrator decides from context whether the task should stay solo, collect more evidence, create trace artifacts, or delegate work to subagents.

### Install

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

`--post-install` reports missing role skills and recommends the `core` set. It never installs anything silently.

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
| `references/role-catalog.md` | role lifecycle, use cases, exclusions, overlap notes |
| `registries/agent-skills.json` | metadata for role skill dependencies |
| `scripts/check-all.py` | repository validation suite |
| `scripts/resolve-agent-config.py` | model/reasoning resolver for `spawn_agent` |
| `scripts/validate-run.py` | traceable run and lane-map validator |

### Key Rules

- AgentFlow is not a general preflight. Unprefixed requests stay outside this skill.
- Project `AGENTS.md` files cannot force AgentFlow on.
- Budget switching is internal; users do not choose workflow depth manually.
- Dependency Gate is mandatory before new feature work: if another active task may affect the new one, the orchestrator stops and recommends waiting or merging the work into one coordinated run.
- Subagents no longer need an explicit user request; the orchestrator turns them on when they add real review, QA, research, or parallel implementation value.
- `.agent-work/tasks/` is local project memory.
- `.agent-work/runs/` is used only for traceable work.
- AgentFlow artifacts should not be included in product commits unless explicitly requested.
- Role frontmatter uses one-line `key: value` entries and inline lists only. Full-line comments and YAML-like inline comments are allowed; multiline YAML is unsupported.

### Large Scopes

For large or risky scopes, the orchestrator can split work into implementation, integration, QA, and review lanes. For traceable runs, `lane-map.json` is the source of truth. `validate-run.py` blocks `Verdict: ship` if a critical lane lacks evidence or a valid replacement lane. Architecture-sensitive code review must be checked against an architect-owned review contract before reviewer readiness verdict.

### Dependency Gate

When a user starts a new feature while another feature is still `in_progress` or `blocked`, the orchestrator checks project memory before planning. If the active work may change the same files, API, data model, UI flow, tests, deploy path, or acceptance criteria, AgentFlow stops the new session.

The warning names the active task, explains the risk in practical terms, and recommends waiting for the active feature to finish. The user can still continue by explicitly accepting the risk, or can merge both pieces of work into one coordinated AgentFlow run.

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
- [Subagent policy](references/subagents.md)
- [Delegation rules](references/delegation.md)
- [Traceable runs](references/traceable-runs.md)
- [Definition of Done](references/definition-of-done.md)
- [Russian documentation](docs/ru/agent-flow.md)
- [English documentation](docs/en/agent-flow.md)
- [License](LICENSE)

## Russian

AgentFlow - это Codex skill для задач, где нужен управляемый workflow агента. Пользователь ставит `Agent Flow` первым в prompt; дальше оркестратор читает контекст, проверяет активные задачи проекта, под капотом переключает внутренние budgets, решает, нужны ли субагенты, и требует проверку перед финальным ответом.

Поддерживаемая среда: Codex с моделями OpenAI. Claude Code, Cursor, Hermes и другие hosts вне scope этого пакета.

В комплекте 25 ролей и 138 зависимостей role skills.

### Контракт

AgentFlow включается только если запрос пользователя начинается с одного из префиксов:

```text
Agent Flow <задача>
$agent-flow <задача>
agent-flow <задача>
```

Префикс должен стоять первым, и этого достаточно. Пользователю не нужно выбирать budgets или явно просить субагентов. Оркестратор сам решает по контексту, оставить задачу solo, собрать больше evidence, создать trace artifacts или делегировать часть работы субагентам.

### Установка

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

`--post-install` показывает missing role skills и рекомендует набор `core`. Тихой установки нет.

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

| Путь | Назначение |
| --- | --- |
| `SKILL.md` | entrypoint скилла и контракт включения |
| `agents/*.md` | 25 встроенных role prompts |
| `agents/agent-identities.json` | стабильные identity ролей для trace и handoff |
| `references/role-catalog.md` | жизненный цикл ролей, сценарии, исключения и пересечения |
| `registries/agent-skills.json` | metadata для зависимостей role skills |
| `scripts/check-all.py` | полный набор проверок репозитория |
| `scripts/resolve-agent-config.py` | resolver model/reasoning для `spawn_agent` |
| `scripts/validate-run.py` | validator traceable runs и lane-map |

### Главные правила

- AgentFlow не является общим preflight для каждого запроса.
- Проектный `AGENTS.md` не может включить AgentFlow без префикса пользователя.
- Переключение budgets работает под капотом; пользователь не выбирает глубину workflow вручную.
- Dependency Gate обязателен перед новой фичей: если другая активная задача может повлиять на неё, оркестратор останавливает старт и рекомендует дождаться результата или объединить работы в один coordinated run.
- Субагенты больше не требуют явной просьбы пользователя; оркестратор включает их, когда они дают реальную пользу для review, QA, research или параллельной реализации.
- `.agent-work/tasks/` - локальная проектная память.
- `.agent-work/runs/` используется только для traceable work.
- AgentFlow artifacts не попадают в product commits без явной просьбы.
- Frontmatter ролей использует однострочные `key: value` и inline lists. Разрешены отдельные строки-комментарии и YAML-like inline comments; multiline YAML не поддерживается.

### Большие задачи

Для больших или рискованных задач оркестратор может разделить работу на implementation, integration, QA и review lanes. Для traceable runs source of truth - `lane-map.json`. `validate-run.py` блокирует `Verdict: ship`, если critical lane не закрыта evidence или валидной replacement lane. Architecture-sensitive code review проверяется against architect-owned review contract до reviewer verdict о готовности.

### Dependency Gate

Когда пользователь запускает новую фичу, а в проекте уже есть задача со статусом `in_progress` или `blocked`, оркестратор сначала смотрит проектную память. Если активная работа может поменять те же файлы, API, модель данных, UI flow, тесты, deploy path или критерии приёмки, AgentFlow останавливает новую сессию.

Предупреждение называет активную задачу, объясняет практический риск и рекомендует дождаться завершения текущей фичи. Пользователь может явно принять риск и продолжить или объединить обе работы в один coordinated AgentFlow run.

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
- [Subagent policy](references/subagents.md)
- [Delegation rules](references/delegation.md)
- [Traceable runs](references/traceable-runs.md)
- [Definition of Done](references/definition-of-done.md)
- [Документация на русском](docs/ru/agent-flow.md)
- [Documentation in English](docs/en/agent-flow.md)
- [License](LICENSE)
