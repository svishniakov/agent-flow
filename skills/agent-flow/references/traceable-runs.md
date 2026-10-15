# Traceable Runs

Document status: done

Traceable runs store evidence for work that needs durable review history.

`.agent-work/tasks/` is project memory and follows the current user's Codex instructions, usually `~/.codex/AGENTS.md`, for all repo tasks. This file governs only `.agent-work/runs/` trace artifacts.

Для консультаций с budget `light` журнал не нужен. Изменение файлов использует
штатный compact-журнал и тот же verification-контракт, что full.

## Публикация и чтение журнала

После `init-run.py` единственный источник опубликованных документов —
`<run>/.journal/state.sqlite3`. Логические пути обозначают документы в журнале.
Не редактируйте одноимённые файлы или экспорт: валидатор их не читает.

Сначала получите действующий контракт. Поля, типы, статусы и примеры берите
из его вывода. Список ниже поясняет общий набор; действующий kind-контракт
определяет допустимые значения конкретной команды.

Закрытый набор статусов: `planned`, `active`, `pass`, `pass-with-risks`, `blocked`,
`fail`, `timed-out`, `replaced`. Для конкретного kind используйте только значения
из его `constraints`; например, `spawned` требует `active`. `summary` и
`sections[].summary` всегда строки. Число вместо текста, неизвестный статус,
лишнее поле или неверный тип — ошибка входа до записи. Содержательный `blocked`
или `fail` с корректным входом — допустимый результат работы.

```sh
python3 -B "$AF_PACKAGE/scripts/journal.py" contract
python3 -B "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" record \
  --kind progress --request-file "$AF_REQUEST_CAPTURE" --operation-id "$AF_OPERATION_ID"
python3 -B "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" read progress.md
```

Агент передаёт смысловой текст и статус. Writer строго проверяет строки,
неизвестные поля и вложенные ссылки до записи request envelope, документов,
квитанций и revision; ту же проверку повторяет внутри транзакции. Отрицательный
результат допустим и сохраняется как результат, а не ошибка входа. Один operation
ID повторяет прежнюю запись без дубликатов; изменённый payload с тем же ID отклоняется.
Код формирует заголовки, время, SHA доказательств и связанные проекции.

Смысловой текст остаётся текстом: строки с Markdown-заголовками, полями Verdict
или HTML-маркерами допустимы. Writer выводит summary и текст вложенных sections
как буквальное содержимое; они не создают поля и разделы журнала. Многострочный
смысл сохраняется. Отображение имени attachment экранируется отдельно, исходный
путь, bytes и SHA доказательства не меняются.

Это правило относится и к служебным JSON: claims, acceptance, risk/resolution,
readiness, continuation, harness и lane observations записываются по одному
содержательному запросу через `record`. При обновлении ID writer сохраняет
остальные записи. Описания JSON ниже показывают результат writer, а не формат
ручной публикации. Не передавайте готовый документ, произвольный patch или
вычисленный результат native producer.

`publish` не заменяет typed writer: он закрыт для служебных журналов, handoffs,
trace, известных lane reports, command reports, boundary и workspace результатов,
индексов и memory-проекций. Raw stdout, screenshots и specs остаются attachments
с точными исходными bytes. Их содержимое само по себе не закрывает service gate.

`init-run.py` создаёт запланированную работу без вымышленных approval requests,
команд, маркеров доказательств и `TODO(agent):` proof rows. Неизвестный обязательный
документ может отсутствовать; это блокирует соответствующий worker-start или
успешный final до появления настоящих данных. Approval и process success
подтверждаются исходной записью и native producer.

`check` запускает переданный argv и сохраняет настоящий exit code и исходный
output. Статус проверки определяется процессом; текст агента не доказывает запуск.
Пишущие проверки продукта запускайте на расходной копии рабочего снимка.

Исходные доказательства и attachments сохраняйте без изменения bytes:

```sh
python3 -B "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" publish \
  --file checks/output.txt "$AF_OUTPUT_CAPTURE"
python3 -B "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" read checks/output.txt
```

`publish` принимает обычные локальные файлы; ссылки, FIFO и пути через ссылки
отклоняются. Writer-owned журналы через `publish` записать нельзя. Timeline,
source completion, handoff state и lane boundary сохраняют штатные adapters
единого writer с собственными проверками и одной транзакцией.

Для handoff передайте смысловой текст, назначение и ссылки по контракту.
Для QA/reviewer `record-agent-trace.py --prepare-conclusion --summary` создаёт
handoff и машинное заключение: укажите собственный lane и опубликованные
доказательства через `--artifact`. Верните точный stdout инструмента как итог
сессии. Подготовка не подменяет настоящее завершение хода и независимую приёмку.
Для final используйте смысловой `--summary` штатного `journal.py finalize`;
writer сам собирает формат и метаданные, а finalize проверяет настоящие completion,
evidence и продуктовую приёмку. Заголовки, JSON заключения и SHA вручную не пишите.

Новые журналы используют формат хранилища version 2 и явное поколение приёмки.
Формат compact/full выбирает состав документов, а не версию SQLite-хранилища.
Старый version 1 доступен новым инструментам для чтения без скрытой миграции;
запись требует явного обновления. Старые инструменты не должны писать version 2.
Успешный `journal.py finalize` закрывает поколение для всех обычных writers:
publish, trace, handoff, boundary, workspace и прямого API транзакций.
Имя операции или повтор старой метки не обходят эту проверку. После закрытия
допустимы чтение, export, точный технический повтор и штатная свежая delivery
того же принятого результата.

Для новых событий назначения с `lane_id` допустимые пары стадии и статуса
выводит `journal.py contract` в `constraints.event.stage_status`.
`spawned` обозначает стадию с текущим статусом выполнения.
Регистратор отклоняет другую пару до подготовительных записей и сообщает
допустимый статус. Прочие служебные стадии сохраняют свои правила.
Исторические записи остаются читаемыми, даже если содержат прежнюю ошибку.

Структурные проверки выполняются внутри транзакции до сохранения предполагаемого
состояния. Они сверяют ID назначений, роли, execution mode, lane-map при его
наличии, summary и обе истории событий. Планируемое назначение может ещё не иметь
UUID; первый source-checked trace выводит actual mode, identity и thread из
исходной сессии. Mode в planned lane — предварительный выбор, не доказательство
исполнения. `--execution-mode`, stable identity, wave и critical можно опустить:
recorder использует подтверждённое назначение и плановые metadata. Явно
переданные значения проверяются как assertions, а не команды переназначения.
Recorder атомарно обновляет summary, lane-map, timeline, trace и index. Ошибка
указывает поле и назначение; отклонённая транзакция ничего не публикует.
Начатые mode, role, identity и thread неизменны, в том числе по истории.

Новый `spawned`/first-active требует явного `task_kind: change` или `analysis`.
Неизвестный task_kind остаётся intake. Read-only analysis допускается без workspace.
Перед implementation/integration/fix обязательны `task_kind: change`, подготовленная
рабочая область, заданные границы и действующая готовность выбранных проверок.
Это правило действует и без lane-map, независимо от роли; product action требует
actual author в объявленном scope. Автор регистрируется при запуске.
Analysis и planning допустимы до `prepare`;
ранняя структурная проверка не требует успешного финального QA или готового candidate.
Для обычного worker и других ролей `--resolve-session` при завершении проверяет
собственные session metadata, parent, роль, модель и последний законченный turn.
Текст `final_answer` должен совпасть с `task_complete.last_agent_message`.
Регистратор сохраняет указатели исходных событий, SHA текста и хеши handoff
и приложений. `--status` обозначает заявленный исход работы; наличие
`task_complete` само по себе не подтверждает качество или успешность реализации.
Обычное завершение не выбирает `verification.qa` или `verification.reviewer`
и не получает `reviewed_result_hash`. Предварительный QA связывается с отдельным
`requirements-plan`, описанным ниже. Принятие продукта остаётся отдельной
проверкой QA/reviewer: строгий JSON, точный result hash, handoff SHA, независимость
и порядок принятия сохраняются. Повтор команды с прежним operation ID сохраняет
исходную квитанцию; завершённое назначение и handoff нельзя подменить.
Если worker уже записал semantic handoff до фактического `task_complete`,
после завершения привяжите источник той же командой `record-agent-trace.py`
и тем же lane. Регистратор один раз добавит вычисленные completion facts
в summary, сохранив прежние статус, handoff, доказательства и обе истории.
Указатель `task_started_event` запуска остаётся прежним; начало завершённого
turn хранится в `completion_task_started_event`. Новые доказательства,
подмена прежних полей и повторное завершение другим turn запрещены.

Существующие повреждения диагностируйте и исправляйте штатными операциями
восстановления и обязательств, сохраняя историю. Новая запись не должна добавлять
или заменять структурную ошибку другой ошибкой.

Если в открытом журнале v2 у текущего QA или reviewer остался ошибочный
`spawned/pass` либо `spawned/pass-with-risks`, зарегистрируйте существующее
завершение штатной командой `record-agent-trace.py` с `--stage handoff`,
прежним успешным `--status`, `--lane-id`, `--completion-turn-id` и точными
`--artifact` для отчёта и доказательств. При использовании `--resolve-session`
сохраните исходный `--agent-path`. Отдельного флага восстановления нет.

Регистратор проверяет собственную сессию агента, результат, хеши, обе истории
и, для reviewer, принятое QA и порядок завершений. Дополнение разрешено только
при отсутствии прежнего завершения, legacy-классификации и разрешённой обязанности.
Под блокировкой он повторяет проверки и одной транзакцией добавляет `handoff`
в обе истории, сводку и квитанцию. Прежний статус, назначение и байты истории
сохраняются. Указатель начала работы относится к принимаемому завершённому turn.
Обычный `publish` такого права не получает. Следующий шаг выполняйте только
после успешной квитанции; потерянный ответ повторяйте с сохранённым ID операции.

Каждая новая операция получает отдельный идентификатор и сохраняет запрос
в `.journal/requests/` до транзакции. Команда печатает `operation_id` и ревизию.
Если ответ потерян, агент берёт идентификатор сохранённого запроса и повторяет
тот же вызов с `--operation-id <id>`. Повтор возвращает сохранённый результат;
иной payload с тем же ID отклоняется. Записи завершённых source turns получают
стабильный ID из исходной сессии. Технический повтор не требует нового ответа
модели и не заменяет сохранённое принятие.

Читатели используют один `JournalSnapshot` на весь вызов, включая ссылки,
перечни и проверки существования. `export` создаёт представление конкретной
ревизии в `.journal/views/`; повторный вызов восстанавливает повреждённый экспорт
из базы без изменения принятия. Этот каталог не является новым run: передавайте
инструментам исходный логический путь запуска.

Старый файловый run доступен для диагностики без автоматического импорта.
Для продолжения остановите его писателей и явно вызовите `journal.py import-legacy`
с `--source-root <original-project>`. Импорт проверяет неизменность исходных байтов
и публикует их одной транзакцией. Исходный проект задаётся при импорте; позднее
происхождение не меняется. Полученный version 1 требует отдельного явного upgrade
или атомарного `reopen --upgrade`, если поколение уже закрыто.
Допустимые исторические файловые ссылки внутри исходного проекта сохраняют
байты и идентичность цели; QA и reviewer не могут сослаться на один handoff
через разные имена. Повреждённая опубликованная база не переключается на старые
файлы. После сбоя до commit импорт можно повторить, после commit читается база.

До первой публикации создаются узнаваемая пустая SQLite schema и пустой
`.journal/storage-required`; файл базы и marker синхронизируются до COMMIT
run_state. Marker только запрещает возврат к старым файлам при утрате базы,
не хранит документы или принятие. Обычные соединения открывают только уже
существующую базу. Повтор init допустим для точного незавершённого bootstrap;
missing/обнулённая/чужая база при marker отклоняется. Удаление всей `.journal`
вместе с marker и согласованный откат всей копии не входят в гарантию восстановления.

Хранилище использует стандартный SQLite с `DELETE`, `synchronous=EXTRA`,
`read_uncommitted=OFF` и `fullfsync` на Darwin. Перед созданием проверяются
блокировки и синхронизация файлов и каталогов на локальной Darwin/Linux FS.
Успех зависит от честного выполнения sync файловой системой и устройством;
проверки завершения процесса не доказывают сохранность при физическом отключении
питания. Сетевые и иные неподдержанные хранилища не входят в этот контракт.


## Адресное чтение журнала

Сначала выберите нужный run, документ, session/lane/event и поля. Читайте опубликованный документ через `journal.py read`; большой ответ сохраняйте в новый capture и фильтруйте существующими средствами до вывода в контекст. Не придумывайте CLI-флаги: при необходимости проверьте `read --help`.

Сохраняйте найденные пути в текущем контексте. При продолжении читайте добавленные записи или изменённые разделы; прежние данные перечитывайте при изменении источника, утрате контекста или конкретной проверке. Усечённый вывод не доказывает полноту. Для проверки хешей, подлинности и принятия по-прежнему нужны полные необходимые исходные bytes и один согласованный snapshot. Адресная навигация не отменяет свежий финальный validator и не разрешает переписывать старые журналы.

## Полная рабочая копия и выдача результата

### Приёмка продукта и аудит журнала

Разработчик занимается задачей и проверкой результата; сведения о ходе работы
собирает инструмент. Обслуживание журнала не становится отдельной разработкой
внутри каждой задачи. Эти правила уточняют прежние требования планов 013 и 018.

`st_dev`, `st_ino` и временные файловые descriptors проверяются в пределах одной
текущей операции чтения или записи. Сохранённые номера устройства и inode не
служат бессрочным условием чтения, повторного seal или приёмки. Каждая операция
заново закрепляет реальные дескрипторы и обнаруживает подмену и недопустимые
symlinks. Принадлежность workspace проверяется по его логическим IDs; сохранность
результата — по полному manifest, содержимому, типам, режимам, целям ссылок и delta.
Проверки контекстных источников, provenance и точных исключений сохраняются.

Новый seal содержит `result_hash_version: 5`. Хеш связывает baseline/candidate
manifest digests и полный delta с содержательным scope: продуктовые и контекстные
корни, provenance и исключения, локальные обязательства и external follow-up,
авторские границы с фактическим `author_thread_id` и обязательные локальные gates. `requirements_sha256` закрепляет
проверенный digest фактического содержимого исходного `verification.task_scope`.
Смена требований инвалидирует прежнюю QA-приёмку; перенос ссылки с теми же
байтами сохраняет хеш. Путь и имя раздела ссылки не входят в него. Номера устройства/inode,
время наблюдения, служебные workspace/candidate UUID и revision,
оформление служебных JSON/Markdown отчётов и пути
служебных отчётов не определяют версию продукта. SHA-ссылки на реальные источники
и evidence проверяются отдельно: исключение оформления из хеша не разрешает
подмену доказательства. Без marker воспроизводятся прежние алгоритмы v2–4;
исторический v1 сохраняется. Неизвестный marker отклоняется. Старые hashes,
receipts и закрытые журналы не переписываются и не требуют миграции.

Положительные `finalize` и `delivery` используют один `validate_product_acceptance`.
`finalize` передаёт явный verdict; при delivery основанием служит единственная
квитанция finalize текущего поколения. Текст `Verdict:` в Markdown не заменяет
эту квитанцию и не разрешает выдачу сам по себе.
Он проверяет сохранённый результат, scope, авторство и permissions, независимые
QA/reviewer одного результата, целостность и завершённость их исходных доказательств,
а также обязательные локальные проверки. Отсутствующий proof, `fail` или `blocked`
обязательной локальной проверки запрещает и `ship`, и `pass-with-risks`.
Внешний follow-up остаётся открытым и не объявляется выполненным.

`validate-run.py` сохраняет полный аудит журнала. Недостатки оформления handoff,
служебных `base_ref`/`head_ref`/`command`/`notes` и буквальных Markdown markers
остаются видимыми замечаниями аудита. Они сами по себе не доказывают дефект продукта.
Предварительный readiness-отчёт не блокирует принятие, если все обязательные
локальные проверки действительно выполнены и подтверждены. Требования ниже
к оформлению и предварительной церемонии относятся к полному аудиту; содержательные
проверки продукта продолжают блокировать приёмку. Успешная delivery заново выполняет
проверку продукта на свежих входах; прежняя квитанция её не заменяет.

Для нового изменения orchestrator сначала создаёт журнал, затем вызывает
`task-workspace.py prepare`. Повторяемый `--source` указывает выбранные продуктовые
корни: репозитории и файловые зависимости приложения, сборки или тестов.
Первый source остаётся основным. Повторяемый `--context-root` задаёт канонические
файлы или каталоги инструкций. Основной source — Git-корень; дополнительные
source могут быть репозиториями, обычными каталогами или регулярными файлами
зависимостей. Создавать Git-репозиторий для файловой зависимости не требуется.
Контекстный каталог выбирает точное поддерево инструкций. Для общего контекста
укажите нужные `AGENTS.md`, память или docs; объявление всего общего родителя
включило бы лишние материалы соседних репозиториев в контекст.
`--destination` необязателен и задаёт отдельный каталог задачи. Для нового run
`<owner>/.agent-work/runs/<run>` default —
`<owner>/.agent-work/workspaces/<run>-<run_uuid>`; для другого расположения —
`<run_dir>/workspace-<run_uuid>`. Якорь определяется журналом, не общим родителем
source. Повтор без destination использует уже зарегистрированный bundle.
Явный destination, включая временный fixture, сохраняет прежнюю совместимость
и не перемещает зарегистрированную привязку. Родители default создаются через
существующие owned/no-follow проверки в пределах текущей операции.
Одиночный source без context
сохраняет workspace version 1; несколько
source или явный context создают version 2. Инструмент сохраняет исходные байты,
включая уже изменённые, staged, untracked и ignored файлы,
и создаёт независимую рабочую копию. Linked worktree поддерживается.
Для Git-корней метаданные копируются через те же проверенные дескрипторы. Команды Git
читают только закрытый временный снимок; рабочая `.git` создаётся FD-копированием
с фиксированным безопасным config. Git-фильтры, hooks, includes, fsmonitor и
сетевые обращения отключены. Поля status/staged/unstaged описывают этот явный
безопасный профиль `private-fd-snapshot-fixed-config-v2`, а не буквальный вывод
Git исходного проекта: например, локальный `.git/info/exclude` не переносится.
Полный manifest всё равно включает ignored файлы. Исходный config сохраняется в доказательстве своим digest,
а его команды и внешние ссылки не переносятся.
Исходные файлы, index, HEAD и refs инструмент не изменяет.

```sh
python3 "$AF_PACKAGE/scripts/task-workspace.py" --run-dir "$AF_RUN" prepare \
  --source "$AF_SOURCE"
python3 "$AF_PACKAGE/scripts/task-workspace.py" --run-dir "$AF_RUN" inspect
```

Для нескольких корней явно перечислите каждый. Например, приложение зависит от
соседнего пакета, а инструкции лежат в общем корне проекта и внешнем skill:

```sh
python3 "$AF_PACKAGE/scripts/task-workspace.py" --run-dir "$AF_RUN" prepare \
  --source "$AF_APP" --source "$AF_SHARED" \
  --context-root "$AF_PROJECT/AGENTS.md" --context-root "$AF_CANONICAL_SKILL" \
  --destination "$AF_WORKSPACE"
```

Общий родитель задаёт взаимное расположение выбранных корней, не разрешает обход
прочих соседних каталогов. `workspace` artifact хранит состав и происхождение
корней, связанные с `verification.task_scope`. Журнал может оставаться внутри
основного репозитория или в общем корне проекта; его место не определяет scope.

Авторы меняют только возвращённый `working_root`. Перед QA остановите записи,
закончите проверки и статусы документов, затем сохраните полный кандидат:

```sh
python3 "$AF_PACKAGE/scripts/task-workspace.py" --run-dir "$AF_RUN" seal
```

`seal` возвращает `candidate_root`, `candidate_id` и полный `changed_paths`.
В version 2 пути `changed_paths` относительны layout anchor; принадлежность корню
определяется `product_roots`, поэтому одинаковые имена в разных репозиториях
различимы.
Он сохраняет отдельную копию без общих hardlinks, исходную копию, manifests и
разницу между ними. Перечень учитывает незаявленные файлы, ignored зависимости,
пустые каталоги, удаления, типы, права и текст относительных ссылок.
Относительные ссылки между выбранными продуктовыми корнями сохраняют смысл.
Абсолютные ссылки внутрь этой области преобразуются в относительные только
в снимках, включая baseline. Исходная цель и преобразование входят в provenance;
нормализация не считается правкой автора. Проверяется вся цепочка ссылок,
включая циклы и конечную цель. Объявленный вложенный репозиторий обходится один
раз; Git каждого репозитория обрабатывается отдельно. Необъявленный вложенный
репозиторий, неизвестная внешняя цель, неподдерживаемые специальные объекты,
нечитаемые или нестабильные объекты вызывают точный отказ с путём
и причиной. Для внешней цели укажите, какой источник нужно объявить.
Имена и цели ссылок, которые нельзя представить в UTF-8,
отклоняются с указанием причины. Проверки выполняются через закреплённые дескрипторы;
подмена имени не должна направить чтение или запись в чужой объект.
Инициализированный gitlink допустим только при явно выбранном соответствующем
вложенном Git-корне. Инструмент не выполняет init/update/fetch и не раскрывает
submodules автоматически.

Ссылка на объявленный источник контекста — точное контекстное исключение:
workspace сохраняет её описание, каноническую цель и хеши выбранных материалов.
Для alias через переадресующие ссылки закрепляется и перепроверяется ограниченная
`resolution_chain` до объявленной канонической цели; содержимое других внешних
целей не читается.
Сам alias отсутствует в исполняемом снимке; агент читает канонический источник.
Исключения и provenance входят в хеш результата, а изменение закреплённого
контекста требует новой revision и приёмки. Правил по имени `.agents/skills` нет.
Файлы приложения, сборки или тестов включаются в продуктовые корни и снимок;
при пересечении категорий продуктовый корень имеет приоритет. QA проверяет
фактический сценарий, reviewer — полноту снимка и обоснованность исключений.
Если исполняемая зависимость ошибочно объявлена контекстом, положительная
приёмка запрещена. Candidate не должен получать через alias доступ к живым
внешним файлам.

Из обхода исключаются точные Git metadata и собственные run/workspace roots.
Обычный `.agent-work` входит в продукт. Исключить весь проектный runtime namespace
может только orchestrator на основании уже принятого назначения этого source.
Он передаёт вместе `--metadata-namespace agent-flow-runtime`,
`--metadata-authorized-source`, `--metadata-scope-ref PATH SHA256`,
`--metadata-qa-proof-ref PATH SHA256` и `--metadata-review-proof-ref PATH SHA256`.
Инструмент проверяет байты ссылок, исходные заключения QA/reviewer и порядок
приёмки, закрепляет точный путь и назначение `source/.agent-work` в записи workspace.
Первоначальный смысл назначения подтверждает orchestrator; код не толкует прозу.
Произвольного пути исключения нет. Повтор не может изменить эту привязку.

До `prepare` зарегистрируйте `task_kind: analysis` и SHA-256 файла scope в
`verification.result_hash`, оставив продуктовые пути и worker lanes пустыми.
QA получает JSON через `--prepare-conclusion` и возвращает его как итог своего
хода. После фактического завершения сессии orchestrator регистрирует handoff
с completion этого хода. Reviewer возвращает свой итог после принятого QA;
orchestrator регистрирует его handoff после завершения сессии reviewer.
Такой scope принимается без workspace.
По зарегистрированным `codex_thread_id` и `completion_turn_id` получите
`completed_turn` каждого проверяющего. Опубликуйте отдельные proof JSON с полями
`thread_id`, `completion_turn_id` и `completion` (факты `completed_turn`,
сериализованные с `default=str`). Handoff содержит заключение и ссылки на
evidence; proof содержит исходные факты завершения сессии. Передайте scope и
оба proof со своими SHA-256 через пять metadata flags выше, сохранив run открытым.

После продуктовой правки выполните `seal` и зарегистрируйте `task_kind: change`.
Recorder рассчитает новый продуктовый хеш и сбросит выбранные scope QA/reviewer.
Продукт требует новых lanes, отдельных сессий QA/reviewer и приёмки нового хеша.
Старые scope conclusions и proof остаются подтверждением исключения metadata.

После `seal` recorder сам заполняет `verification.result_files` полным scanner
delta. Seal остаётся каноническим источником changed paths; новый recorder не
пишет дублирующий `verification.run_changed_files`. Старое поле читается для
совместимости. Неизменённые зависимости тоже входят в хеш полного manifest.
QA и reviewer получают один `candidate_root` и `result_hash`. Проверки, которые
пишут файлы, запускаются на отдельной расходной копии. Сам кандидат сохраняется
для финальной проверки и выдачи. Новый seal использует hash v5, описанный выше;
старый seal без marker сохраняет прежние правила v2–4. Содержательное изменение
результата или scope требует нового `seal` и приёмки.
Изменения исходного проекта после `prepare` не подменяют сохранённый результат.

`record-lane-boundary.py` сохраняет полный scanner delta в `changed_paths`.
Авторство задаётся явным `authored_paths` boundary v2; разрешения проверяются
по подтверждённому вкладу каждой lane. Порядок и формат декларации описаны ниже.
`--render-final` строит Worktree Hygiene из того же перечня и перепроверяет
приёмку. После финальной валидации выдайте сохранённую копию:

```sh
python3 "$AF_PACKAGE/scripts/task-workspace.py" --run-dir "$AF_RUN" delivery
```

Команда проверяет текущие QA/reviewer, журнал и сохранённые байты, затем
возвращает пути полной копии, baseline, manifests, delta и читаемого `delivery.md`.
Она не переносит изменения обратно в source. Повтор использует прежний receipt;
повреждённая копия не выдаётся. Оборванные попытки остаются в собственном каталоге
для диагностики и не дают ложного опубликованного результата.

Baseline, working и candidate сохраняются до явного удаления владельцем;
автоматических backup и GC нет. При явном destination владелец отвечает за
сохранность этого каталога. Default внутри временного fixture тоже временный.
При missing baseline/candidate bytes inspect, validate и delivery называют
конкретный отсутствующий root и непроверенную поставку. Manifest и история
остаются диагностикой; они не восстанавливают байты, не заменяют candidate
живым source и не создают bundle под прежней identity.

Новый `change` без зарегистрированного sealed workspace не получает положительную
приёмку. Старые run читаются для диагностики. При переходе к новому контракту
сохраняется лишь технический повтор уже пригодной старой пары QA/reviewer:
с теми же исходными завершениями, результатом и доказательствами. Новый turn,
новый scope или новое заключение требуют полного workspace.

### Обновление состава открытой задачи

Перед расширением scope остановите исполнителей. Повторите `prepare` с полным
новым составом и новым `--operation-id` в прежнем bundle и журнале. Сохраните
первый source основным. Новый operation ID обозначает изменение состава;
технический повтор использует прежний ID и тот же payload.
Объявления продуктовых и контекстных корней сохраняются при расширении;
основной source и bundle не меняются. Пример добавления репозитория:

```sh
python3 "$AF_PACKAGE/scripts/task-workspace.py" --run-dir "$AF_RUN" prepare \
  --source "$AF_APP" --source "$AF_SHARED" --source "$AF_NEW_REPO" \
  --context-root "$AF_PROJECT/AGENTS.md" --context-root "$AF_CANONICAL_SKILL" \
  --destination "$AF_WORKSPACE" --operation-id "$AF_SCOPE_UPDATE_ID"
```

Следующая workspace revision сохраняет первоначальные baselines старых корней
и текущие правки; новый корень снимается до его изменения. Внутреннее расположение
может измениться для сохранения связей между каталогами. Старые записи, receipts
и кандидаты остаются. Обновите `verification.task_scope`, контекст и задания
до следующих правок. Новый seal относится к текущей revision; прежняя QA/reviewer
становится неактуальной.

`seal`, `result_hash`, `result_files` и delivery используют один состав и полные
manifests с путями, различающими корни. Новые данные получают версию workspace
контракта; прежние форматы и принятые хеши читаются по прежним правилам.
Закрытые или восстановленные записи с закреплённой идентичностью автоматически
не мигрируют и не расширяются.

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
6. pass the substantive result, actual commit evidence and risks through semantic `journal.py finalize --summary`; code writes `final.md`;
7. use `journal.py finalize` to validate the prospective final and atomically close the current generation;
8. run the full audit `scripts/validate-run.py --run-dir <run-dir>` and save its findings outside the journal, then use native delivery for fresh product acceptance;
9. only after successful product acceptance and delivery, and completion of all task criteria, set `Status: done` and send the final answer; preserve audit findings separately.

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
- регистрация таймаута назначения: `python3 "$AF_PACKAGE/scripts/record-agent-trace.py" --run-dir <run-dir> --lane-id <lane-id> --stage timed-out --status timed-out --summary <причина>`; обязанность назначения остаётся открытой, пока replacement lane не принят со статусом `pass` или `pass-with-risks`;
- a `subagent` lane with active or successful status has a matching spawned trace event with `codex_thread_id`;
- a successful `subagent` lane also has a terminal handoff trace event with the same lane id and handoff artifact;
- `role-lane` entries do not require a `codex_thread_id`;
- schema v2 positive lane-map runs require `delegation-summary.json` and a final `Delegation Trace` section;
- schema v2 lane-map runs may opt into Handoff State Gate with `handoff_state_required=true`; then each lane has `handoff_state`, terminal lane status must match handoff state, handoff paths must match, timestamps must be ordered, and batch items must be completed before batch acceptance;
- Mandatory Independent QA Review Gate требует для `change` два отдельных назначения: `qa-verifier` проверяет результат, `reviewer` проверяет его и доказательства QA. Оба работают как реальные дочерние сессии текущего root; их IDs отличаются от IDs авторов. Модели берутся из действующих файлов ролей через `agent_config`. `reviewer.qa` допустим только как имя назначения канонического `reviewer`; QA под этим именем не заменяет reviewer.
- role-lane-only reviewer evidence is rejected when Mandatory Independent QA Review Gate applies;
- При недоступности запуска или исходной сессии записать причину в `verification.blocker` и завершить `blocked` или `fail`; успешные доказательства не выдумывать.
- `Verdict: ship` is rejected while a local critical lane is unresolved, failed, blocked, or missing replacement evidence. Explicit external follow-up retains its actual outcome and coverage.

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

Allowed `mode` values: `task`, `batch`. The CLI accepts caller statuses
`planned`, `active`, `pass`, `pass-with-risks`, `blocked`, `fail`, `timed-out`,
`replaced` (see the examples below); the recorder maps them to the stored domain
values `queued`, `accepted`, `completed`, `blocked`, `failed` shown in the JSON
above. Passing a domain value to `--status` is rejected.

Use:

```bash
python3 "$AF_PACKAGE/scripts/record-handoff-state.py" --run-dir <run-dir> --lane-id worker-a --status planned --summary "Назначение подготовлено" --from architecture-contract --task worker-a-implementation --handoff handoffs/worker-a.md
python3 "$AF_PACKAGE/scripts/record-handoff-state.py" --run-dir <run-dir> --lane-id worker-a --status active --summary "Работа принята"
python3 "$AF_PACKAGE/scripts/record-handoff-state.py" --run-dir <run-dir> --lane-id worker-a --status pass --summary "Результат подтверждён"
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
| `result_files` | После seal полный scanner delta: файлы, ссылки и каталоги с путями относительно working root, различающими выбранные корни |
| `initial_snapshot` | Ссылка с SHA-256 на раздел `Initial Worktree Snapshot` в context.md или route.md |
| `task_scope` | Ссылка с SHA-256 на согласованные границы в плане задачи; можно выбрать раздел |
| `qa`, `reviewer` | `lane_id` двух записей в `subagents`; до назначения допускается `null` |
| `result_hash` | Для change — продуктовый хеш кандидата, baseline, delta, scope, provenance и boundary v2; до seal отсутствует или `null`. Preliminary review его не заполняет |
| `delivery_scope` | Необязательный opt-in version 1 для локальной приёмки с явно открытым external follow-up; отсутствие сохраняет строгую приёмку |
| `behavioral_checks` | Необязательный список проверок поведения, только если это требует приёмка задачи |
| `blocker` | Конкретная причина для `blocked` или `fail` |
| `unverified` | Непустой список непроверенного для отрицательного завершения по новому контракту |

Ссылка имеет вид `{"path":"context.md","section":"Initial Worktree Snapshot","sha256":"<SHA-256>"}`.
`section` необязателен: без него хешируются все байты файла. С ним хешируется
содержимое после заголовка до следующего заголовка, включая пробелы и переводы
строк. Пути доказательств относительны run-каталогу; абсолютный путь допустим
внутри проекта. `..` и выход за проект через symlink запрещены.

Для новых изменений корни source, working и candidate берутся из зарегистрированного
workspace. Исходный `source_root` журнала сохраняет происхождение старых ссылок.
`result_files` заполняется полным scanner delta после `seal`;
декларации автора не ограничивают обход. Старый хеш списка файлов доступен только
для исторической диагностики и разрешённого технического повтора.
До seal наблюдаемые рабочие изменения учитываются отдельно от принятого результата.
Без candidate не заполняйте `result_hash` фиктивным значением.

После содержательного отчёта выполните:

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" --run-dir "$RUN_DIR" --render-final
```

Инструмент формирует служебные блоки Worktree Hygiene и Delegation Trace.
Повтор обновляет только отмеченные блоки и сохраняет остальной текст. Пример:

```markdown
## Worktree Hygiene

Run-owned changed files:
- `docs/prd/example.md`
- `src/example.py`
```

`changed_paths` каждого Boundary Evidence совпадает с полным delta его собственного seal;
`authored_paths` содержит вклад автора. Текущее состояние подтверждает последний вклад
в путь; родительские каталоги созданных и удалённых файлов код учитывает автоматически.
Reviewer сверяет декларации с исходными edit evidence и принятыми границами. Предсуществующие
байты остаются в baseline; дальнейшие чужие изменения source сохраняются отдельно.
Авторство изменений закрепляется назначением worker, а не выводом Git.

### Локальная приёмка и внешний follow-up

Перед соответствующими проверками закрепите смысл разделения и конкретные IDs
в существующем SHA-bound `task_scope`. Объявите записи этих IDs в текущих
артефактах. Scope применяется к `task_kind: change` с самостоятельным локальным
результатом. Через существующий `--verification-json` передайте в `verification`
необязательный объект:

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

Замените примеры реальными объявленными IDs. Все пять массивов обязательны,
могут быть пустыми, содержат уникальные ссылки; неизвестные поля, версии и ссылки
отклоняются. `gate_refs` используют существующие `axis/facet`. Без scope,
а также для всех неперечисленных записей действует прежняя строгая приёмка.
Смысловой scope и проверенное содержимое исходных требований входят в hash v5
нового seal; seal без marker воспроизводит прежний hash v2–4. Изменение классификации сбрасывает
выбранные QA/reviewer и требует новой независимой приёмки.

До соответствующих readiness, QA и reviewer attempts сохраните native запись:

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" --run-dir "$AF_RUN" \
  --role orchestrator --execution-mode role-lane \
  --stage verification-prepared --status active \
  --summary "Область локальной приёмки закреплена до проверок" \
  --verification-json "$AF_SCOPE_VERIFICATION"
```

`AF_SCOPE_VERIFICATION` — личный capture текущего полного объекта verification:
сохраните его существующие поля, ссылки и `task_kind: change`, добавьте scope.
Это не JSON только с delivery_scope. Recorder закрепляет нормализованный scope
и проверенный task_scope SHA в событии и квитанции. Простая публикация summary
или позднее добавление IDs не заменяют эту запись. Дождитесь успешной квитанции,
затем проводите соответствующие проверки. Явное scoped update открытого run
сохраняет историю, меняет result_hash и требует новых выбранных QA/reviewer;
прежние попытки не переклассифицируются задним числом.

Общий parser в `verification_evidence.py` применяется recorder, validator,
readiness, QA verification-results, obligations, critical lanes, risk gates,
finalize, render-final и delivery во всех compact/full/auto. Только перечисленный
операционный follow-up может остаться gap или неуспешным при локальном финале.
Его строки, прежние попытки, blockers, evidence и outcomes сохраняются; они
не превращаются в supported, pass, resolved, exempt или устранённый риск.
Агрегированные readiness/results описывают готовность локальных gates.
Форма, источники, subjects, markers и целостность всех предъявленных evidence
проверяются всегда; supported сохраняет требования поверхности и negative fixtures.

QA и reviewer проверяют причины классификации по исходному заданию. Ошибка
payload, auth, config или логики остаётся локальным дефектом, даже если её выявил
внешний тест. Нельзя исключать авторские implementation/integration lanes,
выбранные обязательные QA/reviewer, manifests, history, boundary и mandatory
local proof. Слова external-provider, timeout, GitHub или недоступность сети
не дают автоматического исключения. Mock подтверждает свой локальный сценарий,
не реальный provider workflow. Локальная неготовность блокирует worker и приёмку.

Final и delivery называют принятый локальный результат, исходную внешнюю цель
и весь непроверенный follow-up. Deploy/e2e цель остаётся незавершённой;
локальный ship не подтверждает её выполнение. Внешняя задача без самостоятельного
локального результата не получает локальную delivery. Read/finalize/delivery
не обращаются к GitHub или consumer по сети; исходные локальные сессии и retained
bytes проверяются. Недоступный обязательный локальный proof блокирует приёмку.

### Декларация авторства boundary v2

Порядок внутри текущего этапа: остановить product writers → seal → опубликовать
собственные handoffs, edit evidence и декларации → вычислить result_hash →
независимый QA → последующий reviewer. Journal artifacts не входят в candidate.
Декларация не содержит result_hash; recorder добавляет текущие workspace/candidate
IDs, baseline/candidate digests, полную дельту и assignment identity.

В новых запусках с seal v5 эти проверки действуют и в compact без lane-map: код использует
зарегистрированные назначения и существующие `checks/lane-boundary-<lane_id>.json`.
Отсутствие карты не делает root автором; совпадение его UUID с участником
предварительной read-only проверки не доказывает вклад. `author_thread_id`
связывается с фактическим автором и входит в хеш. Участник передаёт факты о своём
вкладе, код собирает служебные привязки. Отдельная карта или registry не нужны.
Исторические hashes v1–4 воспроизводятся по прежним правилам.

Подготовьте собственную декларацию, затем опубликуйте её через journal publish.
Все ссылки указывают на сохранённые документы журнала и их настоящие SHA-256:

```json
{
  "lane_id": "worker-a",
  "authored_paths": ["src/example.py"],
  "handoff": {"path": "handoffs/worker-a.md", "sha256": "<SHA-256>"},
  "edit_evidence": [{"path": "checks/worker-a-edits.patch", "sha256": "<SHA-256>"}],
  "coauthors": {}
}
```

Handoff перечисляет собственные authored paths и edit evidence. Код собирает участников
из зарегистрированных вкладов. `coauthors` сохраняется для совместимости: пустой объект
допустим, указанные peer lane IDs должны существовать. Прежний автор не обязан знать
будущих участников. Все участники должны иметь разрешение на путь; forbidden pattern
имеет приоритет.

При регистрации boundary код проверяет SHA, назначение, разрешения и фактический вклад
без проверки формулировок handoff. Полный аудит отдельно требует перечислить в тексте
authored paths и заявленных coauthors; сокращённый текст не освобождает от этого требования.

Пустой вывод команды допустим как evidence только для `checks/command-*.output`
без section, если штатный command report и native receipt подтверждают байты вывода,
exit code и статус. Неудачная команда остаётся failure evidence. Произвольный пустой
файл, пустой handoff или section и вывод из одних пробелов не принимаются.

```sh
python3 "$AF_PACKAGE/scripts/record-lane-boundary.py" --run-dir "$AF_RUN" \
  --lane-id worker-a --declaration-json checks/worker-a-declaration.json
```

`--declaration-json` принимает опубликованный логический путь журнала,
а не private capture. Результат — `checks/lane-boundary-worker-a.json` version 2.
`authored_paths` проверяется по delta собственного seal и различиям с предыдущими
сохранёнными seals: это позволяет записать удаление или возврат к baseline.
Permissions проверяются через `fnmatch.fnmatchcase` по собственному вкладу.
Каждый актуальный изменённый файл должен совпадать с последним зарегистрированным
вкладом в этот путь. Обычные родительские каталоги учитываются кодом; самостоятельные
пустые каталоги, изменения mode или типа пути требуют явного вклада. Незаявленная правка,
путь вне сохранённых изменений, stale/forged handoff и запрещённый вклад блокируют приёмку.
Записанную декларацию нельзя перепривязать к новому seal; исправление получает новое
назначение. Старые handoffs и исходные доказательства не меняются. Root как автор тоже
предъявляет декларацию. Permissions filter общего diff не доказывает авторство.
QA сверяет вклад с edit evidence; reviewer проверяет эту сверку.

Version 1 сохраняется для legacy reading и точного replay принятой прежней пары.
Новая registered приёмка при действующем boundary gate требует v2, включая
единственного автора: его handoff явно подтверждает весь delta теми же evidence.
Изменение candidate или декларации требует нового hash и приёмки;
закрытые/recovered identities автоматически не мигрируют.

Для compact analysis достаточно `task_kind: analysis`, реального root UUID,
пустых `author_thread_ids`, `result_files`, `subagents`, `role_lanes`,
`behavioral_checks` и значений `null` у ссылок и проверяющих. Оба флага
`subagents_used` и `role_lanes_used` имеют значение `false`; final перечисляет
`Subagents Used: no`, `Role Lanes Used: no`, `Subagent Lanes: none`,
`Role Lanes: none`, `Subagent Trace Evidence: none`. Это минимальный вариант без
делегирования. При read-only назначениях сохраняйте их настоящие source traces;
mapped implementation/integration lanes и явные product stages или продуктовые
пути противоречат классификации analysis.

### Запись и собственный итог проверяющего

`AF_PACKAGE` ниже обозначает фактически загруженный каталог skill, `AF_PROJECT` -
рабочий проект, `AF_RUN` - путь, возвращённый init. Для изолированной проверки
используйте временную копию пакета и передайте её точный путь всем участникам.

```sh
python3 "$AF_PACKAGE/scripts/init-run.py" \
  --repo "$AF_PROJECT" --slug completion-check --mode compact \
  --summary "Проверить согласованный сценарий задачи" \
  --plan-summary "Выполнить задачу, запустить проверки и получить независимую приёмку"
```

`init-run.py` создаёт штатный summary версии 1 даже без `--with-lanes`.
Не собирайте summary вручную. `--reuse` сохраняет заполненные данные и формат;
ошибка существующего summary требует явного исправления, не перезаписи init.

Передайте границы задачи через `init-run.py --summary`, план через
`--plan-summary`. Код создаёт `run.md` с `Task Scope`, `plan.md` с `Execution Plan`
и `context.md` с `Initial Worktree Snapshot` по настоящему git status, command и
exit code. Ручные заголовки не нужны. Сохраните отдельный входной объект
verification, взяв его из созданного summary. Установите реальный root UUID
из среды в `root_thread_id`, ссылки `initial_snapshot` и `task_scope`.
До prepare для intake оставьте `task_kind: null`, `qa: null`,
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
До команды init должен получить непустой смысловой текст границ задачи.

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir "$AF_RUN" --role orchestrator --execution-mode role-lane \
  --stage verification --status active --summary "Root UUID и границы записаны до делегирования" \
  --verification-json "$AF_VERIFICATION"
```

Recorder заполняет отсутствующие SHA-256 ссылок и проверяет имеющиеся. Частичный
объект не вычисляет result hash и не разрешает положительный итог или admission
при неизвестном task_kind. Для нового change сначала подготовьте workspace:

```sh
python3 -B "$AF_PACKAGE/scripts/task-workspace.py" --run-dir "$AF_RUN" prepare \
  --source "$AF_PROJECT" --operation-id "$AF_PREPARE_ID"
```

В текущем verification установите `task_kind: change`, сохранив root и исходные
scope/snapshot ссылки, и передайте его той же recorder-командой. Это initial
verification до кода: будущие seal, candidate, boundary и product hash не нужны.
До prepare active change verification отклоняется с причиной
`prepare workspace before initial change verification`. При read-only analysis
явно установите `analysis`; workspace для него не нужен.

Когда архитектурный gate применяется, зарегистрируйте и примите один
source-bound `architecture-outcome` до предварительного readiness QA и worker.
Writer создаёт brief/contract из одного результата с явным Decision:
[Architecture Artifact Authoring](architecture-artifact-authoring.md#один-архитектурный-outcome).

### Предварительный QA требований и плана

Для новых или существенно изменённых требований/плана назначьте независимый
QA до разработки. До его собственного reviewing turn закрепите полный набор
входов через существующий recorder:

```sh
python3 -B "$AF_PACKAGE/scripts/record-agent-trace.py" --run-dir "$AF_RUN" \
  --prepare-review-input --lane-id "$AF_REQUIREMENTS_QA_LANE" \
  --review-subject requirements-plan --review-source run.md \
  --review-source plan.md --review-source "$AF_REQUIREMENTS_SOURCE" \
  --operation-id "$AF_REVIEW_INPUT_ID"
```

Повторите `--review-source` для каждого объявленного constraint/config source,
scope/snapshot документа и опубликованного architecture path. `plan.md`
передавайте, если он существует. При одном brief/contract path передавайте его
один раз. Writer требует полный известный declared set, отвергает дубликаты,
одиночные symlinks и пути через symlink. Для context-only источника используйте
объявленный канонический context path; недекларированный внешний путь не допустим.

Python сохраняет полные bytes в `artifacts/source-captures/` и возвращает native
receipt с `review_subject`, captures, `frozen_at` и delegation input. Digest —
SHA256 от prefix `agentflow:requirements-plan:v1\n` и canonical JSON sorted
manifest `(logical key, full-byte SHA256)`; его вычисляет код. Logical keys имеют
вид `journal:<key>`, `source:<scoped relative path>` или
`context:<slot>/<root-name>[/<member>]`. Для внешнего context root `slot` —
его индекс с нуля в объявленном списке `context_roots`, `root-name` — имя файла
или каталога. Каталог даёт ключи только для regular-file members его manifest;
передайте каждый такой файл через `--review-source`. При пропуске writer
перечислит недостающие logical keys. Два файла `AGENTS.md` в разных slots имеют
разные ключи. Порядок roots, их имена и member paths входят в logical identity;
ordinal key не является постоянным alias. Внутренние contexts сохраняют прежние
`source:` keys, а journal/product paths имеют прежний приоритет. Namespace
`context:` не требует специальной папки в продукте и не разрешает менять context
files. Current reader использует действующие declarations; старый capture
locator не даёт доступа к новому пути. Правила изменения workspace scope не меняются.
Physical roots, location
capture, `st_dev`/`st_ino` не входят в digest: перенос тех же источников сохраняет
subject. Изменение logical path/set или bytes требует нового current review.
Receipt сам по себе не создаёт assignment и не доказывает review.

Передайте receipt проверяющему до dispatch reviewing turn. У уже существующей
QA-сессии допустим новый собственный reviewing turn после pin. После настоящего
запуска свяжите назначение с receipt:

```sh
python3 -B "$AF_PACKAGE/scripts/record-agent-trace.py" --run-dir "$AF_RUN" \
  --role qa-verifier --lane-id "$AF_REQUIREMENTS_QA_LANE" \
  --codex-thread-id "$AF_REQUIREMENTS_QA_ID" --stage spawned --status active \
  --review-input-operation-id "$AF_REVIEW_INPUT_ID" \
  --summary "QA проверяет закреплённые требования и план"
```

QA проверяет полноту требований, проверяемость и применимость обязательных
local checks/permissions. Настоящие команды выполняет `journal.py record
--kind check`: request содержит строковые summary/status/cwd и `command` как
массив argv; пишущая проверка использует disposable. Receipt возвращает paths
native report/output с реальными command/cwd/exit и SHA. Применимый approval
подтверждает разрешение конкретного запуска; execution receipt — его выполнение.

Для применимых selected readiness gates QA передаёт массив observations с ровно
`axis`, `facet`, `check`, `readiness`, `evidence`, `notes` через
`--readiness-gates-file`. Оси — `risk_gates`/`verification_gates`, facet выбран
на этой оси, readiness — `ready`/`needs-approval`/`blocked`. `check` и `notes` —
непустые содержательные строки; evidence — непустой массив references.
Обычная ссылка `{path, sha256?}` указывает опубликованное доказательство;
Python добавляет SHA. Для `ready` нужен применимый успешный native command proof
или поддержанный source-bound tool result вместе с содержательным QA.
Форма и примеры: `journal.py contract`.

Tool locator передаётся тем же `--prepare-conclusion --readiness-gates-file`:

```json
{
  "path": "artifacts/readiness-tool.output",
  "source_event": 17,
  "source_call_id": "<observed call_id>",
  "source_part": 2
}
```

Это пример формы, не доказательство запуска. `path` — логический путь создаваемого
attachment внутри `artifacts/`; `source_event` — положительное целое число,
номер строки исходного JSONL
собственной сессии начиная с 1. `source_call_id` необязателен на входе как assertion;
Python выводит реальный call_id из source и всегда сохраняет его с SHA в ссылке
заключения. `source_part` — необязательное целое число ≥ 0, номер точного public
text block с нуля в `custom_tool_call_output`; у строкового `function_call_output`
part запрещён. Native source output допускается как string `function_call_output`
или полностью доступные public text parts `input_text`/`output_text` с `text: string`
в `custom_tool_call_output`.
Без part используется весь наблюдаемый текст. Необязательный `sha256` — assertion,
не обязанность модели вычислять хеш. Другие locator fields запрещены.

Python проверяет собственный reviewing turn после pin, source event/call_id,
доступность всех public parts и точные bytes, затем атомарно сохраняет output
и SHA с подготовленным conclusion. При actual acceptance event должен находиться
строго между own `task_started` и `task_complete` этого reviewing turn.
Не собирайте JSON журнала вручную и не
повторяйте выполненную проверку ради квитанции. Исходный выбранный text целиком
должен быть одним JSON object CallToolResult: `content` — непустой массив
`{type: "text", text: nonempty string}`; optional `isError`
— boolean, optional `structuredContent` и `_meta` — objects. Иные protocol keys,
media или process/message wrappers не принимаются. `isError: true`, неверный тип,
недоступный/частичный output и произвольный success marker не дают `ready`.
SHA относится к исходному text, не к пересериализованному JSON. Substring,
JSONPath, удаление headers и поиск «успешной части» не поддержаны.

Source подтверждает наблюдаемый внешний tool result, QA — его смысл и применимость
к pinned check. Output `functions.exec` не доказывает независимое происхождение
каждого вложенного вызова. Один общий verifier применяется при подготовке conclusion,
принятии реального QA completion и worker admission. Отрицательные observations
сохраняйте как обычное evidence; последующее настоящее успешное observation может
подтвердить recovery. Tool proof не заменяет approval или final product QA;
current pin, env/blockers и последние применимые command failures сохраняются.

QA получает заключение штатным `--prepare-conclusion` со своим lane, status,
summary и artifact paths; для gate observations добавляет `--readiness-gates-file`.
Он возвращает точный stdout своим финальным ответом. Root регистрирует реальное
завершение той же source-командой, что и final QA ниже. Writer проверяет role,
model, parent, собственный reviewing turn после pin, точный subject/operation,
handoff/evidence и независимость от root и известных авторов входов.

У preliminary `review_subject.kind: requirements-plan`, `reviewed_result_hash`
отсутствует или `null`. Он не выбирает и не очищает `verification.qa/reviewer`
и не принимает продукт. Перед зависимым worker код проверяет текущие полные
входы и атомарно выводит readiness JSON/Markdown/config из принятого QA и
применимых native фактов. Команды ради оформления повторно не исполняются;
настоящий failed/blocked gate и неприменимый approval сохраняют blocker.
При drift повторяется затронутый review; прежний outcome остаётся проверяемым
по сохранённым captures. Unrelated product writes не требуют нового review.

Для устаревшего readiness completion используйте новое QA назначение с новым
pin и собственным reviewing turn, прежним `--obligation-id` и принятым current
conclusion, затем штатный [resolve-obligation](#текущие-обязанности-и-прежние-отказы).
`verification_readiness.lanes` остаётся списком configured QA duties.
Readiness consumer выводит проверенный endpoint той же обязанности из existing
resolution links с `obligation.state: current`. История попыток не расширяет
этот selector: QA другой duty не разрешает admission и не блокирует выбранную
QA своими timestamps/status. Configured, но незарегистрированное QA назначение
даёт обычный atomic readiness refusal с диагностикой. Чужая duty, missing/cyclic,
negative/unresolved или stale endpoint не заменяет выбранную готовность.
Accepted gate references при historical чтении (`current=False`) проверяются
по сохранённому snapshot с исходной proof binding. При `current=True` изменение
текущих bytes по-прежнему отклоняется; история не подменяет current proof.
Не принимайте прежний completion заново и не копируйте его tool output как
новое выполнение. Прежние применимые command receipts можно использовать по
действующему current контракту, без повторного запуска ради формы.

### Финальные QA и reviewer продукта

Перед QA подготовьте окончательные статусы и checklist связанных документов
каждого репозитория по `definition-of-done.md`. Включите исходный план и все
документы поставки в список; приёмка охватывает полные bytes со статусами.
Возьмите текущий verification из summary, установите `task_kind: change`,
полные `author_thread_ids`. Выполните `seal`, опубликуйте boundary v2 декларации
всех авторов; recorder заполнит `result_files`
из полного результата. Не ведите второй список changed paths вручную. Сохраните текущие behavioral_checks
и ссылки. Через recorder передайте объект строкой JSON или путём к локальному
файлу. Команда вычисляет хеш всего сохранённого кандидата вместе со снимком и границами.
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

Каждый проверяющий получает целый JSON-объект через `--prepare-conclusion` и
публикует stdout без пересоздания полей. Пример вызова QA:

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" --run-dir "$RUN_DIR" --prepare-conclusion \
  --role qa-verifier --lane-id qa --status pass \
  --summary "Проверки подтвердили согласованные критерии" \
  --artifact handoffs/qa.md --artifact checks/qa.md
```

Reviewer передаёт свою роль, назначение и файлы; для положительного итога
инструмент добавляет хеш принятого QA handoff. Поля, типы и допустимые статусы
получите из `journal.py contract`. Переданный `--summary` создаёт handoff и ссылки
с SHA; помощник выводит собственный итог для завершения хода. Подготовка handoff
не пишет timeline, принятие или завершение сессии. Режимы `--prepare-conclusion`
и `--render-final` несовместимы с регистрацией события. Пользователь JSON не готовит.

Для отрицательного итога обязательны `--reason` и хотя бы один `--unverified`;
последний повторяется для разных непроверенных пунктов. Например, reviewer
может зафиксировать недоступную проверку до принятия QA:

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" --run-dir "$AF_RUN" \
  --prepare-conclusion --role reviewer --lane-id reviewer-negative --status blocked \
  --summary "Проверка среды заблокирована, доказательство сохранено" \
  --reason "Проверочная среда недоступна; причина зафиксирована в evidence" \
  --unverified "Полный workflow candidate" --unverified "Независимое QA" \
  --artifact handoffs/reviewer-negative.md --artifact checks/review-failure.md
```

Назначение уже должно быть зарегистрировано, evidence — опубликовано.
Помощник создаёт handoff из `--summary`, добавляет пути и SHA evidence,
`reason` и `unverified` в итоговый
JSON. После настоящего завершения root регистрирует тот же исход через обычный
recorder: `--stage blocked --status blocked` либо `--stage fail --status fail`,
со ссылками на тот же handoff/evidence и исходный completion turn. Для canonical
agent path поддерживается `--resolve-session --agent-path`. Причина и непроверенное
берутся из собственного итога; prepare-only flags в обычную регистрацию не передаются.

Каждый проверяющий завершает собственный ход полученным JSON-объектом. Поддержка
последнего fenced блока `json` сохраняется; проза перед необрамлённым JSON
не принимается. Подробный отчёт остаётся в handoff:

Передайте инструменту пути использованных доказательств: writer добавит их
SHA-256 в handoff. Validator сверяет их с исходным итоговым ходом.
Правка checksum только в summary не принимает изменённое доказательство.

```json
{
  "verdict": "passed",
  "review_subject": {"kind": "product-result"},
  "reviewed_result_hash": "<текущий result_hash>",
  "handoff": "handoffs/qa.md",
  "handoff_sha256": "<SHA-256 handoff>"
}
```

Для положительного итога reviewer указывает свой handoff и добавляет
`qa_handoff_sha256` с хешем прочитанного принятого QA handoff.
Допустимые положительные JSON-вердикты: `passed`,
`pass-with-risks`. Для отрицательного итога используйте `fail` или `blocked`.
Root не может заменить отрицательный ответ положительной записью summary.

Новые native final QA/reviewer получают `review_subject: {kind: "product-result"}`
автоматически. Истинно untagged legacy conclusions продолжают читаться по прежнему
продуктовому контракту; неизвестный, `null` или malformed tag не является legacy.
Omission после binding не меняет предмет проверки. Product acceptance и audit
используют один subject predicate, preliminary outcome не выбирается как final.

Тесты AgentFlow проверяют lifecycle, writer и доказательства; tests разрабатываемого
продукта проверяют его поведение. В отчёте назовите предмет каждой проверки.

Отрицательное заключение связывается с настоящим завершённым ходом, handoff
и доказательствами конкретной причины. Если запечатанного результата нет,
`reviewed_result_hash` может быть `null`. Отрицательный reviewer может завершиться
без принятого QA. Такие заключения сохраняются как отрицательные записи и не
становятся выбранным принятием `verification.qa` или `verification.reviewer`.

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
со ссылками и хешами. При положительной приёмке указатель `verification.qa`
или `verification.reviewer` обновляется автоматически. Данные проверяются до записи. Timeline дополняется,
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
Целые Unix-секунды сохраняются как интервал; ISO-строка с зоной, включая строку
без дробной части, остаётся точкой времени. Внешняя публикация хранится отдельно
и может перейти в следующую секунду. Она не уточняет время исполнения.
Неверное присутствующее внутреннее или внешнее поле отклоняется. При отсутствии
внутреннего поля используется внешнее с сохранением происхождения. Если обе
публикации QA/reviewer точные, reviewer должен быть опубликован строго позже;
иначе применяется консервативное сравнение интервалов событий.

Agent Flow пишет собственное время в UTC с шестью знаками долей секунды и `Z`.
Технический повтор прежнего пригодного заключения повторно проверяет источник
и хеши, сохраняет все служебные файлы и принятие reviewer без нового хода модели.
Диагностическое чтение старого журнала и `--reuse` не выполняют скрытую миграцию.
Явные import и upgrade сохраняют доступные исторические байты; отсутствующие поля
или доказательства не становятся подтверждёнными. После восстановления требуется
свежая независимая приёмка. Полный scanner delta из seal
используется только для новой workspace-приёмки.

Reader реализует наблюдённую локальную структуру событий Codex Desktop; другие
клиентские структуры не поддерживаются и блокируют приёмку. Это клиентские
операционные доказательства, не серверная аттестация. Боевые инструменты не
принимают источник тестовых сессий через аргументы или environment.

После исправления обновите редакцию, выполните затронутые QA-проверки и получите
новый итог reviewer по текущему результату и QA handoff. Можно использовать новые
ходы тех же независимых сессий. Полный повтор всех tests и повышение reasoning
не являются автоматическими условиями. Даже без изменения продукта правка QA
handoff требует нового `qa_handoff_sha256` в собственном ходе reviewer.

`--allow-pending` допускает незавершённые данные только при незавершённом verdict
и выводит `PRELIMINARY (not final acceptance)`. Повреждённые и противоречивые данные
отклоняются. Ни этот флаг, ни `--allow-no-check` не ослабляют положительный final.
Для `blocked` и `fail` сохраняются причина в `verification.blocker` и имеющиеся
записи; успешный запуск проверяющих не требуется. Запишите непроверенное в
`verification.unverified` и final вместе с незакрытыми критериями.
`gap` допускает неполное покрытие без успешного владельца
или reviewer и без вымышленных положительных доказательств; заявленный `supported`
по-прежнему требует подтверждения. Все указанные доказательства проверяются
на целостность, структура и история сохраняются. Отрицательный итог можно
штатно закрыть до prepare/QA либо после частичных правок; delivery запрещена.
Для `ship/pass-with-risks` нужны текущий запечатанный candidate, полная дельта,
авторство, независимые QA и последующий reviewer того же результата, подтверждённые
критерии и действующие risk gates. Отрицательный путь эти требования не заменяет.

До seal `result_files=[]` и `result_hash=null` означают отсутствие принятого
результата, а не отсутствие рабочих правок. Наблюдаемые изменения описывайте
отдельно. Если workspace неполон или его нельзя безопасно просканировать целиком,
допустимо честное `blocked/fail` с конкретной причиной и перечнем непроверенного.
Повреждённая рабочая ссылка может быть такой причиной; закрытие отрицательного
отчёта не требует фиктивной полной дельты. Для seal и положительной приёмки
полные manifest и delta обязательны.

### Приёмка контекстных исключений

Для положительного `change` в workspace version 2 с непустым
`task_scope.context_exclusions` собственные итоговые JSON QA и reviewer содержат
`context_classification`. Без исключений поле не требуется; `blocked/fail`
допускает незакрытую проверку классификации.

QA указывает digest текущего запечатанного scope, полный набор путей исключённых aliases
и ссылки на фактический вывод релевантного workflow:

```json
{
  "context_classification": {
    "task_scope_digest": "<current sealed task_scope_digest>",
    "context_exclusions": ["<exclusion.relative_path>"],
    "workflow_evidence": [
      {
        "path": "checks/candidate-workflow.txt",
        "sha256": "<SHA-256 output bytes>",
        "source_event": 17,
        "source_call_id": "<observed QA tool call ID>"
      }
    ]
  }
}
```

Это поле дополняет обычные поля заключения. Reviewer указывает тот же digest
и полный набор `context_exclusions`; повторный запуск workflow reviewer не нужен.
Оба массива exclusions содержат уникальные строки и точно совпадают с текущим
`exclusions.relative_path`, включая раскладку новой revision. Поле читается из
настоящего итогового хода, ручная запись summary его не заменяет.

Передайте объект classification помощнику через `--context-classification-json`
как inline JSON или путь к личному capture-файлу. Он включит объект в stdout;
завершите ход точным выводом помощника без пересоздания остальных полей.
Например, для QA с уже подготовленным capture:

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" --run-dir "$AF_RUN" \
  --prepare-conclusion --role qa-verifier --lane-id qa-final --status pass \
  --artifact handoffs/qa.md --artifact checks/candidate-workflow.txt \
  --context-classification-json "$AF_CLASSIFICATION_CAPTURE"
```

Reviewer использует свою роль, lane и artifacts; его classification содержит
digest и exclusions без обязательного повторного `workflow_evidence`.

Каждый QA workflow reference входит в evidence принятого QA и его handoff.
`source_event` указывает реальный tool output собственного принятого хода QA
между его запуском и завершением; `source_call_id` и хеш соответствуют фактическим
байтам вывода. Файл без такого исходного output или заявление «всё работает»
недостаточны. Один релевантный workflow может охватывать несколько aliases.
QA объясняет охват и проверяет candidate без исключённых файлов. Reviewer
независимо оценивает достаточность workflow, полноту снимка, классификацию всех
aliases и отсутствие доступа к живым внешним файлам.

Если приложение, сборка или тест требует исключённый alias, включите зависимость
в продуктовые корни, создайте новую revision и повторите приёмку. Недостаточное
доказательство остаётся `gap`. Для этой проверки не нужны пользовательские inputs,
`strict_inputs` или `behavior-input-prepared`; отдельно выбранные behavioral checks
сохраняют свои требования.

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

### Текущие обязанности и прежние отказы

Recorder сохраняет у назначения `obligation`: идентификатор обязанности `id`,
обязательность `required` и состояние `state`. При создании `id` по умолчанию равен
`lane-id`; если назначение заменяет прежнее, передайте ему тот же
`--obligation-id`. Это не меняет UUID, роль или выводы исходного назначения.

После принятия замены явно разрешите прежнюю обязанность:

```sh
python3 "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" resolve-obligation \
  --lane-id "$AF_OLD_LANE" --replacement "$AF_NEW_LANE" \
  --reason "$AF_RESOLUTION_REASON" --expected-revision "$AF_REVISION" \
  --operation-id "$AF_RESOLUTION_OPERATION"
```

Команда связывает прежний отказ с конкретным handoff замены и его хешем.
Старые status, trace, handoff, UUID и timestamps сохраняются. Полная проверка
требует принятия всех текущих локальных обязательных назначений, а не только выбранной пары
QA/reviewer. Объявленный external follow-up сохраняет непринятую обязанность.
Активная локальная работа, отсутствующая или непринятая замена, цикл ссылок,
непокрытая обязанность и изменённые доказательства блокируют положительный итог.
Сообщение `done` не означает принятия. Произвольная пометка `historical` не снимает
обязанность; необязательность консультации требует подтверждённого основания.

Для blocked/fail общий evaluator проверяет исторические resolved/exempt отдельно
от успешности текущих обязанностей. Original assignment/outcome, immutable archive
при наличии, obligation ID, reason, evidence hashes и отсутствие циклов обязательны.
Replacement должен быть принят и выполнять ту же обязанность даже при отрицательном
финале. Coverage исключает только проверенные исторические resolutions; прежние
summary и отрицательные events сохраняются. Голый status=replaced не даёт исключения.
Текущая непринятая обязанность остаётся причиной отрицательного финала, не resolved.
Отрицательный финал не принимает QA/reviewer пару и не разрешает delivery;
failed finalize не пишет успешный final event.

### Восстановление прежнего журнала

Восстановление продолжает ту же задачу и тот же сохранённый результат. Оно не
исправляет исходники, не переносит приёмку на другой C5 и не удаляет историю.
Сначала сохраните доступный канонический снимок, проверьте его хеши и отработайте
процедуру на отдельной копии. Остановите писателей исходного журнала.

```sh
python3 "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" diagnose-recovery \
  > "$AF_RECOVERY_DIAGNOSIS"
```

Диагностика ничего не меняет. Сохраните значение `identity` из её JSON в отдельный
файл `AF_RECOVERY_IDENTITY`. Он содержит ожидаемые UUID, исходные пути, корневую
задачу, контракт поведенческих проверок, workspace и хеш результата. Команда
сверяет эти значения с журналом и сохранённым workspace; файл не задаёт новую
идентичность. Редакцию, поколение и ссылку на final тоже берите из диагностики.

Для открытого version 1:

```sh
python3 "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" upgrade \
  --expected-run-uuid "$AF_RUN_UUID" --expected-revision "$AF_REVISION" \
  --expected-generation "$AF_GENERATION" --operation-id "$AF_UPGRADE_OPERATION" \
  --identity-file "$AF_RECOVERY_IDENTITY" --reason "$AF_RECOVERY_REASON"
```

Для закрытого version 1 разрешена только единая операция `reopen --upgrade`.
Отдельный upgrade не должен скрывать прежнее закрытие. `final.index` — номер
события с единицы; `final.sha256` — хеш текущего `final.md`.

```sh
python3 "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" reopen --upgrade \
  --expected-run-uuid "$AF_RUN_UUID" --expected-revision "$AF_REVISION" \
  --expected-generation "$AF_GENERATION" --operation-id "$AF_REOPEN_OPERATION" \
  --identity-file "$AF_RECOVERY_IDENTITY" --reason "$AF_RECOVERY_REASON" \
  --final-index "$AF_FINAL_INDEX" --final-sha256 "$AF_FINAL_SHA256"
```

Для отрицательного закрытия version 2 используйте ту же команду без `--upgrade`.
Узкое исключение для version 1 допускает прежнее положительное событие только
при текущем отрицательном final и сохранённом отказе полной проверки именно этой
попытки. Передайте логический путь этого доказательства через
`--failed-validation-path`; произвольный JSON с ненулевым exit code не подходит.
Положительное закрытие version 2 и успешная delivery запрещают reopen.
Противоречивые сведения о delivery требуют диагностики до любых изменений.

Операция архивирует доступные байты документов и квитанций, их происхождение и
идентичность. Она не восстанавливает выдуманный старый снимок. Прежний timeline
остаётся точным префиксом; новые события идут после него. Архив не содержит
рекурсивных копий других архивов. ID и хеш архива возвращаются в квитанции.

```sh
python3 "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" read timeline.jsonl \
  --archive "$AF_ARCHIVE_ID"
python3 "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" export \
  --archive "$AF_ARCHIVE_ID"
```

После upgrade явно разберите все прежние назначения через `classify-legacy`.
Подготовьте JSON с `archive_id` из квитанции и массивом `assignments`, который
охватывает весь исходный inventory. В обычной записи укажите `lane_id`,
`obligation_id`, `reason` и `scope`: объект с `path`, `sha256` и точной `quote`
из исходного архива. `obligation_id` обозначает конкретную обязанность; одинаковый
ID допустим только у назначений, которые действительно выполняют ту же работу.

| Поле записи | Что оно подтверждает |
| --- | --- |
| `outcome` | Прежний исход: `unresolved`, `pass`, `pass-with-risks`, `blocked` или `fail`. При отсутствии подтверждения используйте `unresolved`. |
| `handoff` | Исходный отчёт в архиве: `path`, `sha256`, точная `quote`. Отчёт должен быть связан с этим назначением. |
| `source` | Собственное завершение субагента: настоящие `thread_id`, `turn_id` и совпадающая `quote`. Оно требуется для подтверждённого исхода субагента. |
| `optional` | Необязательная консультация, подтверждённая первоначальным поручением. Это не способ исключить нужную работу. |
| `planned: true` | Исходное запланированное, но ещё не запущенное назначение. Оно остаётся обязательным до штатного выполнения. |
| `correction` | Отдельная запись ошибочной регистрации: `lane_id`, `reason`, `correction` с `path`, `sha256`, `quote` исходного исправления. Настоящее назначение так исключить нельзя. |

```sh
python3 "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" classify-legacy \
  --classification-file "$AF_CLASSIFICATION" --expected-revision "$AF_REVISION" \
  --operation-id "$AF_CLASSIFICATION_OPERATION"
```

Перед вызовом заново прочитайте редакцию. Подтверждённую классификацию нельзя
переписать другой командой. Отсутствующий статус и `done` не означают pass. Классификация требует исходного
поручения, handoff и подтверждённого источника заключения; консультация может
стать необязательной только по своему первоначальному назначению. Неясные
обязанности остаются открытыми до нового принятого назначения. Архив хранит
прежние значения без изменений. Затем получите свежие QA и reviewer из исходной
корневой задачи на том же сохранённом результате, сохранив все выбранные поведенческие критерии и
`strict_inputs`. Полный validate, finalize и delivery обязательны: reopen сам
по себе не означает приёмку или завершение задачи.

### Ошибочный режим служебной записи

Для служебных записей root всегда указывайте `--execution-mode role-lane`.
По умолчанию recorder выбирает `subagent`; такой вызов требует подтверждённой
идентичности субагента. Пропущенный UUID не означает работу root и не должен
автоматически превращаться в `role-lane`.

Если прежний recorder уже записал служебное событие root с ошибочным режимом,
используйте отдельную ветку только для доказанной поправки этого поля:

```sh
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir "$AF_RUN" --correction-file "$AF_MODE_CORRECTION" \
  --operation-id "$AF_CORRECTION_OPERATION"
```

Файл поправки связывает UUID журнала и root, текущее поколение и редакцию,
причину и operation ID. Для timeline и trace задаются путь, размер исходного
префикса в байтах и SHA-256. Каждый target содержит два индекса строк с единицы,
SHA-256 исходной строки с завершающим LF, старый и новый `execution_mode`,
сохранённый исходный request и ссылки на реальные вызовы root и их ответы.
Для source-ссылок также нужны точные индексы и SHA-256 строк с LF. Команда
проверяет всю связь с канонической квитанцией; копия команды в тексте отчёта
не доказывает её выполнение.

Допускаются только служебные `verification-prepared` и `verification-ready`
события root без признаков назначения: UUID субагента, lane, handoff или
артефактов. Единственная поправка — `subagent` в `role-lane`. Неподдерживаемый
формат команды, отсутствующий источник или неоднозначная связь оставляют
отказ. Нельзя исправлять таким способом реальный отказ субагента или добавлять
фиктивный spawn для прохождения проверки.

Операция добавляет запись аудита и доказательство; прежние строки, timestamps,
статусы и индексы остаются неизменными. Новый запрос к закрытому журналу и
повторная поправка того же target запрещены. Технический повтор с прежними
operation ID и содержимым возвращает сохранённую квитанцию без нового события.
После поправки выполните полную проверку и штатное завершение. Повторный запуск
модели ради оформления не нужен, если результат и доказательства прежней
независимой приёмки остаются пригодными.

### Финальная команда и завершение задачи

Подготовьте содержательный итог и актуальные независимые заключения.
Writer формирует `final.md` и служебные разделы. Финальное событие
создаёт `journal.py finalize`: для положительного изменения файлов проверка
продукта оценивает предполагаемый снимок
до атомарного закрытия текущего поколения. При отказе событие не добавляется,
журнал остаётся открытым, задача — `Status: in_progress`.

Возьмите UUID, редакцию и поколение
из текущего снимка журнала; operation ID сохраните до вызова. Не угадывайте эти
значения и не создавайте новый ID для технического повтора той же операции.

```sh
python3 -B "$AF_PACKAGE/scripts/journal.py" --run-dir "$AF_RUN" finalize \
  --expected-run-uuid "$AF_RUN_UUID" \
  --expected-revision "$AF_REVISION" \
  --expected-generation "$AF_GENERATION" \
  --operation-id "$AF_FINALIZE_OPERATION" \
  --summary "$AF_FINAL_SUMMARY" --status pass
```

Новый final нельзя передать готовыми bytes через `--final-file`: используйте
`--summary`. Старый запрос с готовым final допустим только для точного повтора
уже сохранённой квитанции; его bytes и operation ID остаются прежними.

Не используйте `append-timeline.py --stage final` или запись final через publish
для закрытия. Успех finalize фиксирует final, событие, проверенные входы и квитанцию
одной транзакцией. Та же операция с теми же байтами возвращает прежнюю квитанцию;
другое содержимое с тем же ID отклоняется. Квитанция не подтверждает, что внешние
сессии или файлы остались неизменными после проверки.

Перед выдачей результата выполните полный аудит и сохраните его замечания.
Свежую проверку продукта выполняет штатная `delivery`. `AF_VALIDATION_LOG` должен
указывать на файл вне канонических документов журнала и проверяемого результата.

```sh
python3 "$AF_PACKAGE/scripts/validate-run.py" --run-dir "$AF_RUN" \
  > "$AF_VALIDATION_LOG" 2>&1
AF_VALIDATION_EXIT=$?
printf '\nexit_code: %s\n' "$AF_VALIDATION_EXIT" >> "$AF_VALIDATION_LOG"
cat "$AF_VALIDATION_LOG"
```

Запускайте пример в shell без `errexit`, чтобы сохранить код отказа. Не используйте
`--allow-pending` или `--allow-no-check`. Закрытый журнал не принимает изменения
документов, trace, summary или workspace; штатная delivery отдельно проверяет
тот же сохранённый результат. Чтение, export и точные технические повторы разрешены.
Диагностический лог не служит разрешением на следующее завершение.
Ненулевой код полного аудита требует разобрать причину: повреждённый результат
или обязательное доказательство блокирует delivery; отдельный недостаток
оформления сохраняется в отчёте аудита и сам по себе её не запрещает.

При отказе до закрытия исправьте доступную ошибку и повторите finalize на свежих
входах. Недоступное обязательное доказательство оставляет конкретный
`verification.blocker` и отрицательный итог. Исторический отказ сохраняйте; его
обязанность закрывает явная ссылка на принятое актуальное назначение с подходящими
доказательствами. Пометки `historical` или `done` сами по себе не означают принятия.

После закрытия не исправляйте историю вручную. Для допустимого закрытого отказа
используйте штатное восстановление; для нового продуктового результата нужен
отдельный run. Только успешная свежая проверка вместе с выполненными критериями
и требуемой delivery разрешает `Status: done` и положительный ответ пользователю.
Успешная проверка отчёта `blocked` или `fail` не означает выполнения задачи.

### Последующая приёмка V9

Эти сценарии требуют разрешённых реальных дочерних сессий и выполняются отдельно
от реализации. Используйте свежие временные проекты и копию проверяемого пакета;
всем участникам передайте точный путь копии. Глобальные конфигурации не меняйте.

1. В обычном Desktop создайте compact-задачу: добавить синтетический PRD из трёх
   требований в `docs/example.md`. Зафиксируйте исходный снимок и границы. Запустите
   настоящие QA/Astra и reviewer/Sol штатными средствами Desktop, передайте один
   `result_hash`, запишите два собственных завершённых хода и выполните
   `python3 skills/agent-flow/scripts/validate-run.py --run-dir "$AF_RUN" --mode compact`.
2. В обычном Desktop создайте full-задачу: исправить в синтетической Python-функции
   границу сравнения, сначала получить падающий тест, затем успешный. Выполните
   те же два назначения штатными средствами Desktop и выполните
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
- `init-run.py --architecture-gate` создаёт planned lanes и контекст; Architecture Design Brief, contract, handoffs и proof records появляются из содержательных записей writer по мере выполнения работы;
- init не создаёт `TODO(agent):` proof rows или фиктивные approvals; неизвестные данные остаются незакрытыми, а required gates блокируют начало работы и успешный final;
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
- for `supported`, `owner_lane` must be a successful QA or review lane, `reviewed_by` must be a successful review lane, the owner handoff section must mention the claim id, and every marker must appear in the referenced evidence file;
- positive verdicts require `status=supported` for local claims; an explicitly scoped external claim may remain `gap`, with its structure and supplied evidence checked;
- a negative `gap` may lack successful owner/reviewer and complete evidence coverage; every supplied lane, reference and marker still undergoes structural and integrity checks;
- positive architecture-gated runs require Acceptance Criteria Traceability Gate: the architecture handoff `QA Gates` and `Reviewer Checklist` sections must contain `Acceptance Criteria` ids, and the run root must include `acceptance-traceability.json`;
- `acceptance-traceability.json` uses `version=1` and a non-empty `acceptance` array; every acceptance id is unique kebab-case and every required contract acceptance id has a record;
- each acceptance record names `source`, `requirement`, `subjects`, `contract_types`, `status`, `surface_expectations`, and evidence entries with literal `markers`;
- Surface Evidence Gate requires each `surface_expectations` item to name `surface`, `polarity`, and allowed `proof_kinds`; each `evidence` or `negative_fixture_evidence` record names matching `surface`, `polarity`, and `proof_kind`, and only a matching record can cover that expectation;
- storage or other internal evidence cannot satisfy API, UI, logs, history, provider metadata, or external-provider acceptance unless the target `surface` matches exactly;
- positive verdicts require every local acceptance record to use `status=supported`, every supplied marker must appear in its evidence file, every local `surface_expectations` item must have matching evidence, and every required local acceptance id must have evidence; explicitly scoped external gaps remain open;
- Contract Negative Fixture Gate applies to supported acceptance records marked `gate`, `cli`, `query`, `storage`, `config`, or `parser`; those records must include `negative_fixture_evidence` with at least one evidence path and literal marker for a negative or drift fixture, and `negative_fixture_evidence` cannot use `polarity=positive`. A `gap` in a blocked/fail final may leave target coverage incomplete;
- failed, blocked, or timed-out architecture lanes block `ship`;
- reviewer and QA lanes may pass only after the architecture contract passes;
- when `architecture_contract_independent` is true, the architecture lane must use `subagent` execution with spawned trace evidence.

## Verification Readiness Gate

When `architecture_contract_required=true` and worker lanes exist, schema v2
requires Verification Readiness Gate before implementation:

В поддержанном новом пути QA передаёт содержательные gate observations в своём
заключении `requirements-plan`. При worker start Python атомарно оформляет
существующие readiness JSON/Markdown и выбранную конфигурацию из текущего
принятого QA, native command receipts или source-bound supported tool results
и применимых approvals. Отсутствие формы
само по себе не требует нового запуска проверок. Новые failed/blocked facts,
изменённые источники или неприменимое разрешение блокируют зависимое действие.
Approval и execution остаются разными фактами. Следующие поля описывают
проверяемые проекции writer; модель их вручную не синхронизирует.

- `lane-map.json` records `verification_readiness` with `artifact` pointing to
  `verification-readiness.json` and `lanes` listing configured QA duties;
  validated same-duty endpoints come from existing resolution links, while
  displayed attempt history does not expand this selector;
- readiness lanes use `type=qa`, `role=qa-verifier`, `critical=true`, and run
  after the approved Architecture Design Brief but before worker lanes;
- `verification-readiness.json` uses `version=1` and status `ready`,
  `needs-approval`, `paused-blocked`, or `blocked`;
- readiness attempts cover every selected `risk_gates` and
  `verification_gates` facet from `architecture_context`;
- unknown, unselected, duplicate, wrong-axis, or missing readiness facets fail
  validation;
- `ready` requires all local gate records to be ready and no local blockers; explicitly scoped external rows keep their actual status, blockers and evidence;
- `needs-approval` requires pending `approval_requests` and no successful worker
  lanes;
- approval requests list documented safe commands, a source document,
  manual instruction and affected gates through the generated contract;
- решение пользователя передаётся закрытым typed intent с настоящим root user
  source event и точной цитатой; writer привязывает source и hash. Одно слово
  или временная позиция сообщения не заменяют существующее разрешение;
- actual execution берётся из native check receipt, затем readiness проверяется
  повторно перед стартом workers;
- when the user declines, status becomes `paused-blocked`, final verdict must be
  `blocked`, no successful workers may exist, and `final.md` must include the
  manual instruction. `resume_phrase` — закрытое константное поле артефакта со значением «Готово», а не пароль;
- новое `readiness-approval` использует настоящий root user event, точную
  цитату и закрытое решение. Ранее данное применимое разрешение действует:
  новое сообщение или точная фраза не нужны; NLP-проверки нет;
- successful worker lanes must run after the latest `ready` readiness lane;
- the full audit checks the latest readiness status; product acceptance requires actual successful local verification, not the preliminary ceremony alone;
- post-worker QA records `verification_results` in lane-map and a handoff
  section named `Verification Gate Results`;
- a QA lane may pass only when `verification_results.status=pass`; this aggregate
  describes local gates under delivery_scope. Blocked required local verification
  forces QA `blocked`; external follow-up rows retain their actual outcomes.

## Continuation Gate

When a same-run continuation resumes after a blocked checkpoint, do not rewrite
the old order as if the new gate had existed from the start. If `timeline.jsonl`
contains `stage=blocked-checkpoint` or `stage=continuation` and the final verdict
is `ship` or `pass-with-risks`, schema v2 requires `continuation-summary.json`.

Checkpoint создаёт writer после настоящего `finalize --status blocked` и
native `reopen`: semantic event `blocked-checkpoint` с зарегистрированным
root lane копирует последний authoritative blocked archive. Для continuation
передавайте `checkpoint` из receipt. Путь snapshot и SHA вычисляет writer;
raw attachment не заменяет этот receipt. Разрешение blocker отрицательным
статусом не создаёт resolved record.

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
- `scripts/record-lane-boundary.py --run-dir <run-dir> --lane-id <lane-id> --declaration-json <published-path>`
  writes boundary `version=2` for a new registered result: full `changed_paths`,
  explicit `authored_paths`, own handoff/edit evidence references, coauthors,
  assignment identity and workspace/candidate bindings. Version 1 remains
  readable for historical diagnosis and exact accepted replay;
- `boundary.changed_paths_artifact` is the canonical reference to an existing,
  validated proof; a duplicate entry in lane `evidence` is optional;
- all boundary paths and changed paths must be repo-relative POSIX paths:
  no absolute paths, no empty paths, no backslashes, and no `..`;
- glob matching uses Python `fnmatch.fnmatchcase`; every authored path must
  match at least one `allowed_paths` pattern, and any `forbidden_paths` match
  fails even when the path is also allowed;
- empty `changed_paths` is valid because this gate checks boundaries, not
  whether a worker was required to edit product code;
- each declaration remains bound to its retained seal. Registered contributions
  derive the authors; the latest contribution must match each current path state.
  Ordinary parent directories are inferred, while standalone directory changes
  require authorship. Corrections use new assignments; historic proofs are immutable.
  Every contributor needs edit evidence and permission; unknown coauthors fail;
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

With delivery_scope, the outcome requirements below apply to local risk IDs.
Explicit external risk records retain their actual status and supplied evidence,
remain visible in final/report coverage, and are not treated as resolved.
Structure, references and evidence integrity still apply to every record.

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
- local resolution status must be `fixed`, `mitigated`, or `contained` for `pass-with-risks`;
- `unresolved` is allowed for `blocked` or `fail`, and for explicitly scoped
  external follow-up in a local positive final; it is never reported as resolved;
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
- exactly one `timeline.jsonl` final event in the current lifecycle generation when a timeline exists or any agent trace exists; earlier generations remain unchanged;
- the current generation closes with `stage=final` and `role=orchestrator`, created only by `journal.py finalize` after the applicable prospective acceptance check;
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

For full trace, record semantic outcomes after intake, route, plan, delegation, handoff, verification, review, blocker, fix, and final. The writer updates published documents and timeline; do not edit their exports.

Use `scripts/append-timeline.py` for run-level orchestrator events. Use
`scripts/record-agent-trace.py` for subagent events so the same event appears in
the run-level timeline and in `agents/<role>/trace.jsonl`.

Keep timeline events in real workflow order. Do not batch-write route,
implementation, verification, and final at the end with guessed timestamps. If
checks are rerun after a fix, append a new verification/checks event after the
fix. The final timeline should make the actual sequence readable without
opening chat history.

One final orchestrator event per lifecycle generation is required before final
handoff. `journal.py finalize` creates it after the applicable prospective acceptance check;
call the command described in «Финальная команда и завершение задачи» above.
Do not append this event manually. Archived generations retain their original
final events and verdicts.

If a product commit was created, append its event before finalization:

```bash
python3 "$AF_PACKAGE/scripts/append-timeline.py" \
  --run-dir <run-dir> \
  --stage commit \
  --role orchestrator \
  --stable-agent-name orchestrator \
  --stable-agent-slug orchestrator \
  --status pass \
  --summary "Committed product changes as <hash>." \
  --commit-hash <hash> \
  --next-step "prepare final report and finalize"
```

## Per-Agent Trace Events

Every subagent that receives a delegation packet gets a first-class trace path:

```text
agents/<role>/trace.jsonl
```

The helper creates `agents/<role>/` and `artifacts/agents/<role>/` when needed:

```bash
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir <run-dir> \
  --role python-worker \
  --lane-id backend-cli \
  --stage spawned \
  --status active \
  --codex-thread-id <thread-id> \
  --summary "Spawned python-worker for backend-cli." \
  --next-step "handoff"
```

Then record the terminal handoff:

```bash
python3 "$AF_PACKAGE/scripts/record-agent-trace.py" \
  --run-dir <run-dir> \
  --role python-worker \
  --lane-id backend-cli \
  --codex-thread-id <thread-id> \
  --completion-turn-id <own-completed-turn-id> \
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

- Actual spawned subagent: include a `stage=spawned` event with `--codex-thread-id`, then record the source-bound terminal handoff/blocked/fail event. Source metadata supplies mode and stable identity; `--execution-mode subagent` is an optional assertion.
- Role lane without a spawned runtime: record `--execution-mode role-lane`, or keep it as an orchestrator note outside `agents/<role>/`. Its output is a scoped role review, not subagent execution.

`validate-run.py` fails agent traces that look like subagents but have no
spawned event with `codex_thread_id`, no terminal handoff, or a narrative
sidecar/subagent claim without trace evidence. This is intentional: the trace
must distinguish real parallel/delegated execution from role-labeled
orchestration.
