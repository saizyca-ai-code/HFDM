# HFDM Agent Instructions

## Obsidian collaboration record

- Use Obsidian to maintain the durable development record for this repository. Do not rely on the chat transcript as the only record of plans, investigations, decisions, execution, or verification.
- The Obsidian vault is `dev_keeps` at `I:\_ObsidianBase\dev_keeps`. Obsidian must be running before using its CLI.
- Always target the vault explicitly as the first CLI parameter, for example: `obsidian vault=dev_keeps read path="_Habits/Attribute Items.md"`. Do not rely on whichever vault is currently focused.
- Use the Obsidian CLI for reads, searches, and other read-only vault operations. Because CLI writes are unreliable in this environment, create and update notes by writing the correctly formatted files directly under `I:\_ObsidianBase\dev_keeps`; treat this repository rule as overriding any skill guidance that requires CLI-based writes.
- Before creating or updating records, read:
  - `_Habits/Collaboration settings.md`
  - `_Habits/Attribute Items.md`
- Project records live under:
  - Tasks: `Dev_HFDM/Tasks/Roadmap`
  - Documents: `Dev_HFDM/Doc`
  - Templates: `_template`

## Task workflow

- For substantive or multi-step work, create or reuse one task note before implementation. Small questions or trivial edits do not require a new task note.
- Task filenames use `T001 - Title`; the `task id` property uses the matching `T001`. Determine the next unused ID from existing task notes and never reuse an ID.
- Keep titles semantic and readable, such as `T003 - Fix download progress desynchronization`. Do not add classification prefixes such as `[UI/UX][bug]` to filenames.
- Create tasks from `_template/Task TNNN.md` and keep these properties consistent:
  - `task status`: `Pending`, `In progress`, `Done`, or `Deferred`
  - `task type`: `feature`, `bug`, `refactor`, `chore`, `test`, `docs`, or `research`
  - `component`: `product`, `ui-ux`, `frontend`, `backend`, `download`, `storage`, `security`, or `operations`
  - `owner`: normally one text value
  - `start` and `end`: `YYYY-MM-DD`
  - `複雜度`: `S`, `M`, `L`, or `XL`
  - `milestone`: checkbox value
  - `progress`: number from 0 through 100
- When work starts, set the task to `In progress`, record the start date, and add a concise Plan.
- During work, append meaningful progress, blockers, changed assumptions, and validation results to `執行紀錄`. Do not transcribe the conversation verbatim.
- Before completion, record verification evidence and the actual result. Then set `progress` to 100, `task status` to `Done`, and fill the end date. Do not mark work done when required verification or implementation remains.
- If a task was already underway before these instructions were loaded, first backfill one task note from the existing conversation and repository state, then continue development.

## Development documents

- Document filenames use `D001 - Title`; the `doc id` property uses the matching `D001`. All document types share one D-number sequence and IDs are never reused.
- Keep document titles semantic and readable. Use the scalar `component` property for the document's primary system area instead of filename prefixes.
- Use the generic `_template/Doc DNNN.md` only when no specialized template fits.
- Create `_template/Spike DNNN.md` for time-boxed investigation, experiments, uncertainty, or feasibility research.
- Create `_template/Decision DNNN.md` for consequential choices, alternatives, rationale, tradeoffs, and conditions for revisiting the choice.
- Create `_template/Plan DNNN.md` for implementation plans whose scope, risk, validation, or rollback information should survive the chat.
- Use body wikilinks in the `關聯` callout to connect tasks and documents. Prefer links such as `[[T001 - Title]]` and `[[D001 - Title]]`.
- Keep records concise and decision-oriented. Never store credentials, tokens, secrets, private keys, or unnecessary raw command output in Obsidian.

## Controlled vocabulary

- Treat `_Habits/Attribute Items.md` as the authoritative list of allowed property values and naming rules.
- Actual task and document properties such as `task status`, `task type`, `component`, `owner`, `doc type`, and `複雜度` should normally be scalar text values; lists in `Attribute Items.md` describe allowed values rather than required YAML list types.
- Use optional native `tags` only for secondary cross-cutting concerns that do not fit the controlled primary classification. Do not duplicate `task type` or `component` in tags.
- If a new status, task type, component, owner, document type, complexity value, or naming convention is necessary, update `Attribute Items.md` in the same task and explain the addition in the task record.
- Explicit user instructions override this workflow. If Obsidian or its CLI is unavailable, report it instead of silently omitting the durable record.
