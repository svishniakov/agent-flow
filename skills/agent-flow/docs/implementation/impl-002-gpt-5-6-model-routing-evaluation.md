# План реализации model eval и runtime policy для GPT-5.6

Статус: eval-harness и корпус готовы; pilot выполнен; полное сравнение не запускалось

Дата: 2026-07-10

Основание:
[утверждённый дизайн](../../../../docs/design/2026-07-10-gpt-5-6-model-routing-evaluation.md)

Generic harness и coding-first корпус уже собраны. Все девять evaluator-пакетов
прошли сертификацию: base-снимок падает, gold-снимок проходит. Pilot содержит четыре
запуска Codex по подписке ChatGPT. Отдельный API-ключ и API-тарификация не
используются. Полное сравнение Luna/Terra ещё не запускалось.

## Содержание

- [Результат работы](#результат-работы)
- [Поправки к исходному дизайну](#поправки-к-исходному-дизайну)
- [Границы реализации](#границы-реализации)
- [Целевая структура](#целевая-структура)
- [Этапы](#этапы)
- [Проверка](#проверка)
- [Точки остановки](#точки-остановки)
- [Критерии завершения](#критерии-завершения)

## Результат работы

После выполнения плана Agent Flow должен уметь:

- запускать воспроизводимый coding-first eval на исторических snapshots;
- сравнивать Luna и Terra при одинаковых prompts, tools и reasoning;
- выбирать model/reasoning по роли и фактам задачи;
- продолжать production flow через проверяемую fallback-цепочку;
- вычислять обязательные gates из Task Facts и выводить только активные;
- сохранять запрошенную и фактическую конфигурацию каждого lane;
- не менять модели, reasoning или skills автоматически по сырым результатам eval.

Skills не смешиваются с модельным сравнением. Для Capability Router и skill ablation
после завершения этого плана создаётся отдельный implementation plan.

## Поправки к исходному дизайну

### Scored workspace не может быть обычным git worktree

`git worktree` делит object database с исходным репозиторием. Агент на base revision
сможет найти следующий commit через `git log --all`, `git show` или прямой SHA. Это
раскрывает gold solution и обесценивает сравнение.

Runner должен создавать синтетический репозиторий:

1. Экспортировать base revision через `git archive`.
2. Распаковать snapshot в принадлежащий runner временный каталог.
3. Выполнить `git init`, добавить файлы и создать один synthetic baseline commit.
4. Передать модели только этот репозиторий.
5. Удалить каталог после сохранения evidence.

Исходный репозиторий остаётся read-only источником snapshot. Gold commit, refs и
reflogs в agent workspace отсутствуют.

### Evaluator не запускается в workspace модели

После worker lane runner хранит raw Git patch только в памяти и проверяет, что
workspace не изменился во время сбора evidence. Затем он создаёт новый synthetic
repository из того же base, повторяет setup, применяет raw patch и сверяет пути,
типы файлов и digest содержимого.

Hidden checks добавляются только в этот свежий клон. Во время evaluator-команд весь
workspace доступен только для чтения. Запись разрешена в заранее созданные каталоги
`agent_flow_eval_output/<name>`. Это закрывает подмену hidden test через rename
родительского каталога, hardlink или symlink.

### Cross-repo задачи считаются одной cell, но содержат два worker lane

Задачи `scenarius_campaign_validation` и `scenarius_server_time` нельзя честно
назначить одному существующему worker: backend написан на Go, frontend — на React.
Каждая cell этих задач запускает два lane с одной model/reasoning-конфигурацией:

- `golang-worker` владеет backend snapshot;
- `frontend-worker` владеет frontend snapshot;
- evaluator проверяет оба результата и их совместимость как одну задачу.

Поэтому первая стадия содержит 36–54 task cells и 44–66 запланированных CLI-вызовов.
Лимит с учётом одного retry на lane — 88–132 попытки. План хранит эти числа
раздельно, а каждая cell — фактическое число попыток. Оценка и адаптивный третий
повтор принимаются на уровне cell, а usage суммируется по двум lane.

### Первый корпус даёт прямые данные только по трём ролям

Текущие replay-задачи естественно распределяются так:

- Omnipulse и Aicortex: `bun-worker`;
- Scenarius backend и deliverability: `golang-worker`;
- Scenarius UI: `frontend-worker`.

`backend-worker` и `typescript-worker` не получают прямого role-specific evidence.
Их production routing остаётся без изменений до отдельного корпуса. Назначать эти
роли Bun-задачам только ради покрытия нельзя: такой eval проверял бы искусственный
role contract.

Корпус также не изолирует класс `migration`. Его routing не меняется по результатам
первой серии.

## Границы реализации

### Канонические файлы

Runtime-код живёт в `skills/agent-flow`. Файлы `scripts/*.py` в корне остаются
тонкими wrappers и не содержат второй копии логики.

### Что входит

- generic eval runner и его synthetic fixtures;
- локальный coding-first corpus из девяти задач;
- CLI adapter и единый Lane Result;
- model comparison, reasoning comparison и full-flow validation;
- Task Facts, Model Policy, fallback и Gate Policy;
- validator, golden fixtures и runtime documentation.

### Что не входит

- изменения рабочих деревьев Omnipulse, Scenarius и Aicortex;
- автоматический commit, push, PR или deploy;
- публикация project-specific evaluator fixtures в составе Agent Skills package;
- замена routing для ролей без прямого evidence;
- Capability Router и изменения статических role skill lists;
- новый lane-map schema version, если текущую schema v2 можно расширить совместимо.

### Локальные данные корпуса

Generic harness и synthetic tests входят в пакет. Реальные task prompts, gold
metadata, evaluator fixtures и результаты хранятся отдельно от устанавливаемого
skill:

```text
.agent-work/model-evals/
  corpora/coding-first-v1/
    manifest.json
    certification.json
    tasks/<task-id>/prompt.md
    evaluators/<task-id>/
  runs/<suite-id>/
    plan.json
    provenance.json
    cells.json
    cells/<cell-id>/
    score.json
    report.md
```

Manifest использует логические repository ids и переменные окружения. Локальные
абсолютные пути не попадают в канонические docs, registries или trace.

## Целевая структура

### Generic eval harness

Новые канонические файлы:

- `skills/agent-flow/scripts/model-eval.py` — CLI с командами `validate`, `certify`,
  `run`, `score` и `report`;
- `skills/agent-flow/scripts/model_eval_manifest.py` — чтение и строгая проверка
  corpus manifest;
- `skills/agent-flow/scripts/model_eval_workspace.py` — synthetic snapshot,
  ownership marker и cleanup;
- `skills/agent-flow/scripts/model_eval_adapter.py` — построение Codex CLI command,
  retry, timeout, redaction и Lane Result;
- `skills/agent-flow/scripts/model_eval_evaluator.py` — подготовка зависимостей,
  безопасная инъекция hidden checks и классификация результата;
- `skills/agent-flow/scripts/model_eval_sandbox.py` — изолированный permission profile,
  минимальное окружение и запрет сети;
- `skills/agent-flow/scripts/model_eval_process.py` — завершение всей группы процессов
  при timeout или выходе родителя;
- `skills/agent-flow/scripts/model_eval_runner.py` — paired schedule, границы lane и
  проверка неизменности исходных репозиториев;
- `skills/agent-flow/scripts/model_eval_score.py` — cell aggregation, adaptive repeat
  и лексикографическая оценка;
- `skills/agent-flow/scripts/task_facts.py` — единая схема Task Facts для eval и
  runtime policy;
- `skills/agent-flow/testdata/model-evals/agent-output.schema.json` — model-authored
  output для `codex exec --output-schema`; trusted runtime fields добавляет adapter;
- `skills/agent-flow/testdata/model-evals/fixtures/` — только synthetic repositories
  для тестов harness;
- `skills/agent-flow/scripts/test-model-eval-manifest.py`;
- `skills/agent-flow/scripts/test-model-eval-workspace.py`;
- `skills/agent-flow/scripts/test-model-eval-adapter.py`;
- `skills/agent-flow/scripts/test-model-eval-evaluator.py`;
- `skills/agent-flow/scripts/test-model-eval-sandbox.py`;
- `skills/agent-flow/scripts/test-model-eval-process.py`;
- `skills/agent-flow/scripts/test-model-eval-runner.py`;
- `skills/agent-flow/scripts/test-model-eval-score.py`;
- `skills/agent-flow/scripts/test-task-facts.py`.

В корне добавляются тонкие wrappers `scripts/model-eval.py` и
`scripts/test-*.py` для новых test entrypoints. Internal modules импортируются из
канонического каталога и не копируются.

### Runtime policy

После утверждения результатов добавляются:

- `skills/agent-flow/registries/model-policy.json` — versioned route matrix и
  подтверждённые equivalents;
- `skills/agent-flow/registries/gate-catalog.json` — gate id, owner, evidence contract
  и blocking semantics без routing-условий;
- `skills/agent-flow/scripts/model_policy.py` — requested configuration и ordered
  fallback candidates;
- `skills/agent-flow/scripts/gate_policy.py` — детерминированные mandatory gates и
  add-only optional gates;
- focused tests для каждого policy-компонента.

Существующие `agent_config.py` и role frontmatter остаются безопасным baseline. Они
не заменяются до того, как новая policy пройдёт eval и full-flow validation.

## Этапы

### Этап 0. Зафиксировать safety contract

Цель — не допустить затратных запусков поверх небезопасного harness.

Действия:

1. Зафиксировать перед каждым suite `HEAD` и `git status --porcelain` исходных
   репозиториев.
2. Проверять наличие base и gold SHA, не выполняя checkout в исходном дереве.
3. Запретить shell-команды из manifest: evaluator commands хранятся как argv arrays и
   запускаются с `shell=False`.
4. Ввести ownership marker для каждого временного каталога. Cleanup принимает только
   каталог внутри созданного runner temp root с совпадающим suite/cell id.
5. Не сохранять environment dump, auth files, cookies, tokens или содержимое secret
   variables.
6. Зафиксировать версию Codex CLI и результат `codex login status` без auth details.
7. Зафиксировать toolchain versions и lockfile digests для Bun, Go и pnpm projects.
8. Проверить base trees на git submodules и LFS pointers. Задача не допускается в
   suite, пока runner не умеет воспроизводимо подготовить такие файлы.

Проверка этапа:

```bash
python3 scripts/test-model-eval-workspace.py
python3 scripts/test-model-eval-manifest.py
```

Stop condition: synthetic fixture должна доказать, что agent workspace не содержит
gold object, gold ref или путь к исходному репозиторию.

### Этап 1. Реализовать и проверить corpus manifest

Manifest описывает:

- task id и product id;
- один или несколько repository ids;
- base и gold revisions;
- worker lanes, ownership и primary workspace;
- Task Facts и task classes;
- prompt path;
- allowed и forbidden paths;
- evaluator id и commands;
- timeout, retry policy и resource limits;
- repeat policy и scoring criticality.

`model_eval_manifest.py` использует `task_facts.py`; второй схемы Task Facts внутри
harness нет.

Agent-facing packet строится из allowlist полей. В него не попадают gold SHA,
evaluator path, hidden checks или исходные repository paths.

Validator отклоняет:

- неизвестную role, model, reasoning или repository id;
- абсолютные пути в переносимой части manifest;
- одинаковые base и gold;
- отсутствующий prompt или evaluator;
- task без positive и negative acceptance checks;
- cross-repo cell без отдельного ownership для каждого lane;
- команды в виде shell string.

Локальный manifest создаётся для всех девяти утверждённых task ids. Role mapping:

- `omnipulse_media_url`: `bun-worker`;
- `omnipulse_literal_schedule`: `bun-worker`;
- `omnipulse_registration_destination`: `bun-worker`;
- `scenarius_campaign_validation`: `golang-worker` + `frontend-worker`;
- `scenarius_server_time`: `golang-worker` + `frontend-worker`;
- `scenarius_deliverability_history`: `golang-worker`;
- `aicortex_trace_db_ownership`: `bun-worker`;
- `aicortex_parse_review_read_only`: `bun-worker`;
- `aicortex_footnoted_response_deadline`: `bun-worker`.

Source override consolidation исключён из scored corpus: это рефакторинг без
наблюдаемого изменения поведения, поэтому base и gold нельзя честно различить без
проверки внутренней структуры реализации.

Проверка этапа:

```bash
python3 scripts/model-eval.py validate --corpus .agent-work/model-evals/corpora/coding-first-v1
python3 scripts/test-model-eval-manifest.py
```

### Этап 2. Реализовать synthetic workspace lifecycle

Для каждого lane runner:

1. Вызывает `git archive` для base revision.
2. Безопасно распаковывает snapshot, отклоняя absolute paths, `..` и unsafe links.
3. Создаёт новый git repository с одним baseline commit.
4. Готовит зависимости по lockfile до начала scored time.
5. Записывает `provenance.json` с версиями Codex CLI, Python, Git, Bun, Go, Node и
   pnpm, статусом авторизации без данных учётной записи и digest lock-файлов для
   base/gold.
6. После agent run фиксирует changed paths, patch и итоговый status.
7. Перед cleanup сохраняет evaluator evidence.
8. Сверяет исходные `HEAD` и status с pre-run snapshot.

Dependency provisioning выполняется одинаково для paired cells. Runner использует
frozen lockfiles и общий предварительно прогретый cache, но создаёт отдельное
workspace-local dependency tree. Network во время agent run и evaluator checks
запрещён. Неудачная подготовка зависимости — infrastructure error, а не model fail.

Для Scenarius создаются два synthetic repositories. Codex CLI получает backend через
`--cd`, frontend через подтверждённый CLI flag `--add-dir`.

Тесты работают на disposable repositories и покрывают:

- dirty source repository остаётся byte-for-byte и status-for-status прежним;
- later commit отсутствует в synthetic object database;
- cleanup не принимает source root или чужой temp directory;
- interrupted run можно безопасно найти и удалить по ownership marker;
- multi-repo cell сохраняет раздельные patches.

### Этап 3. Реализовать CLI adapter и Lane Result

Adapter использует проверенные возможности Codex CLI `0.144.1`:

```text
codex --model <model> \
  -c 'model_reasoning_effort="<effort>"' \
  -c 'model_catalog_json="<disposable-codex-home>/model-catalog.json"' \
  -c 'features.multi_agent=false' \
  -c 'features.multi_agent_v2={enabled=false,max_concurrent_threads_per_session=1,root_agent_usage_hint_text="",subagent_usage_hint_text=""}' \
  -c 'features.enable_fanout=false' \
  -c 'agents.max_threads=1' \
  -c 'default_permissions="agent-flow-eval"' \
  [generated permission-profile overrides] \
  --ask-for-approval never \
  exec --ignore-user-config --strict-config \
  --json \
  --output-schema <lane-result-schema> \
  --output-last-message <result-path> \
  --cd <primary-workspace> [--add-dir <secondary-workspace>]
```

Prompt передаётся через stdin, а не shell interpolation.

Каждый lane получает одноразовый `CODEX_HOME`, содержащий только auth-файлы.
Без `--ephemeral` Codex пишет rollout. Adapter находит единственный rollout по
`thread_id`, читает из него только `session_meta` и `turn_context`, затем cleanup
удаляет весь `CODEX_HOME`. Полный rollout в artifacts не попадает.

`model_catalog_json` указывает на disposable-копию
`testdata/model-evals/codex-0.144.1-gpt-5.6-model-catalog.json`. Файл содержит полные
записи Sol, Terra и Luna из catalog Codex `0.144.1`; удалено только поле
`multi_agent_version`. Его SHA-256 —
`0633a84ff035a4484890f65145b38d93a72acddccf1d837b6c623cd42d4073d3`.
Catalog входит в execution harness fingerprint.

Model-level `multi_agent_version` имеет приоритет над обычными feature flags. После
его удаления выключенные `multi_agent`, `multi_agent_v2` и `enable_fanout` дают
disabled-режим. Context probe строится для target model/reasoning и проверяет, что
`spawn_agent` не попал в текстовый prompt; состав tools он не доказывает. После
каждой попытки adapter принимает только rollout-файлы известных root thread ids.
Неучтённый child rollout даёт `invalid-trace`.

Такое evidence подтверждает конфигурацию клиента и отсутствие сообщённого reroute,
но не даёт криптографической аттестации серверной модели. `exact` в этом harness
означает: Codex настроил запрошенные model/reasoning, версия CLI равна `0.144.1`,
provider равен `openai`, reroute не зарегистрирован. Любой зарегистрированный reroute
даёт `substituted`; отсутствующий или противоречивый rollout даёт `unverified`. Оба
статуса исключаются из target score.
Score принимает только `exact`. Статус `equivalent` используется production router,
но не заменяет результат запрошенной eval-конфигурации.

Adapter обязан:

- до создания artifacts проверить точную версию CLI `0.144.1` и вход через подписку
  ChatGPT; `api-key` и `unavailable` останавливают только eval-запуск;
- применять закреплённый model catalog без `multi_agent_version`, выключать
  multi-agent/fan-out flags и лишний rollout; prompt probe ищет текстовую утечку
  `spawn_agent`, но не считается доказательством состава tools. Child runs сделали
  бы model selection, usage и patch attribution неполными;
- фиксировать requested и selected model/reasoning;
- разрешать не более одного retry на lane: для timeout/429/5xx либо для невалидного
  structured output;
- требовать ровно один полный `turn.completed.usage` от каждой попытки. Попытка без
  полного usage немедленно даёт `invalid-trace`, не повторяется и не засчитывается;
- для `exact` требовать отдельные уникальные `thread_id` и `turn_id`, полный usage и
  непротиворечивое rollout evidence каждой попытки;
- повторный отказ structured output при
  операционном `selection_status=exact` считается невыполненным output contract, а
  не поломкой инфраструктуры;
- различать task failure, infrastructure error и substituted run;
- редактировать auth-like значения в stdout/stderr artifacts;
- не использовать optional skills в model eval;
- возвращать один Lane Result независимо от terminal exit path.

Минимальный eval context состоит из role prompt, project rules, task packet и output
schema. Перед pilot выполняется isolation probe. Если CLI снова сообщает об обрезке
skills context или trace показывает необъявленный optional skill, cell получает
`invalid-context`, а scored suite не запускается.

Unit tests не вызывают модель. Они используют fake CLI process и проверяют command
argv, retry classification, timeout, JSONL parsing, schema validation и redaction.

### Этап 4. Создать девять evaluator packages

Каждый evaluator сначала проверяется на двух snapshots:

- base обязан провалить хотя бы один acceptance check;
- gold обязан пройти обязательные checks.

Если это условие не выполнено, задача не допускается в pilot или suite.

Обязательные свойства evaluators:

- проверяют поведение и контракт, а не строки реализации;
- содержат positive и negative fixtures;
- не зависят от network, production credentials или внешних writes;
- разделяют model failure и harness failure;
- для Scenarius 4 и 5 проверяют backend, frontend и интеграционный контракт;
- для UI-задачи используют deterministic component/contract tests. В Scenarius нет
  отдельного component-test runtime, поэтому две frontend-задачи запускают
  существующий локальный Playwright contract с mocked API и без внешней сети;
  full-flow stage добавляет более широкий browser evidence;
- сохраняют command, exit code и краткий evidence artifact.

Gold diff можно читать только при подготовке evaluator. Он не используется для
сравнения patch similarity и не передаётся модели или judge.

### Этап 5. Провести четырёхзапусковый pilot

Pilot-задача: `omnipulse_media_url`.

Конфигурации:

- `gpt-5.6-luna`, `medium`, два повтора;
- `gpt-5.6-terra`, `medium`, два повтора.

Pilot проверяет harness, а не выбирает победителя. Он считается успешным, если:

- все четыре cells завершились с валидным Lane Result;
- исходный Omnipulse status и `HEAD` не изменились;
- ни один trace не содержит gold SHA или evaluator path;
- rollout содержит запрошенную client-конфигурацию model/reasoning и не содержит
  зарегистрированного reroute;
- optional skill context отсутствует;
- evaluator различает base и gold;
- patches, checks, usage и duration сохранились.

Провал task acceptance одной или обеими моделями не означает провал harness. Pilot
останавливается только при проблеме изоляции, контракта, evaluator или adapter.

Четыре запуска pilot выполняются только после отдельного подтверждения. Это запуски
Codex по подписке ChatGPT, а не API-вызовы с отдельной тарификацией.

### Этап 6. Провести Luna/Terra comparison

После успешного pilot runner создаёт randomized paired schedule:

- девять задач;
- Luna `medium` и Terra `medium`;
- два повтора каждой task/config cell;
- третий парный повтор только при нестабильности или конфликте quality verdict.

Размер:

- 36 начальных и до 54 task cells;
- 44 начальных и до 66 запланированных запусков Codex CLI из-за двух двух-lane
  Scenarius tasks;
- не более 88 начальных и 132 адаптивных попыток с учётом retry. Фактическое число
  хранится в `model_attempts` каждой cell.

Каждый schedule item хранит собственные `scheduled_model_calls` и `max_model_calls`.
Оба значения входят в plan fingerprint. Score проверяет границы отдельно для каждой
cell, поэтому глобальная сумма не может скрыть недосчёт одной cell за счёт другой.
Runner увеличивает `model_attempts` сразу после возврата lane adapter — до проверки
isolation, usage и selection evidence. Инфраструктурный отказ поэтому не стирает уже
выполненную попытку.

Runner не запускает две конфигурации одной task cell параллельно на одном hardware
slot. Порядок внутри пары чередуется, чтобы систематический warm-cache или load effect
не помогал одной модели.

Sol `medium` запускается только если обе модели дважды провалили одну задачу. Такой
run маркируется `diagnostic` и не входит в рейтинг.

После suite создаются:

- `score.json` с raw cell outcomes и лексикографической агрегацией;
- `report.md` без заранее выбранного победителя;
- список unstable cells и причин третьего повтора;
- role/task-class coverage matrix;
- список unavailable/substituted cells, исключённых из target score.

`report` не доверяет отдельному `score.json`. Команда требует исходные `plan.json`
и `cells.json`, заново строит canonical plan по текущему corpus и harness, повторно
считает score и только после точного совпадения выводит Markdown:

```bash
python3 scripts/model-eval.py report \
  --corpus .agent-work/model-evals/corpora/coding-first-v1 \
  --score <run>/score.json \
  --plan <run>/plan.json \
  --cells <run>/cells.json \
  --output <run>/report.md
```

`model_eval_score.py` сравнивает конфигурации в таком порядке:

1. меньше критических ошибок;
2. больше пройденных задач;
3. меньше нестабильных task cells;
4. больше blind pairwise wins среди решений, прошедших обязательные checks;
5. меньше token usage, затем duration.

Cross-repo cell получает pass только если оба worker lane и интеграционный check
успешны. Один прошедший lane не компенсирует второй.

LLM judge включается только при ничьей после детерминированных критериев качества.
Judge использует `gpt-5.6-sol` с `medium`, не видит model ids и получает A/B patches в
двух перестановках. Если verdict меняется при перестановке, результат остаётся tie.
Judge calls учитываются отдельно и не входят в worker-call budget.

### Этап 7. Сравнить reasoning

Модель-победитель сравнивается на `medium` и `low` тем же paired protocol. Входные
snapshots, prompts, evaluator versions и skill profile остаются прежними.

`low` принимается для конкретной role/task-class cell только если:

- нет новых критических ошибок;
- task pass rate не ниже `medium`;
- стабильность не хуже;
- public-contract evidence не потеряно;
- выигрыш по usage или duration измерим.

Нельзя снижать reasoning глобально по среднему результату, если критичный класс не
покрыт или нестабилен.

### Этап 8. Реализовать Task Facts и Model Policy

Task Facts получают строгую схему. Минимальные поля:

```json
{
  "role": "bun-worker",
  "changes_files": true,
  "repo_count": 1,
  "surfaces": ["backend"],
  "task_classes": ["public-contract"],
  "public_contract": true,
  "migration": false,
  "external_write": false,
  "production_risk": "normal"
}
```

Primary task class выбирается детерминированно:

1. `high-risk`;
2. `migration`;
3. `public-contract`;
4. `cross-repo`;
5. `local`.

Все совпавшие classes остаются в trace. Primary class используется только для
route lookup, поэтому policy можно воспроизвести без объяснений модели.

`model-policy.json` создаётся только после просмотра eval report. Registry содержит:

- policy version;
- role и primary task class;
- requested model/reasoning;
- eval-confirmed equivalents в порядке предпочтения;
- evidence suite id и coverage limits;
- дату и основание решения.

Для `backend-worker`, `typescript-worker`, `python-worker`, `ios-worker`, migration и
не-coding roles первая версия registry сохраняет текущую конфигурацию.

`resolve-agent-config.py` остаётся совместимым с текущими callers. При переданных Task
Facts он дополнительно возвращает requested configuration, candidate chain, policy
version и selection reason. Без Task Facts работает существующий frontmatter path.

### Этап 9. Реализовать non-blocking fallback и Execution Adapter

Production attempt order:

1. requested configuration;
2. один retry для transient failure;
3. первый eval-confirmed equivalent для role/task class;
4. более сильная доступная конфигурация;
5. inherited current-session model через native host path;
6. любая совместимая конфигурация, которую предоставляет host.

Policy возвращает ordered candidates, но не объявляет модель доступной заранее.
Execution Adapter фиксирует фактический результат каждой попытки.

Native `spawn_agent` нельзя вызывать из Python runtime напрямую. Поэтому общий
контракт делится так:

- Python adapter выполняет exact CLI attempts и создаёт Lane Result;
- orchestrator выполняет native fallback через host tool;
- `record-agent-trace.py` нормализует native handoff в тот же Lane Result со статусом
  `inherited`;
- validator проверяет requested/selected/fallback evidence одинаково для двух paths.

Если model policy registry повреждён или недоступен, frontmatter configuration
остаётся baseline, а verification становится `elevated`. Ошибка конкретной модели не
создаёт verdict `blocked`.

### Этап 10. Реализовать Gate Policy

Сначала составляется каталог уже существующих gates. Новый gate добавляется только
если текущий набор не может выразить обязательное доказательство.

`gate-catalog.json` хранит метаданные. Условия активации остаются в тестируемом
`gate_policy.py`, а не в свободном текстовом prompt или мини-языке выражений.

Алгоритм:

1. Оркестратор формирует Task Facts.
2. Gate Policy вычисляет mandatory gates.
3. Оркестратор может добавить optional gates с причиной.
4. Validator повторяет вычисление mandatory set.
5. Отсутствующий mandatory gate отклоняет положительный run.
6. Output получает только `active_gates`.

`selection_status=degraded` не добавляет новый gate. У уже активных verification
gates меняется `verification_level` на `elevated`.

Интеграционные изменения:

- `init-run.py` принимает `--task-facts-json` и optional gate ids;
- lane-map schema v2 получает `task_facts`, `policy_version` и `active_gates`;
- `validate-run.py` пересчитывает mandatory gates;
- `test-init-run.py` проверяет authoring;
- `test-validate-run-lanes.py` получает positive и negative fixtures;
- `check-all.py` запускает новые focused tests и guard checks.

Старые golden traces без `policy_version` остаются legacy fixtures. Новые runs,
созданные обновлённым `init-run.py`, обязаны содержать Task Facts и active gates.

Обязательные policy examples:

- Scenarius server time активирует contract/surface, UI verification, architecture и
  independent QA evidence;
- Aicortex read-only GET активирует code verification, negative fixture и independent
  QA, но не UI или migration evidence;
- degraded fallback сохраняет тот же набор gates и повышает уровень проверки.

### Этап 11. Провести full-flow validation

Выбирается по одной задаче от Omnipulse, Scenarius и Aicortex с максимальным
расхождением Luna/Terra. Если расхождения нет, берётся самая сложная задача продукта.

Сравниваются:

- current production policy как control;
- proposed Model/Gate Policy как candidate.

Каждая конфигурация получает два повтора на каждой из трёх задач: 12 full-flow cells.
Этот этап дороже worker eval, потому что включает orchestrator, workers, reviewer,
independent QA, gates и handoffs. Для него нужен отдельный execution approval.

Candidate принимается, если:

- нет новых критических ошибок относительно control;
- обязательные gates выбраны и закрыты доказательствами;
- fallback trace правдив;
- итоговый task pass rate не ниже control;
- validator принимает все положительные runs;
- source repositories остаются неизменными.

Full-flow result не пересчитывает чистый worker score. Он решает, можно ли включить
policy в runtime.

### Этап 12. Обновить runtime docs и release guards

После успешного full-flow stage обновляются только затронутые канонические contracts:

- `skills/agent-flow/references/delegation.md`;
- `skills/agent-flow/references/orchestrator.md`;
- `skills/agent-flow/references/subagents.md`;
- `skills/agent-flow/references/traceable-runs.md`;
- `skills/agent-flow/references/definition-of-done.md`;
- `skills/agent-flow/agents/orchestrator.md`;
- role prompts только там, где меняется реальный input/output contract;
- README и RU/EN overviews после стабилизации CLI и policy fields.

Документация не должна утверждать, что `spawn_agent` принимает model/reasoning
overrides, пока host schema этого не поддерживает.

### Этап 13. Отдельно спланировать skills

К этому этапу model, reasoning, fallback и gates уже зафиксированы. Затем создаётся
`impl-003` для Capability Router и skill ablation:

- control: без optional skills;
- candidate: 1–3 router-selected skills;
- одна и та же утверждённая model/reasoning policy;
- отдельные score и promotion decision.

До этого момента статические `skills:` в role frontmatter не меняются.

## Проверка

### Focused tests

```bash
python3 scripts/test-model-eval-manifest.py
python3 scripts/test-model-eval-workspace.py
python3 scripts/test-model-eval-adapter.py
python3 scripts/test-model-eval-score.py
python3 scripts/test-task-facts.py
python3 scripts/test-model-policy.py
python3 scripts/test-gate-policy.py
python3 scripts/test-agent-config.py
python3 scripts/test-validate-agent-config.py
python3 scripts/test-init-run.py
python3 scripts/test-validate-run-lanes.py
```

### Repository checks

```bash
python3 scripts/check-all.py
python3 scripts/check-agent-deps.py --strict
python3 scripts/validate-architecture-capabilities.py
git diff --check
```

### External repository invariant

До и после pilot, suite и full-flow stage сравниваются:

- `git rev-parse HEAD`;
- `git status --porcelain=v1 --untracked-files=all`;
- список существующих worktrees;
- отсутствие новых branches, tags и refs от runner.

Любое расхождение, созданное runner, считается критической ошибкой harness.

## Точки остановки

Scored-запуски Codex не начинаются, если выполняется хотя бы одно условие:

- synthetic workspace видит gold commit или исходный repository path;
- optional skills попали в model context;
- evaluator не различает base и gold;
- manifest содержит непроверенную команду или path;
- rollout не подтверждает `selection_status=exact` в операционном смысле этого
  harness;
- structured output не проходит schema;
- исходный project status изменился;
- redaction test находит credential-like данные в artifacts.

Suite продолжает работу и не приписывает fail модели, если одна cell получила
infrastructure error, target model unavailable или evaluator defect. Такая cell
исключается из target score и остаётся в отчёте.

После pilot, model comparison, reasoning comparison и full-flow validation есть
отдельная точка просмотра результатов. Runtime registry не меняется до явного
утверждения следующего шага.

## Критерии завершения

План выполнен, когда:

- generic harness покрыт synthetic tests и включён в `check-all.py`;
- девять local evaluators доказанно fail на base и pass на gold;
- pilot подтвердил изоляцию и операционный `selection_status=exact` для
  model/reasoning;
- Luna/Terra и reasoning reports воспроизводимы;
- routing меняется только для покрытых role/task classes;
- production fallback не блокирует flow из-за одной недоступной модели;
- Gate Policy воспроизводится из Task Facts и валидируется независимо;
- full-flow candidate не хуже control по критическим ошибкам и task pass rate;
- исходные рабочие деревья трёх продуктов не изменились;
- optional skills не участвовали в model eval;
- commit, push и публикация выполняются только по отдельному запросу.
