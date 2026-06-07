# Agent Flow: описание скилла

Agent Flow - это скилл для Codex, который помогает вести сложную задачу от запроса до проверенного результата. Он не включается автоматически. Пользователь должен начать запрос с одного из префиксов: `Agent Flow`, `$agent-flow` или `agent-flow`.

Главная идея скилла простая: выбрать самый короткий рабочий маршрут, удержать границы задачи, собрать нужный контекст, выполнить работу и проверить результат перед финальным ответом. По умолчанию Agent Flow работает solo: один основной агент отвечает за результат. Субагенты используются только тогда, когда пользователь отдельно попросил использовать subagents, spawn, multi-agent или delegation, и в текущей среде есть инструмент для запуска субагентов.

## Для чего нужен

Agent Flow полезен, когда задача шире одного короткого ответа или одной механической правки. Он помогает:

- разобрать запрос и выбрать подходящий flow;
- не запускать лишний процесс там, где достаточно прямой работы;
- прочитать проектную память и локальные правила перед изменениями;
- не трогать инфраструктуру без необходимости;
- отделить обычную solo-работу от задач с trace artifacts;
- подготовить delegation packet для субагентов, если пользователь явно разрешил их использовать;
- сохранить evidence: проверки, handoffs, timeline, риски и итоговый статус;
- не объявлять работу готовой без свежей проверки.

## Как включается

Скилл включается только в начале пользовательского сообщения:

- `Agent Flow <задача>`
- `$agent-flow <задача>`
- `agent-flow <задача>`

Если префикса нет, запрос остаётся вне Agent Flow. В таком случае Codex работает обычным solo-режимом, без trace artifacts и без автоматической маршрутизации через этот скилл.

## Основные правила

- Agent Flow не является preflight для каждого запроса.
- Нельзя включать его через проектный `AGENTS.md`.
- Нельзя запускать отдельный brainstorming flow перед Agent Flow.
- Субагенты не запускаются по умолчанию.
- Если задача сложная и субагенты могли бы помочь, Agent Flow должен попросить явное разрешение, а не запускать их сам.
- Trace artifacts создаются только когда это оправдано риском, бюджетом или прямой просьбой пользователя.
- `.agent-work/` не должен попадать в product commits.

## Внутренние flow

Agent Flow выбирает минимальный подходящий flow:

- `quick-check-flow` - один короткий ответ, команда или проверка;
- `bugfix-flow` - воспроизведение, исправление, регрессия;
- `feature-flow` - ограниченная реализация новой функции;
- `docs-flow` - документация, PRD, README, спецификация;
- `design-flow` - UI/UX, дизайн-документы, Pencil/Figma/Stitch;
- `ci-release-flow` - CI, релиз, деплой, внешние сервисы;
- `review-flow` - проверка кода, плана или готовности;
- `initiative-flow` - небольшой путь от идеи до готового результата.

## Traceable runs

Для задач с высоким риском, release-gate, CI/deploy, внешними сервисами или явной просьбой пользователя Agent Flow может создать run directory в `.agent-work/runs/YYYY-MM-DD-task-slug/`.

Обычно там лежат:

- `manifest.md`;
- `context.md`;
- `route.md`;
- `plan.md`;
- `checks/`;
- `handoffs/`;
- `artifacts/`;
- `timeline.jsonl`;
- `final.md`.

Timeline фиксирует реальный порядок работы. Если был создан product commit, commit event записывается после успешных проверок и до финального event. Trace artifacts остаются локальной памятью и не добавляются в product commit, если пользователь явно не попросил обратное.

## Lane Sharding

Для больших PRD с явно разрешёнными субагентами Agent Flow может разделить работу на lanes: implementation, integration, QA и review. Это внутренний workflow pattern, а не публичный режим и не обход правила про явное разрешение субагентов.

В traceable run машинным source of truth становится `lane-map.json`. Markdown-файлы вроде `checks/coverage-matrix.md` остаются удобным summary для человека. Перед финальным handoff `validate-run.py` проверяет `lane-map.json` и не допускает `Verdict: ship`, если critical lane не закрыта evidence или валидной replacement lane.

## Субагенты

Репозиторий содержит встроенные role files в `agents/<role>.md` и stable identities в `agents/agent-identities.json`. Эти файлы нужны, чтобы коллеги могли скачать репозиторий с GitHub и получить рабочий набор Agent Flow без ссылок на локальную машину автора.

Субагенты используются только при двух условиях:

- пользователь явно попросил subagents, spawn, multi-agent или delegation;
- среда Codex предоставляет инструмент для запуска субагентов.

Если инструмента нет, role files можно использовать как solo checklist или role lane, но это не считается subagent execution.

Зависимости субагентов описаны в `registries/agent-skills.json`. Файлы ролей в `agents/*.md` указывают, какие skills нужны роли; registry хранит tier, роли, target paths, prompts и инструкции по установке.

После установки Agent Flow рекомендуемый шаг такой:

```bash
python3 ~/.codex/skills/agent-flow/scripts/check-agent-deps.py --post-install
```

Wizard показывает, сколько не хватает в `core` и `full`, и рекомендует начать с core:

- `core` - skills, без которых роль теряет основную функцию: browser, QA, test, find-skills, language/runtime/toolchain и ключевые design/plugin skills;
- `full` - все skills из `agents/*.md`, включая узкие, платные или plugin-gated зависимости.

Target выбирает место установки:

- `global` - глобальная среда пользователя, обычно `~/.codex/skills`;
- `project` - текущий проект, обычно `.codex/skills`.

Тихой установки нет. `--guided-install` выполняет только разрешённые `git`/`local` команды из registry и только после подтверждения `yes`. `plugin`, `prompt` и `manual` entries не выполняются: checker печатает инструкции, официальный prompt или подсказку включить plugin. После guided install checker повторно проверяет missing skills и показывает остаток.

## Проверки

После изменений в скилле стоит запускать:

```bash
python3 -m py_compile scripts/*.py
python3 scripts/validate-agent-skill-registry.py
python3 scripts/check-agent-deps.py --scope core
python3 scripts/init-run.py --help
python3 scripts/append-timeline.py --help
python3 scripts/record-agent-trace.py --help
python3 scripts/validate-run.py --help
python3 scripts/test-validate-run-lanes.py
```

Для проверки языковой чистоты runtime-файлов:

```bash
rg -n '[А-Яа-яЁё]' SKILL.md agents references scripts LICENSE
```

Русский текст допускается только в `README.ru.md` и в этой папке `docs/ru/`.
