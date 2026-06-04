# Agent Flow

## Русский

Agent Flow - скилл для Codex. Он включается только явным префиксом в начале запроса:

- `Agent Flow <задача>`
- `$agent-flow <задача>`
- `agent-flow <задача>`
- `агент-флоу <задача>`

Без такого префикса Codex работает обычным solo-режимом: без маршрутизации Agent Flow, trace artifacts и субагентов.

Это не отдельное приложение. Репозиторий содержит правила процесса, справочные материалы и вспомогательные скрипты.

### Зачем нужен

Agent Flow помогает доводить сложные задачи до проверенного результата без лишнего процесса. Скилл выбирает самый короткий рабочий маршрут: быстрый ответ, bugfix, feature flow, docs flow, design flow, review flow, CI/release flow или инициативу от идеи до сдачи.

Он полезен, когда задача требует:

- ясного маршрута вместо хаотичной работы;
- свежей проверки перед финальным ответом;
- аккуратного ограничения scope;
- локальной памяти по проекту;
- trace artifacts для задач, где нужна история решений;
- явного Definition of Done;
- безопасной работы с субагентами, если пользователь отдельно разрешил их использовать.

### Что умеет

- Разбирает запрос с явным префиксом и выбирает минимальный достаточный flow.
- По умолчанию работает solo, чтобы не плодить лишнюю координацию.
- Не запускает субагентов сам. Для этого нужен отдельный явный запрос пользователя.
- Ведет traceable runs в `.agent-work/runs/`, когда задаче нужна сохранённая история проверок и решений.
- Требует проверки перед словами «готово», «исправлено» или «можно сдавать».
- Держит Agent Flow artifacts вне product commits, если пользователь не попросил включить их явно.
- Поддерживает вспомогательные скрипты для timeline, subagent traces и финальной валидации run.

### Что входит в репозиторий

- `SKILL.md` - точка входа скилла и основные правила маршрутизации.
- `references/` - подробные правила для budgets, flows, orchestration, delegation, design, traceable runs и Definition of Done.
- `scripts/` - Python-скрипты для traceable runs.
- `agents/openai.yaml` - пример конфигурации агента на базе OpenAI.

### Проверка после правок

В репозитории нет build step. Для проверки скриптов:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/init-run.py --help
python3 scripts/append-timeline.py --help
python3 scripts/record-agent-trace.py --help
python3 scripts/validate-run.py --help
```

Smoke-test локального traceable run:

```bash
python3 scripts/init-run.py --repo . --slug smoke-test
python3 scripts/record-agent-trace.py --run-dir .agent-work/runs/YYYY-MM-DD-smoke-test --role python-worker --stage smoke --status pass --summary "Smoke event recorded." --next-step "validate"
python3 scripts/validate-run.py --run-dir .agent-work/runs/YYYY-MM-DD-smoke-test --allow-pending --allow-no-check
```

Замените `YYYY-MM-DD` на дату, которую напечатает `init-run.py`.

### Лицензия

MIT. Текст лицензии: `LICENSE`.

## English

Agent Flow is a Codex skill that activates only when the user starts a request with one of these prefixes:

- `Agent Flow <task>`
- `$agent-flow <task>`
- `agent-flow <task>`
- `агент-флоу <task>`

Without that prefix, Codex stays in normal solo mode: no Agent Flow routing, no trace artifacts, and no subagents.

This is not a standalone application. The repository contains process rules, reference material, and helper scripts.

### Purpose

Agent Flow helps turn complex requests into verified outcomes without adding unnecessary process. It picks the shortest useful route: quick answer, bugfix flow, feature flow, docs flow, design flow, review flow, CI/release flow, or a small initiative from idea to handoff.

Use it when a task needs:

- clear routing instead of ad hoc execution;
- fresh verification before the final answer;
- tight scope control;
- project-local memory;
- trace artifacts for work that needs an audit trail;
- explicit Definition of Done checks;
- guarded subagent delegation, only after the user separately asks for it.

### Capabilities

- Parses an explicitly prefixed request and selects the smallest sufficient flow.
- Runs solo by default to avoid unnecessary coordination.
- Never launches subagents by implication. Subagents require a separate explicit user request.
- Creates traceable runs in `.agent-work/runs/` when durable evidence is useful.
- Requires verification before claiming work is done, fixed, passing, or ready.
- Keeps Agent Flow artifacts out of product commits unless the user asks otherwise.
- Provides helper scripts for timelines, subagent traces, and final run validation.

### Repository Contents

- `SKILL.md` - skill entry point and routing rules.
- `references/` - detailed rules for budgets, flows, orchestration, delegation, design, traceable runs, and Definition of Done.
- `scripts/` - Python helpers for traceable runs.
- `agents/openai.yaml` - example configuration for an OpenAI-based agent setup.

### Validation

This repository has no build step. To check the helper scripts:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/init-run.py --help
python3 scripts/append-timeline.py --help
python3 scripts/record-agent-trace.py --help
python3 scripts/validate-run.py --help
```

Smoke-test a local traceable run:

```bash
python3 scripts/init-run.py --repo . --slug smoke-test
python3 scripts/record-agent-trace.py --run-dir .agent-work/runs/YYYY-MM-DD-smoke-test --role python-worker --stage smoke --status pass --summary "Smoke event recorded." --next-step "validate"
python3 scripts/validate-run.py --run-dir .agent-work/runs/YYYY-MM-DD-smoke-test --allow-pending --allow-no-check
```

Replace `YYYY-MM-DD` with the date printed by `init-run.py`.

### License

MIT. See `LICENSE`.
