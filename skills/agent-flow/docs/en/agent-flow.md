# Working with Agent Flow

Agent Flow coordinates an explicitly requested task and verifies its result. Include `Agent Flow` in the request to start it. Reading or discussing the skill does not start a workflow.

## Before work

Provide the goal, constraints and existing task or plan. Agent Flow reads the relevant To Do, Lessons and Implementation Notes, then the source. Material requirements and architecture decisions are resolved before dependent edits.

For a file-changing task, select a unique task heading and a flat acceptance-criteria list in the existing source document. The journal stores a reference to that section, not a copied requirements document.

## Execution

Work happens in the actual checkout. The journal stores scope paths and a reference to the selected requirements section. It does not scan the repository or track file changes. A separate worktree is optional when real isolation is needed.

The main agent may implement directly. Additional workers receive independent scopes with one writer per path at a time.

Each assignment receives one immutable report verified against its native session and evidence. A correction creates a new assignment with `previous_assignment_id`. Execution status is `completed` or `interrupted`; verification outcome is `passed`, `failed` or `unverified`. A completed check may find a defect. Markdown is rendered from SQLite on read.

## Verification

Positive completion of a file-changing task requires an independent reviewer, followed by independent QA. The reviewer checks the implementation and author reports. QA connects each criterion to actual verification of the result and refers to the accepted reviewer by assignment ID.

Evidence includes the real command, cwd, exit and output. After substantive corrections to their work, the participants repeat the affected checks and review. The journal does not detect changes automatically; work in another chat does not invalidate this task's reports.

Each author reports their own work and paths, without accounting for unrelated changes in the repository. Preserve user edits through focused changes and review. SQLite protects journal writes; it does not roll back source files or external actions.

## Completion and memory

`finalize --report-id` closes the journal using the selected concluding report. Success requires independent QA of the current result and evidence resolving earlier findings through `resolves`; negative reports remain unchanged. A root report records a terminal stop with its reason and unverified scope. After closure, only reads and exact operation replays are allowed.

To Do, Lessons and Implementation Notes remain ordinary persistent project memory. Update them directly; they are not mirrored into SQLite. Historical agent reports are not an input to a new task.

New tasks use contract 2. Earlier SQLite journals remain readable without conversion and reject writes. After a package update, sync roles and start a new chat.

The complete command contract, report shapes and worked sequence are in [traceable-runs.md](../../references/traceable-runs.md). [Installation](installation.md) · [Roles](agents.md).
