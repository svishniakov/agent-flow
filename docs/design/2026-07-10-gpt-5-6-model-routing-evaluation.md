# Дизайн model eval и runtime policy для GPT-5.6

Статус: согласован

Дата: 2026-07-10

Область: Agent Flow, coding-роли, model/reasoning routing, fallback, gates и skills

## Содержание

- [Контекст](#контекст)
- [Архитектура](#архитектура)
- [Model Policy](#model-policy)
- [Execution Adapter](#execution-adapter)
- [Gate Policy](#gate-policy)
- [Skill Policy](#skill-policy)
- [Eval-корпус](#eval-корпус)
- [Протокол сравнения](#протокол-сравнения)
- [Оценка](#оценка)
- [Порядок внедрения](#порядок-внедрения)

## Контекст

Agent Flow уже хранит `model` и `reasoning_effort` в конфигурации ролей. Текущая
документация предполагает, что эти значения передаются в `spawn_agent`, но доступный
native-интерфейс `spawn_agent` не принимает такие аргументы. Поэтому конфигурация
модели сейчас не гарантирует фактический выбор runtime.

Для управляемых запусков выбран Codex CLI. Локальная установка обновлена с `0.125.0`
до `0.144.1`. Ephemeral read-only smoke прошёл для `gpt-5.6-luna` и
`gpt-5.6-terra` с `reasoning_effort=medium`.

Smoke также показал, что глобальный каталог skills превышает допустимый бюджет
контекста: часть описаний была удалена до вызова модели. Scored-запуски должны
получать минимальный и воспроизводимый набор skills.

Generic harness и coding-first корпус уже собраны. Девять evaluator-пакетов прошли
base/gold сертификацию. Запуски модели выполняются через Codex с авторизацией по
подписке ChatGPT: отдельный API-ключ и API-тарификация не используются.

## Цели

- Сравнить Luna и Terra на реальных coding-задачах из трёх продуктов.
- Выбрать model/reasoning по роли и классу задачи, а не одним правилом для всех ролей.
- Продолжать работу при недоступности выбранной модели.
- Централизовать активацию gates и показывать только gates текущего запуска.
- Отделить влияние модели от влияния skills.

## Что не входит в первую итерацию

- Переназначение моделей для всех 27 ролей по результатам coding-корпуса.
- Выводы по `python-worker` и `ios-worker`: корпус не проверяет эти роли.
- Автоматическая публикация новой routing policy без просмотра результатов.
- API-функции, которые не используются runtime Agent Flow.
- Изменения в исходных рабочих деревьях тестовых проектов.

## Инварианты

1. Недоступность конкретной модели не останавливает обычную работу Agent Flow.
2. Fallback не выдаётся за запрошенную модель и всегда отражается в trace.
3. Scored eval меняет только одну переменную за этап: сначала модель, затем reasoning,
   затем skills.
4. Все участники сравнения получают одинаковый task prompt, role contract, tools,
   skill profile, starting revision и лимиты.
5. Реальные проекты не меняются. Каждый запуск работает в отдельном synthetic
   repository, созданном через `git archive` без общей object database.
6. Критическую ошибку нельзя компенсировать скоростью, стоимостью или средним баллом.

## Архитектура

```text
Task Intake
  -> Task Facts
  -> Model Policy -------> requested model/reasoning
  -> Gate Policy --------> active_gates
  -> Skill Policy -------> selected_skills
  -> Execution Adapter --> CLI lane | native lane
  -> Verification
  -> Scoring and policy recommendation
```

### Task Facts

Оркестратор один раз фиксирует проверяемые факты задачи:

- роль;
- тип запроса и наличие изменений файлов;
- число репозиториев и предполагаемых worker lanes;
- затронутые поверхности: backend, frontend, CLI, docs, storage, runtime;
- public contract, migration, external write и production risk;
- допустимые и запрещённые пути;
- бюджет запуска;
- состояние model fallback.

Task Facts не содержат выбранную модель или список gates. Эти решения принимают
отдельные policy-компоненты.

## Model Policy

### Выбор по роли и классу задачи

Модель выбирается по матрице `role x task class`. Первая версия использует такие
факторы:

- `local`: локальный и хорошо ограниченный фикс;
- `cross-repo`: изменение нескольких репозиториев;
- `public-contract`: API, proto, storage или другой внешний контракт;
- `migration`: изменение схемы или данных;
- `high-risk`: широкий blast radius, production-риск или высокая неопределённость.

Матрица детерминирована. Оркестратор фиксирует факты, Model Policy возвращает
конфигурацию и причину. Модель не выбирает себя сама.

Первый корпус может обосновать изменения только для:

- `backend-worker`;
- `bun-worker`;
- `frontend-worker`;
- `golang-worker`;
- `typescript-worker`.

Остальные роли сохраняют текущую конфигурацию до отдельного eval.

### Reasoning

Первое сравнение запускает Luna и Terra с `medium`. После выбора модели победитель
сравнивается с той же моделью на `low`. Более низкий reasoning принимается только
при сохранении task pass rate, стабильности и критических контрактов.

Sol `medium` используется как диагностический потолок качества, если Luna и Terra
дважды провалили одну задачу. Результат Sol не участвует в основном рейтинге.

### Fallback

Production flow использует такую цепочку:

1. Запрошенная конфигурация `model + reasoning`.
2. Один повтор при timeout, 429 или 5xx.
3. Eval-подтверждённый эквивалент для роли и класса задачи.
4. Более сильная доступная модель.
5. Модель текущей основной сессии.
6. Любая совместимая доступная модель.

Каждый запуск записывает:

- `requested_model` и `requested_reasoning`;
- `selected_model` и `selected_reasoning`;
- `selection_status`: `exact`, `equivalent`, `degraded` или `inherited`; eval также
  использует `substituted` и `unverified`;
- `fallback_reason`;
- `verification_level`: `normal` или `elevated`.

`degraded` не создаёт отдельный gate. Gate Policy усиливает существующую verification.

В scored eval fallback-запуск получает статус `substituted` и не засчитывается
запрошенной конфигурации. Suite продолжает остальные задачи.

## Execution Adapter

Model Policy не зависит от способа запуска lane. За исполнение отвечает общий adapter.

### CLI adapter

CLI adapter нужен, когда требуется операционно проверить конфигурацию
model/reasoning. Он запускает `codex exec` с явными overrides, структурированным
output, timeout и ограниченным числом попыток.

Для eval CLI adapter также обязан:

- работать в отдельном synthetic repository;
- использовать одноразовый изолированный `CODEX_HOME`;
- до создания artifacts подтверждать Codex CLI `0.144.1` и вход через подписку
  ChatGPT; API-key auth для eval запрещён;
- использовать закреплённый catalog Codex `0.144.1`, где у Sol/Terra/Luna удалён
  только `multi_agent_version`, затем отключать feature flags multi-agent/fan-out;
  это основной контроль. Prompt probe проверяет только утечку текстового контекста,
  а аудит rollout-файлов отклоняет незаявленные дочерние запуски;
- запрещать внешние и destructive действия;
- получать минимальный skills-контекст;
- временно сохранять rollout, извлекать только evidence выбора модели и удалять
  `CODEX_HOME` при cleanup;
- сохранять очищенный JSONL trace, usage, duration и итоговый structured result;
- связывать каждый `exact` lane с отдельными `thread_id` и `turn_id` для каждой
  фактической попытки; число попыток фиксируется до проверки isolation/output;
- не записывать credentials в artifacts.

`selection_status=exact` означает операционную проверку: rollout Codex содержит
запрошенные `model` и `reasoning_effort`, а CLI не сообщил о reroute. Это не
криптографическое подтверждение модели на сервере. Если Codex сообщает reroute,
запуск получает `substituted` и не участвует в target score. Если rollout отсутствует
или противоречив, запуск получает `unverified` и тоже не засчитывается.
В рейтинге model eval участвует только `exact`; `equivalent` относится к production
fallback и не подменяет результат целевой конфигурации.

Worker patch проверяется в свежем synthetic clone, а не в workspace, где работала
модель. Evaluator получает файлы проекта только для чтения и может писать лишь в
зарезервированные output-каталоги. Версии toolchain и digest lock-файлов сохраняются
рядом с plan/cells, поэтому score и report привязаны к воспроизводимой среде.

### Native adapter

Native adapter использует доступный `spawn_agent`. Поскольку текущий интерфейс не
поддерживает model/reasoning override, результат помечается как `inherited`.

Native adapter используется как production fallback при ошибке CLI. Работа
продолжается, а verification повышается до `elevated`.

### Единый результат lane

Оба adapter возвращают один контракт:

```json
{
  "lane_id": "string",
  "role": "string",
  "adapter": "cli|native",
  "requested_model": "string",
  "requested_reasoning": "string",
  "selected_model": "string",
  "selected_reasoning": "string",
  "selection_status": "exact|equivalent|degraded|inherited|substituted|unverified",
  "selected_skills": [],
  "active_gates": [],
  "status": "pass|fail|blocked|infrastructure-error",
  "changed_paths": [],
  "checks": [],
  "usage": {},
  "duration_ms": 0,
  "handoff_path": "string|null"
}
```

## Gate Policy

Gate Policy получает Task Facts и возвращает только gates текущего запуска.

- Обязательные gates вычисляются детерминированно.
- Оркестратор может добавить optional gate с записанной причиной.
- Оркестратор не может убрать обязательный gate.
- Валидатор повторно вычисляет обязательный набор и отклоняет пропуски.
- Output contract содержит `active_gates`, а не полный каталог.

Каждый active gate содержит `id`, `triggered_by`, `owner`, `required_evidence` и
`blocking`.

Примеры:

- Scenarius server clock: Architecture Contract, Verification Readiness,
  contract/surface evidence, UI verification и Independent QA.
- Aicortex GET без записей: code checks, negative fixture и Independent QA; UI и
  migrations не активируются.
- `selection_status=degraded`: новые gates не добавляются, но действующие проверки
  получают `verification_level=elevated`.

Gate обязан проверять результат или доказательство, а не конкретную модель. Поэтому
Independent QA остаётся обязательной, но может выполняться eval-подтверждённым
fallback.

## Skill Policy

Роль объявляет capabilities, а не длинный статический список skills. Skill Policy
выбирает skills по роли и Task Facts.

Правила выбора:

- явно названный пользователем skill имеет приоритет;
- обязательные проектные skills применяются до optional skills;
- на lane выбирается не больше трёх optional skills;
- полный `SKILL.md` читается только для выбранных skills;
- каждый skill фиксируется как canonical ID, source и digest/version;
- выбор и причина сохраняются в trace.

Модельный eval использует минимальный профиль: Agent Flow contract, role prompt,
project rules и task packet. Optional skills отключены.

После выбора model/reasoning проводится отдельный skill ablation:

- вариант A: без optional skills;
- вариант B: 1-3 skills от Capability Router.

Skill попадает в production routing только при улучшении task pass rate или качества
решения без критической регрессии и при приемлемой стоимости контекста.

## Eval-корпус

Корпус состоит из девяти исторических replay-задач. Agent получает исходное состояние
на parent commit и обычную постановку задачи. Следующий commit используется только как
скрытый источник acceptance criteria и evaluator tests.

### Omnipulse

1. Безопасная нормализация media URL для Telegram и MAX. `mediaAssetId` имеет
   приоритет, абсолютные URL сохраняются, внутренний proxy-path становится абсолютным,
   произвольный относительный путь отклоняется.
2. Буквальное время события сохраняется между каналами без timezone-сдвига.
3. Telegram notification использует числовой `telegramId`, MAX использует `chatId`,
   невалидный Telegram destination завершается до сетевого вызова.

### Scenarius

4. Сквозная валидация связанного campaign scenario в backend и frontend: canonical
   top-level event type, typed Connect details и field-level feedback.
5. Публичный server UTC time endpoint и часы в Campaign Drawer: один запрос на открытие,
   локальное обновление и явный unavailable state.
6. Недоставляемый email означает успешное завершение проверки с причиной `bad`, а
   provider failure сохраняет error status.

Backend и frontend Scenarius считаются одной продуктовой единицей для задач 4 и 5.

### Aicortex

7. Trace snapshot использует DB, которой владеет вызывающий код; route создаёт и
   закрывает соединение, SSE сохраняет свой lifecycle.
8. Обычный masked GET parse-review не запускает auto-advance, не пишет audit и не
   меняет статус. Privileged unmasked GET сохраняет обязательный security audit без
   смены статуса, а явная reconciliation — прежнее auto-advance поведение.
9. Срок ответа с маркером сноски после числа распознаётся в контексте требования и
   нормализуется без потери reason code и source reference.

Изначально девятой задачей был рефакторинг source override. Он не подходит для
поведенческого сравнения: до и после рефакторинга внешние контракты совпадают, а
проверка внутренней структуры нарушила бы правило implementation-independent tests.
Поэтому в корпус вошёл исторический bugfix парсера сроков.

### Точные revisions

Пути к репозиториям задаются локально через переменные окружения и не входят в
каноническую спецификацию. Manifest использует стабильные логические идентификаторы.

```yaml
repositories:
  omnipulse:
    path_from: EVAL_REPO_OMNIPULSE
  scenarius_backend:
    path_from: EVAL_REPO_SCENARIUS_BACKEND
  scenarius_frontend:
    path_from: EVAL_REPO_SCENARIUS_FRONTEND
  aicortex:
    path_from: EVAL_REPO_AICORTEX

tasks:
  omnipulse_media_url:
    repo: omnipulse
    base: ba140b4bbb087525f615dd50e2df8792c713add5
    gold: 621af63acd833da0129bcdac1db27e9dcad8bd50
  omnipulse_literal_schedule:
    repo: omnipulse
    base: 80eaec3b1b164bd03b5c9d94f2d939f654a374ab
    gold: b00644ecb25e8fd7219867d5348ace22dd51ac73
  omnipulse_registration_destination:
    repo: omnipulse
    base: 8ff115589014fa9c3885c1a647e259f259e57714
    gold: 860bc0109ec2e1fefc90d8a09a21dd4ac276182c
  scenarius_campaign_validation:
    backend:
      repo: scenarius_backend
      base: 95b3e1be31850e12537c4cf138c5a170da8e69c0
      gold: 0e414ae6307a4618202702cb8053510bfaa508d4
    frontend:
      repo: scenarius_frontend
      base: cec8ee08fcc4df0b3b8dc551b066e1be5b973e26
      gold: 58db211da9777300c8055e49cfe6db530bbc2f16
  scenarius_server_time:
    backend:
      repo: scenarius_backend
      base: 0e414ae6307a4618202702cb8053510bfaa508d4
      gold: fd6a2c54501befdfe07f5a41e82577c592ee956b
    frontend:
      repo: scenarius_frontend
      base: 0fdf8839194772a64e0211906bb95cde3370e7d8
      gold: 7f7fa17cc262115ea4284284371d6ec1f5b7ec12
  scenarius_deliverability_history:
    repo: scenarius_backend
    base: 9d7f49350061256e8664ba8755e5d40b052fadc9
    gold: 122f9f8af439dfe691b8e0e044a2973210a67ca1
  aicortex_trace_db_ownership:
    repo: aicortex
    base: 4bb8fe410e20fbf6f8692770406b83d65ebc344a
    gold: 70db93e0f1317f221d8ceb59300e4428cf84b23d
  aicortex_parse_review_read_only:
    repo: aicortex
    base: 70db93e0f1317f221d8ceb59300e4428cf84b23d
    gold: 6913dcfd9e366e89a2945a633b644ee8660b9da8
  aicortex_footnoted_response_deadline:
    repo: aicortex
    base: 518266d120cb7810fd5a7482908b0828308fc97f
    gold: 93361e06eda6aab4028e97dc1a82f9e33cae709a
```

## Изоляция eval

Каждая пара `task x config x repeat` получает отдельный synthetic repository с одним
baseline commit. Он не содержит gold commit, общую object database или путь к
исходному репозиторию. Gold diff и hidden tests не видны агенту во время работы.

Evaluator после завершения:

1. фиксирует changed paths;
2. подключает implementation-independent hidden tests;
3. запускает task-specific checks и существующие regression checks;
4. собирает structured result;
5. удаляет synthetic repository после сохранения evidence и проверки ownership marker.

Hidden tests проверяют контракт, а не точное совпадение с gold diff. Тесты с проверкой
строк исходного кода должны быть переписаны в поведенческую форму до scored run.

Текущее dirty-состояние Omnipulse не используется как baseline и не меняется.

## Протокол сравнения

### Этап 1. Luna против Terra

- Девять задач.
- `gpt-5.6-luna`, `medium`.
- `gpt-5.6-terra`, `medium`.
- Два парных повтора каждой конфигурации: 36 task cells, 44 запланированных запуска
  Codex и не более 88 попыток с учётом одного допустимого retry на lane.
- Третий парный повтор запускается при нестабильности или противоречивом результате:
  максимум 54 task cells, 66 запланированных запусков и 132 попытки.

Третий повтор обязателен, если одна конфигурация получила pass и fail на одной задаче
или pairwise quality verdict расходится между первыми двумя повторами.

### Этап 2. Reasoning

Выбранная модель сравнивается на `medium` и `low` тем же адаптивным протоколом.

### Этап 3. Полный flow

В полный flow попадает по одной задаче от каждого проекта с максимальным расхождением
Luna и Terra. Если расхождения нет, выбирается самая сложная задача проекта.

Этот этап проверяет итоговый системный результат вместе с orchestrator, gates,
reviewer и handoff, но не используется для чистого измерения coding-worker.

## Оценка

Результаты сравниваются лексикографически:

1. Число критических ошибок.
2. Число пройденных задач.
3. Стабильность между повторами.
4. Blind pairwise quality среди решений, прошедших обязательные проверки.
5. Токены, стоимость и время.

К критическим ошибкам относятся нарушение контракта, forbidden scope, регрессия,
destructive действие и ложное заявление о выполненной проверке.

Задача считается пройденной по большинству повторов. LLM-as-judge не определяет
корректность и используется только для ничьей по качеству. Judge не видит имя модели.

Fallback-эквивалентность определяется по роли и классу задачи. Общий средний балл не
может скрыть провал на public contract, migration или другом критичном классе.

## Ошибки и восстановление

- На один lane разрешена не более чем одна дополнительная попытка. Её можно
  использовать после timeout, 429, 5xx или ошибки structured output.
- Каждая попытка обязана содержать один полный `turn.completed.usage`. Если usage
  отсутствует или неполон, lane получает `invalid-trace`, не повторяется и не
  участвует в сравнении эффективности.
- Лимит попыток привязан к каждой cell через fingerprinted schedule. Попытки одной
  cell нельзя перенести в другую так, чтобы сохранить только общий итог.
- Недоступная target model в eval: substituted run сохраняется отдельно, target cell
  получает `unavailable`, suite продолжает работу.
- Ошибка CLI adapter: production lane переходит на native adapter с `inherited` и
  `verification_level=elevated`.
- Ошибка structured output: один format-retry, если лимит попыток ещё не исчерпан.
  Если rollout даёт
  `selection_status=exact` в определённом выше операционном смысле, повторная ошибка
  считается невыполненным model output contract. Если selection evidence нет, cell
  получает инфраструктурный статус `model-unverified`.
- Неисправный hidden test или harness: задача исключается из рейтинга до исправления;
  модель не получает fail.
- Ошибка одного проекта не останавливает задачи других проектов.

## Наблюдаемость

Для каждого запуска сохраняются:

- task, role, Task Facts и starting revision;
- requested и selected configuration;
- adapter и fallback reason;
- active gates и selected skills;
- changed paths и verification commands;
- pass/fail evidence;
- token usage, duration и число попыток;
- ссылки на handoff и evaluator artifacts.

Trace не хранит credentials, содержимое auth-файлов и значения секретных переменных.

## Порядок внедрения

1. Создать manifest корпуса и task prompts.
2. Реализовать synthetic repository lifecycle и hidden-test evaluator.
3. Изолировать CLI runner от глобального skills-каталога.
4. Реализовать общий Execution Adapter и structured lane result.
5. Реализовать Task Facts, Model Policy и Gate Policy.
6. Провести Luna/Terra eval, затем reasoning eval.
7. Провести полный flow на трёх выбранных задачах.
8. Подготовить routing recommendation и применить её только после просмотра результатов.
9. Провести отдельный skill ablation и затем заменить статические role skill lists.

## Критерии готовности реализации

- Исходные рабочие деревья трёх продуктов не изменяются.
- Все scored runs воспроизводимы по manifest, revision и skill digest.
- Luna и Terra получают одинаковую среду и одинаковые checks.
- Fallback никогда не засчитывается запрошенной модели.
- Model availability не останавливает production flow.
- Выбор модели и gates восстанавливается из Task Facts и policy version.
- Output contract содержит только active gates.
- Optional skills не превышают установленный лимит и не подмешиваются в model eval.
- `python-worker`, `ios-worker` и не-coding роли не меняются без отдельного корпуса.

## Результаты, которые нельзя предрешить

Спецификация не назначает победителя и не фиксирует будущую model matrix. Итоговые
значения для coding-ролей появляются только после scored eval. Skill mappings также
не меняются до отдельного ablation.

## Источники

- [Latest model guide](https://developers.openai.com/api/docs/guides/latest-model)
- [GPT-5.6 migration guide](https://developers.openai.com/api/docs/guides/upgrading-to-gpt-5p6-sol)
- [GPT-5.6 prompt guidance](https://developers.openai.com/api/docs/guides/prompt-guidance-gpt-5p6)
