# Design Flow

Use for UI, UX, visual systems, Pencil, Figma, screenshots, frontend implementation, assets, or `DESIGN.md`.

Apply [the shared document approval rule](flows.md#утверждение-документов). A design document with any open question cannot be approved or used for implementation. An approved visual direction alone does not establish approval of a document that still contains unresolved decisions.

Creating, revising or reviewing a design document is a document task under [mandatory Doc Flow routing](flows.md#маршрутизация-doc-flow), even inside design-flow. The separate orchestrator owns assignments, sequencing and findings; design-orchestrator supplies design expertise and recommendations without a parallel document route. Preserve the existing design author and specialist checks.

## Maturity Stages

- `raw-idea`: no PRD, audience, or goal. Shape product first.
- `prd-ready`: product context exists, visual direction missing. Research and choose concept.
- `direction-ready`: concept exists, canonical design doc missing. Write design doc.
- `approved-design-doc`: approved design source exists. Implement design artifact, then UI.
- `existing-design-review`: UI or screenshot exists. Run visual QA; redesign only if requested.
- `implementation-blocked`: missing data, assets, copy, or approval.

## Route

1. Gather product context.
2. Use design references when domain or quality bar requires it.
3. Define visual concept and UX flow.
4. Draft or update design doc.
5. Resolve approval only for a material direction or product choice that remains unapproved. Reuse an approved source and prior authorization; do not ask again after merely saving the same design.
6. Produce Pencil/Figma/Stitch artifact when required.
7. Implement frontend only after approved source for non-trivial UI. Select the stack owner under [role-catalog.md](role-catalog.md): `astro-worker` for Astro, `elixir-phoenix-worker` for HEEx/LiveView, or `frontend-worker` for other web UI. Using Bun to run Astro does not by itself require a separate executor.
8. Verify in browser against design source.

## Web Implementation Preflight

Check current web guidance when unknown APIs, browser support, or substantial implementation choices need external evidence. An obvious local HTML/CSS/JS edit does not trigger a search on technology alone. Honor explicit search requests and platform requirements for current facts.

## Visual QA

Check:

- layout and spacing;
- typography and color;
- text clipping and overlap;
- responsive viewports;
- interactive states;
- keyboard and focus behavior;
- asset rendering;
- visible proof of the claimed target state in screenshots;
- screenshot or pixel diff where useful.

Reference actual check records and save a required screenshot artifact once through the journal. Do not create a second report.

For screenshot proof, capture the actual target element or state. A top-of-page screenshot does not prove a below-fold panel, table, modal, toast, error state, hover state, or selected value. Prefer an element screenshot plus a viewport screenshot when the target is inside a scroll container or a dense workspace.
