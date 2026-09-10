# Agent Flow

[English version](README.md)

Agent Flow - скилл для выполнения задач разработки в Codex Desktop и CLI. Главный агент разбирает задачу, выбирает исполнителей, координирует изменения и проверяет результат. Фреймворк применяется в разных проектах; правила конкретного продукта берутся из его документации.

## Запуск

```text
Agent Flow Реализуй план из docs/implementation-plan.md и проверь результат.
```

Достаточно указать `Agent Flow` в запросе. Также поддерживаются `AgentFlow`, `$agent-flow` и `agent-flow`. Без маркера скилл не включается. Состав команды и объём проверок выбирает главный агент по задаче.

## Основные функции

| Функция | Для чего нужна |
| --- | --- |
| Планирование и координация | Разбить задачу на шаги, назначить исполнителей и собрать результат |
| Контекст проекта | Сохранить решения, текущие задачи и результаты проверок между этапами |
| Архитектурная проверка | Согласовать границы модулей и ограничения до сложных изменений |
| Проверка реализации | Проверить критерии приёмки, тесты и замечания независимого reviewer |
| Исправление ошибок | Разобрать причину сбоя и повторить работу с уточнённым заданием |
| Контроль объёма | Выявить лишние зависимости, абстракции и изменения вне задачи |
| CodeGraph | Найти связи кода, затронутые участки и связанные тесты |
| Уроки проекта | Сохранить подтверждённые выводы для последующих задач |

Исполнители получают конкретную задачу, границы правок и критерии приёмки. Независимый reviewer проверяет изменения перед завершением. Существующий валидатор рабочих записей проверяет результаты делегирования и обязательные проверки.

## Агенты и модели

Профиль содержит 27 ролей: 26 используют Astra, reviewer использует Sol. Главный агент запускается отдельно на `gpt-6-astra/high`; роль `orchestrator` служит его помощником.

Reasoning задаёт глубину рассуждений. В таблице указаны исходный уровень и уровень при эскалации. Эскалация зависит от условий в файле роли. Точный ID модели внутри роли сохраняется, в том числе при повторных попытках. Разные роли могут использовать разные модели.

| Агент | Задачи | Модель | Reasoning | При эскалации |
| --- | --- | --- | --- | --- |
| Главный агент | Ведёт задачу и принимает результат | `gpt-6-astra` | `high` | Настройка сеанса |
| [ai-slops-hunter](skills/agent-flow/agents/ai-slops-hunter.md) | Убирает лишний код и шаблонный текст | `gpt-6-astra` | `medium` | `high` |
| [architect](skills/agent-flow/agents/architect.md) | Определяет архитектуру, границы модулей и план реализации | `gpt-6-astra` | `high` | `xhigh` |
| [backend-worker](skills/agent-flow/agents/backend-worker.md) | Реализует серверную логику, API и работу с данными | `gpt-6-astra` | `medium` | `high` |
| [bun-worker](skills/agent-flow/agents/bun-worker.md) | Работает с Bun, зависимостями, сборкой и тестами | `gpt-6-astra` | `medium` | `high` |
| [design-asset-generator](skills/agent-flow/agents/design-asset-generator.md) | Создаёт изображения и графические материалы | `gpt-6-astra` | `medium` | `high` |
| [design-documenter](skills/agent-flow/agents/design-documenter.md) | Ведёт DESIGN.md и требования к дизайну | `gpt-6-astra` | `medium` | `high` |
| [design-orchestrator](skills/agent-flow/agents/design-orchestrator.md) | Координирует дизайн и передачу макетов в разработку | `gpt-6-astra` | `high` | `xhigh` |
| [documenter](skills/agent-flow/agents/documenter.md) | Пишет планы, спецификации, README и документацию | `gpt-6-astra` | `high` | `xhigh` |
| [frontend-worker](skills/agent-flow/agents/frontend-worker.md) | Реализует интерфейсы, стили и клиентское состояние | `gpt-6-astra` | `medium` | `high` |
| [golang-worker](skills/agent-flow/agents/golang-worker.md) | Реализует Go-сервисы, CLI и тесты | `gpt-6-astra` | `medium` | `high` |
| [ios-worker](skills/agent-flow/agents/ios-worker.md) | Реализует SwiftUI и функции платформ Apple | `gpt-6-astra` | `medium` | `high` |
| [marketing-growth-strategist](skills/agent-flow/agents/marketing-growth-strategist.md) | Прорабатывает позиционирование, запуск и рост продукта | `gpt-6-astra` | `high` | `xhigh` |
| [orchestrator](skills/agent-flow/agents/orchestrator.md) | Помогает главному агенту координировать работу | `gpt-6-astra` | `medium` | `high` |
| [pencil-designer](skills/agent-flow/agents/pencil-designer.md) | Создаёт и проверяет макеты в Pencil | `gpt-6-astra` | `medium` | `high` |
| [product-manager](skills/agent-flow/agents/product-manager.md) | Определяет проблему, ценность, объём и критерии приёмки | `gpt-6-astra` | `high` | `xhigh` |
| [python-worker](skills/agent-flow/agents/python-worker.md) | Реализует Python-код, CLI и обработку данных | `gpt-6-astra` | `medium` | `high` |
| [qa-verifier](skills/agent-flow/agents/qa-verifier.md) | Воспроизводит ошибки и проверяет реализацию | `gpt-6-astra` | `high` | `xhigh` |
| [rag-retrieval-engineer](skills/agent-flow/agents/rag-retrieval-engineer.md) | Проектирует поиск, RAG и проверку качества выдачи | `gpt-6-astra` | `high` | `xhigh` |
| [researcher](skills/agent-flow/agents/researcher.md) | Исследует документацию, API и существующие решения | `gpt-6-astra` | `medium` | `high` |
| [reviewer](skills/agent-flow/agents/reviewer.md) | Независимо проверяет результат, регрессии и полноту тестов | `gpt-5.6-sol` | `high` | `xhigh` |
| [senior-qa-verifier](skills/agent-flow/agents/senior-qa-verifier.md) | Разбирает сбои проверок и уточняет тестовые сценарии | `gpt-6-astra` | `high` | `xhigh` |
| [supervising-architect](skills/agent-flow/agents/supervising-architect.md) | Проводит повторную архитектурную оценку сложных блокеров | `gpt-6-astra` | `xhigh` | `xhigh` |
| [typescript-worker](skills/agent-flow/agents/typescript-worker.md) | Реализует TypeScript/JavaScript-код и тесты | `gpt-6-astra` | `medium` | `high` |
| [ui-reference-researcher](skills/agent-flow/agents/ui-reference-researcher.md) | Подбирает примеры интерфейсов и дизайн-систем | `gpt-6-astra` | `medium` | `high` |
| [ui-ux-design-director](skills/agent-flow/agents/ui-ux-design-director.md) | Выбирает концепцию интерфейса и визуальное направление | `gpt-6-astra` | `high` | `xhigh` |
| [ui-ux-designer](skills/agent-flow/agents/ui-ux-designer.md) | Разрабатывает экраны, сценарии и прототипы | `gpt-6-astra` | `medium` | `high` |
| [visual-qa](skills/agent-flow/agents/visual-qa.md) | Проверяет макеты, адаптивность и соответствие DESIGN.md | `gpt-6-astra` | `high` | `xhigh` |

У главного агента нет отдельного файла роли с настройкой эскалации. Его стартовый уровень `high` задан в [SKILL.md](skills/agent-flow/SKILL.md). Настройки остальных агентов берутся из [файлов ролей](skills/agent-flow/agents); инструкции из этих же файлов передаются в конфигурации Codex.

## Последние изменения

Редакция `6f6c078`, 9 сентября 2026 года:

- Обновлены модели и промпты для профиля Astra/Sol.
- Для `documenter` и `qa-verifier` закреплены исходный `high` и эскалация до `xhigh`.
- Добавлен запрет смены модели внутри одной роли. Повышение reasoning сохранено.
- Восстановлен workflow версии `3d95caf`: прежние маршруты, делегирование, проверки и попытки исправления.
- Удалены добавленные обязательные `model-settings.json` и требования захвата `thread/start` и `turn/start`. Прежние рабочие журналы сохранены.

[Действующее решение по миграции](skills/agent-flow/docs/implementation/impl-007-astra-sol-rollout.md).

## Установка и конфигурации

Глобальная установка для Codex:

```bash
npx skills add https://github.com/svishniakov/agent-flow -a codex -g
python3 ~/.agents/skills/agent-flow/scripts/check-agent-deps.py --post-install
python3 ~/.agents/skills/agent-flow/scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents
```

Проверка зависимостей сообщает о недостающих скиллах. Генератор создаёт или обновляет 27 конфигураций ролей. После изменения файлов ролей повторите генерацию.

Пакет скилла находится в `skills/agent-flow/`. При установке из локальной копии ссылка должна вести в этот каталог.

## Проверки репозитория

Команды из корня репозитория:

```bash
python3 scripts/validate-agent-config.py
python3 scripts/validate-role-catalog.py
python3 scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents --check
python3 scripts/check-all.py
```

Полный набор проверок требует зависимостей из [requirements-codegraph.txt](skills/agent-flow/requirements-codegraph.txt). Ожидаемый итог: `PASS all Agent Flow checks`.

## Документация

- [Инструкции скилла](skills/agent-flow/SKILL.md)
- [Правила делегирования](skills/agent-flow/references/delegation.md)
- [Критерии завершения](skills/agent-flow/references/definition-of-done.md)
- [Рабочие записи и их проверка](skills/agent-flow/references/traceable-runs.md)
- [CodeGraph](skills/agent-flow/docs/adr/adr-001-codegraph.md)
- [Лицензия](LICENSE)
