# Design Flow

Use for UI, UX, visual systems, Pencil, Figma, screenshots, frontend implementation, assets, or `DESIGN.md`.

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
5. Get approval when direction changes.
6. Produce Pencil/Figma/Stitch artifact when required.
7. Implement frontend only after approved source for non-trivial UI.
8. Verify in browser against design source.

## Web Implementation Preflight

For HTML, CSS, client JS, layout, browser APIs, forms, media, motion, performance, or accessibility, check current modern web guidance when available.

## Visual QA

Check:

- layout and spacing;
- typography and color;
- text clipping and overlap;
- responsive viewports;
- interactive states;
- keyboard and focus behavior;
- asset rendering;
- screenshot or pixel diff where useful.

Record evidence in `checks/` and artifacts in `artifacts/`.
