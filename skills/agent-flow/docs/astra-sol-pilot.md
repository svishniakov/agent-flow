# Профиль Astra/Sol

Agent Flow использует прежний workflow с обновлёнными моделями и промптами. Расширение workflow из первоначального impl-007 отменено; действующее решение описано в [документе миграции](implementation/impl-007-astra-sol-rollout.md).

## Состав

- Главный агент: Astra/high.
- 26 штатных ролей: Astra.
- Reviewer: Sol.
- Documenter и qa-verifier: high → xhigh.
- Остальные уровни и triggers заданы прежней конфигурацией ролей.

Модель при эскалации остаётся той же. Разные роли могут использовать разные модели. Resolver выбирает reasoning по существующим triggers; сценарий выполнения остаётся общим для Desktop и CLI и не зависит от продуктового проекта.

## Локальные проверки

Из корня пакета выполните:

```bash
python3 scripts/test-agent-config.py
python3 scripts/test-sync-codex-agent-config.py
python3 scripts/test-validate-agent-config.py
python3 scripts/test-validate-role-catalog.py
python3 scripts/check-all.py
```

Полный набор включает целевые проверки. Перед выпуском дополнительно проверьте обычный цикл работы на нейтральной задаче через Desktop и CLI. Оценка качества разных моделей и исторические сравнения не являются условием запуска скилла.
