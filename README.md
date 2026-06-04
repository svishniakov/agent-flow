# Agent Flow

## Русский

Agent Flow - skill для Codex и других coding agents с поддержкой skills. Он помогает вести сложные задачи от запроса до проверенного результата: выбрать маршрут, удержать scope, прочитать проектную память, сделать работу и зафиксировать проверки.

Это не приложение и не фреймворк. Репозиторий содержит:

- `SKILL.md` - точка входа и правила включения Agent Flow;
- `references/` - бюджеты, flows, delegation, traceable runs, Definition of Done;
- `agents/` - описания встроенных субагентов и их stable identities;
- `docs/ru/` - русская справка.

Agent Flow включается только явным префиксом в начале запроса:

```text
Agent Flow <задача>
$agent-flow <задача>
agent-flow <задача>
```

Без такого префикса агент работает обычно: без Agent Flow routing, без trace artifacts и без автоматического запуска субагентов.

## Установка

Самый простой способ: дайте своему coding agent URL этого репозитория и попросите установить skill глобально.

```text
Установи Agent Flow глобально как Codex skill из репозитория:
https://github.com/svishniakov/agent-flow

Если это Codex, клонируй репозиторий в ~/.codex/skills/agent-flow.
Сделай его доступным по префиксам `Agent Flow`, `$agent-flow` и `agent-flow`.
Не запускай Agent Flow без такого префикса.
```

Если ставите руками:

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
```

Если ваша среда читает skills из другой папки, используйте её вместо `~/.codex/skills/agent-flow`.

## Как использовать

Ниже prompts, которые можно копировать и менять под свой проект.

### Быстрый разбор проекта и свод задач

Когда нужно понять, что в проекте уже есть, какие задачи активны и что делать дальше:

```text
Agent Flow Прочитай текущий репозиторий, project memory, README и ключевую документацию. Сделай краткий свод задач: active, blocked, next actions, риски. Ничего не меняй без отдельной просьбы.
```

### Bugfix с проверкой

Когда есть баг, лог, failing test или сломанный сценарий:

```text
Agent Flow Разбери баг: <описание бага>. Найди root cause, исправь минимально, запусти релевантные проверки и верни изменённые файлы, команды проверки и остаточные риски.
```

### Документация по инициативе

Когда идея ещё не готова к реализации, но нужен полный пакет документов:

```text
Agent Flow Подготовь пакет документации для инициативы: <идея>.
Нужны PRD/brief, scope, non-goals, acceptance criteria, архитектурные заметки, design route если есть UI, implementation plan и verification plan. Код не писать, пока документация не согласована.
```

### Полный цикл от идеи до результата

Когда хотите, чтобы агент провёл задачу от постановки до реализации и проверки:

```text
Agent Flow Доведи инициативу от идеи до готового результата: <идея или цель>.
Сначала уточни scope и критерии приёмки, затем выбери минимальный flow, подготовь план, реализуй изменения, проверь результат и дай финальный handoff с файлами, проверками и рисками.
```

### Review готовой работы

Когда нужно проверить PR, branch, patch или план:

```text
Agent Flow Проведи review текущих изменений. Ищи bugs, regressions, missing tests, security/data-loss risks и несоответствие задаче. Вывод: findings по severity, test gaps, open questions, verdict.
```

### UI/design задача

Когда задача касается интерфейса, дизайна, Figma/Pencil, screenshot или frontend:

```text
Agent Flow Разбери UI-задачу: <описание>. Сначала проверь, есть ли approved design source. Если его нет, подготовь design route и DESIGN.md/brief. Реализацию frontend начинай только после согласованного design source.
```

### CI, release, deploy

Когда задача касается CI, release, deploy, auth, payments, secrets или внешних систем:

```text
Agent Flow Проверь release/CI задачу: <описание>. Сначала прочитай project memory и docs по окружению. Не меняй внешние системы без явного approval. Верни evidence, blockers, команды проверки и residual risks.
```

## Субагенты

В репозитории есть встроенные role files для субагентов: `agents/<role>.md`. Список ролей и краткие описания лежат в `references/subagents.md`, stable identities - в `agents/agent-identities.json`.

Важно: префикс Agent Flow сам по себе не разрешает субагентов. Если хотите их использовать, попросите явно:

```text
Agent Flow Используй субагентов для независимого review текущей реализации. Раздели работу по ролям, сохрани handoffs и сведи findings в один итог.
```

Если в вашей среде нет инструмента для запуска субагентов, Agent Flow останется solo workflow.

## English

Agent Flow is a skill for Codex and other coding agents that support skills. It helps route complex work from request to verified outcome: choose a workflow, keep scope tight, read project memory, do the work, and report evidence.

This repository is not an app or framework. It contains:

- `SKILL.md` - entry point and activation rules;
- `references/` - budgets, flows, delegation, traceable runs, Definition of Done;
- `agents/` - bundled subagent role files and stable identities;
- `docs/ru/` - Russian reference docs.

Agent Flow activates only when the user starts the request with one of these prefixes:

```text
Agent Flow <task>
$agent-flow <task>
agent-flow <task>
```

Without that prefix, the agent works normally: no Agent Flow routing, no trace artifacts, no automatic subagents.

## Install

Simplest path: give your coding agent this repository URL and ask it to install Agent Flow globally.

```text
Install Agent Flow globally as a Codex skill from:
https://github.com/svishniakov/agent-flow

If this is Codex, clone the repository into ~/.codex/skills/agent-flow.
Make it available through the `Agent Flow`, `$agent-flow`, and `agent-flow` prefixes.
Do not run Agent Flow unless the user starts the request with one of those prefixes.
```

Manual install:

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
```

If your environment reads skills from another directory, use that path instead.

## Usage Prompts

Copy these prompts and replace the placeholders.

### Project task summary

```text
Agent Flow Read the current repository, project memory, README, and key docs. Return a concise task summary: active items, blockers, next actions, and risks. Do not change files unless I ask.
```

### Bugfix with verification

```text
Agent Flow Investigate this bug: <bug description>. Find the root cause, make the smallest fix, run relevant checks, and report changed files, verification commands, and residual risks.
```

### Initiative documentation package

```text
Agent Flow Prepare a documentation package for this initiative: <idea>.
Include PRD/brief, scope, non-goals, acceptance criteria, architecture notes, design route if UI is involved, implementation plan, and verification plan. Do not write code until the docs are approved.
```

### Full cycle from idea to result

```text
Agent Flow Take this initiative from idea to finished result: <idea or goal>.
First clarify scope and acceptance criteria, then choose the smallest useful flow, plan, implement, verify, and return a final handoff with files, checks, and risks.
```

### Review ready work

```text
Agent Flow Review the current changes. Look for bugs, regressions, missing tests, security/data-loss risks, and mismatch with the task. Output findings by severity, test gaps, open questions, and verdict.
```

### UI/design task

```text
Agent Flow Handle this UI task: <description>. First check whether an approved design source exists. If not, prepare the design route and DESIGN.md/brief. Start frontend implementation only after the design source is approved.
```

### CI, release, deploy

```text
Agent Flow Handle this release/CI task: <description>. Read project memory and environment docs first. Do not mutate external systems without explicit approval. Return evidence, blockers, verification commands, and residual risks.
```

## Subagents

Bundled subagent role files live in `agents/<role>.md`. The role list is in `references/subagents.md`; stable identities are in `agents/agent-identities.json`.

Agent Flow does not use subagents just because the prefix is present. Ask explicitly when you want them:

```text
Agent Flow Use subagents for an independent review of the current implementation. Split the work by role, save handoffs, and synthesize findings into one final result.
```

If your environment has no subagent/spawn tool, Agent Flow stays solo.

## License

MIT. See `LICENSE`.
