# Architecture Matrix

Architecture Matrix turns product and stack evidence into reusable architecture
facets. It is not a project profile catalog and does not create one architect
per repository. Source projects may inform the matrix, but handoffs use neutral
facet names.

Use the matrix when the Architecture Contract Gate applies and product type,
application surface, stack, or risk changes the architecture constraints.

## Router

Before selecting facets, inspect local source evidence:

- PRDs, ADRs, design docs, READMEs, and implementation plans named by the task;
- project memory lessons and implementation notes;
- package manifests, build files, migrations, API contracts, and test commands;
- existing module boundaries and runtime or deploy docs.

Selection rules:

- select only facets that change boundaries, forbidden changes, QA gates, or
  reviewer checklist items;
- do not create or select facets from repository names, customer names, local
  paths, or one-off project labels;
- prefer a small set of facets from different axes over one broad pseudo-profile;
- if no facet adds constraints, say so in the architecture handoff and use the
  base Architecture Contract sections.

Architecture handoffs should include:

- selected matrix facets in `Selected Architecture`;
- rejected architecture options or unused facets in `Rejected Alternatives`;
- facet-driven ownership in `Module Boundaries` and `Worker Ownership`;
- facet-driven invariants in `Forbidden Changes`, `QA Gates`, `Reviewer
  Checklist`, and `Stop Conditions`.

## Matrix Axes

### Product Context

- `saas-service`: tenant or workspace model, onboarding, subscriptions, billing,
  permissions, admin settings, usage limits, notifications, support operations,
  reliability, and audit.
- `workflow-automation`: scenario or workflow authoring, event intake,
  audit/history, delivery operations, and operator controls.
- `document-ai`: document intake, OCR, parsing, retrieval, evidence planning,
  model gateway calls, governance, and operator review.
- `fintech`: budgets, expenses, balances, subscriptions, payments, accounting-like
  records, reconciliation, and audit.
- `crypto-payments`: blockchain payment products for TON, TRON, Ethereum, or
  EVM-compatible networks, including wallet connection, deposits, withdrawals,
  token transfers, confirmations, fee/resource handling, custody model,
  reconciliation, compliance controls, and audit.
- `messenger-commerce`: bot or mini-app entrypoints, platform auth, tickets,
  orders, QR flows, payments, admin, and content management.
- `developer-tooling`: local automation, repo/runtime tooling, trace validation,
  command UX, and private workspace data.
- `child-education`: curriculum, learning progress, child-safe UX, accessibility,
  parent controls, audio, motion, and offline use.
- `agent-orchestration`: role routing, worker lanes, handoffs, traceable runs,
  validation, Evidence Records, and local memory learning.

### Application Surface

- `backend-service`: APIs, domain logic, persistence, jobs, runtime config, and
  service health.
- `frontend-service`: frontend-only application surface, UI state, routing, API
  clients, static build, browser behavior, accessibility, and deploy preview.
- `full-stack-service`: combined frontend and backend surface with shared UI,
  API, data-contract, persistence, auth, deployment, and verification ownership.
- `workflow-engine`: graph or state-machine execution, activation, preview,
  import/export, retries, and audit.
- `backoffice-ui`: internal operations workflows, dense data screens, forms,
  tables, audit views, settings, and controlled actions.
- `landing`: public landing surface, signup, analytics consent, content updates,
  SEO, performance, and deploy preview.
- `mobile-app`: phone or tablet application surface, navigation, platform
  lifecycle, permissions, persistence, offline behavior, push notifications,
  accessibility, and app-store delivery constraints.
- `desktop-app`: desktop application surface, windows, menus, tray or menu-bar
  behavior, local files, private data, background work, updater flow, and
  operating-system permissions.
- `browser-extension`: extension permissions, content scripts, service workers,
  extension UI, storage, and cross-origin boundaries.
- `wallet-payment-surface`: wallet connection, address display, network
  selection, payment request, signature/transaction approval, deposit,
  withdrawal, status, receipt, and user-facing failure recovery.
- `crypto-ops-console`: operator surface for chain incidents, deposit and
  withdrawal review, reconciliation, wallet balances, token allowlists,
  blocked/manual holds, and support audit.
- `embedded-mini-app`: embedded messenger or social mini-app surface, host webview,
  platform auth, bot or deep-link entrypoints, sharing, payments,
  notifications, and host capability limits.
- `social-community-surface`: profile, feed, membership, messaging, moderation,
  notifications, privacy controls, reporting, and optional federation-facing
  behavior.
- `iot-device-fleet`: device-facing and fleet-management surface, provisioning,
  telemetry, commands, device identity, configuration rollout, connectivity
  status, and operational observability.

### Architecture Pattern

- `monolith`: one deployable application with clear internal module boundaries,
  shared runtime ownership, transactional consistency, and coordinated release
  gates.
- `microservices`: independently deployed services with explicit service
  contracts, data ownership, inter-service communication, observability, and
  release coordination.
- `server-rendered-web`: server, static, or hybrid rendered web application with
  explicit route ownership, server/client component boundaries, data loading,
  cache strategy, SEO metadata, and hydration limits.
- `web-app-shell`: browser application shell with client routing, API clients,
  service worker or cache boundaries, offline behavior, installability,
  browser storage, and progressive enhancement.
- `mobile-layered-app`: mobile application with UI/domain/data boundaries,
  navigation state, repositories or use cases, device capabilities, lifecycle,
  offline behavior, and platform accessibility.
- `desktop-shell-app`: desktop application shell with window/menu/tray
  lifecycle, native shell boundaries, renderer or view isolation, local file
  access, background work, updater behavior, and private storage.
- `browser-extension-architecture`: privileged extension runtime split across
  manifest, service worker, content scripts, extension UI, message passing,
  permissions, storage, and cross-origin boundaries.
- `hosted-mini-app`: messenger or social host mini-app with embedded webview
  lifecycle, platform auth, bot or deep-link entrypoints, sharing, payments,
  notifications, and server callback contracts.
- `social-feed-graph`: user identity, profile, follow or membership graph, feed
  generation, ranking, moderation, notifications, privacy boundaries, and
  optional federation contracts.
- `iot-edge-cloud`: device, edge, and cloud boundaries for telemetry, commands,
  provisioning, device identity, connectivity loss, buffering, firmware or
  configuration rollout, and fleet observability.
- `event-driven-architecture`: domain and integration events, producers,
  consumers, broker topology, event contracts, schema evolution, ordering,
  idempotency, retries, dead-letter handling, replay, and observability.
- `event-sourcing`: append-only event log as source of truth, command handling,
  aggregates, projections, snapshots, replay, versioning, and consistency
  boundaries.
- `event-ingestion`: external or internal event intake through webhooks, queues,
  streams, deduplication, retries, ordering, backpressure, and poison-message
  handling.
- `blockchain-payment-adapter`: chain-specific payment adapter boundary for
  wallet connections, transaction construction, broadcasting, gas/fee/resource
  estimation, confirmations, receipt parsing, and failure mapping.
- `smart-contract-boundary`: contract ABI/interface boundary, token standard,
  contract address allowlist, upgrade/admin controls, event semantics,
  decimals, and compatibility across environments.
- `chain-indexer-reconciliation`: blockchain indexer or RPC event pipeline,
  block cursor ownership, confirmation policy, reorg/finality handling,
  idempotent crediting, missed-event backfill, and ledger reconciliation.
- `graph-workflow-runtime`: nodes, edges, validation, traversal, state snapshots,
  activation, preview, and runtime history.
- `adapter-integration`: provider boundaries, protocol translation, retry policy,
  capability flags, and contract tests.
- `local-first-storage`: private local state, sync boundaries, offline behavior,
  conflict handling, backup, and export.
- `rag-evidence-pipeline`: corpus intake, chunking, embeddings, retrieval,
  citations, allowed source ids, and model answer constraints.
- `multi-runtime-service`: one codebase with multiple commands, jobs, migrations,
  workers, health checks, and deploy targets.
- `monorepo-split`: frontend/backend/package ownership, generated contracts,
  shared types, build graph, and release boundaries.

### Stack Runtime

- `go`: packages, interfaces, migrations, CLI/runtime commands, concurrency,
  tests, and deploy binaries.
- `typescript-bun`: Bun or Node-compatible TypeScript services, runtime config,
  API handlers, jobs, tests, and package scripts.
- `react-vite-astro`: client routes, API clients, static builds, UI state,
  browser checks, and deploy preview.
- `swiftui-appkit`: SwiftUI/AppKit architecture, state ownership, persistence,
  accessibility, simulator/device checks, and platform permissions.
- `node-express`: Express APIs, middleware, background jobs, validation, storage,
  and integration tests.
- `browser-extension-mv3`: manifest v3, service worker lifetime, permissions,
  content scripts, storage, and browser smoke checks.
- `event-messaging-runtime`: Kafka, RabbitMQ, BullMQ, Redis-backed queues,
  topics, exchanges, routing keys, consumer groups, acknowledgements, retries,
  dead-letter queues, and broker operations.
- `blockchain-runtime`: TON and Jettons with TON Connect, TRON and TRC-20 with
  TronWeb/resource model, Ethereum/EVM and ERC-20 with JSON-RPC, chain/network
  ids, token decimals, contract ABI, gas or fee estimation, confirmations, and
  testnet/mainnet separation.

### Risk Gates

- `pii-secrets`: data minimization, redaction, secret handling, access boundaries,
  logging, and audit.
- `tenant-isolation`: tenant, workspace, account, or organization data separation,
  cross-tenant access, scoped queries, cache keys, background jobs, and audit
  boundaries.
- `subscription-entitlements`: plan state, trials, subscriptions, feature
  entitlements, quotas, limits, billing status, downgrade behavior, and support
  overrides.
- `auth-permissions`: authentication, session ownership, role checks, admin or
  operator privileges, service credentials, token scope, and privilege
  escalation paths.
- `payments`: money state, provider contracts, idempotency, webhook verification,
  reconciliation, and refund/error paths.
- `financial-data-integrity`: balances, ledgers, expense records, reconciliation,
  currency handling, rounding, historical corrections, and audit trails.
- `custody-key-management`: private keys, signer separation, hot/cold wallet
  boundaries, custodian/HSM integration, key rotation, signature approval,
  secret storage, logs, and emergency freeze.
- `chain-finality-confirmations`: confirmation thresholds, finality assumptions,
  reorgs, duplicate or delayed deposits, stuck transactions, status transitions,
  and manual recovery.
- `token-contract-integrity`: trusted token and contract allowlists, Jetton,
  TRC-20, ERC-20 semantics, decimals, ABI drift, proxy/upgrade/admin controls,
  fake tokens, and wrong-network transfers.
- `rpc-indexer-drift`: RPC provider, node, webhook, or indexer inconsistency,
  missed logs, block cursor gaps, rate limits, provider outage, replay/backfill,
  and source-of-truth precedence.
- `crypto-fee-liquidity`: Ethereum gas, TRON Energy/Bandwidth, TON forwarding
  fees, fee sponsorship, gasless flows, hot wallet liquidity, stuck withdrawals,
  and fee volatility.
- `crypto-compliance-controls`: sanctions or AML screening, withdrawal approval,
  suspicious-activity holds, jurisdictional constraints, customer support
  override limits, and audit evidence.
- `client-server-contract-drift`: frontend/API contract drift, generated or
  handwritten clients, shared types, error shape changes, and version skew
  between deployed surfaces.
- `public-web-consent`: public landing pages, signup paths, analytics consent,
  tracking pixels, SEO metadata, forms, spam boundaries, and privacy copy.
- `browser-extension-permissions`: extension permissions, content-script scope,
  service worker lifetime, cross-origin access, browser storage, and user data
  exposure.
- `module-boundary-erosion`: monolith or multi-runtime dependency direction,
  shared module ownership, business logic leakage, and accidental coupling.
- `distributed-consistency`: microservice data ownership, service compatibility,
  partial failure, retries, timeouts, observability, and deploy-order risk.
- `event-delivery-replay`: event ordering, idempotency, duplicate delivery,
  replay, projection drift, dead-letter handling, backpressure, and broker
  recovery.
- `evidence-source-integrity`: document, RAG, event-sourced, or audit evidence
  provenance, allowed source ids, citation boundaries, source-of-truth rules,
  and model/output trust limits.
- `child-safety`: age-appropriate UX, parent controls, content boundaries,
  accessibility, motion/audio safety, and privacy.
- `accessibility`: keyboard flow, screen-reader semantics, contrast, motion
  preference, focus state, and responsive layout.
- `local-private-files`: local file permissions, private workspace data, offline
  storage, export/delete, and no accidental network disclosure.
- `migrations`: schema changes, data backfill, rollback limits, compatibility,
  and migration verification.
- `release-deploy`: config, health checks, environment drift, CI, deployment
  order, observability, and rollback notes.
- `design-source-lock`: Figma/Pencil/source-of-truth alignment, visual parity,
  token usage, screenshot proof, and no undocumented redesign.

### Verification Gates

- `unit`: focused tests for changed functions, services, reducers, validators, or
  domain rules.
- `integration`: API, storage, queue, provider, or cross-module behavior checked
  through real boundaries.
- `api-contract`: API schema, generated clients, request/response shapes,
  auth/session behavior, status codes, and error contracts checked at the
  service boundary.
- `frontend-build`: typecheck, lint, static build, routing, API client wiring,
  and state-management checks for frontend-only or UI-heavy changes.
- `full-stack-flow`: user workflow checked through UI, API, persistence, auth,
  and deployment/runtime boundary when frontend and backend change together.
- `browser-smoke`: UI workflow exercised in a browser with visible proof when
  behavior or layout changes.
- `landing-seo-performance`: landing page build, signup/contact path, analytics
  consent, SEO metadata, accessibility, responsive layout, and performance
  budget checked before handoff.
- `browser-extension-smoke`: manifest, permissions, service worker, content
  scripts, extension UI, storage, and cross-origin behavior checked in a browser.
- `simulator-device`: iOS/macOS behavior checked on simulator, device, or platform
  tooling suited to the change.
- `module-boundary-regression`: monolith or multi-runtime internal boundaries,
  dependency direction, shared module ownership, and existing regression suite
  checked after architecture-sensitive changes.
- `service-contract`: microservice API/event contracts, data ownership,
  compatibility, observability, and deploy-order assumptions checked across
  service boundaries.
- `event-broker-contract`: topics, queues, exchanges, routing keys, producer and
  consumer contracts, acknowledgements, retries, dead-letter handling,
  idempotency, ordering, and backpressure checked against the selected broker.
- `event-replay-projection`: event-sourcing command handling, event versioning,
  replay, projections, snapshots, and consistency boundaries checked with
  deterministic fixtures.
- `wallet-signing-smoke`: wallet connect, network selection, address display,
  signing prompt, transaction submission, cancellation, rejection, and receipt
  states checked on the selected wallet/runtime.
- `testnet-transaction-lifecycle`: testnet or sandbox deposit/withdrawal
  lifecycle checked from request through broadcast, confirmations, status,
  ledger update, and receipt without unapproved mainnet writes.
- `smart-contract-interface`: contract addresses, ABI, token standard methods,
  events, decimals, allowance/transfer semantics, admin/upgrade controls, and
  environment allowlists checked with deterministic fixtures.
- `chain-reconciliation-replay`: indexed blocks/events replayed through
  idempotent crediting, duplicate delivery, missed-event backfill, reorg/finality
  fixtures, and ledger reconciliation.
- `custody-secrets-review`: signer path, secret storage, environment config,
  logging, key rotation assumptions, custodian/HSM boundaries, and no private
  keys in source or traces reviewed before handoff.
- `fee-resource-simulation`: gas, Energy/Bandwidth, TON forwarding fees,
  sponsorship, hot wallet balance, retry cost, and stuck transaction behavior
  checked against selected networks.
- `migration-schema`: migration, schema, generated type, and seed/backfill checks
  for storage changes.
- `retrieval-evidence-benchmark`: retrieval, citation, answer, model, or parser
  quality checked with fixtures or measured samples.
- `visual-design-source-comparison`: screenshot or visual comparison against the
  approved design source.

## Contract Output Shape

When matrix facets apply, the architecture handoff should record them in this
shape:

```md
## Selected Architecture
- Matrix facets:
  - Product Context: `...`
  - Application Surface: `...`
  - Architecture Pattern: `...`
  - Stack Runtime: `...`
  - Risk Gates: `...`
  - Verification Gates: `...`
```

If `crypto-payments` or blockchain payment facets apply, the same section must
also record:

- chains and networks: TON, TRON, Ethereum/EVM, testnet/mainnet split, and chain
  ids where applicable;
- supported assets: native coins, Jettons, TRC-20, ERC-20, contract addresses,
  decimals, and allowlist ownership;
- wallet and custody model: non-custodial wallet connect, custodial signer,
  custodian/HSM, hot/cold wallet boundaries, and key rotation assumptions;
- payment state model: requested, signed, broadcast, pending confirmations,
  credited, failed, reversed/manual-review, and support-visible statuses;
- confirmation/finality policy: thresholds, reorg handling, stuck transaction
  handling, retry rules, and source-of-truth precedence;
- RPC/indexer sources: node/provider/indexer ownership, block cursor storage,
  replay/backfill strategy, and reconciliation schedule;
- forbidden changes: unapproved mainnet writes, private-key export, token
  allowlist changes, contract upgrade/admin changes, and silent confirmation
  threshold changes;
- required evidence: wallet signing smoke, testnet transaction lifecycle,
  contract-interface fixtures, reconciliation replay, custody/secrets review,
  and fee/resource simulation when those facets are selected.

The matrix does not change `lane-map.json` schema. It makes the Architecture
Contract specific enough for workers, QA, and reviewer to enforce.
