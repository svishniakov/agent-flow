# Working with Agent Flow

Agent Flow coordinates an explicitly requested task and verifies its result. Include `Agent Flow` in the request to start it. Reading or discussing the skill does not start a workflow.

## Before work

Provide the goal, constraints and existing task or plan. Agent Flow reads the relevant To Do, Lessons and Implementation Notes, then the source. Material requirements and architecture decisions are resolved before dependent edits.

For a file-changing task, select a unique task heading and a flat acceptance-criteria list in the existing source document. The journal stores a reference to that section, not a copied requirements document.

## Execution

Work happens in the actual checkout. An initial inventory records paths and fingerprints without baseline, working or candidate source copies. A separate worktree is optional when real isolation is needed.

The main agent may implement directly. Additional workers receive independent scopes with one writer per path at a time.

Each agent returns one report in its own completed native response. SQLite accepts it after checking the actual session and evidence. There are no additional handoff files, lane maps, timelines or mirrored summaries. Markdown is rendered on read.

## Verification

Positive completion of a file-changing task requires independent QA and reviewer agents. QA connects each criterion to actual verification of the current result. The reviewer checks the implementation and the exact accepted QA report.

Evidence includes the real command, cwd, exit and output. A relevant source change invalidates previous conclusions. Repeat affected checks rather than rerunning every check for a new receipt.

A hash inventory cannot recover original uncommitted lines. Preserve user edits through focused changes and review. SQLite protects journal writes; it does not roll back source files or external actions.

## Completion and memory

Final is created only at the end: `completed`, or `failed` with its reason and unverified scope. The orchestrator summarizes the accepted reports, checks and limitations.

To Do, Lessons and Implementation Notes remain ordinary persistent project memory. Update them directly; they are not mirrored into SQLite. Historical agent reports are not an input to a new task.

After a package update, sync roles and start a new chat. Old runtime formats and migrations are not supported. Existing user archives are not automatically deleted.

The complete command contract, report shapes and worked sequence are in [traceable-runs.md](../../references/traceable-runs.md). [Installation](installation.md) · [Roles](agents.md).
