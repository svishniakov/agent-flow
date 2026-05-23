# Automation Patterns

Automate only after the manual workflow proves useful.

## Promotion Path

```text
manual run
-> tuned report
-> repeatable prompt
-> automation proposal
-> scheduled automation
```

## Automation Gate

Before creating an automation, define:

- goal;
- trigger or schedule;
- inputs;
- source of truth;
- approval or policy source;
- dry-run mode if external state changes;
- output format;
- verification artifact;
- retry policy;
- stop conditions;
- owner and escalation path.

## Good Automation Candidates

- recurring bug triage;
- docs drift report;
- dependency or CI watch;
- scheduled QA checklist;
- release readiness report;
- metrics summary.

## Bad Candidates

- unclear product discovery;
- broad refactoring;
- destructive operations;
- sensitive operations without approval source;
- tasks with changing human judgment criteria.
