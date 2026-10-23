# Working with Agent Flow

Agent Flow coordinates an explicitly requested task and verifies its result. Include `Agent Flow` in the request to start it. Reading or discussing the skill does not start a workflow.

## Before work

Provide the goal, constraints and existing task or plan. Agent Flow reads the relevant To Do, Lessons and Implementation Notes, then the source. Material requirements and architecture decisions are resolved before dependent edits.

For a file-changing task, select a unique task heading and a flat acceptance-criteria list in the existing source document. The journal stores a reference to that section, not a copied requirements document.

## Execution

Work happens in the actual checkout. The journal stores scope paths and a reference to the selected requirements section. It does not scan the repository or track file changes. A separate worktree is optional when real isolation is needed.

The main agent may implement directly. Additional workers receive independent scopes with one writer per path at a time.

Each subagent returns one report in its own completed native response; the main agent registers its own report from its current turn. SQLite accepts reports after checking the actual session and evidence. There are no additional handoff files, lane maps, timelines or mirrored summaries. Markdown is rendered on read.

## Verification

Positive completion of a file-changing task requires an independent reviewer, followed by independent QA. The reviewer checks the implementation and author reports. QA connects each criterion to actual verification of the result and refers to the accepted reviewer by assignment ID.

Evidence includes the real command, cwd, exit and output. After substantive corrections to their work, the participants repeat the affected checks and review. The journal does not detect changes automatically; work in another chat does not invalidate this task's reports.

Each author reports their own work and paths, without accounting for unrelated changes in the repository. Preserve user edits through focused changes and review. SQLite protects journal writes; it does not roll back source files or external actions.

## Completion and memory

Final is created only at the end: `completed`, or `failed` with its reason and unverified scope. The orchestrator summarizes the accepted reports, checks and limitations.

To Do, Lessons and Implementation Notes remain ordinary persistent project memory. Update them directly; they are not mirrored into SQLite. Historical agent reports are not an input to a new task.

After a package update, sync roles and start a new chat. Old runtime formats and migrations are not supported. Existing user archives are not automatically deleted.

The complete command contract, report shapes and worked sequence are in [traceable-runs.md](../../references/traceable-runs.md). [Installation](installation.md) · [Roles](agents.md).
