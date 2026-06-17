# AgentFlow

[English version](README.md)

AgentFlow - локальный фреймворк оркестрации и проверки агентной разработки в
Codex. Он превращает один явно помеченный запрос в управляемый инженерный
workflow: с проектной памятью, ограниченными role lanes, архитектурными
gate-проверками, проверкой trace artifacts и локальным обучением на реальных
прогонах.

Используйте его, когда coding agent должен не просто внести правку, а удержать
scope, показать evidence, оставить проверяемые `handoff`-файлы и ответить на
главный вопрос: что изменилось, почему это безопасно и чем это проверено.

AgentFlow рассчитан на Codex и модели OpenAI. Claude Code, Cursor, Hermes и
другие hosts не входят в scope этого пакета.

## Зачем он нужен

Coding agents часто ломаются одинаково:

- выходят за границы задачи;
- делают архитектурные заявления без подтверждения в файлах или тестах;
- называют работу в role-lane subagent run, хотя subagent trace не было;
- оставляют риск как `pass-with-risks`, но не доводят его до resolution;
- повторяют локальную ошибку, потому что прошлый урок был записан как обычная
  заметка, а не как структурированное evidence.

AgentFlow ставит на такие места явные gate-проверки. Пользователь не выбирает
глубину workflow вручную. Оркестратор читает задачу, смотрит состояние проекта,
выбирает минимально достаточный route и требует evidence до финального ответа.

## Запуск

AgentFlow включается только если последний запрос пользователя содержит один из
маркеров:

```text
Agent Flow <задача>
AgentFlow <задача>
$agent-flow <задача>
agent-flow <задача>
```

Маркер может стоять в начале, середине или конце промпта. Если маркера нет,
запрос остаётся обычной solo-работой Codex. Проектный `AGENTS.md` не может
самостоятельно включить AgentFlow.

## Что контролирует AgentFlow

У фреймворка пять основных слоёв.

### 1. Оркестрация

Оркестратор отвечает за route, порядок работы и финальную интеграцию. Он решает,
останется ли задача solo, нужны ли trace artifacts, role lanes или настоящие
subagents. `light`-задачи остаются компактными. `standard` и `release` могут
подключать architecture, implementation, QA, review и integration lanes, если
дополнительный контроль действительно нужен.

### 2. Проектная память

Перед новой фичей AgentFlow читает task memory текущего проекта. Dependency Gate
останавливает или откладывает новый run, если активная задача может затронуть те
же файлы, API, модель данных, UI flow, тесты, deploy path или acceptance
criteria.

Task Status Completion Gate следит, чтобы память не врала: завершённая работа
переходит из `in_progress` в `done` только после проверки или commit evidence.

### 3. Архитектурный контроль

Для архитектурно чувствительной работы AgentFlow требует Architecture Contract до
старта workers. Trace runs со schema v2 могут включать:

- Architecture Matrix facets для product, surface, stack, risk и verification
  context;
- Architecture Capability Router из
  `registries/architecture-capabilities.json`;
- Architecture Design Mode и approved Architecture Design Brief;
- Architecture Artifact Authoring Automation через
  `init-run.py --architecture-gate`;
- Architecture Context Propagation от architect к workers, QA и reviewer;
- Architecture Execution Control, включая Engineering Simplicity Gate, Simplicity
  Scope Coverage, Lane Boundary Evidence Gate и Claim Evidence Gate.

Это не декоративный checklist. `scripts/validate-run.py` блокирует
положительный финальный verdict, если нужных artifacts нет, они устарели, идут в
неправильном порядке или не подтверждены evidence.

### 4. Проверяемые trace runs

Traceable work сохраняет локальную историю выполнения в run directory. Главный
машинный файл - `lane-map.json`; validator проверяет ownership lanes, handoffs,
artifact paths, timeline events, subagent traces, handoff state, архитектурные
controls и правила финального verdict. Opt-in Handoff State Gate использует
`handoff_state_required` и `scripts/record-handoff-state.py` для состояний
`queued`, `accepted` и `completed`.

Golden Trace Runs в `testdata/golden-traces/` - пакет acceptance-проверок для
runtime. Там есть валидные и специально невалидные runs, чтобы изменения
архитектурного слоя проверялись на полных сохранённых traces.

### 5. Локальное обучение

AgentFlow учится локально, а не глобально. Harness Evaluation Loop пишет
`harness-evaluation.json`, когда run даёт полезный материал для обучения:
continuation, blocked recovery, risk resolution, architecture drift, readiness
recovery или non-positive architecture final.

Проверенные findings можно промоутить только в `## Evidence Records` текущего
проекта. Architecture Matrix, capability registry, role prompts, validator
guards и Golden Trace Runs остаются каноническими runtime artifacts и не
становятся promotion targets для project traces.

Local Best Practice auto gate переиспользует подход только после подтверждения
через Evidence Records analyzer: контекст должен совпадать, reuse boundaries
должны быть записаны, `Do not reuse when` не должен срабатывать, а свежая
проверка должна пройти.

## Что лежит в репозитории

| Path | Назначение |
| --- | --- |
| `SKILL.md` | Codex entrypoint и runtime contract |
| `agents/*.md` | 27 встроенных role prompts |
| `agents/agent-identities.json` | стабильные identities ролей для traces и handoffs |
| `references/architecture-matrix.md` | переиспользуемые архитектурные facets |
| `references/architecture-capability-router.md` | capability routing и Soft Skill Binding |
| `references/architecture-artifact-authoring.md` | контракт generated architecture artifacts |
| `references/traceable-runs.md` | структура run directory и validator contract |
| `references/harness-evaluation-loop.md` | контракт локального обучения |
| `references/definition-of-done.md` | completion gates |
| `references/role-catalog.md` | lifecycle и границы ролей |
| `registries/agent-skills.json` | metadata зависимостей ролей |
| `registries/architecture-capabilities.json` | capability registry |
| `scripts/check-all.py` | полный набор проверок репозитория |
| `scripts/validate-run.py` | validator trace и lane-map |
| `scripts/init-run.py` | generator trace skeleton |
| `scripts/record-handoff-state.py` | recorder состояния Handoff State Gate |
| `scripts/record-lane-boundary.py` | recorder changed-path boundary для worker lanes |
| `scripts/promote-harness-evaluation.py` | promotion из Harness Evaluation в Evidence Records |
| `scripts/analyze-evidence-records.py` | analyzer локального обучения |
| `scripts/test-golden-traces.py` | acceptance runner для Golden Trace Runs |
| `testdata/golden-traces/` | полные valid и invalid trace fixtures |

В репозитории сейчас 27 ролей и 138 role skill dependencies.

## Установка

```bash
git clone https://github.com/svishniakov/agent-flow.git ~/.codex/skills/agent-flow
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

`--post-install` показывает недостающие зависимости ролей и рекомендует набор
`core`. Ничего не устанавливается без явного действия пользователя.

## Обновление

```bash
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py --dry-run
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py
```

Скрипт делает `fetch` из `origin`, показывает локальное состояние и обновляет
только clean checkout через fast-forward. `--overwrite` нужен только тогда,
когда локальные правки или divergent commits действительно надо отбросить.

## Локальные проверки

AgentFlow разрабатывается и проверяется локально. Команды запускаются из корня
репозитория:

```bash
python3 scripts/check-all.py
python3 scripts/check-agent-deps.py --strict
python3 scripts/validate-architecture-capabilities.py
```

Ожидаемая последняя строка `check-all.py`:

```text
PASS all Agent Flow checks
```

## Примеры промптов

Прочитать репозиторий без правок:

```text
Agent Flow Прочитай репозиторий и проектную память. Верни активные задачи, блокеры, следующие действия и риски. Ничего не меняй.
```

Исправить баг с проверкой:

```text
Agent Flow Разбери баг: <описание>. Найди причину, внеси минимальную правку, запусти проверки и верни изменённые файлы плюс остаточные риски.
```

Сделать архитектурно чувствительное изменение:

```text
Agent Flow Реализуй <feature>. Используй architecture gates там, где они нужны, держи worker changes внутри approved boundaries, проверь результат и покажи evidence.
```

Сделать refactor на основе архитектурного анализа:

```text
Agent Flow Перед refactor проанализируй проект на architecture drift. Опиши текущие module boundaries, data flow, public contracts и ownership hotspots. Если refactor не нужен, так и скажи. Если нужен, предложи минимальный behavior-preserving refactor, реализуй только этот scope и запусти релевантные проверки.
```

Убрать overengineering через Simplicity Gate:

```text
Agent Flow Проверь кодовую базу на overengineering через Engineering Simplicity Gate и Simplicity Scope Coverage. Найди лишние abstractions, duplicated helpers, dependency drift, слишком широкие изменения или код под проблемы, которых у нас нет. Убирай только evidence-backed issues, не добавляй новые frameworks, сохрани поведение и проверь cleanup.
```

Подготовить release review:

```text
Agent Flow Подготовь эту feature к release. Прогони architecture, QA и review gates, затем верни статус ship/pass-with-risks/blocked с evidence.
```

## Документация

- [English README](README.md)
- [Architecture Matrix](references/architecture-matrix.md)
- [Architecture Capability Router](references/architecture-capability-router.md)
- [Traceable Runs](references/traceable-runs.md)
- [Harness Evaluation Loop](references/harness-evaluation-loop.md)
- [Definition of Done](references/definition-of-done.md)
- [Subagent Policy](references/subagents.md)
- [Delegation Rules](references/delegation.md)
- [Role Catalog](references/role-catalog.md)
- [English overview](docs/en/agent-flow.md)
- [Русское описание](docs/ru/agent-flow.md)
- [License](LICENSE)
