# Agent Flow: описание скилла

Agent Flow - это скилл для Codex, который помогает вести сложную задачу от запроса до проверенного результата. Он не включается автоматически. Пользователь должен написать в prompt один из маркеров запуска: `Agent Flow`, `AgentFlow`, `$agent-flow` или `agent-flow`.

Главная идея скилла простая: пользователь запускает Agent Flow одним явным маркером в любой части prompt, а оркестратор сам выбирает подходящий маршрут. Он удерживает границы задачи, проверяет активные работы в проекте на зависимости, собирает нужный контекст, под капотом переключает внутренние budgets, решает, нужны ли субагенты, выполняет работу и проверяет результат перед финальным ответом.

Поддерживаемая среда - Codex с моделями OpenAI. Claude Code, Cursor, Hermes и другие hosts вне scope этого пакета.

## Для чего нужен

Agent Flow полезен, когда задача шире одного короткого ответа или одной механической правки. Он помогает:

- разобрать запрос и выбрать подходящий workflow без ручного выбора режима пользователем;
- не запускать лишний процесс там, где достаточно прямой работы;
- прочитать проектную память и локальные правила перед изменениями;
- остановить новую фичу, если активная работа в проекте может на неё повлиять;
- не трогать инфраструктуру без необходимости;
- отделить обычную solo-работу от задач с trace artifacts;
- подготовить delegation packet для субагентов, если форма задачи оправдывает делегирование;
- сохранить evidence: проверки, handoffs, timeline, риски и итоговый статус;
- не объявлять работу готовой без свежей проверки.

## Как включается

Скилл включается, когда последнее сообщение пользователя содержит один из маркеров запуска:

- `Agent Flow <задача>`
- `AgentFlow <задача>`
- `$agent-flow <задача>`
- `agent-flow <задача>`

Если маркера запуска нет, запрос остаётся вне Agent Flow. В таком случае Codex работает обычным solo-режимом, без trace artifacts и без автоматической маршрутизации через этот скилл.

## Основные правила

- Agent Flow не является preflight для каждого запроса.
- Нельзя включать его через проектный `AGENTS.md`.
- Нельзя запускать отдельный brainstorming flow перед Agent Flow.
- Пользователь не выбирает budgets и не обязан явно просить субагентов.
- Оркестратор сам решает по контексту, оставить задачу solo или подключить субагентов.
- Перед новой фичей оркестратор проверяет активные задачи со статусом `in_progress` и `blocked`. Если одна из них может повлиять на новую работу, Agent Flow останавливает старт и рекомендует подождать или объединить работы в один coordinated run.
- После успешной проверки или product commit текущая секция задачи должна перейти из `in_progress` в `done`, если checklist закрыт и blocker больше нет.
- Trace artifacts создаются только когда это оправдано риском, внутренним routing decision или прямой просьбой пользователя.
- `.agent-work/` не должен попадать в product commits.

## Dependency Gate

Dependency Gate защищает отдельные feature-сессии от конфликтов. Перед стартом новой фичи оркестратор читает проектную память и смотрит задачи со статусом `in_progress` или `blocked`.

Перед блокировкой он проверяет stale завершённые секции. Если у секции `in_progress` отмечены все пункты checklist, записана проверка и нет blocker, оркестратор сначала закрывает её как `done`. Если evidence не хватает, gate считает секцию `uncertain` и просит проверить или закрыть её перед новой работой.

Если активная задача может изменить те же файлы, API contracts, модель данных, UI flow, тесты, deploy path или критерии приёмки, новая сессия останавливается до planning и implementation. Предупреждение называет активную задачу, объясняет практический риск и рекомендует дождаться результата текущей фичи.

Пользователь может явно принять риск и продолжить. Второй вариант - объединить работы в один coordinated Agent Flow run. Внутренние lanes одного Agent Flow run этот gate не блокирует.

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

Для больших PRD или релизных задач Agent Flow может разделить работу на lanes: implementation, integration, QA и review. Это внутренний workflow pattern, а не публичный режим для пользователя.

В traceable run машинным source of truth становится `lane-map.json`. Markdown-файлы вроде `checks/coverage-matrix.md` остаются удобным summary для человека. Перед финальным handoff `validate-run.py` проверяет `lane-map.json` и не допускает `Verdict: ship`, если critical lane не закрыта evidence или валидной replacement lane.

## Субагенты

Репозиторий содержит встроенные role files в `agents/<role>.md` и stable identities в `agents/agent-identities.json`. Эти файлы нужны, чтобы коллеги могли скачать репозиторий с GitHub и получить рабочий набор Agent Flow без ссылок на локальную машину автора.

Субагенты используются только при двух условиях:

- оркестратор решил, что они нужны для проверки, исследования, review или параллельной работы;
- среда Codex предоставляет инструмент для запуска субагентов.

Если инструмента нет, role files можно использовать как solo checklist или role lane, но это не считается subagent execution. В финальном ответе нужно явно сказать о таком downgrade.

Зависимости субагентов описаны в `registries/agent-skills.json`. Файлы ролей в `agents/*.md` указывают, какие skills нужны роли; registry хранит tier, роли, target paths, prompts и инструкции по установке.

Frontmatter ролей намеренно узкий: однострочные записи `key: value` и inline lists вроде `[Read, Write]`. Разрешены отдельные строки-комментарии и YAML-like inline comments; multiline YAML по-прежнему не поддерживается, чтобы validation оставалась предсказуемой.

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

## Обновление

Для обновления уже установленного Agent Flow используйте встроенный updater, а не повторный `git clone` поверх существующей папки:

```bash
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py --dry-run
python3 ~/.codex/skills/agent-flow/scripts/update-agent-flow-skill.py
```

`--dry-run` делает `fetch` и показывает, clean checkout или нет, отстаёт он от remote, опережает его или разошёлся с ним. Реальное обновление делает только fast-forward для clean checkout. Чтобы отбросить локальные правки или divergent commits, нужно явно запустить с `--overwrite`.

## Проверки

После изменений в скилле стоит запускать:

```bash
python3 scripts/check-all.py
python3 scripts/check-agent-deps.py --scope core
python3 scripts/validate-role-catalog.py
```

`check-all.py` должен закончиться строкой `PASS all Agent Flow checks`.

Для проверки языковой чистоты runtime-файлов:

```bash
rg -n '[А-Яа-яЁё]' SKILL.md agents references scripts LICENSE
```

Русский текст допускается только в `README.ru.md` и в этой папке `docs/ru/`.
