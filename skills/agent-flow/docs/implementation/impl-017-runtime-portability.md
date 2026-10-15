# План 017. Переносимость Agent Flow между средами выполнения

Document status: done
Implementation status: not_started

Дата: 3 октября 2026 года. Исходная редакция кода: `0698404`.

## 1. Результат и границы

Выделить общее ядро из проверенной реализации Codex, сохранить её поведение и строгость приёмки, затем подключить установленный локальный DeepSeek Harness (DSH) вторым адаптером. Переиспользовать существующие capture/freeze, journal, workspace и проверки результата. После extraction новая среда добавляется через адаптер, bindings и профили моделей. Названия инструментов, способы вызова и разбор нативных событий принадлежат адаптеру; обязанности ролей и решения приёмки остаются общими.

Один набор сценариев с одними ожидаемыми решениями проверяет Codex, DSH и каждый следующий зарегистрированный адаптер через его reader и общее ядро. Нативные fixtures различаются, policy tests и expected verdicts не копируются. Отсутствие fixtures или непроверенный зарегистрированный адаптер не допускают общего PASS.

После создания или актуализации контекста проекта Agent Flow предлагает команду по используемым ролям: `роль → профиль конкретной модели + reasoning + подключение/host`. Пользователь принимает предложение, переопределяет назначения и может изменить их позднее. Это конфигурация проекта; глобальные роли при пользовательском выборе не переписываются. Один проект допускает несколько execution targets.

Этот документ готовит будущую реализацию через Agent Flow. Подготовка плана не запускает адаптеры, не меняет настройки DSH и не означает успешный `validate-run.py`. Архитектурное направление согласовано пользователем; проверки реализации ещё предстоят. `Document status` относится к готовности текста, а `Implementation status` — к выполнению работ.

В объём входят контракты выполнения и доказательств, Codex-адаптер, профили моделей, проектные назначения, DSH-адаптер и их проверки. Не входят performance-бенчмарки, сравнительный рейтинг моделей, подключение GLM/Kimi или других названных для примера моделей, фиксированный frontend-маршрут, оркестрация всех desktop-приложений, новый daemon, UI automation как production transport, сервисы, контейнеры и DI-фреймворк. Возможности MCP или факт установки приложения сами по себе не подтверждают управляемое подключение.

## 2. Основания и текущее состояние

Основные подтверждённые факты включены ниже, поэтому локальная `.agent-work/` не нужна для понимания и исполнения плана в другой копии репозитория.

- Пилот на DSH `0.2.0-rc.2` показал три отдельные дочерние сессии author, QA и reviewer с общей файловой системой. QA и reviewer работали на той же модели, что и author; разнообразие моделей в этом цикле не проверялось.
- QA выполнил 18 тестов, reviewer отдельно прочитал код и отчёт QA и обнаружил пробел Unicode. Продолжение через `send_message` создало второй ход той же QA-сессии. Источник сообщения подтверждён журналом.
- Замена `lower()` на `casefold()` оставила тесты зелёными, но checker отклонил новый SHA-256, а QA вернул `rejected`. Восстановление байтов вернуло исходный хеш; последний ход QA остался `rejected`. Для новой приёмки требуется актуальный ход проверки.
- Два отдельных вызова `workflow.agent` подтвердили overrides `deepseek-account/deepseek-flash` и `deepseek-account/deepseek-v4-pro` по конфигурации запроса и `assistant/message.source`. Это проверка маршрутизации на коротком задании, без оценки качества разработки. Runtime model ID не удостоверяет скрытую версию весов провайдера.
- В проверенном workflow доступны provider/model, но нет явного reasoning-параметра; `high` был выбран самим Harness. Workflow-дети были one-shot, background-subagent — continuable. Сочетание выбора модели, явного reasoning и продолжения живым тестом ещё не подтверждено.
- Чтение установленного кода 3 октября показало opt-in `modelSelectionSettings`, параметры `provider`, `model`, `reasoning_effort` и передачу `agentOptions` в `startContinuable`. Текущая пилотная сессия эти параметры не открывает. Настройки не менялись; статическое чтение не доказывает работоспособность сочетания.
- DSH может дополнять вход служебным блоком; followup имеет источник `agent-message/relay`. У синхронного запуска session ID может потребовать сверки с `subagent/catalog`; у workflow различаются `runId`, `childId` и session ID. `agentPreset=standard` не доказывает роль Agent Flow.
- В предыдущей диагностике Codex входы parent→subagent, включая followup, могли оставаться `encrypted_content`, хотя прямые user→CLI-входы читались. Это историческое ограничение доступности доказательств, а не текущий PASS и не основание ослабить `strict_inputs`.

Provenance: локальные `report.ru.md` в редакции от 3 октября, `dsh-capability-review-2026-10-03.json` и `runtime-audit.json` из `.agent-work/diagnostics/dsh-portability-pilot-2026-10-02/`; `reuse-review.json` из диагностики плана от 3 октября и релевантные уроки проекта. Существенные выводы перенесены в этот документ. Пилотные проекции не заменяют reader production validator. Повторные проверки используют первичные журналы текущих назначений.

Текущая реализация состоит из Python-скриптов, Markdown-инструкций, JSON-конфигураций и журнала с проверкой хешей. Ниже — карта переиспользования. Изменяются параметры, ключи идентичности и точки выбора source; алгоритмы транзакций, хеширования и приёмки сохраняются.

| Существующий механизм | Что сохранить и что отделить |
| --- | --- |
| `CodexSessionSource`, `completed_turn`, `validate_behavioral_checks` в [verification_evidence.py](../../scripts/verification_evidence.py) | Native resolution/decoding и названия полей вынести в Codex adapter. Общими оставить проверку всех observed models, единственного собственного завершения, начала/конца, последнего хода, порядка inputs/outputs и выбранного `strict_inputs`. Для новых assignments ожидаемая конфигурация берётся из snapshot |
| `CapturedSource` и [ValidationInputs](../../scripts/validation_inputs.py) | Переиспользовать кеширование фактов и внешних входов с запретом новых зависимостей после freeze. Обобщить ключи session resolution, не создавать второй capture layer |
| `capture_validation`, `freeze_validation`, `finalize` в [journal_lifecycle.py](../../scripts/journal_lifecycle.py) | Сохранить порядок: чтение/проверка до write lock → freeze → проверка journal preconditions и повторная валидация захваченных данных под lock → commit. Receipt сохраняет `input_hashes`; это не вечный архив raw session bytes |
| Operation IDs, receipts, transactions, immutable completion в [journal_io.py](../../scripts/journal_io.py) и recovery | Сохранить идемпотентность операций журнала, конфликт payload, revision/generation guards и неизменность завершённого назначения. Обобщить identity/source refs. Receipt не гарантирует exactly-once внешнего native call |
| `prepare`, `seal`, `delivery`, `capture_metadata_scope` в [task_workspace.py](../../scripts/task_workspace.py) и CLI [task-workspace.py](../../scripts/task-workspace.py) | Сохранить workspace ownership, baseline/candidate seal, result hashes и revalidation до публикации. Заменить default Codex source выбором reader по binding; legacy proofs остаются читаемыми |
| `result_hash`, `accepted_record`, `completion_follows`, recorder и validator | Переиспользовать hashes результата/handoff, независимость QA/reviewer, актуальность и порядок приёмки. Нормализованные факты не содержат готовый verdict адаптера |
| `check_package` в [package_distribution.py](../../scripts/package_distribution.py), [check-installed-package.py](../../scripts/check-installed-package.py) | Сохранить общую integrity-проверку пакета; Codex role setup выделить в readiness конкретного адаптера. Для DSH не требовать `CODEX_HOME` и Codex TOML |
| `write_files`/`replace_file` в [sync-codex-agent-config.py](../../scripts/sync-codex-agent-config.py) | Переиспользовать подход guarded write и atomic replace при записи project team. Добавить expected revision/hash и проверку stale writer; atomic replace сам по себе не предотвращает потерю чужого обновления |

[agent_config.py](../../scripts/agent_config.py) содержит закрытые `ALLOWED_MODELS`, общий список reasoning и названия инструментов. [resolve-agent-config.py](../../scripts/resolve-agent-config.py) выдаёт настройки для `spawn_agent`; [sync-codex-agent-config.py](../../scripts/sync-codex-agent-config.py) генерирует TOML Codex из ролей. [model_eval_adapter.py](../../scripts/model_eval_adapter.py) уже выполняет роль Codex CLI backend оценки, но импортирует общие GPT-списки и фиксирует CLI `0.144.1`/provider `openai`. Его отделение не даёт разрешения запускать новые оценки или переносить benchmark-систему на DSH.

Действующая процедура [Required Intake](../../references/project-memory-and-env.md#required-intake) собирает достаточный контекст и пересобирает его при изменении запроса. [init-run.py](../../scripts/init-run.py) создаёт журнал; он не заменяет содержательную сборку контекста. Шаг предложения команды встраивается после этой сборки и до первого зависимого назначения.

Для плана применены:

- `ai-agents-architect`: явные результаты native calls, отсутствие слепого retry при неизвестном исходе и неподтверждённой отмены; единая проверка адаптеров. Новые agent loop, registry framework и численные лимиты из примеров skill не вводятся.
- `clean-architecture`: контракты определяет ядро; адаптер переводит операции и события. Это приводит к двум небольшим границам — execution и evidence reader — внутри существующего Python-пакета, без новой инфраструктуры.
- [Implementation Plan Authoring](../../references/implementation-plan-authoring.md): этапы разделены по проверяемому сохранению Codex, новому проектному выбору и второму runtime. Проверки входят в каждый этап.
- `humanizer-ru`: факт пилота, требование и неизвестное обозначаются отдельно; условия завершения сформулированы наблюдаемым результатом.

Доступных навыков и проектных источников достаточно для плана. Неизвестный production channel DSH и сочетание его capabilities остаются задачами проверки этапа 3. Для будущего Architecture Capability Router рекомендации `system-design-doc` и `test-scenarios` в текущем списке skills не найдены; установки не требуется. Architect и QA покрывают границы и отрицательные сценарии проектными контрактами. Это не считается проверкой работы DSH.

## 3. Архитектурное решение

### Границы модулей

Ядро хранит обязанности ролей, ограничения назначения, ownership, независимость QA/reviewer, порядок приёмки, handoff, result hash, актуальность последнего хода и DoD. Оно не знает имён `spawn_agent`, `subagent`, `workflow.agent`, схем нативных аргументов или формата `session.v4.jsonl.zstd`.

Execution adapter получает разрешённое назначение и описывает перевод операций запуска, ожидания, продолжения и отмены в доступные инструменты среды. Фактический native call выполняет оркестратор в host-сессии: в Codex через доступный subagent tool, в DSH через его нативные инструменты. Python готовит и проверяет пакет, разрешает конфигурацию и читает evidence; он не получает скрытой возможности вызывать инструменты чата. Unsupported-операция возвращает явный отказ. Достаточно декларативных host-инструкций и небольших функций преобразования пакета; daemon, MCP-server и универсальный scheduler не требуются.

Evidence reader читает первичные источники соответствующей среды и возвращает нормализованные факты с происхождением. Нативный decoder и отображение событий находятся рядом с адаптером. Из `completed_turn` выносится только native decoding: проверки всех наблюдаемых моделей, единственного собственного завершения, временного порядка и последнего незавершённого хода остаются общей политикой. Аналогично обобщаются native fields поведенческих проверок. Adapter не возвращает решение PASS и не подставляет requested model/reasoning/input вместо отсутствующего observed. JSON автора и успешный ответ native tool сами по себе не закрывают приёмку.

Предлагаемые новые модули: `scripts/runtime_contract.py`, `scripts/runtime_adapters/codex.py`, затем `scripts/runtime_adapters/dsh.py`. Небольшая composition root в CLI связывает явные встроенные адаптеры с контрактом. Run-артефакт не может подставить Python import, произвольный reader, команду или синтетический источник. Существующая инъекция тестового source остаётся внутренним test seam.

### Публичные контракты и данные

| Контракт | Обязательный смысл |
| --- | --- |
| `AssignmentSpec` | Стабильный ID назначения, role slug, версия/хеш инструкций роли и профиля, входные артефакты, ограничения, требуемые capabilities, конкретное подключение и host, выбранный reasoning. Разрешённые значения фиксируются до dispatch |
| `ExecutionRef` | Adapter ID, connection ID, host ID, непрозрачный native session ID, attempt и turn; ссылка на предыдущее назначение/ход при продолжении. Пространство идентичности включает target, поэтому одинаковые session ID разных сред не сталкиваются |
| `ExecutionEvidence` | Связь запуска с назначением и родителем, requested/effective inputs, модель/провайдер и reasoning запроса и наблюдаемые значения, начало/конец/состояние хода, вывод, source refs с хешами и указателями. Неизвестные поля остаются неизвестными |
| Capability result | Наблюдённые операции и сочетания для конкретных adapter, version, connection, host и session policy; источник проверки и область применимости. `model selection` и `continuation` по отдельности не означают поддержку их комбинации |
| Model profile | Стабильный ID и версия профиля конкретной модели, её инструкции, допустимые reasoning-варианты и default. Общие правила семейства можно переиспользовать; семейство не заменяет профиль модели |
| Connection binding | Соответствие profile ID реальным provider/model IDs и поддержанным параметрам данного target. Секреты не входят в профиль, назначение или evidence. Alias считается доказанным только при проверенной привязке |
| Project team | Назначения используемых ролей на profile ID, reasoning и connection/host, принятые переопределения и их revision. Сохранённое назначение не доказывает доступность подключения |

Для независимости используется каноническая identity сессии: доверенно разрешённое пространство runtime/host и native session ID. Turn, attempt и произвольный connection alias в неё не входят. Два aliases одной сессии должны давать одну identity; новый turn не превращает автора в независимого QA. Реально разные runtime namespaces не сталкиваются при одинаковой строке session ID. Adapter подтверждает namespace по source/binding, а не произвольной метке из run-data.

Политика профиля не вводит GPT-only enum и не требует копии профиля для каждого reasoning. Значения проверяются по пересечению профиля и конкретного binding; одинаковое слово `high` у разных моделей не приравнивает их возможности. Неявный runtime default допускается только как явно выбранный режим с наблюдаемым эффективным значением; он не выполняет требование явно управлять reasoning.

Для проекта предлагается `.agent-flow/team.json` с переносимыми назначениями без секретов. Локальные сведения об управляемых подключениях — `.agent-work/runtime-connections.json`; credentials остаются в уже используемом хранилище среды. На другом host недоступное connection ID требует явного сопоставления. Это продуктовая конфигурация будущей реализации; при написании плана файлы не создаются. Запись team использует expected revision/hash прочитанной редакции: проверка и atomic replace выполняются в одной защищённой операции записи, stale writer получает conflict без перезаписи файла. Подход берётся из существующих guarded-write механизмов, новый storage/lifecycle не нужен. Snapshot разрешённого назначения публикуется существующим journal API до dispatch и связывается с lane. Изменение team revision действует только на новые назначения. Текущий или завершённый assignment, его модель, target и уже принятые hashes не переписываются.

Инструкции профиля добавляются к существующему delegation packet и host export рядом с инструкциями роли; отдельный instruction engine не нужен. Обязательные правила роли и требования задачи имеют приоритет над советами профиля, конфликтующий профиль отвергается до dispatch. Snapshot связывает версии/хеши и точные отправляемые bytes; reader проверяет фактическую доставку роли и профиля по effective input либо наблюдаемой host-конфигурации. Наличие локального profile file не доказывает его доставку.

При retry модель и target сохраняются. Предусмотренное действующим маршрутом повышение reasoning внутри той же модели проверяется по профилю и оформляется новой попыткой с собственным snapshot; изменение проекта не меняет прошлую попытку. Недоступность выбранного target останавливает зависимое назначение. Автоматическая замена возможна лишь по заранее принятому правилу, которое также фиксируется в новом назначении.

Unknown outcome native call не разрешает слепо повторить запуск. Сначала доступным способом сверить факт запуска и session reference; если это невозможно, оставить явный gap/blocked для зависимой работы. Timeout не равен завершению, а неподтверждённая cancel не означает остановку worker. Эти правила входят в adapter contract и текущий handoff/journal; новая launch state machine не создаётся.

### Цепочка доказательств и совместимость

Поток данных: контекст → доступные bindings → принятое назначение → snapshot → dispatch → native reference → первичные события → нормализованные факты → решение общего validator. Requested input сохраняется до отправки. Effective input берётся из источника среды; разрешённые служебные добавления проверяются структурно и не теряют точных байтов пользовательской части, включая завершающий перевод строки.

Reader каждого адаптера работает внутри существующих `CapturedSource`/`ValidationInputs` и `capture_validation`/`freeze_validation`. Внешние чтения завершаются до journal write lock; под ним повторяются проверки захваченных входов и актуальности revision. Новая зависимость после freeze вызывает отказ с повторным capture вне lock. Сохраняются input hashes receipt и source provenance. Для растущего журнала хеш всего файла характеризует момент чтения, а не запрещает последующее добавление событий; при новой проверке заново определяется последний ход. Отдельные DSH journal, snapshots и lifecycle исключены.

Независимость проверяется по фактическим execution identities и источнику запуска. У DSH роль связывается с assignment и доставленными инструкциями, а не с `agentPreset` или самоописанием ответа. Для смешанных targets одного локального проекта родительская связь может подтверждаться source-bound dispatch в одной среде и соответствующим входом в другой. Одной записи оркестратора о якобы внешнем запуске недостаточно. При отсутствии связуемых доказательств такой маршрут не допускается к приёмке.

Для новых записей нужен версионированный runtime-neutral envelope. Старые записи читаются через compatibility mapping Codex; действующие `result_contract_version`, workspace seal, journal generation и правила immutable completion сохраняются. Legacy hashes вычисляются по прежним байтам и алгоритмам. Миграция принятых журналов, их массовая перезапись и «обновление» исторических QA не нужны. Прежний CLI `--codex-thread-id` остаётся совместимым входом Codex; новые записи используют явную ссылку выполнения. Неизвестная версия или конфликт old/new identity отвергаются.

Сохраняются действующие правила независимого QA, reviewer после актуального QA, hashes результата/handoff/evidence и последнего завершённого хода. `strict_inputs` сохраняет условия применимости и уровень, выбранный для конкретного сценария: план не делает его универсальным gate всех runs и не разрешает ослабить уже выбранную проверку. Если новый ход начат и не завершён или завершён отказом, прежнее одобрение не становится актуальным после восстановления файла. Reader сохраняет ссылки на источник, но не выгружает private reasoning или секреты. Если неполный/зашифрованный source не позволяет выполнить выбранную проверку, фиксируется evidence gap, а не ложное подтверждение.

Отвергнуты: замена только названий инструментов/UUID; моделирование ролей ответами одного контекста; принятие экспортированного JSON как первичного source; Workflow как единственный backend DSH; глобальное редактирование ролей при выборе команды; автоматический выбор «лучшей» модели по вымышленному рейтингу; обязательный универсальный мост между приложениями.

### Общий набор проверок адаптеров

В существующих Python tests и запланированном `scripts/test-runtime-contract.py` выделить один acceptance/conformance corpus: сценарий, входные условия и ожидаемое решение определяются один раз. `test-verification-evidence.py` и golden traces дают исходные проверки; journal/workspace tests переиспользуют тот же набор там, где проверяется adapter policy. Native fixtures и decoder tests готовят события конкретной среды, затем общий сценарий проходит через её reader и то же ядро. Mock source injection остаётся внутренним test seam.

Для итоговой приёмки каждого зарегистрированного адаптера выполнить все сценарии corpus. Сверить полноту регистрации с trusted production adapter list: отсутствие fixture, потерянный сценарий, неизвестный adapter или пропуск запуска не превращаются в PASS. Следующий адаптер обязан подключить свои native fixtures к тому же corpus; копирование policy suite/expected verdicts или исключения ради зелёного отчёта запрещены. Новый framework и динамический runtime loader не нужны.

Corpus расширяется вместе с реализацией контрактов, сохраняя одни сценарии и ожидаемые решения для всех адаптеров. На этапе 1 обязательны CF-01, CF-02, CF-04–CF-06, существующие input/role checks из CF-03 и workspace/legacy checks из CF-07. Доставка profile instructions и их конфликты из CF-03, project team/stale writer и влияние project revision на snapshots из CF-07 относятся к этапу 2. До его завершения эти cases явно отмечаются `pending: этап 2` в отчёте покрытия; это не PASS и не скрытый skip. Этап 1 закрывается по parity реализованных контрактов, а общий PASS corpus пока запрещён. Этап 2 закрывает отложенные cases и повторяет реализованный corpus для всех зарегистрированных к этому моменту адаптеров. Этап 3 и общая поставка требуют полного CF-01–CF-07 для всех зарегистрированных адаптеров без pending. Эти отметки ведутся в существующих тестовых отчётах; новый stage framework не нужен.

| Общий сценарий | Единое ожидаемое решение |
| --- | --- |
| CF-01: identity/parent, aliases, turns/attempts | Подтверждённая независимая сессия допускается; подмена родителя, alias той же сессии и другой turn автора как QA отвергаются |
| CF-02: несколько observed models и параметры назначения | Все наблюдаемые model values должны совпасть с binding; смешанный список и отсутствующее обязательное observed значение не подтверждают назначение |
| CF-03: requested/effective input, роль и profile instructions | Проверяются точные bytes и разрешённые дополнения; недоставленный профиль, конфликт обязательных правил и подмена source отвергаются. Selected strictness сохраняется |
| CF-04: source integrity/capture/freeze | Missing/ambiguous source и новые внешние чтения под write lock отвергаются; захваченные данные повторно используются. Рост журнала вне проверки не трактуется как порча прошлого capture |
| CF-05: собственное завершение и freshness | Только один собственный актуальный завершённый ход; duplicate/missing completion, последний unfinished/rejected turn и stale hashes отвергаются. Восстановление bytes не возобновляет старое approval |
| CF-06: операции выполнения | Supported launch/continue подтверждается native evidence; unsupported operation, unknown launch outcome и неподтверждённая cancel не дают ложного успеха и слепого retry |
| CF-07: workspace и конфигурация | Prepare/seal/delivery/revalidation выбирают правильный adapter; legacy hashes сохраняются. Unknown runtime, wrong reader, stale team writer и изменение текущего snapshot отвергаются |

Условия допуска и отказа каждого сценария задаются заранее. Если runtime не предоставляет операцию, его fixture проверяет явный unsupported-отказ по общему правилу; выдумывать поддержку или пропускать сценарий нельзя. Такой тест подтверждает корректность отказа, но не поддержку обязательной возможности: capability/live readiness остаётся gap. Общий PASS corpus означает согласованность решений на предоставленных событиях; production readiness дополнительно требует живые сценарии Codex/DSH, выбранный `strict_inputs` и фактическую delivery.

## 4. Этап 1. Выделить Codex и сохранить приёмку

### Результат, границы и порядок

Первый работающий адаптер выполняет существующие назначения Codex через выделенное из текущего кода ядро. Проверки этапа 1 из общего corpus сохраняют сегодняшние положительные и отрицательные решения; будущие profile/config cases остаются явно pending до этапа 2. В этой же поставке отделяются Codex defaults от содержания ролей: исходные модель/reasoning-настройки сохраняются как baseline profiles/bindings, без смены фактических моделей. Интерактивный проектный выбор относится к этапу 2.

Зависимости: прочитать актуальную редакцию этого плана и affected code, зарегистрировать исходное состояние и доступность Codex evidence на одном изолированном назначении. DSH не является зависимостью. Небольшой ранний DSH feasibility probe допустим для уточнения границы до фиксации контракта; это не отдельный обязательный этап и не блокирует независимую работу над Codex. Сначала зафиксировать контракт и legacy mapping, затем вынести decoder/перевод native calls, обобщить параметры recorder/validator и существующих journal/workspace, согласовать инструкции и package inventory.

Scope относительно `skills/agent-flow/`:

- `scripts/runtime_contract.py`, `scripts/runtime_adapters/codex.py`; `verification_evidence.py`, `record-agent-trace.py`, `validate-run.py`, `validation_inputs.py`: extraction по карте переиспользования и проверки реализованных контрактов в общем corpus с явным учётом pending cases этапа 2, без второй реализации acceptance policy.
- `scripts/journal_io.py`, `journal_lifecycle.py`, `journal_recovery.py`, затронутые вызовы `journal.py` и `init-run.py`: identity, source capture, repair/reopen и revalidation через единый контракт. Алгоритмы транзакций и хеширования не перепроектировать.
- `scripts/task_workspace.py`, `scripts/task-workspace.py`: workspace prepare, seal, delivery и повторная проверка используют тот же контракт execution reference. В `capture_metadata_scope` legacy `root_thread_id`/`thread_id` читаются через Codex compatibility mapping, новые proofs — через `ExecutionRef`; конфликт форматов отвергается. CLI и повторный `prepare` выбирают reader из доверенного adapter binding назначения, без неявного `CodexSessionSource` для новых runtime. `delivery` передаёт тот же выбранный source в capture/revalidation, включая проверку перед публикацией. `seal` сохраняет эти привязки без переинтерпретации и без нового чтения source там, где оно сейчас не требуется. Не менять transactional/hash algorithms или historical hashes.
- `scripts/agent_config.py`, `resolve-agent-config.py`, `validate-agent-config.py`, `validate-role-catalog.py`, `sync-codex-agent-config.py`, `agents/*.md`, baseline profiles в `registries/model-profiles.json`: отделить role content и Codex binding, сохранить legacy read path и текущие defaults.
- `scripts/model_eval_adapter.py`: локализовать существующие pinned CLI/provider/catalog и их проверки в Codex backend оценки, сохранив интерфейс callers и результаты. Не связывать общий role resolver с benchmark allowlist. Другие eval-модули менять только при необходимой совместимости импорта/контракта.
- `SKILL.md`, `references/subagents.md`, `delegation.md`, `orchestrator.md`, `traceable-runs.md`, `role-catalog.md`, `docs/ru/agent-flow.md`, `docs/en/agent-flow.md`: общие инструкции получают операции контракта; конкретный Codex mapping размещается в новом `references/runtime-codex.md`.
- `scripts/check-installed-package.py`, `package_distribution.py`, `package-files.txt`, package/config guards: общая integrity часть и readiness выбранного adapter. Codex сохраняет проверку своих roles, DSH и следующий adapter не требуют `CODEX_HOME`/Codex TOML. `--package-only` остаётся integrity-only и не доказывает runtime readiness. Sync остаётся Codex exporter; глобальные установки не обновляются автоматически.

В корне репозитория затронуты `scripts/verify-distributions.py` и `scripts/test-build-distributions.py`: переиспользовать существующие build/archive verification helpers и временную материализацию проверенных файлов для адресного smoke поставляемого пакета. Target-specific readiness не должна требовать Codex setup для другого adapter. Новые CI/deploy или установка пакета пользователю не входят в scope.

### Приёмка и проверки

`RP-01` — общий policy layer не читает native events, не проверяет Codex UUID и не выбирает модели из глобального GPT enum. Codex decoder воспроизводит прежние факты, общее ядро — прежние отказы. Adapter подставляется только из доверенной composition root. Обязательные проверки этапа 1 из §3 проходят через Codex reader; отчёт покрытия учитывает trusted adapter list и явно показывает будущие cases этапа 2 как pending. `RP-01` подтверждает extraction/parity, а не общий PASS всего corpus. Readiness выбранного target отделена от общей integrity пакета.

`RP-02` — legacy traces, включая принятую историю и reopened generations, читаются без изменения байтов и исторических hashes. Новый malformed envelope, неоднозначная identity, подмена source и конфликт legacy/new fields отвергаются. Команды старого Codex CLI сохраняют смысл.

Workspace prepare, seal, delivery и повторная проверка входят в `RP-02`: новый runtime-neutral proof проходит весь путь через reader своего adapter, включая metadata scope, если он выбран. Несовпадающий adapter/target или неизвестный runtime отклоняется до публикации результата; положительный отдельный `validate-run.py` не заменяет проверку delivery.

`RP-03` — свежий Codex author → QA → reviewer проходит общую политику; неверная модель, общий execution ID у автора и QA, reviewer до QA, изменённый handoff, stale approval и начатое незавершённое продолжение не проходят. Для `strict_inputs` проверяются source input и output выбранного хода; зашифрованный input остаётся отрицательным случаем.

Выполнить существующие `test-verification-evidence.py`, `test-golden-traces.py`, `test-validate-run-lanes.py`, `test-init-run.py`, `test-journal-io.py`, `test-journal-lifecycle.py`, `test-journal-recovery.py`, `test-journal-storage.py`, `test-task-workspace.py`, `test-agent-config.py`, `test-validate-agent-config.py`, `test-validate-role-catalog.py`, `test-sync-codex-agent-config.py`, `test-model-eval-adapter.py` и корневой `scripts/test-build-distributions.py`. Расширить существующие tests и добавить `scripts/test-runtime-contract.py` как runner общего corpus, без скопированной Codex policy suite. Сопоставить решения до/после extraction на тех же сценариях. Native decoder fixtures дополняют эти проверки.

Через `test-task-workspace.py` проверить CF-07 для CLI, metadata proof и revalidation. В journal tests проверить CF-04 с barrier/reader spies: после capture/freeze новые внешние чтения под write lock запрещены, receipt сохраняет hashes входов. В package tests проверить integrity и readiness раздельно, включая отсутствие Codex environment для non-Codex target. Проверять выбранный source и отсутствие публикации при отказе.

Живой Codex сценарий выполняется после короткого source-read probe. Если parent→child inputs недоступны, зафиксировать конкретный evidence gap и поведенческий результат отдельно. Реализацию можно передать как выполненный объём с незакрытой формальной проверкой; `RP-03` и полную приёмку не объявлять выполненными. Ремонт CLI, смена runtime или расшифровка источника не входят автоматически в этот этап.

Риск и фокус Devil's Advocate: decoder может незаметно забрать policy, `completed_turn` — потерять проверку нескольких моделей, а workspace/readiness — сохранить default Codex source. Проверить карту callers и обязательные cases этапа 1, включая alias identity и запрет внешних чтений под lock. Завершение этапа для реализации — extraction и релевантные тесты; формальное закрытие — доказанные `RP-01`–`RP-03` и явный список pending cases этапа 2. Их наличие не блокирует parity этапа 1, но запрещает общий PASS corpus. Любой другой разрыв между реализацией и приёмкой указан явно.

Этап нельзя безопасно объединить с этапом 2: отдельно нужен результат «тот же Codex и та же строгость», прежде чем менять источник ожидаемой конфигурации и пользовательский workflow. Иначе расхождение моделей/приёмки нельзя уверенно отнести к extraction или новому выбору команды.

## 5. Этап 2. Предлагать и сохранять команду проекта

### Результат и затронутые контракты

После готового контекста Agent Flow предлагает назначения используемых ролей из доступных управляемых подключений. Принятый выбор сохраняется, а каждая новая lane получает immutable snapshot. Настройка допускает разные targets; исполнение включается только там, где соответствующее подключение проверено.

Зависимость — runtime-neutral `AssignmentSpec`, capability result и Codex baseline этапа 1. Продолжать разработку при известном Codex evidence gap можно только с сохранением gap в итоговой приёмке; это не разрешение ослабить проверки.

Scope: расширить `scripts/agent_config.py`, `resolve-agent-config.py`, `validate-agent-config.py`, `registries/model-profiles.json`; добавить `scripts/project_team.py` и тонкий CLI `scripts/configure-project-team.py` для валидации, чтения и guarded записи принятой конфигурации по expected revision/hash. Названия новых файлов — целевые границы ownership, не уже существующие команды. `init-run.py`, recorder и journal сохраняют snapshot существующим API. `sync-codex-agent-config.py` и существующий delegation packet/export получают profile instructions с приоритетом обязательных role/task rules и сохраняют прежнее поведение без override. Общие helpers выделяются только для реально общего кода guarded write; новые instruction engine или lifecycle не нужны.

В `references/project-memory-and-env.md`, `orchestrator.md`, `delegation.md`, `subagents.md`, `SKILL.md`, роли orchestrator и двух пользовательских руководствах добавить последовательность: актуальный контекст → используемые роли → discovery → предложение → принятие/переопределение → сохранение → dispatch. Новый `references/runtime-portability.md` описывает общие контракты и пользовательскую настройку. Package inventory и проверки конфигурации дополняются вместе с кодом.

Текущее правило роли «сохранять exact model ID» относится к модели зафиксированного назначения после resolution. В канонических role instructions убрать привязку этого правила к глобальному frontmatter; текущие Codex defaults сохранить как baseline. Если custom-agent TOML или session schema применяют новый project binding только после reload/новой сессии, показать состояния «сохранено» и «применено» отдельно и запросить нужное адресное действие. Уже запущенные агенты продолжают своё назначение; новый snapshot допускается к dispatch только после подтверждения эффективной host-конфигурации.

Предложение использует существующие defaults и совместимость с требованиями назначения; при отсутствии совместимого default показывает доступные варианты и пробел. Оно не утверждает превосходство модели по качеству. Уже принятый пользовательский выбор имеет приоритет. Новая роль, появившаяся после уточнения scope, проходит этот же шаг до её запуска; все роли каталога заранее настраивать не нужно.

### Приёмка, проверки и завершение

`RP-04` — два профиля конкретных моделей остаются различимыми при одном провайдере; один профиль поддерживает несколько допустимых reasoning-вариантов. Некорректный reasoning, неизвестный профиль, неподдержанный binding и незаявленная комбинация capabilities отвергаются до dispatch. Все observed model values сверяются после запуска. CF-03 проверяет фактическую доставку profile instructions и приоритет обязательных role/task rules. Названия моделей из примеров не попадают в available catalog без discovery.

`RP-05` — на новом проекте команда предлагается после контекста и до первого назначения; пользователь принимает и переопределяет отдельную роль, позднее меняет выбор. Сохранённый выбор читается при следующей задаче. Новая роль запрашивает только недостающую настройку. Глобальные role files неизменны при этих действиях.

`RP-06` — обновление team revision не меняет текущие или завершённые assignments и их hashes. Два writer одной старой revision не перезаписывают выбор друг друга: первый сохраняет, второй получает conflict и перечитывает актуальный выбор. Недоступное подключение не вызывает скрытый fallback. Два connection/host bindings сохраняются в проекте; CF-01 различает runtime namespaces и сводит aliases одной сессии к одной identity. Installed-only target не предлагается как управляемый.

Расширить `test-agent-config.py`, `test-validate-agent-config.py`, `test-sync-codex-agent-config.py`, `test-init-run.py`; добавить `scripts/test-project-team.py` для round-trip, override precedence, конфликтов revision/hash и одновременных writers. Реализовать оставшиеся pending cases CF-03/CF-07 и выполнить полный CF-01–CF-07 для всех зарегистрированных к этому моменту адаптеров, включая Codex. Разные reasoning и aliases задаются fixture-каталогом; это не тест доступности вымышленных моделей.

Живой сценарий на реально доступном Codex connection: новый тестовый проект → контекст → предложение двух используемых ролей → подтверждение с override в поддержанных пределах → spawn → смена проектного выбора → повторное чтение текущего и нового assignment. Сохранить входы, предложенные строки, ответ пользователя и разрешённые значения; проверить отсутствие записи в глобальные роли. Capability discovery повторяется перед запуском, чтобы устаревший каталог не разрешал недоступный target. Несколько targets проверяются конфигурационными fixtures; это ещё не доказательство межприложенческого исполнения.

Риск и фокус Devil's Advocate: конфигурация может выглядеть переносимой, но validator продолжит читать свежий глобальный frontmatter или общий reasoning enum. Проверить изменение project/profile files между spawn и acceptance, а также совпадение имён effort при разных bindings. Этап закрывается при доказанных `RP-04`–`RP-06`, закрытых profile/config cases CF-03/CF-07 без pending и актуальных регрессиях этапа 1. Полный corpus проверен для текущего списка адаптеров; DSH присоединяется на этапе 3.

Этап отделён от этапа 3: механизм проектного выбора проверяется на уже работающем Codex, а DSH имеет собственную неопределённость каналов и session policy. Объединение сделало бы недоступность DSH неразличимой с ошибкой project resolver. От этапа 1 его отделяет новый контракт пользовательской конфигурации и immutable selection.

## 6. Этап 3. Подключить и проверить текущий DSH

### Условие старта и выбор механизма

Использовать текущее установленное приложение DSH; зафиксировать его версию и фактическую schema перед проверками. Ожидаемая исходная версия — `0.2.0-rc.2`. Не подменять проверку другой установкой, runtime или переустановкой. Действующая авторизация локальных тестов DSH, включая разрешённую работу с пилотным чатом, сохраняется; повторное общее разрешение на тестовые сессии не требуется. Дополнительное согласование нужно лишь для конкретного изменения настроек или доступа вне уже разрешённого scope, если оно потребуется.

Первое действие этапа — подтвердить управляемый production launch/continue channel и reader первичных журналов. Проверить в текущей среде доступность нативного subagent и session policy, способ адресовать child и читать source. Различать запуск Agent Flow внутри DSH через его инструменты и удалённое управление DSH из Codex: пилот доказал первое через диагностическое взаимодействие, но не production connector второго. UI bridge пилота не переносится в production.

Предпочтительный условный путь: штатный DSH subagent provider, который поддерживает `agentOptions` и `prepareContinuable`, с session policy выбора моделей (`modelSelectionSettings` и включённый выбор для сессии) и `backgroundMode=continuable`. На той же установке создать тестовую сессию в пределах действующей авторизации, проверить опубликованную schema и фактический вызов с provider/model/reasoning, затем продолжить ту же сессию. Если необходимое изменение настройки выходит за разрешённый scope, отдельно согласовать именно его. Возможность только в коде не переводит capability в supported.

Если путь требует изменения настроек за пределами разрешённого scope, до согласования этого изменения он остаётся unverified. Если канал управления или комбинация не подтверждены, выполнить независимые reader/contract fixtures, записать точное ограничение и оставить зависимые live-критерии незакрытыми. Workflow можно использовать для отдельных one-shot назначений с provider/model, но он не заменяет обязательный continuation-тест и явное управление reasoning. Продолжение новой сессией не считается same-session continuation. Полная поддержка DSH не объявляется до прохождения обязательного сочетания.

### Scope и порядок

Зависимости — выделенное ядро этапа 1 и project bindings/snapshot этапа 2. Обычный diff этапа ограничен `scripts/runtime_adapters/dsh.py`, профилями/bindings обнаруженных моделей, точечной trusted registration, `package-files.txt`, `references/runtime-dsh.md` и native fixtures/tests. `scripts/test-runtime-dsh.py` проверяет только DSH decoding/перевод native calls; общая приёмка запускается через CF-01–CF-07 в `test-runtime-contract.py`. В `testdata/runtime-adapters/dsh/` хранятся native данные этих сценариев, без копии expected verdicts. Подключение следующего адаптера следует тем же правилам.

Зафиксировать разрешённые paths этапа и проверить реальный diff штатным `record-lane-boundary.py`. Общие policy rules, journal/capture/freeze и workspace не получают DSH-ветвей. Необходимая правка общего ядра означает подтверждённый пробел extraction: architect пересматривает контракт, расширяет ownership именно для исправления, повторно проверяет Codex parity и общий corpus для всех адаптеров. DSH-specific обход внутри core запрещён.

Reader декодирует фактический `session.v4.jsonl.zstd` на текущей установке: session header, parent, catalog/workflow связи, request config, все `assistant/message.source`, входы и собственный `turn/end` ребёнка. Оценку этих фактов выполняет общее ядро. Зафиксировать способ декодирования и доступную зависимость, без скрытой установки. Использовать существующий capture/freeze, точные event refs и source provenance; хеш всего сжатого файла описывает прочитанное состояние растущего журнала и не проверяется как постоянный hash будущих состояний. Неподдержанная версия, обрыв и неоднозначность источника дают gap/error. Новые DSH snapshots, journal/lifecycle или долговременный архив всех raw bytes не нужны; секреты/private reasoning не попадают в fixtures.

Сначала проверить source-read и комбинацию capabilities одним небольшим назначением. Затем провести полный свежий цикл author → QA → reviewer через общий recorder/validator, continuation, stale rejection и delivery/revalidation. Дополнить target-aware readiness этапа 1: временно материализованный поставляемый пакет проходит DSH smoke без Codex role setup и `CODEX_HOME`. Использовать существующие packaging helpers, явно указанные workspace/файлы и действующую авторизацию; глобальная установка и миграция настроек не нужны.

### Приёмка и живые сценарии

`RP-07` — независимые author, QA и reviewer DSH связаны с действительными assignments. Request config и наблюдаемые provider/model совпадают с bindings; effective input соответствует requested input и разрешённым добавлениям. Успех workflow без собственного завершения child не принимается.

После свежей приёмки DSH выполнить штатные workspace delivery и повторную проверку результата через DSH reader. Зафиксировать успешную публикацию delivery, неизменный accepted result hash и source refs актуальных QA/reviewer. Проверить повторный prepare с DSH proof при выбранном metadata scope. Без успешной delivery/revalidation `RP-07` не закрывается, даже если отдельный validator уже вернул положительный результат.

`RP-08` — в одной проверяемой continuable-сессии выбранные provider/model/reasoning подтверждены первичным источником, followup дошёл в ту же сессию и породил новый завершённый ход. Провести отдельные отрицательные проверки неподдержанного effort/комбинации, неправильной модели и незавершённого followup. Это обязательная живая проверка сочетания; исторические два отдельных теста не закрывают её.

`RP-09` — после принятого результата изменить файл так, чтобы старый тестовый набор оставался зелёным, но result hash изменился. Validator и свежий QA отвергают прежнюю приёмку. Восстановить исходные байты: последний отказ QA продолжает блокировать результат. Новый завершённый QA и последующий reviewer могут принять его только по актуальным source-bound hashes. Проверить также отсутствующий source, изменённый handoff, неправильного родителя, неоднозначный child ID, failed launch и расхождение requested/effective input.

`RP-10` — boundary evidence подтверждает адаптерный diff и отсутствие DSH policy в core. Один набор CF-01–CF-07 с одинаковыми ожидаемыми решениями проходит для всех зарегистрированных адаптеров; coverage check обнаруживает отсутствующий adapter/fixture и silent skip. Project team хранит Codex и DSH bindings, каждый поддержанный target проходит свой launch/read path и package readiness. Cross-app маршрут допускается только при доступном управлении и подтверждённой связи dispatch→child; иначе он явно отклоняется. Два bindings не доказывают работающую смешанную desktop-команду.

Выполнить DSH native decoder tests и один общий CF-01–CF-07 через Codex/DSH readers; повторно использовать `test-verification-evidence.py`, `test-golden-traces.py`, `test-validate-run-lanes.py`, `test-task-workspace.py` и затронутые journal/config tests. Проверить пропущенную регистрацию/fixture отдельной отрицательной проверкой покрытия. Через корневой `scripts/test-build-distributions.py` проверить readiness поставляемого DSH-пакета. Живые сценарии RP-07–RP-09 остаются обязательными: fixtures и корректный unsupported-отказ их не закрывают. Повторить Codex smoke после подключения второго адаптера, без benchmark других моделей.

Риск и фокус Devil's Advocate: общую suite могут скопировать для DSH с более мягкими verdicts, unsupported-отказ выдать за support, а policy DSH — незаметно изменить модель/reasoning. QA сопоставляет coverage всех адаптеров, native source и live evidence, reviewer — границы diff. Этап закрывается при доказанных `RP-07`–`RP-10`; неизвестный production channel или непроверенная обязательная комбинация оставляют соответствующую часть blocked, даже если conformance corpus проходит.

Этап нельзя объединить с этапом 2: это доказательство пригодности уже существующего контракта для второго runtime с отдельными форматами source и ограничениями запуска. Успех DSH должен потребовать адаптер и binding, а не переписывание общего policy layer.

## 7. Исполнение через Agent Flow

Реализация начинается отдельным явным поручением пользователя. Оркестратор собирает актуальный контекст, сверяет зависимости и создаёт штатный run до делегирования. Бюджет выбирается по реальному объёму; число этапов не задаёт публичный режим и не добавляет selector/stage state machine.

Для Architecture Contract Gate исходная выборка: `product_context=[developer-tooling, agent-orchestration]`, `application_surface=[]`, `architecture_pattern=[adapter-integration]`, `stack_runtime=[]`, `risk_gates=[local-private-files, evidence-source-integrity]`, `verification_gates=[integration]`. Пустой stack axis отражает отсутствие точного Python facet в текущей матрице, а не отсутствие Python-кода. Capabilities: `developer-tooling-architecture`, `agent-orchestration-architecture`, `adapter-integration-architecture`. При запуске сверить эти IDs с текущими [Matrix](../../references/architecture-matrix.md) и [registry](../../registries/architecture-capabilities.json).

Ограничения facets: адаптеры изолируют протоколы; источники и местные настройки не раскрывают private data; независимость и hashes доказываются первичными событиями; integration проверяет реальное выполнение. Architect заполняет generated Architecture Design Brief и Contract до workers, переносит все выбранные facets/capabilities и удаляет `TODO(agent):`. Этот план не присваивает будущему brief статус approved. Отдельный ADR для повторения этих решений не требуется; изменение публичного контракта сначала отражается здесь и в execution contract.

### Ownership и запреты

Один `python-worker` на активный этап владеет перечисленными в его scope Python-модулями, JSON-конфигурациями и testdata. Этап 1 включает workspace CLI/library/tests, capture/freeze callers, target-aware package readiness и названные корневые distribution scripts. Этап 2 — resolver, guarded team writes и packet/export. В этапе 3 разрешены adapter, profiles/bindings, trusted registration, inventory и tests; общий core/capture/journal/workspace запрещены без подтверждённого extraction gap и architect re-check. До spawn оркестратор разворачивает эти границы в точные `boundary.allowed_paths`/`forbidden_paths`; параллельная работа над общими файлами не назначается. `documenter` получает перечисленные Markdown-инструкции, runtime docs, role bodies, руководства и этот план; role config согласуется с Python worker. `package-files.txt` и guards принадлежат Python worker.

QA и reviewer не реализуют продукт. `qa-verifier` сначала проверяет требования и наблюдаемые критерии, затем поведение; reviewer независимо проверяет финальные bytes и QA handoff. Architect делает re-check при изменении границ, возможностей, схем или политики приёмки. Оркестратор маршрутизирует, фиксирует артефакты и интегрирует результат через штатные journal/workspace команды; ручные callback-фразы пользователя не участвуют в завершении.

Запрещены вне отдельного поручения: чужая `.playwright-mcp/`, глобальные пользовательские роли/настройки, данные исторических runs, реальные credentials, новые провайдеры, benchmark jobs, CI/deploy, коммиты, переустановка приложений и скрытый fallback. Для DSH policy учитывается действующая авторизация; отдельно согласуются только изменения настроек или доступа за её пределами. Изменение scope или ожидаемого поведения возвращается пользователю; технический фикс внутри принятого контракта дополнительного approval не требует.

### QA Gates и Reviewer Checklist

В `acceptance-traceability.json` перенести `RP-01`–`RP-10` с источником в этом плане и `owner_lane`. Для каждого сохранить subjects, буквальные evidence markers и ссылки на фактические outputs. Не закрывать критерий только отчётом автора.

| IDs | Surface и допустимое доказательство | Отрицательные fixtures |
| --- | --- | --- |
| RP-01, RP-02 | `cli`, `parser`, `storage`: обязательные cases этапа 1 из общего corpus, legacy hashes, capture/freeze и package readiness; будущие profile/config cases явно pending, без общего PASS | Wrong adapter/reader, конфликт canonical identity, внешнее чтение после freeze, повреждённая история; `negative_fixture_evidence` обязателен |
| RP-03 | `external-provider`, `logs`, `cli`: реальные Codex source events и validator output | Stale/unfinished turn, неверная модель, encrypted input, нарушенная независимость |
| RP-04, RP-06 | `config`, `storage`, `cli`, `logs`: resolver, effective profile instructions, snapshots; закрытие отложенных CF-03/CF-07 и полный corpus текущих адаптеров | Unsupported binding, недоставленный/конфликтующий профиль, stale writer, silent fallback, изменение текущего assignment |
| RP-05 | `logs`, `config`: source-bound предложение в чате, принятие/override и повторное чтение конфигурации | Dispatch до принятия, попытка применить unavailable target; файл конфигурации один не доказывает шаг предложения |
| RP-07, RP-08 | `external-provider`, `logs`, `cli`: DSH child events, все observed model values, два хода одной сессии и delivery/revalidation | Wrong model/parent, no turn end, unsupported combination, wrong delivery reader; corpus/static-check не заменяет live proof |
| RP-09 | `cli`, `history`, `logs`: последовательность source verdicts и validator outputs с hashes | Mutation → отказ → восстановление bytes → сохранённый отказ, подмена handoff/input |
| RP-10 | `config`, `cli`, `audit` и поддержанный `external-provider`: coverage общего corpus, boundary evidence, package smoke, adapter cycles | Пропущенный adapter/fixture, silent skip, DSH policy в core, installed-only target, недоступный connector |

QA задаёт `surface_expectations` с точными `surface`, `polarity`, `proof_kind`: для fixtures — `unit-test`/`integration-test`, для живого цикла — `smoke` и связанный `source-read`/`manual-log`, для границ кода — `static-check`. Положительные и отрицательные сценарии получают отдельные ожидания; новые enum не вводятся. Типы критериев: RP-01–RP-03 и RP-09 — `gate`; RP-04–RP-06 — `config`; RP-07–RP-08 — `parser` вместе с обязательным живым доказательством; RP-10 — `config`. Для contract-like критериев отрицательное или drift-доказательство обязательно до положительного закрытия. Source storage не заменяет доказательство пользовательского шага или провайдерского поведения.

В `claim-evidence.json`: `RP-C1` — reuse/Codex parity/legacy hashes (`RP-01`–`RP-03`); `RP-C2` — project selection, profile delivery и immutable assignments (`RP-04`–`RP-06`); `RP-C3` — живая DSH combination, freshness и delivery (`RP-07`–`RP-09`); `RP-C4` — общий corpus всех adapters и соблюдение границ расширения (`RP-10`). Каждый claim содержит `owner_lane`, subjects, literal markers и `supported` либо `gap`. Reviewer проверяет актуальный result hash, общее происхождение expected decisions и отсутствие ложного общего PASS при пропущенном adapter или недоказанной обязательной capability.

Stop conditions: изменение strictness; необходимость переписать принятую историю; неподдержанная обязательная capability; недоступный source для выбранной строгой проверки; необходимость нового transport/инфраструктуры вне scope. Останавливается зависимая часть; независимые contract tests и диагностика могут продолжаться. Повторные неудачи проходят существующий Blocked Resolution Gate, а не смену модели по умолчанию.

## 8. Общий контроль и поставка

Отдельного testing-only этапа нет. После трёх этапов выполнить один общий контроль: полный CF-01–CF-07 для каждого trusted adapter без pending и пропусков, релевантные регрессии, `python3 scripts/check-all.py` из корня, package inventory и адресный smoke временно материализованного пакета существующими distribution helpers. Частичный parity-результат этапа 1 не заменяет итоговый corpus или delivery. Общий итог отдельно показывает conformance, runtime readiness и живые Codex/DSH workflows. Если проверка недоступна, сохранить причину; не превращать skip в PASS и не ремонтировать инфраструктуру молча. UI-тесты не нужны; DSH проверяется через согласованный нативный канал.

Поставлять изменение сначала с Codex defaults и явным opt-in DSH. Существующие принятые журналы остаются неизменными. При проблеме с новым target выключить его для новых назначений и сохранить прежние snapshots/evidence; откат не удаляет историю. Никаких per-stage rollback-процедур или миграции всех старых планов.

Перед QA подготовить финальные статусы всех изменённых документов, включая этот план, и полный список delivery files. Выполнить штатный workspace seal; QA принимает текущие result/handoff hashes, reviewer читает собственный актуальный QA handoff и завершает позже QA. Любая последующая правка возвращает кандидата на соответствующую проверку. Затем штатные recorder/final validation/delivery закрывают run по [Definition of Done](../../references/definition-of-done.md). Общий контроль включает успешные delivery и revalidation живого DSH-результата на его adapter и сохранение legacy Codex-пути. Наличие SHA или зелёных unit tests не заменяет эту цепочку.

Для начала исполнения требуется отдельное поручение реализовать план. Для живого этапа DSH сохраняется действующая авторизация локальных тестов; нужны доступный управляемый launch/continue channel и читаемые первичные источники. Отдельное согласование требуется только при необходимости конкретного изменения настроек или доступа вне разрешённого scope. Точные route IDs, reasoning options и поддержка их сочетаний определяются discovery текущей установки. Алгоритм сравнительного ранжирования моделей и универсальные межприложенческие мосты остаются вне этой поставки.
