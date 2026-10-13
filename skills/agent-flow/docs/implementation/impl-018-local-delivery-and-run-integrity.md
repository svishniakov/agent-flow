# План 018. Локальная доставка и целостность запуска

Document status: done
Implementation status: done

Уточнение от 12 октября 2026 года: бессрочная зависимость от saved dev/ino,
дублирующий `run_changed_files`, обязательная предварительная readiness-церемония
и полный аудит как условие доставки заменены [контрактом приёмки продукта](../../references/traceable-runs.md#приёмка-продукта-и-аудит-журнала).
Новые seals используют hash v5; старые v1–4 воспроизводятся без миграции.
Ниже сохранена историческая постановка. Обязательные локальные proof, scope,
авторские границы и независимые QA/reviewer остаются условиями доставки.

## Цель и область

Agent Flow должен принимать и выдавать проверенный локальный результат при недоступной внешней операционной проверке. Непроверенные GitHub CI, deploy или consumer workflow остаются открытыми. Известный дефект кода, неполный результат, повреждённое доказательство или отсутствие независимых QA/reviewer блокируют локальную приёмку.

Реализация охватывает четыре проблемы: область локальной приёмки, принадлежность итоговых изменений авторам, проверенные замены назначений при `blocked`/`fail` и устойчивое расположение workspace по умолчанию. Поручение пользователя от 2026-10-12 разрешает реализацию, проверки и штатную локальную delivery в новом запуске. Commit, push и deploy запрещены. Прежний закрытый журнал подготовки служит только контекстом; его QA/reviewer не принимает новый результат.

Не входят: изменения Polydesk, восстановление отсутствующих файлов из manifests, автоматическая миграция закрытых журналов, общий рефакторинг, новые режимы, роли, registry, policy DSL, scheduler, блокировки авторов, сборщик транскриптов или backup manager. Незакоммиченный принятый R3 сохраняется как исходная редакция.

## Исходные факты подготовки

При подготовке плана исследована рабочая редакция Python/SQLite/JSON-контрактов
и Markdown-инструкций. Ниже сохранена диагностика этой исходной редакции,
а не выводы проверки новой реализации. Номера строк относятся к исследованию;
при реализации искать указанные функции и проверять свежий diff.

- [validate-run.py](../../scripts/validate-run.py), `validate_acceptance_traceability_records`, строки 2778–2925, и `validate_claim_evidence_records`, 2929–3094: положительный финал требует `supported` у каждой записи, включая записи вне списка required IDs. Это не специальная зависимость от GitHub.
- [verification_evidence.py](../../scripts/verification_evidence.py), `evaluate_obligations`, строки 286–379: проверяется принятие всех текущих обязательств. [validate-run.py](../../scripts/validate-run.py), `validate_verification_readiness`, 3876–4130, и проверка critical lanes, 6254–6270: внешние ограничения могут блокировать общий исход и через другие потребители.
- [record-lane-boundary.py](../../scripts/record-lane-boundary.py), `capture_boundary`, строки 56–69: каждой lane записывается полный `sealed.changed_paths`. `validate_lane_boundary_artifact` и `validate_lane_boundary` в validator, строки 4483–4620, сверяют этот полный список с разрешениями одного автора.
- `validate_delegation_summary` в validator, строки 2152–2194, вычисляет проверенные `resolved` только для положительного финала, но всегда исключает `replaced` из ожидаемых текущих lanes. Историческая запись становится лишней при отрицательном финале.
- [task-workspace.py](../../scripts/task-workspace.py), строка 18: `prepare` требует `--destination`. [task_workspace.py](../../scripts/task_workspace.py), `delivery`, строки 914–967, проверяет принятие и сохранённый candidate; [journal_io.py](../../scripts/journal_io.py), строки 1039–1040, требует закрытую generation для delivery.

При подготовке диагностический Bun run revision 118 содержал проверенную замену
`verification-readiness-1 → verification-readiness-2`, но получал ошибку coverage
при `blocked`. Контролируемый вызов boundary-функций по сохранённому seal дал
12 и 28 ошибок двух авторских lanes. На момент исследования baseline, working
и candidate временного bundle отсутствовали; причина неизвестна. Это воспроизведение
семантики validator, а не проверка потерянных продуктовых байтов. Доказательства
и SHA исходников сохранены в журнале подготовки: `checks/research.md`,
`checks/research-reproduction.json`.

## Применённые skills

- Agent Flow: [implementation-plan-authoring](../../references/implementation-plan-authoring.md) определяет поля этапа, раздельные статусы документа/реализации и независимую проверку полных итоговых байтов. [Traceable Runs](../../references/traceable-runs.md) и [Definition of Done](../../references/definition-of-done.md) сохраняют sealed candidate, историю и независимость QA/reviewer.
- `architecture`, включая FAILURE и PURITY: ожидаемая недоступность сервиса отделяется от нарушения инварианта; правила выбора области задаются функциями над проверенными данными, I/O остаётся в существующих recorder/validator. Это обосновывает общий разбор одного контракта для нескольких потребителей, но не отдельную платформу или новый слой модулей.
- `humanizer-ru`: русский текст проверяется на точность источников, канцелярит и лишнее форматирование перед сдачей.

Специализация `qa-requirement-reviewer` недоступна в проверенных локальных каталогах. Для этого плана достаточно полного задания `qa.requirements` канонической роли `qa-verifier`, исходников и отрицательных fixtures. Skill не устанавливается и не считается применённым. Архитектурные Matrix/capability IDs выбираются при маршрутизации будущего исполнения по действующему контракту, без изменения router или registry этим планом.

## Выбранные контракты

### Область локального результата

Добавить необязательный версионированный объект в существующий `delegation-summary.json.verification`, передаваемый существующим `--verification-json`:

```json
{
  "delivery_scope": {
    "version": 1,
    "completion_target": "local-deliverable",
    "external_followup": {
      "acceptance_ids": ["external-consumer-workflow"],
      "claim_ids": ["external-consumer-accepted"],
      "lane_ids": ["consumer-verification"],
      "gate_refs": [{"axis": "verification_gates", "facet": "selected-external-gate"}],
      "risk_ids": ["external-environment-unavailable"]
    }
  }
}
```

Имена в примере заменяются реальными IDs текущего запуска. `gate_refs` используют существующую пару `axis/facet`, а не новый namespace gate IDs. Все пять массивов обязательны, могут быть пустыми, содержат уникальные ссылки на объявленные записи. Неизвестные поля, версии и ссылки отклоняются. Отсутствие объекта сохраняет прежнюю строгую приёмку; всё не перечисленное в `external_followup` тоже остаётся локальным обязательством.

Смысл разделения фиксируется при intake/планировании в существующем `task_scope` с проверяемой SHA-ссылкой. Конкретные IDs закрепляются до соответствующих проверок. Новый hash version 4 включает нормализованный объект `delivery_scope` и привязки boundary v2 вместе с прежними inputs; он применяется при наличии нового scope или деклараций. Старые hash версии 1–3 без этих контрактов вычисляются по прежним правилам. Изменение классификации, источника scope или состава результата требует нового хеша и новой независимой приёмки. Recorder сбрасывает выбранные QA/reviewer так же, как при другом изменении результата.

QA и reviewer проверяют по исходному заданию и причинным доказательствам, что external follow-up касается операционного окружения. Broken payload, auth, config или логика продукта, выявленные внешним тестом, остаются локальным code blocker даже после отключения сервиса. Машина проверяет структуру и закреплённые ссылки; она не доказывает смысл причины по одному ID или хешу.

Нельзя относить к follow-up авторские implementation/integration lanes, выбранные обязательные QA/reviewer, целостность manifests/истории/ссылок, boundary-проверки и известные дефекты продукта. Нельзя автоматически исключать запись по словам GitHub, external-provider, timeout или по недоступности сети. Mocks остаются доказательством своего локального сценария и не закрывают реальный provider workflow.

Все потребители используют один проверенный разбор scope в существующем `verification_evidence.py`:

1. Recorder и `result_hash` связывают scope с текущим результатом и сбрасывают устаревшую приёмку.
2. Claims/acceptance сохраняют полную проверку формы, IDs, subjects, source и каждого предъявленного evidence. Только объявленные external gaps могут остаться `gap` при локальном положительном финале; `supported` по-прежнему требует настоящие markers, поверхность и negative fixtures.
3. Readiness и QA verification-results требуют готовности/успеха всех локальных gates. Агрегированный статус при opted-in scope описывает локальную готовность; строки внешних gates и прежние попытки сохраняют фактические статусы, blockers и evidence. Локальная неготовность блокирует старт worker и приёмку.
4. Obligations, critical lanes и risk mitigation/resolution не требуют операционного успеха перечисленного follow-up для локального финала. Исходные исходы и незавершённые обязательства остаются в coverage и отчёте; они не становятся `resolved`, `exempt`, `pass` или фиктивно устранённым риском. Исторические resolution, локальные риски и code findings проверяются по строгим правилам.
5. Compact/full/auto, `finalize`, `render-final` и `delivery` применяют одинаковую область. Final и delivery явно называют принятый локальный результат, отдельно перечисляют непроверенный follow-up и исходную внешнюю цель. Если пользователь требовал deploy/e2e, эта цель остаётся незавершённой; локальный `ship` не означает выполнение всего внешнего задания. Внешняя задача без самостоятельного локального результата не получает локальную delivery.

Чтение журнала, finalize и delivery не выполняют сетевых запросов к GitHub/consumer. Они сохраняют проверки доступных локальных исходных сессий, handoff, hashes и retained bytes. Недоступность обязательного локального proof остаётся ошибкой.

### Полная дельта и декларации авторов

Добавить version 2 существующего `checks/lane-boundary-<lane>.json`. `changed_paths` остаётся полным scanner delta, привязанным к workspace/candidate IDs и digest. Новое `authored_paths` содержит явный перечень итоговых путей автора в той же системе координат; recorder принимает его из опубликованного handoff/declaration, а не вычисляет пересечением с `allowed_paths`.

Декларация включает `lane_id`, ссылку `{path, sha256}` на собственный handoff и ссылки на сохранённые доказательства правок автора. Lane связывается с существующей идентичностью назначения и trace. QA сверяет декларацию с исходными edit evidence и candidate; reviewer проверяет эту сверку. Хеш обеспечивает целостность декларации, а не причинное доказательство авторства.

Порядок: остановить записи → seal продукта → опубликовать декларации в журнале → вычислить принимаемый result hash → QA → reviewer. Декларации связываются с candidate IDs/digests, но не содержат итоговый result hash. Новый hash version 4 включает digests деклараций, когда применяется boundary v2 или delivery_scope; принятие старых hash v1–3 не меняется. Это исключает цикл вычисления и изменение декларации после приёмки без нового хеша. Journal artifacts не записываются в sealed product candidate.

Разрешения и forbidden patterns проверяются по `authored_paths` каждого автора. Forbidden имеет приоритет. Каждый заявленный путь должен присутствовать в полном delta. На уровне запуска объединение деклараций всех авторов должно точно покрывать этот delta: неизвестный, потерянный или никому не приписанный итоговый путь блокирует приёмку. Root-owned изменения тоже требуют явной декларации, если root является автором.

Совместный путь допустим только при явном раскрытии соавторства в handoff каждого участника и доступных edit evidence для каждого. Доказательство вклада может показывать промежуточные bytes: каждый участник не обязан независимо создавать итоговый файл. Каждый соавтор должен иметь разрешение на этот путь и не иметь запрещающего pattern. Необъяснённые пересечения отклоняются. Проверяется итоговый delta; аудит всех промежуточных и отменённых правок не добавляется.

Version 1 сохраняется только для чтения прежних доказательств и точного технического replay уже принятой пары; он не подходит для новой приёмки авторства. Когда действующий boundary gate требует авторское доказательство нового registered результата, декларация version 2 обязательна для одного или нескольких авторов. Старый полный список нельзя автоматически разрезать по permissions или выдавать за доказанное авторство. Для единственного автора достаточно явного подтверждения полного итогового delta в существующем handoff с теми же ссылками на evidence; отдельного workflow или дублирования каждого пути вручную не требуется. Это сохраняет единый контракт без неоднозначной fallback-ветки. Изменение candidate делает старую boundary-привязку неактуальной.

### История замен при отрицательном финале

В `evaluate_obligations` отделить проверку исторического resolution от требования принятия всех текущих обязанностей. Использовать ту же логику разрешения цепочек и ссылок; не копировать её в `validate_delegation_summary` и не отключать общий validator.

Для `blocked`/`fail` вычислять `resolved` только из корректных `resolved`/`exempt` записей. Проверять original assignment/outcome, immutable archive при наличии, obligation ID, reason, hash evidence, отсутствие циклов и существование принятого replacement с совпадающей обязанностью. Непринятые текущие обязанности разрешены как причина отрицательного исхода; они не попадают в `resolved`. Replacement для исторического resolution всё равно должен быть принят, даже если финал отрицательный.

Coverage сравнивает текущие lanes после исключения только проверенных исторических resolution; исторические summary-записи и отрицательные events сохраняются. Голый `status: replaced`, отсутствующий replacement, неверный хеш или другая obligation не дают исключения. Положительный финал без scoped follow-up сохраняет полный прежний набор требований. Отрицательный финал не разрешает delivery и не создаёт принятую пару QA/reviewer.

### Retained workspace по умолчанию

Сделать CLI `--destination` необязательным. Для нового canonical run `<owner>/.agent-work/runs/<run>` default: `<owner>/.agent-work/workspaces/<run>-<run_uuid>`. Для noncanonical run без такой формы default: `<run_dir>/workspace-<run_uuid>`, точный собственный subroot. Якорь определяется расположением журнала, а не общим родителем `--source`, первым репозиторием или эвристическим `snapshot.source_root`.

При повторе без destination сначала использовать уже registered `bundle`. Явный destination сохраняется, включая временные каталоги fixtures; новое значение не перемещает существующую привязку. Сохранить payload/replay совместимость прежних явных вызовов. Создание родителей default использует существующие owned/no-follow directory primitives и проверку идентичности; не вводить небезопасный `mkdir` fallback. Exact run/bundle exclusions предотвращают рекурсивное копирование без исключения чужих продуктовых файлов.

Baseline, working и candidate сохраняются до явного удаления владельцем. Инструкции называют default retained location и ответственность за явный путь; автоматического GC или backup нет. Default внутри временного fixture остаётся временным: инструмент не обещает абсолютную защиту от удаления родительского каталога.

При отсутствующих baseline/candidate bytes `inspect`, validate и delivery сообщают конкретный отсутствующий root и факт непроверенной поставки. Они не обещают восстановление по manifest, не подменяют candidate живым source и не создают новый bundle под прежней identity. Уже опубликованные manifests и история остаются читаемыми для диагностики.

## Один этап реализации

### Цель, результат и границы

Внести согласованное исправление существующей цепочки приёмки/выдачи и инструкций. Результат: новый локальный scope проходит те же независимые gates, общий delta сохраняет полноту без приписывания чужих файлов каждому автору, отрицательный финал сохраняет проверенную историю замен, CLI выбирает retained bundle по умолчанию.

Начали с одного этапа и сохраняем его: общей миграции, обязательного внешнего развёртывания или отдельно поставляемого foundation нет. Четыре ошибки проверяются своими fixtures внутри одной принимаемой редакции. Дробление на этапы по файлам или ролям не уменьшит риск рассогласования потребителей scope. Тестового этапа и rollback для каждой группы нет.

### Зависимости и порядок

1. Перед исполнением проверить текущие незакоммиченные изменения, документальную цепочку и исходные функции; получить `qa.requirements` на этот план. При архитектурном gate подготовить предусмотренный им контракт до worker, без нового ADR по умолчанию.
2. Добавить проверенный разбор delivery_scope и его hash binding. Затем подключить все перечисленные потребители, сохранив legacy branch. Обновить recorder и отчёт вместе с validator, а не оставлять разные трактовки между ними.
3. Добавить явные boundary-декларации и run-level coverage. Отделить историческую проверку resolution от outcome policy. Внести default destination через существующий prepare для single/multi-root.
4. Добавить отрицательные и положительные fixtures, обновить инструкции и примеры. Проверить compact/full, историю и delivery, сохранить candidate; получить независимые QA и reviewer его полного хеша.

### Модули, контракты и владение

Один `python-worker` владеет изменениями в `scripts/verification_evidence.py`, `validate-run.py`, `record-agent-trace.py`, `record-lane-boundary.py`, `journal_lifecycle.py`, `task-workspace.py`, `task_workspace.py`; в `journal_structure.py`, `workspace_scope.py`, `journal_io.py` только точечные изменения проверки новых полей/default, если существующий caller этого требует. Нельзя переделывать SQLite storage, recovery protocol, descriptor scanner или session source.

Ему же принадлежат связанные fixtures: `test-verification-evidence.py`, `test-validate-run-lanes.py`, `test-record-lane-boundary.py`, `test-journal-lifecycle.py`, `test-task-workspace.py`, `test-procedure-completion.py`, нужные golden traces и их manifest. `check-all.py` меняется только для подключения отсутствующей релевантной проверки, без переписывания существующих guards.

Инструкции менять в `references/traceable-runs.md`, `delegation.md`, `definition-of-done.md`, `orchestrator.md`, `SKILL.md` и канонических `agents/orchestrator.md`, `qa-verifier.md`, `reviewer.md` ровно по новым контрактам и примерам; models/reasoning/frontmatter не менять. Если требуется отдельный documenter, его разрешённые пути ограничены этими документами; runtime/tests ему запрещены, worker не пишет его документы. Каждый автор получает свою декларацию. Полный запуск не должен проверяться по разрешениям одного исполнителя.

Все пути выше относительны `skills/agent-flow/`. Запрещены изменения глобальных config/ролей, registries, старых закрытых журналов, Polydesk и чужих dirty files. QA/reviewer читают один retained candidate, пишущие проверки выполняют на расходной копии. Изменение статусов этого плана при фактическом исполнении принимается вместе с остальными связанными документами.

### Критерии и проверки

Каждый критерий требует положительный сценарий и указанные отрицательные fixtures. Контрактные criteria записать в существующем `acceptance-traceability.json` с `contract_types`, `surface_expectations`, `surface`, `polarity`, `proof_kind` и `negative_fixture_evidence`; claims — в существующем `claim-evidence.json` с владельцем QA/reviewer и буквальными markers. Внутренний hash не заменяет CLI или history evidence.

| Acceptance / Claim Evidence ID | Поверхность и проверяемый результат |
|---|---|
| `local-delivery-scope` / `claim-local-delivery-scope` | `gate`, `cli`, `parser`, positive/negative. При отключённой сети к третьей стороне валидный local scope, локальные tests и независимая приёмка дают finalize/delivery; внешний workflow остаётся gap. Без scope, с локальным defect, неподтверждённым local ID, отсутствующим локальным proof или сломанным code/auth/config — отказ. Проверить claims, readiness, obligations, critical/risk, worker preflight, recorder, report и delivery; ни один consumer не даёт обхода. Scope edit после QA меняет hash и отвергает старую приёмку. Mocks не закрывают provider proof; внешний deploy-only запрос не считается выполненным. |
| `author-delta-integrity` / `claim-author-delta-integrity` | `gate`, `parser`, positive/negative. Два автора с disjoint разрешениями проходят на одном полном candidate. Незаявленный итоговый файл, authored path вне delta, forged/stale handoff, forbidden/out-of-scope путь или необъяснённое соавторство дают отказ. Раскрытый shared path требует доказательств и разрешений всех участников. Tracked/untracked/ignored, удаление, type/mode и одинаковые относительные имена разных product roots не теряются. Permissions сами не создают author declaration. |
| `negative-resolution-history` / `claim-negative-resolution-history` | `gate`, `parser`, `storage`, positive/negative. В compact и full корректная сохранённая замена допускает blocked/fail при текущих непринятых обязанностях; исходный отрицательный outcome и archive bytes не меняются. Missing/cyclic replacement, wrong obligation/hash, непринятый replacement, голый replaced и пропущенная текущая lane дают отказ. Положительный финал сохраняет обязательную локальную приёмку. Failed finalize не пишет успешный final event; blocked/fail не дают delivery. |
| `retained-workspace-default` / `claim-retained-workspace-default` | `cli`, `storage`, positive/negative. Prepare без destination выбирает заявленный default для canonical/noncanonical, single/multi-root; повтор использует прежний bundle, explicit destination и temp fixtures работают. Common source parent не меняет anchor. Exact exclusions предотвращают самокопирование, а чужие файлы остаются в manifest. Root swap/identity mismatch и отсутствующие baseline/candidate вызывают точный отказ без восстановления из живого source и без false delivery. Старые явные receipts, boundary v1, hash v1–3 и closed/recovered identities читаются без изменения. |

Литеральные markers берутся из фактических названий fixtures, CLI errors и проверенных журналов; заранее придумывать успешные evidence нельзя. Выполнить перечисленные профильные test scripts, `test-golden-traces.py`, затем штатный `check-all.py` и `git diff --check` на итоговой редакции. Python запускать с `PYTHONDONTWRITEBYTECODE=1`. Live GitHub и внешний consumer не нужны для принятия этих общих изменений Agent Flow; реальная доступность provider ими не доказывается.

### Риски и Devil’s Advocate

Главный риск — превратить классификацию external follow-up в обход code defects. QA проверяет источник требования и причины, reviewer пытается протащить реальный дефект через каждый потребитель. Scope без классификации по умолчанию строгий; обязательные verifier/identity/history не исключаются.

Другой риск — скрыть чужой файл фильтрацией delta либо принять декларацию за автоматическое доказательство причинности. QA сверяет edit evidence, union и соавторов; reviewer проверяет незаявленный итоговый путь и запрещённый contributor. Нет scheduling/locking решения и требований аудита промежуточных правок.

Для lifecycle reviewer проверяет, что разделили принятие исхода и валидность истории, а не отключили integrity для negative final. Для retention — что default не изменил bindings старых runs, не включил чужие excludes и не обещает восстановление отсутствующих bytes. Ошибка одной группы не закрывается успехом другой.

### Условие завершения

Все четыре criteria и claims подтверждены свежими matching evidence, отрицательные bypass fixtures отвергнуты, профильные проверки и общий набор завершены успешно либо конкретное ограничение оставляет соответствующий criterion открытым. Независимые QA и reviewer приняли один текущий полный candidate/result_hash, fresh validator прошёл без permissive flags, delivery выдал именно сохранённый результат. Внешние follow-up показаны отдельно и не объявлены выполненными.

## Совместимость и отклонённые альтернативы

Только `delivery_scope` применяется по явному opt-in. Boundary version 2 обязателен для новой приёмки авторства при действующем boundary gate; version 1 остаётся для legacy reading и точного replay прежней приёмки. Открытый run может принять scoped update только с сохранением прежней истории, новым hash и новой приёмкой; нельзя молча переклассифицировать завершённую проверку. Closed/recovered accepted identities автоматически не мигрируют. Старые вычисления hashes, explicit destination, readers и receipts сохраняются. Отсутствующие declarations нового результата требуют новых доказательств, а не правки старых records.

Отвергнуты blanket exemption всех external-provider checks и простой `gap → supported`: они скрывают реальные дефекты. Одного prose scope или только фильтра required acceptance IDs недостаточно: obligations, claims и readiness продолжают блокировать либо расходятся между consumers. Копировать весь исходный run в новую «локальную» задачу ради обхода blockers тоже не нужно: явный opt-in остаётся связанным с исходной областью и незавершёнными требованиями.

Отвергнуты фильтрация общего diff по permissions, расширение разрешений каждого автора на весь workspace и снятие boundary gate. Для negative final отвергнуты удаление исторических lanes и полный вызов строгого positive evaluator без разделения режимов. Для хранения отвергнуты запрет всех temp paths, автоматические backups/GC, перенос старых bundle и восстановление candidate по одному manifest.

В текущем запуске supplemental architecture review разрешил точечную адаптацию
WC04 в `test-journal-io.py`: setup, вызов, assertions и imports для direct boundary
v2 fixture. Это совместимость тестового caller с утверждённым контрактом;
исходные требования и границы SQLite storage/recovery не изменены.

До реализации получены свежие qa.requirements и approved архитектурный контракт
нового запуска `2026-10-12-impl-018-local-delivery-integrity`. Код, fixtures и
инструкции по четырём критериям реализованы и локально проверены. Профильные
проверки, golden traces и diff check прошли; результаты сохранены в checks запуска.
Полный `check-all` не объявлен успешным: baseline distribution fixture пытается
заменить прежнюю модель `gpt-6-astra`, хотя qa-verifier уже использует `gpt-6.1-sol`,
и затем ожидает отказ без фактического изменения файла. Это устаревший setup
за пределами плана, а не доказанный дефект detector; ограничение оценивают
независимые QA и reviewer.

`Implementation status: done` описывает завершённую реализацию candidate.
Приёмку и штатную delivery подтверждают только native receipts этого запуска
для одного текущего полного candidate/result_hash: независимые QA и reviewer,
finalize, свежий validate-run без permissive flags и delivery. До успешной записи
этих подтверждений candidate не объявляется принятым или доставленным.
Окончательный статус входит в принимаемые байты; исторические запуски
не переписываются.
