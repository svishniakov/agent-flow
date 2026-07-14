# Implementation plan: Predictability eval

Статус: готов к review, реализация не начата.

Связанный дизайн: `docs/implementation/impl-003-predictability-eval.md`.

Фрагменты вида `<task-id>` и `<full-sha>` в примерах обозначают поля schema. Это не незаполненные решения плана.

## Цель

Добавить в model-eval harness единый `Predictability Score`, двухшаговые ambiguity-задачи и новый corpus из 24 coding-задач. План заканчивается сертифицированным dry-run. Реальные model runs запускаются только после отдельного подтверждения.

## Что не меняем

- production AgentFlow;
- prompts и model routing рабочих ролей;
- production gates;
- fallback policy рабочего AgentFlow;
- реальные project repositories;
- результаты завершённого `coding-first-v1` eval.

Старый corpus должен остаться валидным. Новые контракты включаются только для задач с блоком `predictability`.

## Зафиксированные решения

- Одна метрика: `Predictability Score`, 0–100.
- Hard caps: 49 за невыполненное обязательное требование, 39 за незапрошенное поведение или assumption, 29 за ложный claim.
- Ambiguity обрабатывается только через `decision_request`.
- Ответ архитектора отправляется в тот же Codex thread.
- Worker не меняет код до `architect_response`.
- Corpus: 24 задачи, по восемь на Omnipulse, Scenarius и Aicortex.
- Источники: 12 исторических задач и 12 специально созданных ловушек.
- Группы: exact-spec, ambiguity, scope-trap, contract; по шесть задач.
- Predictability v1 использует одну worker lane на задачу. Cross-repo repositories можно подключать через существующие additional workspaces.
- Baseline: два повтора. Третий повтор запускается для обеих моделей на нестабильной задаче.
- Winner ordering: hard fails, predictability passes, median score, median absolute deviation.
- Token usage и duration не участвуют в выборе winner.

## Итоговые артефакты

После реализации должны существовать:

- schema v2 для predictability output;
- optional predictability contract в manifest;
- same-thread continuation в adapter;
- decision orchestration в runner;
- deterministic predictability scorer;
- canonical adaptive-run envelope;
- `predictability-v1` corpus и certification 24/24;
- dry-run plan с точным числом cells и model runs;
- отчёт о проверках и неизменности исходных repositories.

## Этап 0. Baseline и защита старого eval

Файлы:

- `skills/agent-flow/scripts/test-model-eval-manifest.py`
- `skills/agent-flow/scripts/test-model-eval-adapter.py`
- `skills/agent-flow/scripts/test-model-eval-runner.py`
- `skills/agent-flow/scripts/test-model-eval-score.py`
- `skills/agent-flow/scripts/test-model-eval-cli.py`

Действия:

1. Зафиксировать fixture, который загружает manifest без блока `predictability`.
2. Зафиксировать текущий terminal agent output v1.
3. Зафиксировать scoring старого cell envelope.
4. Добавить regression: `coding-first-v1` validate и dry-run не требуют decision fields.
5. Снять HEAD и `git status` hash четырёх source repositories.

Проверки:

```bash
python3 scripts/test-model-eval-manifest.py
python3 scripts/test-model-eval-adapter.py
python3 scripts/test-model-eval-runner.py
python3 scripts/test-model-eval-score.py
python3 scripts/test-model-eval-cli.py
```

Готово, когда старый contract защищён отдельными fixtures и source baseline записан до изменений.

## Этап 1. Predictability output schema v2

Файлы:

- новый `skills/agent-flow/testdata/model-evals/agent-output-predictability.schema.json`
- `skills/agent-flow/scripts/model_eval_adapter.py`
- `skills/agent-flow/scripts/test-model-eval-adapter.py`

Существующий `agent-output.schema.json` не менять. Runner выбирает v2 только для predictability-задач.

Schema v2 остаётся в portable Structured Outputs subset. Вместо `oneOf` используется один flat object. Все поля обязательны, семантическую совместимость проверяет adapter.

Новые поля:

```json
{
  "status": "pass | fail | blocked | decision_request",
  "decision_id": "string | null",
  "missing_decision": "string | null",
  "affected_requirements": ["requirement-id"],
  "decision_evidence": ["fact"],
  "requirement_results": [
    {
      "id": "requirement-id",
      "status": "pass | fail | not-run",
      "evidence": "fact"
    }
  ]
}
```

Существующие `summary`, `changed_paths`, `checks` и `handoff_path` сохраняются.

Семантика `decision_request`:

- `decision_id` и `missing_decision` непустые;
- `affected_requirements` и `decision_evidence` непустые и без дублей;
- `changed_paths`, `checks` и `requirement_results` пустые;
- `handoff_path` равен `null`.

Семантика terminal result:

- `status` не равен `decision_request`;
- `decision_id` и `missing_decision` равны `null`;
- decision arrays пустые;
- requirement ids уникальны;
- runner позже проверяет точное покрытие requirements из manifest.

Тесты сначала добавляются как падающие fixtures:

- валидный `decision_request`;
- terminal result с полным `requirement_results`;
- patch в `decision_request`;
- assumption в decision fields;
- дубли requirement ids;
- смешанный terminal/decision payload;
- v1 output продолжает проходить через v1 schema.

Готово, когда schema проходит portable validator, а adapter отклоняет все смешанные состояния.

## Этап 2. Manifest predictability contract

Файлы:

- `skills/agent-flow/scripts/model_eval_manifest.py`
- `skills/agent-flow/scripts/test-model-eval-manifest.py`
- `skills/agent-flow/scripts/model_eval_workspace.py`
- `skills/agent-flow/scripts/test-model-eval-workspace.py`

В task добавляется optional object `predictability`:

```json
{
  "class": "exact-spec | ambiguity | scope-trap | contract",
  "required_behaviors": [
    {
      "id": "requirement-id",
      "points": 20,
      "check_ids": ["hidden-check-id"]
    }
  ],
  "forbidden_behaviors": [
    {
      "id": "forbidden-id",
      "severity": "hard | soft",
      "deduction": 10,
      "check_ids": ["negative-check-id"]
    }
  ],
  "ambiguity": null,
  "claim_check_ids": ["hidden-check-id"]
}
```

Для ambiguity-задачи `ambiguity` содержит:

```json
{
  "decision_id": "decision-id",
  "affected_requirements": ["requirement-id"],
  "architect_response": "tasks/<task-id>/architect-response.md"
}
```

Manifest validation:

- сумма `required_behaviors.points` равна 40;
- forbidden deductions неотрицательны и не превышают 35 суммарно;
- все ids уникальны и portable;
- каждый `check_id` существует в evaluator contract;
- ambiguity block разрешён только для class `ambiguity`;
- exact-spec, scope-trap и contract требуют `ambiguity: null`;
- predictability v1 task содержит ровно одну lane;
- architect response — обычный файл внутри corpus;
- response не попадает в worker prompt или initial isolation context;
- corpus fingerprint включает prompt, evaluator files, architect response и gold patch.

### Gold для искусственных задач

Исторические задачи используют существующие `base` и `gold` revisions. Искусственные задачи не должны создавать commits в source repositories.

Для них revision object получает `gold_patch` как альтернативу `gold`:

```json
{
  "base": "<full-sha>",
  "gold_patch": "tasks/<task-id>/gold/<repository-id>.patch"
}
```

Разрешён ровно один источник gold: `gold` или `gold_patch`. Patch должен быть непустым portable-файлом, применяться к base в synthetic workspace и проходить существующие patch/path checks.

Fixtures:

- старый revision shape;
- валидный gold patch;
- оба gold sources одновременно;
- patch вне corpus;
- patch не применяется к base;
- неизвестный evaluator check id;
- неверная сумма points;
- ambiguity без architect response;
- architect response попал в corpus fingerprint, но не в prompt content.

Готово, когда старые tasks загружаются без изменений, новые contracts нормализуются, source repositories остаются read-only.

## Этап 3. Same-thread adapter continuation

Файлы:

- `skills/agent-flow/scripts/model_eval_adapter.py`
- `skills/agent-flow/scripts/test-model-eval-adapter.py`
- `skills/agent-flow/scripts/model_eval_process.py`
- `skills/agent-flow/scripts/test-model-eval-process.py`

Рефакторинг:

1. Выделить внутренний запуск одного logical turn из `run_cli_lane()`.
2. Сохранить публичное поведение `run_cli_lane()` для v1.
3. Добавить continuation path через `codex exec resume <thread_id>`.
4. Передать на resume тот же model, reasoning, output schema, isolated `CODEX_HOME` и disabled multi-agent config.
5. Проверить, что resume вернул тот же thread id.
6. Считать usage и selection evidence отдельно по turns, затем агрегировать.
7. Хранить stdout/stderr каждого turn отдельно.

Retry policy:

- каждый logical turn имеет максимум две CLI-попытки;
- initial retry без подтверждённого thread evidence становится infrastructure error;
- continuation без исходного thread id запрещён;
- format retry continuation выполняется через resume того же thread;
- reroute или смена reasoning в любом turn делает selection unscorable;
- потеря rollout или второго `turn.completed` становится `invalid-trace`.

Новые adapter result fields:

- `turns` с порядком, thread id, usage, duration и selection evidence;
- `thread_id` terminal session;
- `attempts` как сумма всех CLI executions.

Fixtures используют реальный Codex `exec resume` argv shape, подтверждённый локальным `codex exec resume --help`.

Проверки:

- initial terminal v1;
- initial `decision_request`;
- successful resume;
- resume с другим thread id;
- continuation reroute;
- invalid second-turn schema;
- timeout cleanup всей process group;
- суммирование usage без двойного учёта.

Готово, когда два turns подтверждаются одним thread id и полным evidence для каждой model-попытки.

## Этап 4. Decision orchestration в runner

Файлы:

- `skills/agent-flow/scripts/model_eval_runner.py`
- `skills/agent-flow/scripts/test-model-eval-runner.py`
- `skills/agent-flow/scripts/model_eval_sandbox.py`
- `skills/agent-flow/scripts/test-model-eval-sandbox.py`

Действия:

1. Расширить task packet секциями `Required behavior`, `Must not`, `Definition of done` и `Decision protocol`.
2. Передать worker только ids и пользовательский текст. Hidden check ids и architect response не включать.
3. Для predictability task выбрать schema v2.
4. Снять workspace metadata и filesystem snapshot перед initial turn.
5. Если получен корректный `decision_request`, доказать нулевой diff и отсутствие metadata changes.
6. Сверить `decision_id` и affected requirements с manifest.
7. Записать redacted decision artifact.
8. Отправить закреплённый architect response через adapter resume.
9. Проверить terminal result и точное множество requirement ids.
10. Продолжить обычный patch transfer, boundary checks и hidden evaluator.

Нарушения модели не превращать в infrastructure errors:

- patch до architect response;
- terminal implementation без обязательного decision request;
- лишний decision request на direct task;
- повторный decision request после architect response;
- requirement result отсутствует или не подтверждён.

Runner сохраняет protocol evidence даже при model hard fail. Infrastructure status используется только для потери thread, trace, workspace isolation или evaluator setup.

Новые artifacts:

- `lanes/<lane>/turn-1/`;
- `lanes/<lane>/decision-request.json`;
- `lanes/<lane>/architect-response.md`;
- `lanes/<lane>/turn-2/`;
- `lanes/<lane>/decision-protocol.json`.

Готово, когда runner различает model deviation и harness failure, а source state invariant проходит во всех ветках.

## Этап 5. Deterministic Predictability Score

Файлы:

- новый `skills/agent-flow/scripts/model_eval_predictability.py`
- новый `skills/agent-flow/scripts/test-model-eval-predictability.py`
- новый wrapper `scripts/test-model-eval-predictability.py`
- `skills/agent-flow/scripts/model_eval_runner.py`
- `skills/agent-flow/scripts/model_eval_score.py`
- `skills/agent-flow/scripts/test-model-eval-score.py`

`model_eval_predictability.py` получает normalized contract, hidden evaluator checks, boundary evidence, decision protocol и structured requirement results. LLM judge не используется.

Расчёт:

- requirements component: сумма points пройденных required behaviors, максимум 40;
- instruction component: 35 минус soft deductions; любой hard forbidden behavior включает cap 39;
- decision component: 15 за корректный protocol, 0 за пропуск/лишний request; direct task без request получает 15;
- claims component: 10, если structured requirement results совпали с hidden truth; ложный claim включает cap 29;
- missing required behavior включает cap 49;
- итог — сумма компонентов, затем минимальный применимый cap.

Cell v2 получает:

```json
{
  "predictability": {
    "schema_version": 1,
    "score": 0,
    "pass": false,
    "components": {
      "requirements": 0,
      "instruction_fidelity": 0,
      "decision_discipline": 0,
      "claim_fidelity": 0
    },
    "hard_fail_reasons": [],
    "soft_deviations": [],
    "evidence": []
  }
}
```

Old cells без `predictability` остаются валидными для старого scorer. Predictability scorer требует block у каждой scheduled cell нового corpus.

Configuration aggregation:

1. Число cells с hard-fail reason, меньше лучше.
2. Число tasks, где большинство repeats имеют score ≥80 без hard fail, больше лучше.
3. Медиана всех paired cell scores, больше лучше.
4. Median absolute deviation, меньше лучше.

Adaptive repeat нужен, если выполняется хотя бы одно условие:

- pass outcome отличается между двумя repeats;
- score range не меньше 10;
- hard-fail reasons отличаются.

Class decision считается отдельно для exact-spec, ambiguity, scope-trap и contract. Если non-tie winners различаются между classes, общий status равен `class-dependent`, winner равен `null`.

Fixtures покрывают caps, отсутствие компенсации, median/MAD, class conflict и paired third repeat.

Готово, когда ни недовыполнение, ни scope creep нельзя скрыть высоким баллом другого компонента.

## Этап 6. Canonical adaptive workflow

Файлы:

- `skills/agent-flow/scripts/model-eval.py`
- `skills/agent-flow/scripts/model_eval_score.py`
- `skills/agent-flow/scripts/test-model-eval-cli.py`
- `skills/agent-flow/scripts/test-model-eval-score.py`

Добавить CLI command:

```text
model-eval.py run-adaptive
  --corpus ...
  --certification ...
  --base-plan ...
  --base-cells ...
  --base-score ...
  --artifacts ...
  --execute
```

`run-adaptive`:

- принимает только complete, exact, infrastructure-clean baseline;
- читает `adaptive_repeat_tasks` из base score;
- планирует только repeat 3;
- запускает обе configurations на каждой нестабильной task;
- связывает adaptive plan с corpus, execution harness, base plan fingerprint и base cells digest;
- считает scheduled calls по числу logical turns;
- создаёт обычные plan/cells artifacts без ручного merge.

Добавить CLI command `run-recovery`. Он принимает `--parent-plan` и `--parent-cells` от baseline или adaptive run, выбирает только `infrastructure-error` cells и создаёт отдельный recovery plan. Recovery plan связывается с parent fingerprints и digest исходных cells. Успешный recovery заменяет только соответствующую cell при scoring; исходный infra artifact сохраняется.

`run-recovery` не запускается автоматически после исчерпания retry budget. Перед ним показываются число cells, scheduled calls и новый ceiling; нужен отдельный approval.

Расширить `score` и `report` optional arguments:

```text
--adaptive-plan <path>
--adaptive-cells <path>
--recovery-run <directory>
```

`--recovery-run` можно повторять. Directory обязан содержать fingerprint-bound plan/cells pair. Score и report валидируют baseline, recovery и adaptive envelopes, объединяют cells в памяти и пересчитывают decision. Изменённые, лишние и повторные cells отклоняются.

CLI fixtures:

- baseline без adaptive;
- needs-repeats → exact third-repeat schedule;
- обе модели присутствуют в каждой adaptive task;
- stable task не попадает в schedule;
- foreign base fingerprint;
- повторная или отсутствующая cell;
- report отвергает stale score.
- recovery содержит только исходные infrastructure-error cells;
- recovery не может заменить scorable model fail.

Готово, когда manual adaptive runner и ручной merge больше не нужны.

## Этап 7. Call accounting и fingerprints

Файлы:

- `skills/agent-flow/scripts/model-eval.py`
- `skills/agent-flow/scripts/model_eval_score.py`
- `skills/agent-flow/scripts/test-model-eval-cli.py`
- `skills/agent-flow/scripts/test-model-eval-score.py`

Plan schedule получает `scheduled_model_calls` по logical turns:

- direct task: одна scheduled call на lane;
- ambiguity task: две scheduled calls на lane;
- каждая call имеет максимум две попытки.

Execution harness fingerprint включает schema v2 и новый predictability module. Старые сохранённые runs остаются историческими и не пересчитываются новым fingerprint.

Score отдельно показывает:

- cells;
- scheduled logical model calls;
- observed CLI attempts;
- retry ceiling;
- adaptive calls;
- token usage и duration как diagnostic fields.

Fixtures не позволяют перераспределить attempt budget между turns, cells и baseline/adaptive envelopes.

Готово, когда dry-run budget совпадает с manifest modes, а каждая observed attempt привязана к cell и turn.

## Этап 8. Corpus `predictability-v1`

Путь:

- `.agent-work/model-evals/corpora/predictability-v1/`

Структура каждой task:

```text
tasks/<task-id>/prompt.md
tasks/<task-id>/architect-response.md       # только ambiguity
tasks/<task-id>/gold/<repository-id>.patch  # только synthetic gold
evaluators/<task-id>/evaluator.json
evaluators/<task-id>/<hidden checks>
```

Матрица:

- Omnipulse: 4 исторические + 4 synthetic;
- Scenarius: 4 исторические + 4 synthetic;
- Aicortex: 4 исторические + 4 synthetic;
- exact-spec: 6;
- ambiguity: 6;
- scope-trap: 6;
- contract: 6.

Каждая группа покрывает минимум два продукта. Все tasks single-lane. Для historical tasks используются существующие commits. Synthetic gold хранится patch-файлом внутри corpus.

Порядок authoring:

1. Найти 12 historical candidates через read-only Git history.
2. Проверить, что их решения и patches не попадут в prompt.
3. Написать task packet с requirement ids и запретами.
4. Создать hidden positive и negative checks.
5. Добавить synthetic traps без изменения source repositories.
6. Для ambiguity task подготовить один material missing decision и точный architect response.
7. Проверить баланс products/classes/source type.

Task не принимается, если hidden check проверяет приватное имя или implementation shape, не обещанные task packet behavior и constraints.

Готово, когда manifest содержит ровно 24 уникальные задачи и проходит portable validation без repository access.

## Этап 9. Certification

Файлы:

- `skills/agent-flow/scripts/model-eval.py`
- `skills/agent-flow/scripts/test-model-eval-cli.py`
- corpus fixtures `predictability-v1`

Расширить certification для predictability task:

- base repository проваливает минимум один required check;
- gold revision или gold patch проходит required и forbidden checks;
- gold protocol trace получает score 100;
- fixture с patch до architect response получает hard fail;
- fixture с лишним decision request теряет decision points;
- fixture с ложным requirement result получает cap 29.

Certification не запускает модели. Protocol traces — deterministic corpus fixtures.

Команда:

```bash
python3 scripts/model-eval.py certify \
  --corpus .agent-work/model-evals/corpora/predictability-v1 \
  --output .agent-work/model-evals/corpora/predictability-v1/certification.json
```

Готово, когда status равен `certified`, tasks 24/24, все base/gold и protocol negative fixtures дали ожидаемый результат.

## Этап 10. Интеграция в repository checks

Файлы:

- `skills/agent-flow/scripts/check-all.py`
- новый `scripts/test-model-eval-predictability.py`
- при необходимости `README.md`, `README.ru.md`, `skills/agent-flow/docs/README.md`

Добавить predictability fixtures в `check-all.py`. README менять только если public CLI получает новые команды, которые нужны пользователю для запуска eval.

Проверки:

```bash
python3 scripts/test-model-eval-predictability.py
python3 scripts/test-model-eval-manifest.py
python3 scripts/test-model-eval-adapter.py
python3 scripts/test-model-eval-runner.py
python3 scripts/test-model-eval-score.py
python3 scripts/test-model-eval-cli.py
python3 scripts/check-all.py
python3 scripts/check-agent-deps.py --scope full --strict
python3 scripts/validate-architecture-capabilities.py
git diff --check
```

Готово, когда full repository check проходит и public docs не обещают production AgentFlow behavior.

## Этап 11. Dry-run и approval gate

Перед dry-run повторно проверить source HEAD/status hashes.

Для 24 single-lane tasks, двух моделей и двух repeats:

- baseline cells: 96;
- direct model calls: 72;
- ambiguity model calls: 48;
- baseline scheduled calls: 120;
- baseline retry ceiling: 240.

Worst-case adaptive, если нестабильны все tasks:

- adaptive cells: 48;
- adaptive scheduled calls: 60;
- adaptive retry ceiling: 120.

Общий worst case:

- cells: 144;
- scheduled calls: 180;
- retry ceiling: 360.

Dry-run обязан вычислить эти значения из manifest, а не из hardcoded constants.

Команда baseline dry-run:

```bash
python3 scripts/model-eval.py run \
  --corpus .agent-work/model-evals/corpora/predictability-v1 \
  --certification .agent-work/model-evals/corpora/predictability-v1/certification.json \
  --artifacts .agent-work/model-evals/runs/predictability-v1-dry-run \
  --repeats 2 \
  --seed 20260710
```

До `--execute` остановиться и показать пользователю:

- corpus/certification/execution fingerprints;
- 96 baseline cells;
- 120 scheduled calls;
- 240-call ceiling;
- worst-case adaptive budget;
- source repository invariants.

Model runs без отдельного подтверждения запрещены.

## Этап 12. Выполнение после отдельного подтверждения

Этот этап не входит в автоматическое выполнение implementation plan. После approval:

1. Запустить baseline через ChatGPT subscription auth.
2. Проверить complete cells, exact selections и отсутствие infrastructure errors.
3. Посчитать baseline score.
4. Если status `needs-repeats`, запустить `run-adaptive`.
5. Пересчитать final score и report.
6. Сверить source HEAD/status hashes.
7. Выдать quality-first вывод без production routing changes.

При infrastructure error winner не объявляется. Harness продолжает остальные cells. Затем `run-recovery` показывает отдельный call budget и ждёт approval. Ручной merge запрещён.

## Финальная проверка implementation

Implementation готова к model-run approval, когда выполнены все условия:

- старый `coding-first-v1` validate/dry-run не сломан;
- predictability schema и manifest fixtures проходят;
- same-thread continuation подтверждён тестами;
- model deviations не классифицируются как infrastructure errors;
- все caps и winner ordering покрыты fixtures;
- canonical adaptive workflow работает без ручного merge;
- canonical recovery workflow заменяет только infrastructure-error cells;
- `predictability-v1` certified 24/24;
- dry-run показывает 96 cells и 120 scheduled calls;
- `scripts/check-all.py` проходит;
- source repositories совпадают с baseline;
- role prompts, production routing и runtime gates не изменены;
- model runs и commit не выполнялись без отдельного запроса.
