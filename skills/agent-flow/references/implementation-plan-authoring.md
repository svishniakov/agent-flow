# Implementation Plan Authoring

Document status: done

Use this reference when Agent Flow creates or materially revises an implementation plan, whether the user asks for the plan directly or another flow reaches the implementation-plan step.

This is a prompt-level contract. It adds no selector, registry, runtime stage picker, plan state machine, Markdown parser, JSON artifact, public mode, lane type, execution gate, Architecture Capability Router change, role frontmatter change, `agent-skills.json` change, `recommended_skills` change, testing-only stage, per-stage rollback, or fixed upper limit on stage count.

## Goal

Create the smallest implementation plan that can be executed and verified safely. The plan may have any number of implementation stages, starting with one. Stage count follows real dependencies and independently checkable outcomes, not directory count, role count, technology count, or selected skill count.

Light, Standard, and Release remain future execution budgets. They do not get separate plan-authoring flows.

## Required Authoring Sequence

1. Collect context from the latest user request, approved product and architecture documents, current code, project memory, local instructions, and explicit constraints.
2. Identify the project stack and affected technical areas before deciding stage count.
3. From the skills available to the main agent in the current environment, select the minimal relevant specialist skills for that stack and affected areas.
4. Read every selected skill completely before applying it.
5. Apply the selected skills to assess affected modules, contracts, data, migrations, dependencies, infrastructure, user workflows, compatibility, risks, checks, and stage boundaries.
6. Record the skills used, the purpose of each skill, and any expertise gaps. Skill names alone are not enough; their conclusions must appear in scope assessment, risks, checks, dependencies, or stage boundaries.
7. Use project sources, local instructions, approved documents, and current code as primary evidence. General skill guidance cannot expand product scope or override project constraints.
8. Decide the minimal justified number of implementation stages. Start from one stage and split only when a separate boundary is proven.
9. Write a draft implementation plan with `Document status: in_progress` and separate `Implementation status: not_started` (or the factual current implementation state for an existing plan).
10. Prepare the final-status candidate (`Document status: done`, truthful separate implementation status) before hashing and sending its complete bytes to independent QA/Devil's Advocate. Task preparation remains active until acceptance; a rejected candidate is not completed delivery.
11. Fix technical findings directly when they do not change product scope or expected behavior.
12. After any review-driven fix, send the revised draft to a new independent Devil's Advocate review.
13. Repeat fix and review until the current revision receives verdict `passed`.
14. Finalize only the revision that received `passed`, including its status fields. Status edits after acceptance require renewed review. If commit was requested, inspect staged and committed document bytes; otherwise do not require commit to complete plan preparation.

Document completion means the current text is ready, not that its implementation
was performed. A prepared plan normally has `Document status: done` and
`Implementation status: not_started`, even inside a commit. Partial work remains
`in_progress` or `blocked`; a SHA is not proof of all requirements. When executing
the plan, include it and linked documents across every affected repository in the
delivery list under `definition-of-done.md`. Preserve ADR decision statuses and
unresolved requirements; do not migrate unrelated historical plans.

Involve the user only when a decision changes product scope or expected behavior.

## Missing Specialist Skill

If a relevant specialist skill is unavailable, do not install it automatically.

Continue with project sources and role instructions when they are enough for a reliable assessment. If the gap prevents reliable assessment of impact, risks, checks, dependencies, or stage boundaries, keep the plan as draft.

Route architecture gaps to Architect when module boundaries, contracts, sequencing, stage split, or verification need expert judgment. Route missing external or current-source gaps to Researcher under the existing Researcher instructions when API, SDK, external documentation, source material, or local examples are needed. Do not change the Researcher prompt for this contract.

After Architect or Researcher clarification, reassess scope and stage boundaries before finalizing.

## Stage Boundary Rules

Create a separate implementation stage only when at least one concrete reason exists:

- real dependency or required order;
- data migration or data transformation;
- public or inter-module contract boundary;
- architecture foundation with its own checkable result;
- separate failure mode;
- result that can be independently verified before the next change.

Do not create a stage merely because work touches another directory, language, technology, role, generic cleanup topic, or rollback concern.

Selected skills do not determine stage count. Several skills may support one coherent stage. One skill may reveal several stages when it exposes a migration, contract boundary, independent failure mode, or required order.

## Required Stage Fields

Every implementation stage must include:

- goal and expected result;
- scope and change boundary;
- dependencies and order;
- affected modules and contracts;
- acceptance criteria;
- tests and other checks;
- risks and unique Devil's Advocate review focus;
- completion condition.

When a plan has multiple stages, each stage must also explain why it cannot be safely merged with an adjacent stage. A one-stage plan does not need that explanation.

Testing belongs inside the relevant implementation stage. Do not create a separate testing stage. Add one final cross-cutting check section only when the whole plan needs it.

Do not create per-stage rollback. If rollout or rollback is genuinely needed, use one plan-level rollout/rollback section for the delivery.

## Devil's Advocate Review

The reviewer checks the current draft for:

- missing scope, affected contracts, dependencies, or checks;
- stage count inflated by skills, directories, roles, technologies, or cleanup;
- stage count too small for real migration, contract, failure-mode, or ordering boundaries;
- selected skills not actually applied to impact, risks, checks, or stages;
- missing skill or expertise gap treated as solved;
- automatic skill installation;
- project sources overridden by generic skill advice;
- testing-only stage;
- per-stage rollback;
- new selector, registry, runtime picker, public mode, lane type, execution gate, JSON artifact, parser, or fixed stage cap.

Findings must target the current draft. If the orchestrator changes the draft after review, the previous verdict is stale and cannot finalize the plan.

## Readback Scenarios

Use these scenarios when checking the contract:

1. A coherent frontend scope with a relevant frontend skill remains one stage when it has one independently checkable result.
2. Multiple selected skills do not create multiple stages without real dependencies or separate checkable results.
3. One selected skill can reveal multiple stages when it finds a migration, contract boundary, or separate failure mode.
4. A critical expertise gap keeps the plan as draft, routes clarification to Architect or Researcher, and does not install a skill automatically.

## Compatibility

Existing plans and trace artifacts require no migration. This contract governs future implementation-plan authoring and material revisions only.
