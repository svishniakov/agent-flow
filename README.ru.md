# Agent Flow

[English version](README.md)

Agent Flow - скилл для разработки в Codex Desktop и CLI. Он разбивает задачу на шаги, подключает специалистов по необходимости, согласует изменения и проверяет результат.

## Использование

Добавьте `Agent Flow` в запрос:

```text
Agent Flow Реализуй согласованный план и проверь результат.
```

Также подходят `AgentFlow`, `$agent-flow` и `agent-flow`. Без маркера скилл не включается.

Используйте его, чтобы:

- Разрабатывать функции и исправлять баги.
- Планировать изменения с участием нескольких специалистов.
- Проверять код и готовую работу.
- Сохранять решения и контекст проекта между задачами.

Опишите нужный результат и ограничения. Укажите план или файлы, если они уже есть.

## Установка

Скачайте `agent-flow-X.Y.Z-skill.zip` из [Releases](https://github.com/svishniakov/agent-flow/releases/latest)
и установите по [инструкции для ZIP](skills/agent-flow/docs/ru/installation.md#готовый-zip-из-releases).
Там же описаны проверка контрольных сумм, настройка ролей и обновление.
До первого пронумерованного выпуска список Releases будет пуст.

[Подготовка окружения разработки и проверки перед коммитом](skills/agent-flow/docs/ru/development.md).

Или установите скилл командой для Codex:

```bash
npx skills add https://github.com/svishniakov/agent-flow -a codex -g
python3 ~/.agents/skills/agent-flow/scripts/check-agent-deps.py --post-install
python3 ~/.agents/skills/agent-flow/scripts/sync-codex-agent-config.py --output-dir ~/.codex/agents
```

Вторая команда сообщает о недостающих скиллах. Третья создаёт конфигурации
специалистов и сохраняет эталон для безопасного обновления. При конфликте она
сохраняет пользовательские файлы и выводит их пути. Повторите её после обновления ролей.

Плагин Codex пока доступен в сборках Actions; [установка из архива](skills/agent-flow/docs/ru/installation.md#плагин-codex).

[Инструкции скилла](skills/agent-flow/SKILL.md) · [Лицензия](LICENSE)
