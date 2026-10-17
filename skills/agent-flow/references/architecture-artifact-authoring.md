# Architecture Artifact Authoring Automation

Перед записью получите `journal.py contract`. Содержание архитектурного результата
передавайте через `journal.py record --kind architecture-outcome`; `publish`
сохраняет только исходные доказательства и attachments. Читайте результат через
`journal.py read`. Порядок повторов: [Traceable Runs](traceable-runs.md#публикация-и-чтение-журнала).

Architecture Artifact Authoring Automation makes architecture artifacts agent-authored by default. The human does not fill the Architecture Design Brief, Architecture Contract, worker compliance, QA invariants, or reviewer sections by hand.

Во всех ownership пунктах ниже «fill» означает передачу содержательных данных
штатному semantic writer. Готовые служебные JSON и опубликованные handoffs
вручную не редактируйте.

## Init Run

Use `scripts/init-run.py --architecture-gate` only with `--with-lanes`, `--budget`, `--architecture-context-json`, `--architecture-capabilities`, and any worker lanes needed for the run.

Example:

```bash
python3 "$AF_PACKAGE/scripts/init-run.py" \
  --repo <repo> \
  --slug <slug> \
  --with-lanes \
  --architecture-gate \
  --budget standard \
  --architecture-context-json '<json>' \
  --architecture-capabilities backend-service-architecture,modular-monolith-architecture \
  --worker-lane worker-a:implementation:typescript-worker
```

Команда создаёт `lane-map.json` schema v2 с planned lanes и выбранным контекстом.
Handoffs и proof records появляются из содержательных записей writer по мере
выполнения работы; init не выдумывает проверки или approvals.

## Один архитектурный outcome

После prepare и initial change verification зарегистрируйте настоящий запуск
архитектора штатным `record-agent-trace.py`. Первый trace выводит фактический
mode и identity из исходной сессии; planned mode не переключайте вручную.

Архитектор возвращает один semantic request по контракту `architecture-outcome`:
`summary`, `status`, `lane_id`, явное `decision`, `sections` и применимые ссылки
на evidence, `claim_evidence_ids`, `acceptance_criteria_ids`. `summary` и текст
каждой section — строки. После завершения его собственного хода orchestrator
передаёт точный request writer:

```sh
python3 -B "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" record \
  --kind architecture-outcome --request-file "$AF_ARCHITECT_OUTCOME" \
  --operation-id "$AF_ARCHITECT_OPERATION_ID"
```

Writer сверяет request с реальным завершением назначенного архитектора, создаёт
один handoff и атомарно направляет `handoff` и `architecture_design_brief` этой
lane на него. Заголовки, hashes и служебные markers формирует Python. Старые
runs с двумя отдельными paths продолжают читаться.

Один результат сохраняет все 20 разделов прежних brief и contract:

| Brief | Contract |
| --- | --- |
| Problem Shape | Selected Architecture |
| Selected Matrix Facets | Rejected Alternatives |
| System Boundaries | Module Boundaries |
| Data And State Model | Data And State Flow |
| Public Interfaces | Public Contracts |
| Execution Plan | Worker Ownership |
| Risk Model | Forbidden Changes |
| Verification Strategy | QA Gates |
| Open Questions | Reviewer Checklist |
| Decision | Stop Conditions |

`decision` имеет только `approved`, `needs-revision` или `rejected`; допустимые
сочетания со статусом задаёт `constraints.architecture-outcome.decision_by_status`.
`status: pass` и само наличие `task_complete` не означают approval. Writer
выводит единственный управляющий `Status:` из `decision`; свободный narrative
цитируется. Выбранные facets/capabilities и Claim Evidence/Acceptance Criteria
markers код добавляет из проверенного контекста и переданных IDs. Содержательное
обоснование архитектор описывает в sections.

## Agent Placeholder

Исторические архитектурные шаблоны могут содержать маркер:

```md
TODO(agent):
```

Маркер обозначает незавершённое содержание. Перед положительным final ответственный
агент передаёт настоящий результат writer; опубликованные файлы вручную не правит.

## Runtime Rule

When `architecture_contract_required=true`, `validate-run.py` blocks `ship` and `pass-with-risks` if any architecture lane artifact still contains `TODO(agent):`.

The scan covers Architecture Design Brief, Architecture Contract, worker handoffs, QA handoff, reviewer handoff, and lane evidence files referenced from `lane-map.json`.

`blocked` and `fail` may keep placeholders so the run can honestly end when design or implementation did not reach an approved state.

## Role Ownership

- Orchestrator chooses `architecture_context`, `architecture_capabilities`, and Simplicity Gate (входит в: Engineering Simplicity Gate, Simplicity Scope Coverage) scope coverage through `engineering_simplicity_scope.primary_surfaces`, `secondary_surfaces`, evidence, and notes; it declares lanes with `--architecture-gate` and keeps dependent workers blocked until the applicable architecture decision and readiness facts are accepted.
- Architect supplies one source-bound `architecture-outcome` before workers start.
- QA reviews pinned requirements/plan before workers and supplies applicable readiness observations with native command evidence and scoped approvals. Python projects the existing Verification Readiness artifacts at worker start. Final product QA separately covers `Verification Gate Results`; approval alone never proves command execution. See [preliminary review](traceable-runs.md#предварительный-qa-требований-и-плана).
- Workers fill their own `Architecture Compliance`, `Engineering Simplicity`, and `Boundary Evidence` handoff sections, then update lane-map `architecture_compliance.engineering_simplicity`, worker `scope_coverage`, `boundary.allowed_paths`, `boundary.forbidden_paths`, and `changed_paths_artifact`. They fix now if fixable; Simplicity Gate is not a reporting gate. Primary surfaces must be audited before secondary surfaces can help close the gate. They pass `--authored-path` in the worker conclusion; the writer stores `checks/lane-boundary-<lane-id>.json` evidence for `changed_paths_artifact`.
- QA fills `Architecture Invariants` from selected `risk_gates`, `verification_gates`, contract QA gates, and Lane Boundary Gate (входит в: Lane Boundary Evidence Gate) worker lane ids, plus `Engineering Simplicity Scope` with every primary surface.
- QA and reviewer fill Contract Evidence Gate (входит в: Claim Evidence Gate, Acceptance Criteria Traceability Gate, Surface Evidence Gate, Contract Negative Fixture Gate) data in `claim-evidence.json`: each `Claim Evidence` id gets an `owner_lane`, `supported` or `gap`, subjects, evidence paths, and literal `markers`.
- QA and reviewer fill Contract Evidence Gate data in `acceptance-traceability.json`: each `Acceptance Criteria` id gets source, requirement, subjects, `supported` or `gap`, `surface_expectations`, evidence paths, literal `markers`, and matching `surface`/`polarity`/`proof_kind` evidence.
- Contract Evidence Gate data also lives in `acceptance-traceability.json`: any `gate`, `cli`, `query`, `storage`, `config`, or `parser` record gets `negative_fixture_evidence` with a negative or drift fixture path, literal marker, and no `polarity=positive`.
- Reviewer fills `Architecture Matrix Mismatches` and `Contract Drift` with `covers` listing every selected facet and capability; `Contract Drift` `covers` must include `engineering-simplicity`, `boundary-evidence` worker lane ids, every primary surface, every fixed worker lane id, and `rejected-peripheral-only-closure`.

No role should ask the human to write these sections manually.
