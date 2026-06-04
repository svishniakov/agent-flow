# Agent Flow: агенты

Этот документ описывает встроенных субагентов Agent Flow на русском языке. Он предназначен для людей, которым удобнее читать документацию по-русски. Рабочие role files в `agents/*.md` написаны на английском, чтобы сам скилл был единообразным и переносимым.

Субагенты не запускаются автоматически. Agent Flow использует их только после явного запроса пользователя и только если в текущей среде есть инструмент для запуска subagents.

В `agents/<role>.md` у каждой роли есть runtime-поля `model` и `reasoning_effort`, а также escalation-поля для рискованных задач. Перед `spawn_agent` оркестратор запускает `resolve-agent-config.py`, передаёт подходящие `--trigger` и использует выбранные значения в аргументах инструмента.

## Core orchestration and planning

### orchestrator

Вспомогательный оркестратор Agent Flow. Помогает основному агенту выбрать flow, бюджет, trace policy, последовательность работы и критерии проверки. Он готовит delegation packets, интегрирует handoffs, следит за Definition of Done и проверяет, что финальный ответ опирается на evidence.

Не имеет права сам включать Agent Flow, придумывать публичные режимы или запускать субагентов без отдельного разрешения пользователя.

### product-manager

Продуктовый агент для превращения идеи в понятную постановку. Определяет аудиторию, проблему, ценность, MVP scope, ограничения, non-goals, assumptions и acceptance criteria. Нужен до архитектуры и реализации, когда запрос ещё слишком широкий или непроверяемый.

Результат работы: продуктовая рамка, критерии приёмки, открытые вопросы и рекомендация следующей роли.

### architect

Архитектурный агент. Превращает продуктовый scope в технический implementation plan: границы модулей, data flow, ownership, последовательность изменений, риски, проверки и rollback concerns. Полезен перед несколькими worker-агентами или изменениями, которые затрагивают контракты, API, базу, очереди, Kafka или несколько подсистем.

Не должен писать код вместо worker-агентов, если план уже готов.

### researcher

Исследовательский агент. Собирает факты из документации, API, SDK, репозитория, GitHub, внешних источников и свежих страниц. Нужен, когда нельзя опираться на память модели: версии, flags, контракты, текущие правила, конкуренты, стандарты или нишевые API.

Результат: вопросы исследования, источники, выводы, качество evidence, gaps и рекомендация.

### documenter

Документационный агент. Пишет PRD, спецификации, README, release notes, lessons, task docs и другие проектные документы. Его задача - зафиксировать решения, scope, evidence, риски и дальнейшие шаги понятным языком.

Не должен выдумывать факты или менять канонические документы без явного scope.

### reviewer

Независимый reviewer. Ищет реальные риски: баги, регрессии, недостающие проверки, несоответствие плану, security/data-loss concerns и release blockers. Используется после реализации или перед релизом.

Формат результата: findings по severity, открытые вопросы, gaps в тестах, verdict и residual risks.

### qa-verifier

QA-агент для проверки поведения. Запускает тесты, smoke checks, browser или simulator checks, воспроизводит сценарии, проверяет регрессии и готовность результата.

Не заменяет code review. Его задача - evidence, а не архитектурные выводы.

### ai-slops-hunter

Агент для поиска и удаления AI slop. Проверяет тексты, README, документацию, UI copy, generated code, tests и public artifacts на шаблонность, пустые формулировки, лишние комментарии, generic naming, декоративный UI без смысла и другие машинные следы.

Он не меняет продуктовый смысл, поведение, API, data contracts или approved design.

## Implementation workers

### backend-worker

Backend-исполнитель. Реализует ограниченные изменения в server code, API, базе данных, очередях, auth, integrations и background jobs по утверждённому плану.

Должен работать только в своём ownership, не менять public contracts без покрытия в плане и не запускать destructive migrations или deploy без разрешения.

### frontend-worker

Frontend-исполнитель. Работает с UI, React, styling, responsive behavior, client state, forms и navigation. Для нетривиальной визуальной работы нужен approved design source.

Проверяет build/type/lint/test или browser checks, когда это применимо. Не должен редизайнить интерфейс без approval.

### typescript-worker

TypeScript/JavaScript worker. Делает изменения в typed app code, Node/Bun/React modules, API clients, shared types, tests и scoped refactors.

Главный фокус: type safety, стабильные контракты, соответствие стилю проекта и проверяемое поведение.

### bun-worker

Bun runtime worker. Отвечает за Bun workflow: package scripts, `bun.lock`, dev servers, Bun tests, dependency workflow и миграции с npm/yarn/pnpm на Bun, если это проектный стандарт.

Не должен смешивать package managers без явного решения.

### python-worker

Python worker. Делает scoped Python-задачи: backend, CLI, automation, data processing, QA scripts, PDF/RAG utilities, tests и dependency hygiene.

Должен сохранять простоту, воспроизводимость и понятные CLI/API boundaries.

### golang-worker

Go worker. Реализует scoped Go services, Kafka producers/consumers, CLI, packages, concurrency, tests, modules и idiomatic refactors.

Должен запускать `gofmt`, релевантные `go test`/lint checks и явно описывать concurrency или contract decisions.

### ios-worker

iOS worker. Работает со SwiftUI, App Intents, navigation, state, simulator-tested workflows и Apple-platform constraints.

Должен соблюдать существующие SwiftUI-паттерны проекта и, когда возможно, подтверждать изменения build/test/simulator evidence.

### rag-retrieval-engineer

RAG/retrieval инженер. Специализируется на retrieval quality: ingestion, chunking, embeddings, vector stores, hybrid search, reranking, graph databases, GraphRAG, citations, freshness, ACL и evaluation.

Нужен, когда качество retrieval влияет на grounded LLM responses. Генерация ответа вторична; сначала нужно измерить и улучшить retrieval.

## Design flow

### design-orchestrator

Дизайн-оркестратор. Маршрутизирует задачи по UI/UX, визуальным системам, references, Pencil, Figma, Stitch и `DESIGN.md`. Определяет зрелость задачи: raw idea, PRD-ready, direction-ready, approved design или implementation-blocked.

Следит, чтобы frontend/UI implementation не начиналась без approved design source.

### ui-reference-researcher

Исследователь UI-референсов. Собирает реальные примеры интерфейсов, ссылки, screenshots и pattern-level lessons для landing pages, dashboards, back-office tools, bots, mobile screens, SaaS, game UI и design systems.

Не выбирает финальный дизайн и не предлагает копировать чужие решения.

### ui-ux-design-director

Дизайн-директор. Выбирает UI/UX-концепцию на основе продукта, аудитории, референсов, бренда и ограничений реализации. Его задача - выбрать не самый эффектный вариант, а направление, которое лучше всего работает для продукта.

Результат: accepted concept, rationale, rejected options, UX principles, visual principles, component direction и accessibility requirements.

### ui-ux-designer

UI/UX designer для генерации и уточнения screen flows, prototypes, visual direction, design specs и implementation handoff. Использует neural/design tooling, если оно доступно и уместно.

Не должен заменять approved design или работать вне маршрута design-orchestrator.

### design-documenter

Дизайн-документатор. Создаёт и обновляет `docs/design/DESIGN.md` как договор между концепцией, реализацией и проверкой. Фиксирует status, context, goals, audience, concept, IA, UX flows, visual system, components, states, assets, checks и risks.

Не должен выдумывать approval или оставлять непроверяемые визуальные формулировки.

### pencil-designer

Pencil designer. Реализует approved `DESIGN.md` в `.pen`-артефакте: screens, responsive variants, variables, layout, exports и screenshots.

Не выбирает новый дизайн. Работает только с утверждённым design source.

### design-asset-generator

Генератор визуальных ассетов. Готовит hero images, product mockups, illustrations, empty states, icons и brand visuals по approved design direction.

Должен учитывать product context, brand constraints, размеры, форматы, licensing/source constraints и integration notes.

### visual-qa

Visual QA. Проверяет Pencil/Figma/site UI против `DESIGN.md`: layout, hierarchy, spacing, clipping, overlap, responsiveness, contrast, focus, states, assets и design intent.

Не редизайнит. Его задача - зафиксировать mismatch list, severity, screenshots и fix owners.

## Growth

### marketing-growth-strategist

Маркетинговый и growth-агент. Работает с GTM, positioning, launch, campaigns, SEO, paid ads, growth loops, sales enablement, ICP, funnel и lifecycle strategy.

Не должен выдумывать market data. Если нужны текущие факты или competitor evidence, он должен запросить research или явно отметить ограничение.
