# Traceable Runs

Document status: done

Traceable runs store evidence for work that needs durable review history.

`.agent-work/tasks/` is project memory and follows the current user's Codex instructions, usually `~/.codex/AGENTS.md`, for all repo tasks. This file governs only `.agent-work/runs/` trace artifacts.

Для консультаций с budget `light` журнал не нужен. Изменение файлов использует
штатный compact-журнал и тот же verification-контракт, что full.

## Location

Use the current project repo:

```text
<project-repo>/.agent-work/runs/YYYY-MM-DD-task-slug/
```

Use the local date for `YYYY-MM-DD`; Agent Flow runs locally and should not default to UTC.

Do not reuse an existing run directory for a new task. `scripts/init-run.py` rejects date/slug collisions unless `--reuse` is passed deliberately.

If no project repo exists, either skip trace for simple local skill work or create a local run directory only when it adds value. Do not initialize git or change `.gitignore` unless user asked.

## Compact Structure

For `standard` budget, prefer a compact trace:

```text
run.md
checks.md
context.md
final.md
delegation-summary.json
timeline.jsonl
artifacts/
```

Use this when the user needs continuity and evidence, but not a full release record.

Создавайте compact командой `init-run.py --repo <project> --slug <task> --mode compact`.
Без `--mode` сохраняется прежний full. Compact не создаёт lane-map и архитектурные
handoffs; несовместимые архитектурные параметры отклоняются до записи. `--reuse`
сохраняет прежний формат и заполненный summary. Некорректный summary требует
диагностики и явного исправления; повторный init не заменяет его новым.

## Full Structure

For `release` budget or explicit full-trace requests, include:

```text
manifest.md
context.md
route.md
plan.md
definition-of-done.md
decisions.md
lane-map.json
delegation-summary.json
handoffs/
checks/
artifacts/
artifacts.json
timeline.jsonl
final.md
```

Runs that use delegated subagents additionally include per-agent traces and owned artifact directories:

```text
agents/
  <role>/
    trace.jsonl
artifacts/
  agents/
    <role>/
```

Runs that use Lane Sharding add `lane-map.json` as the machine-readable source
of truth and usually add `checks/coverage-matrix.md` as a human-readable
summary. Create those skeletons with `scripts/init-run.py --with-lanes`.

## Local Ignore Rule

Agent artifacts must not enter product commits. Prefer `.git/info/exclude` for `.agent-work/` unless the user explicitly approves `.gitignore`.

## Product Commits And Local Trace

Trace artifacts are local audit memory. They are not part of the product commit by default.

When a traceable run creates a product commit, use this order:

1. finish implementation and required checks; audit the related-document list established at intake for every repository, including the source plan;
2. prepare final document/implementation statuses and checklists under `definition-of-done.md` before result hashing; include all related delivery documents in `result_files` and obtain QA/reviewer acceptance of their full current bytes. Task memory remains active while this candidate awaits acceptance;
3. compare staged diff with the accepted delivery list, inspect document statuses in the index, create scoped product/docs commits, and verify SHA and committed document bytes with `git show` in every affected repository;
4. update related `.agent-work/tasks/todo.md` sections in each repository with commit/check evidence, keeping the current task `Status: in_progress` until final validation;
5. append a run-local `stage=commit` orchestrator event with the commit hash;
6. write or update `final.md` with the commit hash, evidence and risks;
7. append the single final orchestrator timeline event;
8. run `scripts/validate-run.py --run-dir <run-dir>` and save stdout/stderr plus exit code as shown below;
9. only after exit 0 and completion of all task criteria, set `Status: done` and send the final answer.

No commit is required when the user did not request it. A finished document uses
`Document status: done`; separate implementation status stays factual. An explicitly
requested intermediate snapshot retains WIP status. Never put a future commit's own
SHA inside its document. Status edits after acceptance require renewed acceptance.
Commit failure leaves delivery incomplete; validation failure after commit retains
the actual SHA and unresolved criteria and requires correction before completion.
Historical record correction uses verified sources under `project-memory-and-env.md`,
without a new run or repeat acceptance of the old implementation.

Do not create a second commit just to include `.agent-work/` trace changes. The
timeline records the product commit hash locally after the product commit
succeeds.

## Worktree Hygiene

At traceable run intake, capture the project worktree state before edits:

```bash
git status --short
```

Record it in `context.md` or `route.md` under `Initial worktree snapshot`.
If the worktree is dirty, classify the files before making product changes:

- pre-existing unrelated changes;
- pre-existing files that this run must touch;
- new files expected from this run;
- generated artifacts that must not be committed.

Do not hide dirty state in the final report. `final.md` must include a short
worktree section for traceable implementation runs:

- initial dirty files, if any;
- run-owned changed files;
- pre-existing dirty files left untouched;
- pre-existing dirty files touched by the run and why;
- untracked/generated files that should not be committed.

If a run touches a file that was already dirty at intake, say so explicitly in
`final.md`. This keeps commit hygiene reviewable and prevents Agent Flow from
silently mixing user work with run-owned changes.

## File Purposes

- `manifest.md`: goal, scope, invocation, flow, agents, blockers, status, verdict.
- `context.md`: source request, relevant docs, assumptions.
- `route.md`: chosen flow and skipped roles with reasons.
- `plan.md`: checkable plan and ownership.
- `definition-of-done.md`: task-specific gates.
- `decisions.md`: decisions and reasons.
- `lane-map.json`: machine-readable lane map for Lane Sharding runs.
- `handoffs/`: one file per delegated subagent.
- `checks/`: commands, QA, visual diff, review notes.
- `artifacts/`: screenshots, logs, exports, generated assets.
- `artifacts/agents/<role>/`: generated assets, logs, and evidence owned by one subagent.
- `artifacts.json`: machine-readable artifact index. Prefer the top-level array
  shape for new runs; helpers also preserve the object shape
  `{ "artifacts": [...] }` when an existing run uses it.
- `timeline.jsonl`: one JSON event per significant stage.
- `agents/<role>/trace.jsonl`: one JSON event per significant stage for that subagent.
- `final.md`: final result, evidence, residual risks.

## Validation

Run `scripts/validate-run.py --run-dir <run-dir>` before final handoff. By default it fails pending verdicts, missing check files, invalid JSON, and incomplete timeline events. Use `--allow-pending` or `--allow-no-check` only for early structural checks, not final handoff.

Validation remains backward compatible with no-subagent runs: `agents/` is not required. If `agents/<role>/` exists, `timeline.jsonl` and `trace.jsonl` are required. Agent trace files are checked with the same minimum event schema as `timeline.jsonl`, and every agent trace event must also be present in the run-level timeline.

If `lane-map.json` exists, validation also checks the Lane Sharding contract:

- `schema_version` is `1` or `2` and `lanes` is an array;
- lane ids are unique;
- lane type, execution mode, and status use allowed values;
- allowed lane types include `architecture`, `implementation`, `integration`, `qa`, and `review`;
- successful critical lanes point to existing handoff and evidence artifacts;
- a timed-out critical lane points to an existing replacement lane whose status is `pass` or `pass-with-risks`;
- a `subagent` lane with active or successful status has a matching spawned trace event with `codex_thread_id`;
- a successful `subagent` lane also has a terminal handoff trace event with the same lane id and handoff artifact;
- `role-lane` entries do not require a `codex_thread_id`;
- schema v2 positive lane-map runs require `delegation-summary.json` and a final `Delegation Trace` section;
- schema v2 lane-map runs may opt into Handoff State Gate with `handoff_state_required=true`; then each lane has `handoff_state`, terminal lane status must match handoff state, handoff paths must match, timestamps must be ordered, and batch items must be completed before batch acceptance;
- Mandatory Independent QA Review Gate требует для `change` два отдельных назначения: `qa-verifier` проверяет результат, `reviewer` проверяет его и доказательства QA. Оба работают как реальные дочерние сессии текущего root; их IDs отличаются от IDs авторов. Модели берутся из действующих файлов ролей через `agent_config`. `reviewer.qa` допустим только как имя назначения канонического `reviewer`; QA под этим именем не заменяет reviewer.
- role-lane-only reviewer evidence is rejected when Mandatory Independent QA Review Gate applies;
- При недоступности запуска или исходной сессии записать причину в `verification.blocker` и завершить `blocked` или `fail`; успешные доказательства не выдумывать.
- `Verdict: ship` is rejected while any critical lane is unresolved, failed, blocked, or missing replacement evidence.

## Delegation Trace Gate

Для любого положительного итога требуется `delegation-summary.json` версии 1.
Ниже показаны поля назначений; объект `verification` описан далее и обязателен
для `ship` и `pass-with-risks`, включая compact без lane-map:

```json
{
  "version": 1,
  "subagents_used": false,
  "role_lanes_used": true,
  "subagents": [],
  "role_lanes": [
    {
      "lane_id": "architecture-contract",
      "role": "architect",
      "reason": "Architecture Contract Gate executed as role-lane."
    }
  ],
  "notes": "No spawned subagents were used. Role lanes are not subagent execution."
}
```

`final.md` must include `Delegation Trace` with these canonical lines:
`Subagents Used`, `Role Lanes Used`, `Subagent Lanes`, `Role Lanes`, and
`Subagent Trace Evidence`. If `subagents_used=false`, run-owned narrative files
must not claim sidecar/subagent work. If a real subagent was used, the summary
must point to `agents/<role>/trace.jsonl`, the lane handoff, and the matching
`codex_thread_id`.

## Handoff State Gate

Handoff State Gate keeps lane handoff lifecycle state in `lane-map.json`. It is
opt-in unless generated by `init-run.py --architecture-gate --with-lanes`.

Set the root flag:

```json
{
  "handoff_state_required": true
}
```

Each lane may carry:

```json
{
  "handoff_state": {
    "version": 1,
    "mode": "task",
    "status": "completed",
    "task": "worker-a-implementation",
    "from": "architecture-contract",
    "to": "worker-a",
    "handoff": "handoffs/worker-a.md",
    "queued_at": "2026-06-17T12:00:00+03:00",
    "accepted_at": "2026-06-17T12:05:00+03:00",
    "completed_at": "2026-06-17T12:30:00+03:00"
  }
}
```

Allowed `mode` values: `task`, `batch`. Allowed `status` values: `queued`,
`accepted`, `completed`, `blocked`, `failed`.

Use:

```bash
python3 scripts/record-handoff-state.py --run-dir <run-dir> --lane-id worker-a --status queued --from architecture-contract --task worker-a-implementation --handoff handoffs/worker-a.md
python3 scripts/record-handoff-state.py --run-dir <run-dir> --lane-id worker-a --status accepted
python3 scripts/record-handoff-state.py --run-dir <run-dir> --lane-id worker-a --status completed
```

The recorder updates only `lane-map.json`. Timeline and per-agent traces still
come from `record-agent-trace.py`.

## Mandatory Independent QA Review Gate

После изменения кода, tests, PRD, планов, runtime docs или шаблонов нужны два
отдельных назначения. `qa-verifier` проверяет результат, `reviewer` проверяет
его и доказательства QA. Правило действует при любом budget. `light` сохраняет
одного автора реализации. Для обычной консультации run-каталог не нужен.

Оба проверяющих должны быть прямыми дочерними сессиями текущего root. Их IDs
отличаются друг от друга и от авторов результата. `reviewer.qa` допустим как имя
назначения роли `reviewer`, но не как `agent_type` и не как замена QA.
Ожидаемые модели читает `agent_config` из действующих файлов ролей. Роль из
самоотчёта, событие `spawned` и role-lane сами по себе не доказывают исполнение.

`delegation-summary.json` остаётся версии 1. Его `subagents` содержит обычные
записи с `lane_id`, `role`, `codex_thread_id`, `trace` и `handoff`. В compact
`lane_id` служит стабильным ID назначения без lane-map. Если lane-map существует,
сохраняются проверки покрытия, роли, режима исполнения и успешного статуса lane.
В final нужен `Delegation Trace`; краткий раздел `Mandatory Independent QA Review`
может объяснить результат обеих проверок, но не заменяет машинные доказательства.

### Объект verification

| Поле | Содержание |
| --- | --- |
| `task_kind` | `change` для продуктовых изменений; `analysis` для анализа с пустым результатом |
| `root_thread_id` | Реальный UUID текущего root; его модель и запуск не проверяются |
| `author_thread_ids` | UUID авторов; для root-owned работы включает root, для worker subagents включает их IDs |
| `result_files` | Полный список новых, изменённых и удалённых файлов относительно корня проекта |
| `initial_snapshot` | Ссылка с SHA-256 на раздел `Initial Worktree Snapshot` в context.md или route.md |
| `task_scope` | Ссылка с SHA-256 на согласованные границы в плане задачи; можно выбрать раздел |
| `qa`, `reviewer` | `lane_id` двух записей в `subagents`; до назначения допускается `null` |
| `result_hash` | Хеш, вычисленный recorder; при приёмке валидатор пересчитывает его по файлам |
| `behavioral_checks` | Необязательный список проверок поведения, только если это требует приёмка задачи |
| `blocker` | Конкретная причина для `blocked` или `fail` |

Ссылка имеет вид `{"path":"context.md","section":"Initial Worktree Snapshot","sha256":"<SHA-256>"}`.
`section` необязателен: без него хешируются все байты файла. С ним хешируется
содержимое после заголовка до следующего заголовка, включая пробелы и переводы
строк. Пути доказательств относительны run-каталогу; абсолютный путь допустим
внутри проекта. `..` и выход за проект через symlink запрещены.

Корень проекта определяется по родительскому `.agent-work`, затем по ближайшему
Git-корню. Для отдельного run вне Git используется его родительская папка.
В `result_files` указываются отдельные файлы, не каталоги. Рабочая память и `.git`
не входят в продукт. Удаление кодируется значением `deleted`. Хеш строится из
отсортированных путей и хешей текущих байтов, ссылок на исходный снимок и границы
через однозначное JSON-кодирование. Untracked-файлы также читаются с диска.

До QA зафиксируйте состав результата. В compact перечислите принадлежавшие задаче
файлы в final, по одному пути в строке:

```markdown
## Worktree Hygiene

Run-owned changed files:
- `docs/prd/example.md`
- `src/example.py`
```

В full валидатор также использует существующие Boundary Evidence и списки
run-owned changed paths. Пропуск такого пути в `result_files` отклоняется.
Reviewer сверяет список с исходным снимком, согласованной задачей и текущим
`git status --short`. Предсуществующие и параллельные изменения вне задачи
не включаются автоматически. Валидатор не определяет авторство по Git.

Для compact analysis достаточно `task_kind: analysis`, реального root UUID,
пустых `author_thread_ids`, `result_files`, `subagents`, `role_lanes`,
`behavioral_checks` и значений `null` у ссылок и проверяющих. Оба флага
`subagents_used` и `role_lanes_used` имеют значение `false`; final перечисляет
`Subagents Used: no`, `Role Lanes Used: no`, `Subagent Lanes: none`,
`Role Lanes: none`, `Subagent Trace Evidence: none`. Worker lanes или продуктовые
пути противоречат классификации analysis.

### Запись и собственный итог проверяющего

`AF_PACKAGE` ниже обозначает фактически загруженный каталог skill, `AF_PROJECT` -
рабочий проект, `AF_RUN` - путь, возвращённый init. Для изолированной проверки
используйте временную копию пакета и передайте её точный путь всем участникам.

```sh
python3 "$AF_PACKAGE/scripts/init-run.py" \
  --repo "$AF_PROJECT" --slug completion-check --mode compact
```

`init-run.py` создаёт штатный summary версии 1 даже без `--with-lanes`.
Не собирайте summary вручную. `--reuse` сохраняет заполненные данные и формат;
ошибка существующего summary требует явного исправления, не перезаписи init.

До первого spawn заполните `context.md` с разделом `Initial Worktree Snapshot`
и границы задачи в `run.md` или `plan.md`. Сохраните отдельный входной объект
verification, взяв его из созданного summary. Установите реальный root UUID
из среды в `root_thread_id`, ссылки `initial_snapshot` и `task_scope`.
Для этого промежуточного шага оставьте `task_kind: null`, `qa: null`,
`reviewer: null`, пустые `author_thread_ids`, `result_files` и `behavioral_checks`.
Например, источником UUID может быть `CODEX_THREAD_ID`, если он совпадает с
`session_meta.id` текущей сессии. При отсутствии подтверждённого UUID остановите
делегирование с конкретной диагностикой; canonical path его не заменяет.

```json
{
  "task_kind": null,
  "root_thread_id": "<реальный UUID текущего root>",
  "author_thread_ids": [],
  "result_files": [],
  "initial_snapshot": {"path": "context.md", "section": "Initial Worktree Snapshot"},
  "task_scope": {"path": "run.md", "section": "Task Scope"},
  "qa": null,
  "reviewer": null,
  "behavioral_checks": []
}
```

`AF_VERIFICATION` - файл с этим объектом, например `checks/verification-input.json`.
Заголовок `Task Scope` и непустой текст границ должны существовать до команды.

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir "$AF_RUN" --role orchestrator --execution-mode role-lane \
  --stage verification --status active --summary "Root UUID и границы записаны до делегирования" \
  --verification-json "$AF_VERIFICATION"
```

Recorder заполняет отсутствующие SHA-256 ссылок и проверяет имеющиеся. Частичный
объект не вычисляет result hash и не разрешает положительный итог. После этой
записи canonical-only назначения уже можно разрешать по точному parent UUID.

Перед QA подготовьте окончательные статусы и checklist связанных документов
каждого репозитория по `definition-of-done.md`. Включите исходный план и все
документы поставки в список; приёмка охватывает полные bytes со статусами.
Возьмите текущий verification из summary, установите `task_kind: change`,
полные `author_thread_ids` и `result_files`. Сохраните текущие behavioral_checks
и ссылки. Через recorder передайте объект строкой JSON или путём к локальному
файлу. Команда вычисляет штатный хеш файлов вместе со снимком и границами.
Не заменяйте его собственной формулой хеширования списка файлов.

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir "$AF_RUN" --role orchestrator --execution-mode role-lane \
  --stage verification --status active --summary "Редакция подготовлена к QA" \
  --verification-json "$AF_VERIFICATION"
```

Команда выводит `result_hash`. Передайте обоим проверяющим этот хеш, `result_files`,
снимок, границы, критерии задачи и полные инструкции роли. QA записывает проверки
в handoff. Сначала зарегистрируйте собственный итог QA; reviewer получает уже
зарегистрированное принятие и читает этот handoff до своей проверки.

Каждый проверяющий завершает собственный ход целым JSON-объектом. Поддержка
последнего fenced блока `json` сохраняется; проза перед необрамлённым JSON
не принимается. Подробный отчёт остаётся в handoff:

В handoff перечислите пути использованных доказательств и их SHA-256.
Validator требует эти значения в handoff, связанном с исходным итоговым ходом.
Правка checksum только в summary не принимает изменённое доказательство.

```json
{
  "verdict": "passed",
  "reviewed_result_hash": "<текущий result_hash>",
  "handoff": "handoffs/qa.md",
  "handoff_sha256": "<SHA-256 handoff>"
}
```

Reviewer указывает свой handoff и добавляет `qa_handoff_sha256` с хешем
прочитанного QA handoff. Допустимые положительные JSON-вердикты: `passed`,
`pass-with-risks`. Для отрицательного итога используйте `fail` или `blocked`.
Root не может заменить отрицательный ответ положительной записью summary.

После реального запуска запишите назначение; подставляйте UUID из среды.
Если инструмент вернул UUID, прямой путь остаётся доступен:

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir "$AF_RUN" --role qa-verifier --lane-id qa-final \
  --codex-thread-id "$AF_QA_ID" --stage spawned --status active \
  --summary "QA запущен"

python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir "$AF_RUN" --role qa-verifier --lane-id qa-final \
  --codex-thread-id "$AF_QA_ID" --completion-turn-id "$AF_QA_TURN" \
  --stage handoff --status pass --summary "QA завершён" \
  --artifact handoffs/qa.md --artifact checks/qa.md
```

Если ответ содержит только canonical name, передайте точный возвращённый путь:

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir "$AF_RUN" --role qa-verifier --lane-id qa-final \
  --resolve-session --agent-path "$AF_QA_PATH" --stage spawned --status active \
  --summary "Назначение QA разрешено по исходной сессии"

python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir "$AF_RUN" --role qa-verifier --lane-id qa-final \
  --resolve-session --agent-path "$AF_QA_PATH" --stage handoff --status pass \
  --summary "Собственное завершение QA зарегистрировано" \
  --artifact handoffs/qa.md --artifact checks/qa.md
```

Resolver ищет metadata по exact canonical path, `verification.root_thread_id`
и канонической роли. При нуле кандидатов, нескольких совпадениях или конфликте
явного UUID запись отклоняется. Он не выбирает похожее имя или самую свежую сессию.
Для handoff без `--completion-turn-id` выбирается собственное завершение,
связанное с текущим hash и handoff; незавершённое продолжение и неоднозначность
отклоняются. Явный `--completion-turn-id` сохраняется для различимого хода.
Вывод содержит использованные UUID и номера исходных событий. Время регистрации
остаётся текущим, наблюдаемое время запуска хранится отдельно.

Путь `handoff` в JSON относителен run-каталогу и точно совпадает с `--artifact`.
Сразу после QA выполните recorder: неверное оформление или путь исправляется
до reviewer. Если достаточно исправить аргументы команды, повторно используйте
готовое завершение. Если нужен другой ответ, продолжайте ту же сессию с прежними
моделью, достигнутым reasoning и счётчиком; не переписывайте исходный ответ.

Для reviewer повторите команды с `--role reviewer`, отдельным `lane-id`,
его UUID, завершённым turn ID и файлами reviewer. Успешная запись добавляет
`completion_turn_id`, `reviewed_result_hash`, `handoff_sha256`, список `evidence`
со ссылками и хешами; указатель `verification.qa` или `verification.reviewer`
обновляется автоматически. Данные проверяются до записи. Timeline дополняется,
старые события и timestamps сохраняются. В full синхронизируйте статусы lanes.
Новая редакция снимает оба прежних принятия; новое QA снимает принятие reviewer.
Старые записи остаются в summary, но их невыбранные доказательства не принимают
текущую редакцию и не мешают записать новую проверку.

Исходная сессия ищется по UUID среди файлов `$CODEX_HOME/sessions`, по умолчанию
`~/.codex/sessions`. Export из run-каталога не подходит. Проверяются `session_meta.id`,
родство, каноническая роль, согласованность дублирующих metadata, собственные
`task_started`, `turn_context.turn_id/model`, `task_complete.turn_id` и итоговый
JSON выбранного хода. `session_meta.session_id` может содержать ID внешнего root
для вложенного назначения. Такая связь принимается только по непрерывной цепочке
исходных сессий до этого root; прямой родитель и роль назначения проверяются отдельно.
Унаследованный `turn_context` до собственного запуска не подтверждает модель.

Reader принимает ISO-время с часовым поясом и целые Unix-секунды, исключая bool.
Для целого числа согласованная внешняя метка должна находиться в той же секунде
и может уточнить порядок. Конфликт или некорректная внутренняя метка отклоняются.
Без уточняющей метки секундный интервал не становится миллисекундным временем.

Reader реализует наблюдённую локальную структуру событий Desktop. Поддержка той
же структуры из CLI требует свежего V9-прогона; неподдерживаемый формат блокирует
приёмку. Это клиентские операционные доказательства, не серверная аттестация.
Production CLI не принимает источник тестовых сессий через аргументы или environment.

После исправления обновите редакцию, выполните затронутые QA-проверки и получите
новый итог reviewer по текущему результату и QA handoff. Можно использовать новые
ходы тех же независимых сессий. Полный повтор всех tests и повышение reasoning
не являются автоматическими условиями. Даже без изменения продукта правка QA
handoff требует нового `qa_handoff_sha256` в собственном ходе reviewer.

`--allow-pending` допускает незавершённые данные только при незавершённом verdict
и выводит `PRELIMINARY (not final acceptance)`. Повреждённые и противоречивые данные
отклоняются. Ни этот флаг, ни `--allow-no-check` не ослабляют положительный final.
Для `blocked` и `fail` сохраняются причина в `verification.blocker` и имеющиеся
записи; успешный запуск проверяющих не требуется.

### Условные проверки поведения

`behavioral_checks` нужен только для выбранных критериев поведения агента.
До запуска запишите выбранные критерии в scope/план и QA checklist. Для каждого
заранее определите достаточность доступных входов и `strict_inputs`. QA обязан
отклонить отсутствующую запись выбранного критерия; непустого массива недостаточно.
Каждая запись содержит `criterion_id`, `session_thread_id`, `verifier_thread_id`
независимого reviewer, ссылку `handoff`, флаг `strict_inputs`, списки `inputs`
и `outputs`. Каждая ссылка фиксирует `path`, `sha256`, `source_event` - номер
строки исходного JSONL, начиная с 1. Для tool output нужен также `source_call_id`.

До назначения reviewer поле `verifier_thread_id` остаётся незаполненным в
промежуточной записи. QA проверяет выбранный критерий, inputs и outputs; после
регистрации его принятия запустите reviewer, разрешите его настоящий UUID и
дополните это поле через recorder. Reviewer проверяет окончательную привязку к
собственной сессии. Положительный итог требует заполненного поля; запускать
reviewer заранее ради UUID или подставлять чужой ID не нужно.

Сохраните начальный пакет и каждый followup до передачи. Запишите событие
`behavior-input-prepared` через recorder с одним `--artifact`: оно автоматически
получит `input_sha256`. В соответствующем input укажите `prepared_event` - номер
строки timeline. Привяжите запись к фактическому UUID сессии после запуска.
Validator сравнит хеши, исходные байты и время подготовки с исходным событием
передачи; одного mtime недостаточно. Outputs перечисляются в исходном порядке.
`required_order` может задавать пары номеров выходных событий, например чтение
источника перед вопросом. Смысл событий и полноту критериев оценивают QA и reviewer.

При зашифрованных входах `strict_inputs: true` блокирует положительный итог.
`false` разрешён, только если приёмке достаточно подготовленной копии; передача
именно этих байтов тогда остаётся неподтверждённой. Поздняя копия, изменение байтов
и синтетическая перестановка событий не являются реальным доказательством поведения.
Нельзя ослаблять `strict_inputs` после обнаружения шифрования, чтобы получить PASS.

В Desktop вход от родителя может иметь тип `agent_message`, а результат инструмента -
`custom_tool_call_output` со списком текстовых частей. Reader проверяет адресата и
родителя сообщения; для вывода инструмента сверяет весь текст и `call_id`. Служебный
текст рядом с зашифрованной частью не подтверждает байты входа.
Если критерий требует доказать вопрос агента, сохраните его обычный ответ из исходной
сессии до followup. Зашифрованный аргумент `send_message` и пересказ получателя этого
не доказывают; недоступный output оставляет критерий незакрытым.

### Финальная команда и завершение задачи

Подготовьте `final.md` с допустимым `Verdict`, остальные документы и единственное
финальное событие timeline. Пока команда не прошла, отчёт остаётся кандидатом,
текущая задача имеет `Status: in_progress`.

```sh
python3 "$AF_PACKAGE/scripts/validate-run.py" --run-dir "$AF_RUN" \
  > "$AF_RUN/checks/final-validation.txt" 2>&1
AF_VALIDATION_EXIT=$?
printf '\nexit_code: %s\n' "$AF_VALIDATION_EXIT" >> "$AF_RUN/checks/final-validation.txt"
cat "$AF_RUN/checks/final-validation.txt"
test "$AF_VALIDATION_EXIT" -eq 0
```

Запускайте этот пример в shell без `errexit`, чтобы сохранить код отказа.
Не используйте `--allow-pending` или `--allow-no-check`. Лог финальной команды
не включается в проверяемый result hash или собственные QA/reviewer evidence,
иначе его запись изменит входы той же проверки. Это отчёт, не кэш разрешения.
Каждое последующее завершение требует свежего вызова валидатора.

При exit 1 исправьте доступную ошибку и повторите команду. При недоступном
обязательном доказательстве запишите `verification.blocker`, отрицательный
`Verdict` и незакрытый критерий. Только при exit 0 и выполненных критериях
установите `Status: done`, затем отправьте положительный final пользователю.
Не редактируйте после этого результат, handoffs, summary или итоговый отчёт без
затронутого повторного принятия и свежей валидации. Обновление статуса в памяти
не меняет продуктовый результат. Число tests и текстовое одобрение reviewer
не заменяют финальную команду; Python не перехватывает произвольный ответ Codex.

### Последующая приёмка V9

Эти сценарии требуют разрешённых реальных дочерних сессий и выполняются отдельно
от реализации. Используйте свежие временные проекты и копию проверяемого пакета;
всем участникам передайте точный путь копии. Глобальные конфигурации не меняйте.

1. В обычном Desktop создайте compact-задачу: добавить синтетический PRD из трёх
   требований в `docs/example.md`. Зафиксируйте исходный снимок и границы. Запустите
   настоящие QA/Astra и reviewer/Sol штатными средствами Desktop, передайте один
   `result_hash`, запишите два собственных завершённых хода и выполните
   `python3 skills/agent-flow/scripts/validate-run.py --run-dir "$AF_RUN" --mode compact`.
2. В обычном CLI создайте full-задачу: исправить в синтетической Python-функции
   границу сравнения, сначала получить падающий тест, затем успешный. Выполните
   те же два назначения без launcher/API. До добавления иной ветки reader сохраните
   обезличенную fixture фактически наблюдённой CLI-структуры. Выполните
   `python3 skills/agent-flow/scripts/validate-run.py --run-dir "$AF_RUN" --mode full`.
3. В каждом проекте измените один файл результата после принятия: валидатор должен
   отказать. Получите новые QA/reviewer turns по новой редакции и повторите проверку.
   Если первый run уже имеет финальное событие, сохраните его историю и создайте
   штатным init отдельный run для новой редакции. Продолжайте те же независимые
   назначения; время повторной регистрации остаётся текущим. Не добавляйте второе
   final к завершённому timeline. Общую тестовую задачу оставляйте `in_progress`
   до окончания всех проверок и последней успешной финальной команды.
   Сохраните UUID, turn IDs, result hash, команды, exit codes и исходные клиентские
   журналы на их местах. Синтетические tests не подтверждают фактический запуск модели.
4. Отдельный независимый reviewer принимает точный итоговый diff реализации и
   реальные доказательства обоих прогонов. До этого V9 и окончательная приёмка
   неподтверждены. Исторические runs и A/B/C не переписываются; прежний PASS относится
   к прежнему валидатору. Старые неполные runs могут не пройти новую финальную проверку.

Для проверки процедуры завершения в одном свежем run заранее выберите
поведенческий критерий и сбой регистрации. Сохраните все inputs до отправки;
QA должен отклонить пропущенную обязательную behavioral запись. Неверный путь
handoff должен отказать до записи; исправление аргумента использует готовый ход.
Финальная команда с отсутствующим обязательным полем должна отказать; после
исправления root повторяет её до `done` и положительного ответа. Наблюдатель
проверяет source metadata, собственные `task_complete`, tool calls и порядок
событий. Повторный пропуск команды оставляет приёмку процедуры открытой, даже
при успешных Python tests. При недоступности среды или строгих входов фиксируется
точный blocker; другая модель, API и сброс подписки не заменяют проверку.

Schema v2 adds the Architecture Contract Gate:

- `budget` is required and must be `standard` or `release`;
- `architecture_contract_required` is a boolean;
- `architecture_contract_independent` is a boolean when present;
- `standard` runs with two or more worker lanes (`implementation` or `integration`) require `architecture_contract_required=true`;
- `release` runs require `architecture_contract_required=true`;
- when `architecture_contract_required` is true, a critical `architecture` lane must exist;
- when `architecture_contract_required` is true, `architecture_context` is required and must include `product_context`, `application_surface`, `architecture_pattern`, `stack_runtime`, `risk_gates`, and `verification_gates`;
- each `architecture_context` axis is an array, at least one facet must be selected across all axes, and every facet id must exist under the matching axis in `references/architecture-matrix.md`;
- `validate-run.py` parses allowed Architecture Matrix facets from the markdown source of truth, not from duplicated constants;
- when `architecture_contract_required` is true, `architecture_capabilities` is required and must include a non-empty `selected` array and non-empty `notes`;
- selected `architecture_capabilities` must exist in `registries/architecture-capabilities.json` and must cover every selected `architecture_context` facet;
- Architecture Capability Router uses Soft Skill Binding: registry `recommended_skills` are checked by `validate-architecture-capabilities.py`, but do not block individual runtime validation;
- Architecture Design Mode runs before implementation when `architecture_contract_required=true`;
- Architecture Artifact Authoring Automation can initialize the run with `init-run.py --architecture-gate`, creating agent-authored templates for Architecture Design Brief, Architecture Contract, worker, QA, reviewer, and evidence artifacts;
- generated templates use the exact placeholder marker `TODO(agent):`, and agents must replace it themselves instead of asking the human to fill the artifacts;
- positive final verdicts, `ship` and `pass-with-risks`, are blocked while any architecture artifact referenced from `lane-map.json` still contains `TODO(agent):`;
- every successful critical `architecture` lane must set `architecture_design_brief` to an existing Architecture Design Brief;
- the Architecture Design Brief must include `Problem Shape`, `Selected Matrix Facets`, `System Boundaries`, `Data And State Model`, `Public Interfaces`, `Execution Plan`, `Risk Model`, `Verification Strategy`, `Open Questions`, and `Decision`;
- `Selected Matrix Facets` must include every selected `architecture_context` facet id, and `Decision` must contain exactly one canonical status line: `Status: approved`, `Status: needs-revision`, or `Status: rejected`;
- Architecture Design Brief `Execution Plan` must include every selected `architecture_capabilities` id;
- final `ship` and `pass-with-risks` require `Status: approved`, and successful worker lanes must run after an approved Architecture Design Brief;
- the architecture handoff `Selected Architecture` section must include every selected `architecture_context` facet id and every selected `architecture_capabilities` id;
- final `ship` requires a successful architecture lane with handoff and evidence;
- the successful architecture handoff must include these headings: `Selected Architecture`, `Rejected Alternatives`, `Module Boundaries`, `Data And State Flow`, `Public Contracts`, `Worker Ownership`, `Forbidden Changes`, `QA Gates`, `Reviewer Checklist`, and `Stop Conditions`;
- positive architecture-gated runs with QA and reviewer lanes require Claim Evidence Gate: the architecture handoff `QA Gates` and `Reviewer Checklist` sections must contain `Claim Evidence` ids, and the run root must include `claim-evidence.json`;
- `claim-evidence.json` uses `version=1` and a non-empty `claims` array; every claim id is unique kebab-case and every required contract claim id has a record;
- each claim record names `owner_lane`, `reviewed_by`, owner handoff `section`, `status`, `claim`, `subjects`, and evidence entries with literal `markers`;
- `owner_lane` must be a successful QA or review lane, `reviewed_by` must be a successful review lane, the owner handoff section must mention the claim id, and every marker must appear in the referenced evidence file;
- positive verdicts require `status=supported`; `status=gap` is allowed only for `blocked` or `fail`;
- positive architecture-gated runs require Acceptance Criteria Traceability Gate: the architecture handoff `QA Gates` and `Reviewer Checklist` sections must contain `Acceptance Criteria` ids, and the run root must include `acceptance-traceability.json`;
- `acceptance-traceability.json` uses `version=1` and a non-empty `acceptance` array; every acceptance id is unique kebab-case and every required contract acceptance id has a record;
- each acceptance record names `source`, `requirement`, `subjects`, `contract_types`, `status`, `surface_expectations`, and evidence entries with literal `markers`;
- Surface Evidence Gate requires each `surface_expectations` item to name `surface`, `polarity`, and allowed `proof_kinds`; each `evidence` or `negative_fixture_evidence` record names matching `surface`, `polarity`, and `proof_kind`, and only a matching record can cover that expectation;
- storage or other internal evidence cannot satisfy API, UI, logs, history, provider metadata, or external-provider acceptance unless the target `surface` matches exactly;
- positive verdicts require every acceptance record to use `status=supported`, every marker must appear in the referenced evidence file, every `surface_expectations` item must have matching evidence, and every required acceptance id must have evidence;
- Contract Negative Fixture Gate applies to acceptance records marked `gate`, `cli`, `query`, `storage`, `config`, or `parser`; those records must include `negative_fixture_evidence` with at least one evidence path and literal marker for a negative or drift fixture, and `negative_fixture_evidence` cannot use `polarity=positive`;
- failed, blocked, or timed-out architecture lanes block `ship`;
- reviewer and QA lanes may pass only after the architecture contract passes;
- when `architecture_contract_independent` is true, the architecture lane must use `subagent` execution with spawned trace evidence.

## Verification Readiness Gate

When `architecture_contract_required=true` and worker lanes exist, schema v2
requires Verification Readiness Gate before implementation:

- `lane-map.json` records `verification_readiness` with `artifact` pointing to
  `verification-readiness.json` and `lanes` listing readiness lanes;
- readiness lanes use `type=qa`, `role=qa-verifier`, `critical=true`, and run
  after the approved Architecture Design Brief but before worker lanes;
- `verification-readiness.json` uses `version=1` and status `ready`,
  `needs-approval`, `paused-blocked`, or `blocked`;
- readiness attempts cover every selected `risk_gates` and
  `verification_gates` facet from `architecture_context`;
- unknown, unselected, duplicate, wrong-axis, or missing readiness facets fail
  validation;
- `ready` requires all gate records to be ready and no blockers;
- `needs-approval` requires pending `approval_requests` and no successful worker
  lanes;
- approval requests can list only documented safe commands, a source document,
  manual instruction, affected gates, and `resume_phrase`;
- when the user approves, the agent records `approval_executions` with evidence,
  then repeats readiness before starting workers;
- when the user declines, status becomes `paused-blocked`, final verdict must be
  `blocked`, no successful workers may exist, and `final.md` must include the
  manual instruction plus `resume_phrase=Готово`;
- successful worker lanes must run after the latest `ready` readiness lane;
- positive final verdicts require the latest readiness status to be `ready`;
- post-worker QA records `verification_results` in lane-map and a handoff
  section named `Verification Gate Results`;
- a QA lane may pass only when `verification_results.status=pass`; blocked
  required verification forces QA `blocked` and prevents positive final verdicts.

## Continuation Gate

When a same-run continuation resumes after a blocked checkpoint, do not rewrite
the old order as if the new gate had existed from the start. If `timeline.jsonl`
contains `stage=blocked-checkpoint` or `stage=continuation` and the final verdict
is `ship` or `pass-with-risks`, schema v2 requires `continuation-summary.json`.

`continuation-summary.json` uses `version=1` and records:

- `status`: `resumed-ready`, `resumed-blocked`, or `resumed-fail`;
- `previous_checkpoint` with the `blocked-checkpoint` `lane_id`, `verdict=blocked`,
  and a snapshot path such as
  `artifacts/checkpoints/orchestrator-blocked-checkpoint/final.md`;
- `resolved_blockers` with kebab-case ids, concrete resolution text, and evidence;
- `readiness_lane` for the ready Verification Readiness lane;
- `historical_worker_lanes` for worker lanes completed before the blocked
  checkpoint or before the new readiness gate existed;
- `new_worker_lanes` for worker lanes run after continuation readiness became
  ready;
- `revalidated_lanes` for historical worker lanes rechecked after readiness;
- `qa_recheck_lane`, `reviewer_recheck_lane`, and notes.

Continuation validation is timeline-based, not wave-only:

- the previous checkpoint must exist in `timeline.jsonl` as
  `stage=blocked-checkpoint`;
- the readiness lane must have a successful timeline event after the checkpoint;
- every successful worker, readiness, QA, and reviewer lane in a positive
  continuation must have a timeline event with matching `lane_id`;
- worker events before the ready readiness lane are allowed only as declared
  `historical_worker_lanes`, and those lanes must be listed in
  `revalidated_lanes`;
- worker events after the blocked checkpoint but before ready readiness fail;
- new worker events after ready readiness must be listed in `new_worker_lanes`;
- positive continuation requires final `Continuation Summary`, QA
  `Continuation Revalidation`, and reviewer `Continuation Review` sections
  covering every resolved blocker id and every historical or new worker lane id.

## Harness Evaluation Loop

Harness Evaluation Loop turns validated trace evidence into
`harness-evaluation.json`. It is required for full traceable lane-map runs when a
learning trigger exists:

- continuation evidence: `continuation-summary.json`, `blocked-checkpoint`, or
  `continuation` timeline stage;
- risk evidence: `risk-mitigations.json` or `risk-resolutions.json`;
- blocked resolution evidence: blocked attempts, `blocked_lesson`, `rollback`,
  or `forbidden_repeat`;
- architecture evidence: worker `architecture_compliance.status=drift` or an
  architecture re-check after drift;
- readiness evidence: `needs-approval`, `paused-blocked`, `blocked`, approval
  execution, or readiness retry in `verification-readiness.json`;
- final `pass-with-risks`, `blocked`, or `fail` when
  `architecture_contract_required=true`.

`harness-evaluation.json` uses `version=1` and records:

- `status`: `evaluated`, `needs-review`, or `blocked-learning`;
- `learning_triggers` that must match real persisted triggers in the run;
- `source_artifacts` with existing evidence paths;
- non-empty `findings` and `proposals` for `evaluated` and `needs-review`;
- `blocked_reason` and evidence when status is `blocked-learning`;
- proposal `type=evidence-record`, `target=Evidence Records`,
  `status=proposed`, and `requires_human_approval=false`.

Every finding and proposal id must be kebab-case, unique within its array, backed
by existing evidence, and mentioned in final `Harness Evaluation`. Findings may
reference selected `architecture_context` facets and selected
`architecture_capabilities`, but unselected or unknown references fail
validation.

Positive lane-map runs with a learning trigger require reviewer
`Harness Evaluation Review` covering every finding and proposal id. The loop can
promote validated findings only into the current project's Project Memory
`## Evidence Records` through `scripts/promote-harness-evaluation.py`.
Architecture Matrix, capability registry, role prompts, validator guards, and
Golden Trace Runs remain canonical runtime artifacts and are not promotion
targets for project traces.

Schema v2 also enforces Architecture Execution Control and Engineering Simplicity Gate when
`architecture_contract_required=true`:

- successful `implementation` and `integration` lanes must include
  `architecture_compliance` with `status`, `contract_sections`, `notes`, and
  optional `recheck_lane`;
- `architecture_compliance.status` must be `compliant` or `drift`;
- `contract_sections` must name existing Architecture Contract sections;
- Architecture Context Propagation requires
  `architecture_compliance.matrix_facets` as a non-empty selected
  `architecture_context` subset for each successful worker lane;
- `architecture_compliance.engineering_simplicity` is required for every
  successful worker lane, with status `pass`, `fixed`, or `drift`;
- `engineering_simplicity.checks` must include `no-extra-work`,
  `stdlib-native-first`, `existing-helper-first`, `dependency-justified`,
  `abstraction-justified`, `smallest-working-diff`, and `tests-fit-risk`;
- Simplicity Gate is not a reporting gate: fix now if fixable;
- Simplicity Scope Coverage prevents peripheral-only closure: positive
  architecture-gated runs with worker lanes must include
  `engineering_simplicity_scope` with non-empty unique kebab-case
  `primary_surfaces`, optional `secondary_surfaces`, evidence, and notes;
- every successful worker lane must include
  `architecture_compliance.engineering_simplicity.scope_coverage`;
- worker `scope_coverage.primary_surfaces` and `secondary_surfaces` may only
  reference surfaces declared in top-level `engineering_simplicity_scope`;
- every top-level primary surface must be covered by at least one successful
  worker lane, and scope evidence files must mention the covered surface ids
  literally;
- worker `Engineering Simplicity` handoffs must mention every covered surface
  id;
- `pass` Engineering Simplicity cannot report fixable overengineering,
  duplicated helper, unnecessary abstraction, dependency/stack drift, or
  wider-than-needed implementation;
- `fixed` Engineering Simplicity requires non-empty findings and actions, and
  every action must appear literally in the worker `Engineering Simplicity`
  handoff;
- `drift` Engineering Simplicity requires parent
  `architecture_compliance.status=drift` and an architect `recheck_lane`;
- true simplicity drift routes to architect re-check instead of reporting-only
  closure;
- retained dependency or abstraction in Engineering Simplicity notes/actions
  must cite a selected `architecture_capabilities` id;
- successful worker handoffs must include `Architecture Compliance` and
  `Engineering Simplicity`;
- Lane Boundary Evidence Gate applies to schema v2 positive architecture-gated
  runs with successful worker lanes: every successful `implementation` or
  `integration` lane must include `boundary.allowed_paths`, optional
  `boundary.forbidden_paths`, `boundary.changed_paths_artifact`, and notes;
- `scripts/record-lane-boundary.py --run-dir <run-dir> --lane-id <lane-id>`
  writes `checks/lane-boundary-<lane-id>.json` with `version=1`, matching
  `lane_id`, `changed_paths`, `tracked_changed_paths`, `untracked_paths`,
  `base_ref`, `head_ref`, `command`, and notes;
- `boundary.changed_paths_artifact` must exist and be listed in the lane
  `evidence`;
- all boundary paths and changed paths must be repo-relative POSIX paths:
  no absolute paths, no empty paths, no backslashes, and no `..`;
- glob matching uses Python `fnmatch.fnmatchcase`; every changed path must
  match at least one `allowed_paths` pattern, and any `forbidden_paths` match
  fails even when the path is also allowed;
- empty `changed_paths` is valid because this gate checks boundaries, not
  whether a worker was required to edit product code;
- worker handoffs must include `Boundary Evidence`; QA `Architecture
  Invariants`, reviewer `Contract Drift`, and final `Boundary Evidence` must
  mention every successful worker lane id;
- reviewer `Contract Drift` must reject reporting-only simplicity closure and
  mention `Engineering Simplicity` plus every fixed worker lane id;
- QA handoff must include `Engineering Simplicity Scope` and every primary
  surface; reviewer `Contract Drift` must mention every primary surface and
  reject peripheral-only closure; final `Engineering Simplicity` must mention
  every primary surface;
- worker `Architecture Compliance` sections must include every facet id declared
  in `architecture_compliance.matrix_facets`;
- `compliant` worker lanes must not set `recheck_lane`;
- architecture or simplicity drift blocks `ship` and `pass-with-risks` until
  `recheck_lane` points to a later, successful, critical `architecture` lane
  with contract handoff and evidence;
- when worker lanes exist, final `ship` requires successful QA and reviewer lanes;
- QA must pass after workers and any architecture re-check, and QA handoff must
  include `Architecture Invariants` with every selected `risk_gates` and
  `verification_gates` facet;
- reviewer must pass after architecture, workers, and QA, and reviewer handoff
  must include `Architecture Matrix Mismatches` and `Contract Drift` covering
  every selected `architecture_context` facet and selected
  `architecture_capabilities` id; `Contract Drift` must cover Engineering
  Simplicity.

Mitigation Gate applies to every traceable run with `Verdict: pass-with-risks`:

- `risk-mitigations.json` is required at the run root;
- `risk-mitigations.json` must use `version=1` and a non-empty `risks` array;
- each risk id must be unique kebab-case;
- each risk status must be `identified`;
- each risk category must be one of `verification-gap`, `architecture-drift`, `incomplete-implementation`, `test-gap`, `security-risk`, `data-risk`, `ux-risk`, `dependency-risk`, `release-risk`, or `unknown`;
- each risk records non-empty `detected_by`, `problem`, `impact`, `affected_scope`, `evidence`, `next_gate`, and `owner_lane`;
- every evidence path must exist, and `next_gate` must be `resolution`;
- `final.md` must include `Risk Mitigations` and every risk id;
- when `lane-map.json` exists, `detected_by` and `owner_lane` must reference existing lane ids;
- when `lane-map.json` exists, a successful reviewer lane must include `Risk Mitigation Review` and every risk id.

Resolution Gate follows Mitigation Gate for every traceable run with `Verdict: pass-with-risks`:

- `risk-resolutions.json` is required at the run root;
- `risk-resolutions.json` must use `version=1` and a non-empty `resolutions` array;
- every resolution `risk_id` must match an identified risk from `risk-mitigations.json`;
- every identified risk must have exactly one resolution record for `pass-with-risks`;
- resolution status must be `fixed`, `mitigated`, or `contained` for `pass-with-risks`;
- `unresolved` is allowed only for `blocked` or `fail`;
- `resolution_type` must be one of `code-change`, `test-added`, `evidence-added`, `scope-contained`, `architecture-recheck`, `config-change`, `docs-corrected`, or `not-resolved`;
- each resolution records non-empty `owner_lane`, `resolution`, `evidence`, `verification`, `verified_by`, and `reviewed_by`;
- every evidence path must exist;
- `final.md` must include `Risk Resolutions` and every risk id;
- when `lane-map.json` exists, `owner_lane`, `verified_by`, and `reviewed_by` must reference existing lane ids;
- when `lane-map.json` exists, `verified_by` must be a successful QA lane and `reviewed_by` must be a successful review lane;
- when lane waves are present, the order is `owner_lane <= verified_by <= reviewed_by`;
- when `lane-map.json` exists, QA handoff must include `Risk Resolution Verification` and every risk id;
- when `lane-map.json` exists, reviewer handoff must include `Risk Resolution Review` and every risk id.

Blocked Resolution Gate is part of Resolution Gate and uses the same `risk-resolutions.json` artifact:

- flat resolution records remain valid when no blocked recovery is needed;
- when `attempts` is present, attempt numbers must be contiguous from `1`, at most three attempts are allowed, and statuses must be `fixed`, `mitigated`, `contained`, `blocked`, or `unresolved`;
- every blocked attempt records `blocked_lesson`, `forbidden_repeat`, `rollback`, `blocked_reason`, evidence, verification, `verified_by`, and `reviewed_by`;
- `rollback.status=not-possible` is allowed only for final `blocked` or `fail`;
- attempt 1 blocked requires a Blocked Recovery Path with Senior QA `Senior QA Test Design Review`, then architect `Resolution Architect Review`; attempt 2 owner lane must run after that architect lane;
- attempt 2 blocked requires `Supervising Architect Review`; attempt 3 owner lane must run after that supervising architect lane;
- Senior QA recovery lanes use `type=qa`, `role=senior-qa-verifier`, successful status, and handoff coverage for the risk id;
- architect recovery lanes use `type=architecture`, `role=architect`, `critical=false`, successful status, `Resolution Architect Review`, and the worker instruction;
- supervising architect recovery lanes use `type=architecture`, `role=supervising-architect`, `critical=false`, successful status, `Supervising Architect Review`, and the final retry instruction or final blocker;
- a third blocked attempt forces final `blocked` or `fail`; it cannot close as `pass-with-risks`.

For final handoff, `validate-run.py` also requires:

- exactly one `Verdict:` field in `final.md`;
- exactly one valid final verdict value: `ship`, `pass-with-risks`, `blocked`, or `fail`;
- exactly one run-level `timeline.jsonl` final event when a timeline exists or any agent trace exists;
- the last timeline event must be `stage=final` and `role=orchestrator`.
- timeline timestamps must be non-decreasing in file order;
- if the timeline contains orchestrator `implementation` or `fix` events, the
  final successful orchestrator `verification` or `checks` event must come after
  the last such implementation/fix event.
- if `final.md` declares a product commit hash, `timeline.jsonl` must contain a
  matching orchestrator `stage=commit` event before the final event;
- if an orchestrator `stage=commit` event exists, it must come after the last
  successful orchestrator `verification` or `checks` event and before the final
  event.

For compact traces, `timeline.jsonl` is optional only when there are no role/agent traces. If a compact run records timeline or agent traces, the final timeline event rule applies.

## Timeline Event Minimum

```json
{
  "timestamp": "2026-05-22T12:00:00+03:00",
  "stage": "verification",
  "role": "qa-verifier",
  "stable_agent_name": "qa-verifier",
  "stable_agent_slug": "qa-verifier",
  "status": "pass",
  "summary": "Build and regression tests passed.",
  "artifacts": [],
  "next_step": "final review"
}
```

## Trace Updates

For full trace, update `manifest.md` and `timeline.jsonl` after intake, route, plan, delegation, handoff, verification, review, blocker, fix, and final.

Use `scripts/append-timeline.py` for run-level orchestrator events. Use
`scripts/record-agent-trace.py` for subagent events so the same event appears in
the run-level timeline and in `agents/<role>/trace.jsonl`.

Keep timeline events in real workflow order. Do not batch-write route,
implementation, verification, and final at the end with guessed timestamps. If
checks are rerun after a fix, append a new verification/checks event after the
fix. The final timeline should make the actual sequence readable without
opening chat history.

Exactly one final orchestrator event is mandatory before final handoff:

If a product commit was created, append the commit event first:

```bash
python3 scripts/append-timeline.py \
  --run-dir <run-dir> \
  --stage commit \
  --role orchestrator \
  --stable-agent-name orchestrator \
  --stable-agent-slug orchestrator \
  --status pass \
  --summary "Committed product changes as <hash>." \
  --commit-hash <hash> \
  --next-step "write final.md and validate run"
```

```bash
python3 scripts/append-timeline.py \
  --run-dir <run-dir> \
  --stage final \
  --role orchestrator \
  --stable-agent-name orchestrator \
  --stable-agent-slug orchestrator \
  --status pass \
  --summary "Final checks passed and final.md recorded the verdict." \
  --next-step "handoff to user" \
  --artifact final.md \
  --artifact checks.md
```

## Per-Agent Trace Events

Every subagent that receives a delegation packet gets a first-class trace path:

```text
agents/<role>/trace.jsonl
```

The helper creates `agents/<role>/` and `artifacts/agents/<role>/` when needed:

```bash
python3 scripts/record-agent-trace.py \
  --run-dir <run-dir> \
  --role python-worker \
  --execution-mode subagent \
  --lane-id backend-cli \
  --wave 2 \
  --critical \
  --stage spawned \
  --status active \
  --codex-thread-id <thread-id> \
  --summary "Spawned python-worker for backend-cli." \
  --next-step "handoff"
```

Then record the terminal handoff:

```bash
python3 scripts/record-agent-trace.py \
  --run-dir <run-dir> \
  --role python-worker \
  --execution-mode subagent \
  --lane-id backend-cli \
  --wave 2 \
  --critical \
  --stable-agent-name "Python Worker" \
  --stable-agent-slug python-worker \
  --stage handoff \
  --status pass \
  --summary "Python worker completed helper changes and verification." \
  --next-step "orchestrator review" \
  --artifact handoffs/backend-cli.md
```

Pass each owned artifact with repeated `--artifact` flags. The helper indexes
those paths in `artifacts.json` with `role`, `stable_agent_name`,
`stable_agent_slug`, `execution_mode`, `source: agent-trace`, and timestamp metadata. Repeated
records for the same `role` and `path` update the existing artifact entry instead
of duplicating it. If `artifacts.json` is a top-level array, the helper writes a
top-level array back. If it is an object with an `artifacts` array, the helper
updates that field and preserves the object shape.

## Subagent Vs Role Lane

Do not call a role lane a subagent unless an actual subagent/spawn tool was used.

- Actual spawned subagent: record `--execution-mode subagent`, include a `stage=spawned` event with `--codex-thread-id`, then record the terminal handoff/blocked/fail event.
- Role lane without a spawned runtime: record `--execution-mode role-lane`, or keep it as an orchestrator note outside `agents/<role>/`. Its output is a scoped role review, not subagent execution.

`validate-run.py` fails agent traces that look like subagents but have no
spawned event with `codex_thread_id`, no terminal handoff, or a narrative
sidecar/subagent claim without trace evidence. This is intentional: the trace
must distinguish real parallel/delegated execution from role-labeled
orchestration.
