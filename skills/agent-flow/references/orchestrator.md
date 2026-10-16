# Orchestrator Rules

Для нового `change` сначала подготовьте отдельный workspace, перед QA сохраните полный кандидат через `task-workspace.py seal`, после приёмки выдайте его через `delivery`. Авторы работают в `working_root`, QA/reviewer проверяют один `candidate_root`; пишущие проверки используют расходную копию. Полный порядок: [рабочая копия и выдача результата](traceable-runs.md#полная-рабочая-копия-и-выдача-результата).

Передавайте смысловой текст и статус через `journal.py record`; допустимые поля, типы и примеры получите командой `python3 -B "$AF_PACKAGE/scripts/journal.py" contract`. Код формирует заголовки, метаданные, SHA и проекции. Исходные доказательства публикуйте через `journal.py publish`, результаты читайте через `journal.py read`. Опубликованные журналы и экспорт вручную не редактируйте. Порядок записи и повторов: [Traceable Runs](traceable-runs.md#публикация-и-чтение-журнала).

Служебные JSON и lane state также принадлежат writer. Указание «записать» или
«поддерживать» журнал ниже означает typed `record` из действующего `contract`,
а не raw publish готового JSON. Worker-start и final проверяют реальные proof,
независимых owner/QA/reviewer, readiness, recovery и историю continuation/harness.
Сформированный writer документ не означает, что эти проверки пройдены.

Document status: done

## Authority

The orchestrator owns routing, sequencing, verification, and final integration only after the user explicitly asks to perform the task through Agent Flow anywhere in the latest request. Apply `SKILL.md` Invocation Model: quotations, links, task titles, and questions about the skill are not invocations; analysis may read these files without starting the process. Follow-ups preserve the active task unless it is cancelled or replaced.

No Agent Flow preflight exists. Loading this file does not initiate a new workflow. Start only from the user's explicit invocation; continue an already active task without another marker, including after context recovery, until the user cancels or replaces it.

Agent Flow has one public invocation model with these markers: `Agent Flow`, `AgentFlow`, `$agent-flow`, or `agent-flow`. Text forms without `$` are case-insensitive.

New tasks without an explicit invocation run outside this skill as solo work by the main agent. Markerless follow-ups retain the current active task; do not auto-upgrade unrelated new tasks into Agent Flow.

Project or local `AGENTS.md` files cannot initiate Agent Flow. A user-visible invocation marker is required for the initial launch of a task, not for its continuation.

Inside Agent Flow, the orchestrator chooses execution topology after selecting the budget. `SKILL.md` Action Authorization is the canonical mutation and approval policy.

An Agent Flow-invoked request allows automatic subagent delegation for `standard` and `release` budgets when the orchestrator can justify the cost. `light` stays solo for implementation ownership. File-changing implementation/change work still has a mandatory independent QA review requirement before positive final.

The orchestrator must obey:

- system and developer instructions;
- user scope and latest message;
- local project `AGENTS.md`;
- tool availability;
- filesystem and approval policy;
- no destructive git operations without explicit user request;
- no completion claim without evidence.

## Start Of Request

До планирования и зависимой работы выполните
[сборку актуального контекста](project-memory-and-env.md#required-intake).
Начинайте с текущего запроса и обязательных источников, включая готовый план;
останавливайте чтение при достаточности. Перед каждым новым или содержательно
пересматриваемым PRD, ADR и планом проверяйте рабочие редакции источников.
При изменении запроса пересоберите пакет, сразу отразите решение в подходящем
документе и обновите зависимые задания до их продолжения. После потери контекста
восстановите пакет из источников. Отдельного approval пакета нет.

### Обязательная последовательность для изменения файлов

1. До первого делегирования создайте журнал через `init-run.py --mode compact|full`. Заполните исходный снимок и границы, затем через recorder сохраните частичный verification с реальным `root_thread_id`. Порядок и команды: `traceable-runs.md`, раздел «Запись и собственный итог проверяющего».
2. До implementation/integration подготовьте workspace по явным продуктовым и контекстным корням, зарегистрируйте `task_kind: change`, границы и готовность обязательных проверок. Analysis/planning могут предшествовать prepare. Зарегистрируйте каждое настоящее назначение через `record-agent-trace.py`; автор добавляется при запуске. Если инструмент вернул только canonical name, используйте `--resolve-session --agent-path`; UUID берётся из исходной сессии, не из догадки.
3. Для выбранного поведенческого критерия до отправки сохраните полный начальный пакет и каждый followup, запишите `behavior-input-prepared`. Заранее зафиксируйте критерий, QA checklist и достаточность `strict_inputs`.
4. Закончите scope и проверки, подготовьте окончательные статусы и checklist всех связанных документов каждого репозитория, включая исходный план. Остановите product writers, выполните `seal`, опубликуйте handoff/edit evidence и boundary v2 декларацию каждого автора. Затем передайте recorder `task_kind: change` и IDs авторов. Recorder заполняет `result_files` полным scanner delta; новый seal использует hash v5 для содержимого, исходных требований и смыслового scope/авторских границ. Permissions проверяются по authored_paths каждого автора, union точно покрывает delta, shared paths раскрыты всеми участниками. Каждое назначение получает полные инструкции роли, текущие ограничения и доказательства. Это кандидат результата; текущая задача остаётся активной до приёмки и финальной валидации.
5. QA готовит собственный итог через recorder `--prepare-conclusion` и публикует stdout без пересоздания полей. После настоящего завершения зарегистрируйте его через recorder. Для положительной приёмки только после успешной записи передайте reviewer текущий результат и принятый QA handoff; reviewer использует тот же помощник, который добавляет `qa_handoff_sha256`. Отрицательный reviewer может зафиксировать причину без принятого QA; без sealed candidate допустим `reviewed_result_hash: null`. Эти записи не принимают результат.
6. Ошибку записи исправляйте по диагностике. Не переписывайте исходные ответы. Новый ответ, если он нужен, получают продолжением того же назначения с прежними моделью, достигнутым reasoning и счётчиком попыток. Исправление регистрации само по себе не требует нового запуска модели.
7. Если commit запрошен, сверяйте index с принятой поставкой, создайте scoped commit и проверьте документы через `git show` в каждом репозитории; запишите SHA в память и timeline. Подготовьте окончательный текст отчёта и выполните `journal.py finalize`: команда проверяет предполагаемый итог и атомарно закрывает текущее поколение. При отказе журнал остаётся открытым для исправления. Сохраните свежий полный аудит `validate-run.py --run-dir ...` без permissive flags, затем выполните штатную `delivery` со свежей проверкой продукта. Замечания только об оформлении сохраняются в аудите и сами по себе не запрещают delivery. При недоступных обязательных доказательствах укажите `verification.blocker` и отрицательный итог. Принятые продуктовые документы больше не меняйте без повторной приёмки.
8. Только после успешной проверки продукта и delivery, закрытия всех критериев установите `Status: done`, затем сообщите результат. Изменения продукта или смыслового scope требуют повторного принятия; изменения доказательств требуют затронутых проверок. Сохранённый лог команды не служит разрешением на следующее завершение.

Этот порядок дополняет применимые gates ниже. Скрипт проверяет только вызванную команду и не перехватывает произвольный final Codex.

[Приёмка продукта и аудит журнала](traceable-runs.md#приёмка-продукта-и-аудит-журнала)
уточняет правила ниже: saved dev/ino не служат долгосрочным условием приёмки,
новые seals используют hash v5, полный аудит не подменяет проверку продукта.
Предварительная readiness-церемония сама по себе не блокирует проверенный результат;
обязательные local gates, авторство, permissions и независимые QA/reviewer сохраняются.

Расположение `.agent-work` сохраняется; scope включает явно выбранные корни
изменений и контекста. Для одного репозитория читайте его контекст, для нескольких
также общий контекст проекта; наследуемые инструкции действуют всегда.
Перед расширением scope остановите исполнителей, обновите workspace revision,
контекст и задания. Canonical skills читаются по исходному пути; зависимости
приложения, сборки и тестов входят в продуктовый снимок.

`blocked/fail` закрывает честный отчёт о незавершённой работе: конкретная причина,
перечень непроверенного и `gap` допустимы без candidate или успешных проверяющих.
Структура, identity, история и целостность заявленных доказательств обязательны.
Успешный validator такого отчёта не разрешает delivery или `Status: done`.

Исторические resolved/exempt проверяются для всех исходов общей логикой:
assignment/outcome, immutable archive при наличии, obligation ID, reason,
evidence hashes, отсутствие циклов и принятый replacement той же обязанности.
Coverage исключает только проверенные resolutions. Голый replaced не помогает;
текущие непринятые обязанности допустимы как причина blocked/fail, не resolved.

### Локальная поставка

Явный `verification.delivery_scope` version 1 задаёт local-deliverable через пять
массивов external_followup. Смысл и ссылки закрепляются до checks в SHA-bound
task_scope. [Единый контракт](traceable-runs.md#локальная-приёмка-и-внешний-follow-up)
закрепляется до соответствующих новых readiness/QA/reviewer attempts штатным
recorder `--stage verification-prepared --status active --verification-json`
с текущим полным verification и delivery_scope. Event и квитанция фиксируют
нормализованный scope/task_scope SHA. Scoped update сохраняет историю, требует
нового result_hash и новых выбранных QA/reviewer.
Контракт
применяется claims/acceptance, readiness/results, obligations, critical/risk,
recorders, finalize, report и delivery. Требования успешности gates ниже относятся
к локальной области при таком opt-in; external gaps и outcomes остаются открытыми.
Без scope всё строго. Auth/config/payload/code defects, авторство, выбранные
QA/reviewer, history и обязательный local proof продолжают блокировать приёмку.
Scope edit сбрасывает stale QA/reviewer. В итогах отдельно назовите local result,
исходную внешнюю цель и follow-up; deploy-only задача без local deliverable
не получает delivery. Чтение/finalize/delivery не требует сети к внешнему сервису.

Prepare без destination использует journal-anchored retained default:
canonical run → `.agent-work/workspaces/<run>-<run_uuid>`,
noncanonical → `<run_dir>/workspace-<run_uuid>`. Повтор использует existing bundle;
explicit/temp destination совместим, прежняя binding не перемещается.
No-follow/identity checks сохраняются. Missing baseline/candidate bytes дают
точный отказ; manifests не восстанавливают результат, source его не заменяет.

1. Enter this route only for an explicit instruction to use `Agent Flow`, `AgentFlow`, `$agent-flow`, `agent-flow`, or `агент-флоу` to perform the task; apply the canonical Invocation Model.
2. Strip the invocation marker and read local project rules.
3. Detect the project repo and apply global project memory rules from the current user's Codex instructions, usually `~/.codex/AGENTS.md`.
4. Read relevant existing memory and maintain it when sustained work, handoff, or durable findings need it. Create `lessons.md` for an actual lesson; keep the mandatory run evidence for changes.
5. Read `implementation-notes.md` when global criteria make it relevant.
6. Read named PRD/spec/design docs and environment docs needed for the task. List all related delivery documents in existing scope per repository, including the source implementation plan, for later status and acceptance checks.
7. Check old task facts and later evidence across named repositories before dependency classification; narrowly correct confirmed completed records under `project-memory-and-env.md` without repeat acceptance of old implementation. Read another chat only from a link explicitly supplied by the user as a source, never merely from a file or memory link.
8. Run the dependency gate for new feature work, product edits, cross-file implementation, or delegation.
9. If this is a traceable implementation run inside a git repo, capture `git status --short` before edits and record the initial worktree snapshot.
10. Classify request type.
11. Choose the internal flow.
12. Choose the smallest execution budget: `light`, `standard`, or `release`.
13. If the request creates or materially revises an implementation plan, read `references/implementation-plan-authoring.md` and use it as the authoring contract.
14. Update `.agent-work/tasks/todo.md` for repo tasks before product changes.
15. If the budget/task shape justifies subagents or a required subagent gate applies, discover `spawn_agent`.
16. State the selected skill/tool briefly when user-facing rules require it.

## Invocation Semantics

Unmarked request:

- Do not use Agent Flow.
- Do not spawn subagents.
- Do not create Agent Flow trace artifacts.
- Product edits are handled by the main agent under normal non-Agent-Flow rules.
- If the task is too broad, risky, or needs independent verification, tell the user to invoke Agent Flow explicitly.

Agent Flow-invoked request:

- Strip the invocation marker and route the remaining task through this skill.
- Do not run the `brainstorming` skill as a pre-step. Handle uncertainty through Agent Flow intake, route, planning, checks, and verification.
- Authorizes the orchestrator to choose solo or subagent execution according to budget.
- Keeps `light` solo for implementation ownership.
- Allows `standard` and `release` subagents when they add independent evidence, parallelism, or review value.
- Для положительного итога после изменения файлов требует отдельные `qa-verifier` и `reviewer` с доказательствами текущей редакции.

## Subagent Discovery

Only after the budget/task shape justifies subagents or a required subagent gate applies:

1. Check active tools for `spawn_agent` or equivalent.
2. If not present and `tool_search` is available, search `spawn_agent subagent multi-agent tools`.
3. If a subagent tool becomes available, use it.
4. If not, continue only when no required subagent gate applies. If Flow Integrity Gate (входит в: Delegation Trace Gate, Handoff State Gate, Mandatory Independent QA Review Gate, Continuation Gate) mandatory independent QA review applies, record a launch/runtime blocker and close `blocked`.

## Practical Defaults

- Prefer `light` budget unless there is a concrete reason to escalate.
- Prefer solo implementation for `light`.
- In `standard`, use subagents only for narrow independent lanes, QA, review, or research evidence.
- In `release`, consider architect, QA, reviewer, and worker lanes by default; skip only with a concrete reason.
- Flow Integrity Gate mandatory independent QA review требует для положительной приёмки изменения файлов отдельные `qa-verifier` и `reviewer`. Контракт `delegation-summary.json.verification` одинаков для compact/full/auto; role-lane его не заменяет. При недоступных доказательствах запишите `verification.blocker` и завершите `blocked` или `fail`. Подробности: `references/traceable-runs.md`.
- Use workflow patterns as internal recipes only when they strengthen routing or verification.
- Treat unclear Agent Flow scope as intake and routing work, not as a reason to launch brainstorming.
- Stop only the part with a confirmed active conflict or a specific required result still unproven after available checks. Stale status and unavailable session listing alone are not blockers; continue independent scope.
- Use Evidence Records from `implementation-notes.md` as local learning input when a similar problem and approach appear again.
- Local Best Practice auto gate may apply an analyzer-confirmed active practice automatically only when context match is clear, `Do not reuse when` does not match, helpful evidence outweighs harmful evidence, the action is not an external write, and fresh verification evidence exists.
- For implementation-plan authoring, apply `references/implementation-plan-authoring.md`: gather project context, identify stack and affected technical areas, select the minimal relevant skills available to the main agent, read selected skills completely, apply their conclusions to impact/risk/check/stage analysis, record used skills and gaps, start from one stage, split only for real dependencies or independently verifiable boundaries, keep tests inside stages, avoid per-stage rollback, and require independent Devil's Advocate `passed` on the current revision before finalizing.
- If no relevant specialist skill is available, do not install it automatically. Use project sources and role instructions when sufficient; route architecture gaps to Architect, route missing external or current-source gaps to Researcher under existing Researcher instructions, and keep the plan as draft when the gap prevents reliable impact, risk, check, dependency, or stage-boundary assessment.
- Prefer narrow delegation over broad role chains.
- Use the Architecture Gate (входит в: Architecture Contract Gate, Architecture Design Mode, Architecture Capability Router, Architecture Context Propagation, Architecture Execution Control, Architecture Approval Gate) for release, for `standard` traceable runs with two or more worker lanes, and for architecture-sensitive work before QA or reviewer verdict.
- For Architecture Gate work, read `references/architecture-matrix.md` and select Architecture Matrix facets from local source evidence before the architect writes the contract.
- In lane-map schema v2, record selected facets in `architecture_context` with `product_context`, `application_surface`, `architecture_pattern`, `stack_runtime`, `risk_gates`, and `verification_gates`.
- Apply Architecture Gate capability routing after Matrix selection: record `architecture_capabilities`, select capability ids from `registries/architecture-capabilities.json`, and use Soft Skill Binding so `recommended_skills` inform preparation but do not block `validate-run.py`.
- Enforce Architecture Gate design mode before implementation when `architecture_contract_required=true`: require `architecture_design_brief`, an Architecture Design Brief, `Selected Matrix Facets`, and `Status: approved` before worker lanes and before `ship` or `pass-with-risks`.
- `init-run.py --architecture-gate` создаёт planned lanes и архитектурный контекст без вымышленных approvals, command results и proof rows. Затем роли передают смысл через `journal.py record`; формат запроса берут из `journal.py contract`. Отсутствующие результаты закрывают старт worker и успешный final.
- When the Architecture Gate applies, enforce Architecture Gate execution control: workers record `Architecture Compliance` and `Engineering Simplicity`, fix now if fixable before QA/reviewer, any architecture or simplicity drift routes to architect re-check, QA records `Architecture Invariants`, and reviewer records `Architecture Matrix Mismatches` plus `Contract Drift` covering Engineering Simplicity.
- Simplicity Gate (входит в: Engineering Simplicity Gate, Simplicity Scope Coverage) is recorded in `architecture_compliance.engineering_simplicity` with `no-extra-work`, `stdlib-native-first`, `existing-helper-first`, `dependency-justified`, `abstraction-justified`, `smallest-working-diff`, and `tests-fit-risk`. Simplicity Gate is not a reporting gate: fixable overengineering, duplicated helper, unnecessary abstraction, dependency/stack drift, or wider-than-needed implementation must be fixed or routed as drift; reporting-only closure is invalid.
- Enforce Simplicity Gate scope coverage by recording `engineering_simplicity_scope.primary_surfaces` for core task surfaces, `secondary_surfaces` for peripheral evidence, and worker `scope_coverage`; primary scope must be audited before peripheral-only closure can pass QA, reviewer, or final.
- Enforce Lane Boundary Gate (входит в: Lane Boundary Evidence Gate) for schema v2 positive architecture-gated worker runs: workers record `boundary.allowed_paths`, optional `boundary.forbidden_paths`, and `changed_paths_artifact`; the worker conclusion call with `--authored-path` writes `checks/lane-boundary-<lane-id>.json` atomically; QA `Architecture Invariants`, reviewer `Contract Drift`, and final `Boundary Evidence` mention every worker lane id. This gate checks explicit product-code path boundaries through `fnmatch.fnmatchcase`; it is not a new Matrix facet, role, lane type, schema v3, or worktree-isolation mode.
- When `scripts/codegraph.py` is available, `boundary` and `context` may provide graph-backed affected surface evidence for Boundary Evidence and implementation planning. CodeGraph output supports the gate; it does not replace changed-path evidence, source reads, reviewer judgment, or release/security conclusions.
- Enforce Architecture Gate context propagation: worker `architecture_compliance.matrix_facets` covers selected worker-owned facets, QA covers selected `risk_gates` and `verification_gates`, and reviewer covers the full selected `architecture_context` plus selected `architecture_capabilities`.
- Enforce Contract Evidence Gate (входит в: Claim Evidence Gate, Acceptance Criteria Traceability Gate, Surface Evidence Gate, Contract Negative Fixture Gate) for positive architecture-gated runs: Architecture Contract `QA Gates` and `Reviewer Checklist` list `Acceptance Criteria` ids, `acceptance-traceability.json` records every required id as `supported`, and every referenced evidence marker exists.
- Enforce Contract Evidence Gate inside `acceptance-traceability.json`: every acceptance record has `surface_expectations`, and every `evidence` or `negative_fixture_evidence` record names matching `surface`, `polarity`, and `proof_kind`; storage/internal evidence cannot satisfy API, UI, logs, history, provider metadata, or external-provider acceptance unless the target surface matches.
- Enforce Contract Evidence Gate for contract-like acceptance records: any `gate`, `cli`, `query`, `storage`, `config`, or `parser` acceptance item has `negative_fixture_evidence` with marker-backed negative or drift fixture evidence before `ship` or `pass-with-risks`, and `negative_fixture_evidence` cannot use `polarity=positive`.
- Enforce Verification Readiness Gate before workers through the typed writer: cover selected `risk_gates` and `verification_gates`, use documented safe commands and actual user authorization, record native execution evidence, stop immediately as `paused-blocked` with final `blocked` when the user declines, and require post-worker QA `Verification Gate Results`.
- `resume_phrase` — закрытое константное поле артефакта со значением «Готово», а не пароль. Новое `readiness-approval` связывает решение с настоящим root user event и точной цитатой. Прежнее применимое разрешение действует без нового сообщения, точной фразы или NLP-проверки.
- Enforce Flow Integrity Gate continuation for resumed runs: keep the original `blocked-checkpoint` in `timeline.jsonl`, write `continuation-summary.json`, preserve the checkpoint snapshot, distinguish `historical_worker_lanes` from `new_worker_lanes`, require `revalidated_lanes`, and do not start new worker work before ready Verification Readiness. Final must include `Continuation Summary`; QA writes `Continuation Revalidation`; reviewer writes `Continuation Review`.
- Enforce Harness Evaluation Loop when a learning trigger exists: write `harness-evaluation.json`, record `learning_triggers`, findings, Evidence Records proposals, and source evidence, add final `Harness Evaluation`, and route reviewer `Harness Evaluation Review` for positive lane-map runs. Proposals must stay `proposed`, target only `Evidence Records`, and set `requires_human_approval=false`; project-trace learning may promote only into the current project's Project Memory. Promotion may add ACE-inspired analyzer metadata, but it must not add an ACE runtime dependency, external MCP server, vector store, or separate skillbook.
- Enforce Risk Lifecycle Gate (входит в: Mitigation Gate, Resolution Gate, Blocked Resolution Gate) mitigation step before `pass-with-risks`: write `risk-mitigations.json`, mark every risk as `identified`, include `problem`, `impact`, `affected_scope`, evidence, and `next_gate=resolution`, record the ids in `Risk Mitigations`, then route reviewer `Risk Mitigation Review`.
- Enforce Risk Lifecycle Gate resolution step after the mitigation step before `pass-with-risks`: write `risk-resolutions.json`, create one record per identified risk, include `resolution_type`, concrete `resolution`, evidence, `verification`, `verified_by`, `reviewed_by`, record the ids in `Risk Resolutions`, route QA `Risk Resolution Verification`, and route reviewer `Risk Resolution Review`; only `fixed`, `mitigated`, or `contained` may close `pass-with-risks`, while `unresolved` is only valid for `blocked` or `fail`.
- Enforce the blocked-resolution step of Risk Lifecycle Gate inside the resolution step: blocked attempts require `blocked_lesson`, `rollback`, `forbidden_repeat`, and a Blocked Recovery Path; attempt 1 blocked routes to Senior QA `Senior QA Test Design Review` and architect `Resolution Architect Review` before attempt 2; attempt 2 blocked routes to `Supervising Architect Review` before attempt 3; a third blocked attempt ends as `blocked` or `fail`.
- Enforce Flow Integrity Gate delegation trace for every positive traceable run, including compact without lane-map: keep `delegation-summary.json`, final `Delegation Trace`, `Subagents Used`, `Role Lanes Used`, and `Subagent Trace Evidence` synchronized with actual trace evidence.
- Flow Integrity Gate handoff state безусловен для durable lane-map runs: writer вычисляет `handoff_state` при каждом событии назначения, включая lane declarations и resolve-obligation; ручной синхронизации статусов нет. Terminal lane status должен соответствовать вычисленному состоянию, а batch items должны завершиться до batch acceptance.
- For lane-map schema v2, set `budget`, `architecture_contract_required`, `architecture_contract_independent`, `architecture_context`, and `architecture_capabilities` explicitly.
- Send rejected, regressed, or uncertain architecture attempts through the Architecture Gate approval before retrying implementation.
- Treat regression demotion as immediate: a reused approach that regresses is no longer auto-applicable until reviewed.
- Model/reasoning upgrade is not the default fix; improve context, architecture analysis, evidence, or verification before raising reasoning within the same model.
- For code review touching architecture, public contracts, APIs, data flow, security, migrations, or multiple subsystems, require an architect-owned review contract before reviewer verdict.
- Prefer existing project patterns over new abstractions.
- Prefer direct verification evidence over narrative.
- For loops and tournaments, define budget caps, stop conditions, and failure handoff before starting.
- Quarantine roles that read untrusted content; sanitized findings may feed privileged actions.
- Before browser proof, probe the selected browser-control surface and clear only safe browser/MCP/test-runner conflicts. Do not touch project infra while fixing browser tooling.
- For UI proof, exercise the claimed user workflow through the UI. API calls can set up or inspect state, but cannot replace the UI action being claimed.
- For visual UI proof, make the screenshot prove the exact claim. Scroll or capture the element so the claimed heading/status/value is visible, and list the visible target evidence in `checks/browser-proof.md`.

## Orchestrator Edit Boundary

Apply `SKILL.md` Action Authorization. When subagents are used, keep write ownership disjoint; the main agent owns integration and final verification.

## Stop Conditions

Stop or ask the user when:

- scope is contradictory;
- required credential or approval is missing;
- product direction needs user choice;
- after checking available sources, the dependency gate confirms a live conflict or cannot establish readiness of a specific required result; stop that dependent part, continue independent work;
- design approval is required before UI implementation;
- destructive action is requested ambiguously;
- an implementation-plan expertise gap prevents reliable impact, risk, check, dependency, or stage-boundary assessment;
- subagents are required by risk/budget, user request, or Flow Integrity Gate mandatory independent QA review, but no subagent tool is available;
- verification cannot be performed and no credible fallback exists.

## Final Integration

Before final answer:

1. Check latest user message.
2. Verify changed files and command outputs. Audit the per-repository document list established at intake, including the source implementation plan and separately tracked PRD/ADR/spec/research. Prepare final document and implementation statuses and checklists before result hashing and QA/reviewer; include their full bytes in `result_files` under `definition-of-done.md`. Keep the current task active until acceptance and final validation.
3. Confirm trace artifacts only if used.
4. Confirm Flow Integrity Gate delegation trace: no role-lane is described as sidecar/subagent unless spawned trace evidence and terminal handoff exist.
5. Confirm Flow Integrity Gate handoff state for durable lane-map runs: no required lane has missing state, accepted terminal state, missing `completed_at`, handoff mismatch, or invalid batch order.
6. Проверьте собственные `completion_turn_id` QA/reviewer, фактические роли и модели, текущий `reviewed_result_hash` и `qa_handoff_sha256` из итогового хода reviewer. Незавершённые или устаревшие доказательства блокируют положительный итог.
7. Confirm Contract Evidence Gate when architecture governance applies: `claim-evidence.json` exists, every `Claim Evidence` id has an `owner_lane`, `supported` status, evidence `markers`, and no unresolved `gap` before a positive final verdict.
8. Confirm Contract Evidence Gate when architecture governance applies: `acceptance-traceability.json` exists, every `Acceptance Criteria` id is `supported` with marker-backed evidence, every `surface_expectations` item has matching `surface`/`polarity`/`proof_kind` evidence, and every `gate`, `cli`, `query`, `storage`, `config`, or `parser` item has marker-backed `negative_fixture_evidence`.
9. If a traceable run has learning triggers, create `harness-evaluation.json` before final validation and keep it signal-only.
10. If `implementation-notes.md` gained Evidence Records, run or account for the evidence analyzer before relying on a learned practice.
11. If an implementation plan was created or materially revised, confirm the current revision has independent Devil's Advocate verdict `passed`; any review-driven edit makes the previous verdict stale.
12. If commit is authorized, compare staged diff with accepted delivery files and inspect document statuses in the index. Create scoped commits and verify SHA and document bytes with `git show` in every affected repository. If commit is not requested, do not make it a condition of completion. Do not include `.agent-work/` in the product commit unless the user explicitly requested it.
13. Проверьте связанные checklist и память во всех затронутых репозиториях; запишите commit evidence и недостающие доказательства. До финальной валидации сохраняйте текущую задачу `Status: in_progress`; недоступные обязательные доказательства означают `Status: blocked`. Ошибка commit не разрешает заявлять поставку; отказ validator после commit сохраняет SHA и незакрытые критерии. Правка статусов документов после QA требует принятия новой редакции.
14. If a trace timeline exists and a product commit was created, append an orchestrator `stage=commit` event with the commit hash.
15. Compare the initial worktree snapshot with current `git status --short`.
16. In `final.md`, record run-owned changes, product commit hash when applicable, pre-existing dirty files left untouched, and pre-existing dirty files touched by the run.
17. Call recorder `--render-final --run-dir <run-dir>` to prepare Worktree Hygiene and Delegation Trace from structured data; this does not close the journal. Seal the workspace before QA; the recorder derives `result_files` from the complete scanner delta. Do not maintain duplicate `run_changed_files`; old records remain readable. Repeating an unchanged accepted QA/reviewer completion preserves its original trace, timestamps and acceptance; it needs no new model turn. Resolve historical failures explicitly with accepted evidence for the same obligation; retain their original events and handoffs.
18. Выполните `journal.py finalize` с ожидаемыми UUID, редакцией, поколением, сохранённым operation ID и окончательным текстом отчёта. Не добавляйте `stage=final` через append или publish. После закрытия сохраните свежий полный аудит `validate-run.py --run-dir ...` без permissive flags и выполните штатную `delivery`. Квитанция finalize не заменяет свежую проверку продукта и его доказательств при delivery.
19. Только при успешной проверке продукта и delivery, полном checklist и отсутствии продуктовых блокеров выполните Task Status Completion Gate и установите `Status: done`. Остаточные риски и замечания аудита сохраняйте явно; правка итоговых доказательств после проверки требует свежей валидации.
20. Keep final answer short and evidence-based. Положительный финальный ответ следует после успешной валидации и обновления памяти.
