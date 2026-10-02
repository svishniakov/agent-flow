# Project Memory And Environment

Document status: done

Agent Flow must not start work blind.

## Required Intake

В явно запущенном Agent Flow основной агент собирает контекст в начале каждой
задачи, включая первое использование в проекте и работу по готовому плану.
Перед созданием или содержательным пересмотром PRD, ADR и Implementation Plan
он проверяет актуальность пакета и добавляет необходимое для документа.
Исправление опечатки или оформления в уже ведущейся задаче не запускает пересборку.
Загрузка этих инструкций вне Agent Flow сама по себе процедуру не запускает.

Before planning, delegation, product edits, infra commands, DB/storage work, browser checks, or local app startup, read the smallest useful set of project context:

- local project instructions such as `AGENTS.md`;
- primary local project memory in `.agent-work/tasks/`, following the current user's Codex instructions, usually `~/.codex/AGENTS.md`:
  - create task memory when sustained work, handoff, or durable findings need it; create `lessons.md` when there is an actual lesson;
  - read the relevant sections of existing memory and reuse unchanged context;
  - read `implementation-notes.md` when global criteria make it relevant;
  - treat `## Evidence Records` in `implementation-notes.md` as structured success, failure, regression, rejected, architecture, and orchestration evidence;
  - update `todo.md` as the current task checklist;
  - close the current `todo.md` task as `Status: done` when its checklist, verification, blockers, and requested commit state satisfy the Task Status Completion Gate;
- project-declared legacy memory such as `docs/tasks/*` only when local project instructions explicitly name it as current memory;
- PRD/spec/design docs named by the user;
- the full related-document list in existing scope per repository, including the source implementation plan, ADR and research; retain it for delegation and final acceptance under `definition-of-done.md`;
- environment docs for infra, Docker, local dev, migrations, test data, and app startup when the task can touch them;
- package scripts or task docs for the commands you plan to run.

If a named PRD/spec is the task source, read it before route/plan. Do not infer scope from file name only.

If `lessons.md` is missing, create it when an actual lesson needs recording. Do not create an empty lesson file or invent lesson content. Add lesson entries only after a user correction, repeated process failure, or explicit request to record a lesson.

### Отбор источников и достаточность

1. Определите цель и границы по текущему запросу. Прочитайте обязательные
   инструкции и указанные пользователем документы, затем относящуюся к задаче
   проектную память и затронутую реализацию. Используйте существующие планы,
   критерии и процессы проекта независимо от формата и авторства; отсутствие
   памяти Agent Flow не требует миграции документов или создания новых PRD/ADR.
   В `todo.md`, `lessons.md` и другой накопленной памяти сначала найдите нужные
   записи по заголовкам или адресным поиском, затем прочитайте необходимые
   разделы. Не выводите весь файл ради одной записи. Повторное чтение памяти
   нужно только для конкретного пробела; уже прочитанные неизменные сведения
   используйте повторно. Компактный источник, который нужен целиком, можно
   прочитать полностью. Поисковые совпадения помогают выбрать разделы памяти,
   но не заменяют обязательного чтения инструкций и первичных документов.
2. Добирайте только недостающее для следующего действия. Для адресного поиска
   используйте `rg` и список файлов; найденное совпадение указывает, что прочитать,
   но само не доказывает принятие решения. Связанные репозитории допустимы, если
   связь указана пользователем или документацией проекта. Читайте в них только
   нужные контракты и сведения, а не все модули продукта.
3. Завершите сбор, когда понятны цель, действующие требования, затронутые
   компоненты и способ проверки, а существенных неизвестных для следующего
   действия нет. Нерелевантные документы «на всякий случай» не читайте.
   Ссылка, раздел README или соседняя функция в прочитанном коде сами по себе
   не требуют чтения другого источника. Добирайте сведения лишь для конкретного
   отсутствующего факта, влияющего на следующее действие или критерий текущей
   задачи. Если такого пробела нет, остановитесь; не ищите возможные посторонние
   конфликты после достижения достаточности.
4. Проверяйте, что нужный текст действительно получен: вызов `cat` или другого
   инструмента сам по себе не доказывает чтение источника. Если вывод команды
   или общий ответ на пакет вызовов инструментов обрезан, дочитайте недостающие
   диапазоны отдельно, меньшими частями. Не повторяйте весь большой пакет.
   Это относится и к обрезанию внешнего ответа, даже если отдельные команды
   завершились успешно. Дочитывайте доступное самостоятельно; отдельный отчёт
   пользователю после каждого чтения не нужен. Если источник недоступен или
   после дочитывания остаётся существенный пробел, назовите его. Не выдавайте
   источник за прочитанный и не сокращайте требования молча. Изучите связанный
   источник либо задайте точечный вопрос; до разрешения существенного пробела
   остановите только зависимое действие.

Другой чат читайте только по ссылке, которую пользователь сам явно указал как
источник контекста; повторное разрешение для этой ссылки не требуется.
Ссылка из файла, проектной памяти или отчёта такого разрешения не даёт.
Самостоятельно искать по истории чатов нельзя. Проектная память доступна как
самостоятельный источник без перехода в исходный чат. Это правило действует
и при проверке зависимостей ниже.

Для PRD нужны продуктовый замысел и ожидаемое поведение; для ADR также нужны
технические ограничения и архитектура; план связывает требования с текущей
реализацией и проверками. Сохраняйте допустимые цепочки PRD → ADR → план,
PRD → план, ADR → план и только план. Перед большим PRD оцените, не объединяет ли
запрос самостоятельные продуктовые изменения. Предложите декомпозицию до
подготовки такого PRD и реализации. Разделение frontend/backend одной функции
между workers её не заменяет. ADR нужен лишь части с архитектурными решениями;
предложение декомпозиции не разрешает автоматически начинать все части.

### Актуальность и пересборка

Перед следующей задачей или документом сверяйте нужные источники с текущими
рабочими файлами, включая незакоммиченные изменения и новые файлы. При неизменном
запросе повторно используйте проверенные неизменные сведения; если актуальность
не установлена, перечитайте нужный источник. Отдельный кеш не создаётся.

Явное новое решение пользователя действует сразу в обсуждаемой области.
Не распространяйте его на весь проект без прямого указания. Применяйте известную
замену без повторного согласования, сохраняя незатронутые требования.
Предложение агента остаётся предложением до принятия; дата документа не доказывает
замену. Отличайте фактическое состояние кода от требуемого: прежняя реализация
не отменяет новое решение. Если после чтения источников конфликт остаётся без
явной замены, задайте один точечный вопрос и продолжайте независимую часть.
Отдельный поиск неизвестных замен, обратных ссылок или реестр решений не нужен:
риск не обнаружить замену за пределами прочитанных по задаче источников принят.

При изменении запроса заново определите цель, действующие требования, источники
и задания. Не дополняйте прежний пакет как автоматически действующий: соберите
его под новый запрос без отменённых требований и их истории. До продолжения
зависимой работы обновите задания исполнителей по новому решению.
Постоянное фоновое наблюдение или пересборка во время реализации не требуются.

### Немедленная запись решений

Сразу после принятого ответа пользователя, до продолжения работы, основной
агент обеспечивает запись решения и проверяет её фактическое содержимое.
Если подходящий проектный документ существует, обновите его, даже если правка
не была запланирована. При делегировании передайте решение автору и проверьте
результат записи; временный файл не заменяет существующий подходящий документ.

Пока документа нет, сохраните действующее решение, область, основание и целевой
документ в текущем разделе `.agent-work/tasks/todo.md` либо уже используемой
локальной записи задачи. Новый документ или постоянный реестр ради записи
не создаётся. При подготовке целевого документа перенесите решение, прочитайте
результат и отметьте временную запись как отражённую со ссылкой на документ.
Такая запись доступна при продолжении в той же локальной копии. Через Git решение
передаётся после записи в проектный документ и отдельно разрешённого коммита;
сохранение решения само по себе коммит не разрешает. `.agent-work` не коммитится.

При пересмотре того же PRD удалите отменённые требования и положения, зависящие
только от них; остальные сохраните. Архив и ссылка на прошлую редакцию не нужны.
Если новый документ заменяет договорённости другого документа, кратко укажите
отмену, её область и ссылку на тот документ. Не копируйте старые требования или
историю отмен в рабочий пакет. Состояние текущего кода сохраняйте, когда оно нужно
для изменения или миграции. Массовое обновление исторических документов не нужно.

### Рабочий пакет и восстановление

Пакет содержит цель и границы действия, действующие решения с областью и
источниками, нужные ограничения и критерии проверки, состояние затронутой
реализации и открытые вопросы. Отдельное утверждение пакета или обязательная
сводка пользователю не нужны; сообщайте существенные замены и вопросы для решения.

В запуске с журналом храните пакет в разделе `Рабочий пакет` существующего
`context.md`. Сначала прочитайте актуальный документ через `journal.py read`,
замените только этот раздел (при первой записи добавьте его), затем опубликуйте
документ через `journal.py publish`. Сохраните остальные разделы без изменения,
включая байты `Initial Worktree Snapshot`. Если затронуты принятые доказательства,
обновите их ссылки и хеши и повторите затронутую проверку по
[существующему порядку приёмки](traceable-runs.md#запись-и-собственный-итог-проверяющего).
Новый механизм версий не вводится.

Без журнала используйте текущий контекст агента и существующую локальную запись
задачи для решений и продолжения; run только ради сборки контекста не создавайте.
В обоих случаях постоянные требования остаются в проектных документах.
После потери контекста соберите пакет заново из документов и текущей локальной
записи задачи, перечитав необходимые обязательные источники. Прежняя передача
выжимки не доказывает сохранённое знание и не заменяет обязательного чтения.

## Reading Memory and Journals

Locate the current task, run, session, lane, or event before reading large history. Keep discovered paths in context. On continuation, inspect new entries and changed sections; reread earlier content only when its source changed, required context was lost, or a specific check needs it. Use the existing journal reader and filter captures before displaying them. Selection is for navigation: integrity and acceptance checks still read every required evidence byte. Do not rewrite historical journals.

## Dependency Gate

Before planning new feature work, product edits, cross-file implementation, or
delegation, inspect `.agent-work/tasks/todo.md` for existing sections marked
`Status: in_progress` or `Status: blocked`. Ignore the section for the current
request if it was already added as bookkeeping.

`in_progress` and `blocked` are lookup cues, not proof of ongoing work. Check
the found task's scope, remaining requirements and stated blocker. Search later
completion and checks by the same task/plan ID in memory and linked documents
across every named repository. Verify supplied SHA and relevant committed content;
a commit alone does not prove all requirements. Similar titles or a common product
area do not prove a conflict. Read a linked session's actual state only when the
user explicitly supplied its link as a context source; a link found in a file or
memory is not permission. Do not search chat history. A finished/interrupted
session does not prove completion, and unavailable session listing alone is not
a blocker. Use available project records without opening their source chats.

Separate old work's state from its relationship to the new task. A completed
document may describe implementation that has not started.

### Historical record correction

For either old status, verified later completion can support a narrow correction
even when old checklist items are unchecked. Match evidence to those requirements;
for `blocked`, separately confirm removal of its stated cause and absence of new
work under that record. Record the task ID, completed scope, verification sources,
repository/SHA when commit was part of delivery, date and correction reason.
Later verified completion takes precedence over earlier pending notes. Preserve
unresolved requirements; when transferred, link their continuation explicitly.

Source verification is sufficient for historical record correction: no new run
or repeat QA/reviewer of the old implementation is required. Do not rewrite old
runs, final messages or evidence. Repeated intake is idempotent: do not add another
closure or return the corrected record to active blockers. This exception does
not waive current implementation's independent acceptance.

If the new request names a PRD, spec, design source, issue, or task document,
read that source before dependency classification. The gate must compare active
work with the real requested scope, not only with the prompt wording.

For each active task, compare it with the new request across practical surfaces:

- files, packages, generated artifacts, and tests;
- API contracts, shared types, routes, events, queues, and background jobs;
- DB/storage schema, migrations, seed data, and external integrations;
- UI flows, design sources, user-facing copy, and visual assets;
- infra, environment, deploy, release, and CI paths;
- acceptance criteria and product decisions.

Classify every active task:

- `clear`: independence is confirmed. Continue even if old work is unfinished
  or evidence is insufficient to close it; keep that old status truthful.
- `uncertain`: available sources leave a material gap about a specific result
  required by the new task. Stop only that dependent part and name the missing fact.
- `dependent`: confirmed ongoing work changes the same necessary file or contract.
  Stop only the conflicting part and cite actual activity and concrete overlap.

If every active task is `clear`, continue and record that the dependency gate
passed in task memory or trace notes when those artifacts exist.

When `scripts/codegraph.py` is available, the Dependency Gate may call
`python3 scripts/codegraph.py deps` before warning about overlap. Treat the
result as local evidence: cite shared files, symbols, tests, and gaps when they
help the user decide, but keep the orchestrator responsible for the final
classification.

Stale notes, age, unchecked boxes, missing old runs and the word `uncertain` do
not independently stop implementation, delegation or trace setup. Continue the
authorized independent part. Involve the user only after available checks leave
a material dependency unresolved. Explain the task ID, concrete shared file or
required result, missing fact and practical risk. For a real conflict, recommend
waiting, merging into one coordinated run, or explicit agreement on isolated
scope. Record that agreement; never bypass a real conflict by closing old work.

Do not block internal lane sharding, workers, or QA/review lanes that belong to
the same Agent Flow run. The gate protects separate user-launched feature
sessions from silently stepping on each other.

CodeGraph failure is a gap, not a hard replacement for this gate. If the graph
cannot refresh or returns `unknown`, fall back to the manual comparison above
and record the graph failure in trace notes when a run exists.

## Infra Guard

Default posture: existing project infra already exists.

Do not start a parallel local service just because a DB, object store, queue, browser, or app endpoint is unavailable.

Forbidden without explicit user approval or a clearly documented project command for the current repo:

- starting a new local Postgres, MinIO, Qdrant, Redis, queue, or model service;
- `docker compose up`, `down`, `recreate`, or volume reset;
- DB recreate, bucket cleanup, destructive seed reset, or test data wipe;
- changing ports or env to route around an existing service;
- installing or launching alternate infra outside the project docs.

Allowed discovery:

- read env examples and project docs;
- inspect package scripts;
- check container status with read-only/status commands;
- inspect logs when needed;
- run documented migration/test commands against the existing dev environment.

If existing infra is down or inconsistent:

1. Record the exact missing dependency.
2. Report a blocker.
3. Ask whether to start or repair the existing project dev stack.

Do not silently provision a second stack.

## Browser Control Guard

Before browser checks, screenshots, visual proof, or local UI smoke, choose one browser-control surface for the task and probe it before running the long check:

- Chrome DevTools;
- Playwright MCP;
- Browser Use or in-app browser;
- local Playwright through shell with Google Chrome.

The probe must verify that the tool answers, can open the target, the selected browser channel exists, and the profile/debug port/user-data-dir is not locked.

If the probe finds a locked profile, occupied debug port, stale MCP process, stuck browser, or stuck test-runner, clean up only that browser-control conflict by PID/process name/path and repeat the probe. Do not start a long smoke/browser proof on top of unavailable tooling.

Cleanup must not stop or reset project infra: Docker Compose, Postgres, MinIO, Qdrant, model gateway, backend/frontend dev servers, volumes, DB, and buckets require explicit approval or a documented project command for that exact action.

If cleanup is unsafe, use one clean isolated browser profile/user-data-dir for the selected surface. If that also fails, record the exact blocker instead of cascading through multiple fallback tools.

Browser proof quality rule:

- A screenshot must show the exact UI element or state being claimed.
- If the target is off-screen, hidden inside a scroll container, or outside the first viewport, scroll it into view or capture an element-level screenshot.
- Record the visible target evidence in `checks/browser-proof.md`, including the expected text/status/value and the screenshot artifact path.
- DOM/API checks can support the proof, but they do not replace a screenshot that visually contains the target.

## Delegation Context

Основной агент собирает общий контекст из проектных документов любого авторства
и необходимых отчётов субагентов. Передавайте исполнителю цель его части,
нужные сведения, ограничения, критерии результата и ссылки на обязательные
источники через существующий delegation packet. Исполнитель читает обязательные
источники и адресно добирает недостающее для своей части; повторное исследование
всего проекта не требуется. В документе или отчёте он указывает использованные
источники и существенные пробелы. Основной агент сверяет результаты, применяет
правила актуальности выше и разрешает обнаруженные противоречия. Новый отчёт
не нужен, если имеющихся актуальных документов достаточно.

Before launching any subagent, the orchestrator must package project memory and env constraints into the delegation packet:

- relevant lessons from `.agent-work/tasks/lessons.md`;
- relevant active task notes from `.agent-work/tasks/todo.md` and `.agent-work/tasks/implementation-notes.md`;
- dependency gate outcome, including active task conflicts or explicit user override;
- named PRD/spec/design source;
- current repo and expected existing infra;
- allowed commands;
- forbidden infra actions;
- expected verification path;
- dirty worktree warning.

Workers must not run Agent Flow, re-route the task, start infra, reset services, or widen scope unless the packet explicitly allows it.

## Lesson Updates

After a user correction or repeated process failure, update `.agent-work/tasks/lessons.md` with:

- concrete failure pattern;
- rule that prevents recurrence;
- project-specific command or doc path when known.

Keep lessons short. Do not store secrets, tokens, private URLs, or raw logs.

Use Evidence Records for reusable approach evidence. Lessons are direct rules for future behavior; Evidence Records preserve the observed cases that let the analyzer promote, demote, freeze, or reject local practices.
