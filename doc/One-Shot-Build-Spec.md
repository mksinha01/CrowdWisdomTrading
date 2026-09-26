The "One-Shot Build Spec" Template
Extracted from OutboundAI's spec doc — generalized so you can reuse it for DevKicks, FlowZint, or anything else.


Why this format works
This is a self-contained build contract for an AI (or a future version of you) to execute without asking clarifying questions. It works because of five properties, in order of importance:

No ambiguity left on the table. Every file that will exist is named up front (Section 1) before any code is shown. Nothing is discovered mid-build.
Config is separated from logic. Env vars, schema, and dependencies come before any application code — so the build order never has to backtrack to "oh wait, we need a new env var."
Rules are stated as wrong-vs-correct pairs. Not prose explanations — actual broken code next to the fixed code. This is the single highest-leverage section in the whole doc (Section 13 in the original).
Failure modes are pre-catalogued. A gotchas table (symptom → cause → fix) means the builder doesn't have to discover the same bug you already found.
It ends with an operational checklist, not just code — deployment and first-run steps are part of the spec, not an afterthought.

Use this template as your Section skeleton. Delete what doesn't apply; don't add sections that break the ordering logic below (config → schema → logic → API → UI → rules → ops).


TEMPLATE SKELETON
0. Mission Statement
One paragraph: what the system does, in plain language, as a bullet list of capabilities (not features — capabilities, i.e. verbs: "dials," "books," "persists," "records").
A tech stack list, flat, no explanation — just names and versions.
An explicit instruction block for whoever/whatever executes this spec:

"Build this exactly. Do not skip anything. Do not simplify. Do not substitute libraries." This framing matters even when you're the one building it later — it stops future-you from "improving" things mid-flight and drifting from the design.
1. File Structure
A literal directory tree, every file named, with a one-line ← comment per file describing its single responsibility.
Rule of thumb: if you can't write a one-line responsibility for a file, it's doing too much — split it.
2. Environment / Secrets
Full .env template, grouped by external service with a # ── Service Name ── comment header and a link to where to obtain each key.
Mark which vars are optional vs required inline.
Always pair with .gitignore.
Decide and state your settings precedence chain up front (e.g. runtime env vars > DB-stored settings > code defaults). This single decision prevents an entire class of "why isn't my config change taking effect" bugs later — write it down even if it seems obvious now.
3. Data Schema
All DDL idempotent (CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS) so the script is safely re-runnable.
Keep the schema's evolution visible in the file — don't rewrite history. Later ALTER TABLE statements appended below the original CREATE TABLE block document when a field was added and implicitly why (you can see the feature that needed it). This is cheap free changelog.
State access-control posture explicitly per table (RLS on/off, or equivalent), don't leave it implicit.
4. Dependencies
Flat pinned/minimum-version list. No explanation needed — this section is reference, not narrative.
5. Containerization / Runtime
Dockerfile: base image, system deps, install step, copy, expose port, CMD.
start.sh (or equivalent entrypoint): explicit echo statements showing config being loaded — this becomes your first debugging tool in production logs. Cheap to add, saves an SSH session later.
State the port ownership map if more than one process runs in the container (e.g. "API server: 8000, worker: internal, never collide").
6. Domain Config / Prompt-and-Behavior Templates
If the system has any "personality," rules-engine, or prompt layer, isolate it into its own file, separate from orchestration code.
Use a build_x() function pattern: a template string with {placeholders}, plus a builder function that interpolates and falls back gracefully (e.g. try/except KeyError: return template) so a malformed custom override never crashes the system.
Within the template itself, structure by: entry condition → flow steps → objection/edge-case handling → style/tone rules → tool-usage rules. This ordering (happy path first, then edges, then constraints) is reusable for any conversational or decision-driven system, not just voice agents.
7. Data Access Layer
One file, functions grouped by domain/table with # ── Table Name ── section comments.
Every function does exactly one query/mutation. No business logic here — just CRUD plus light aggregation (stats, joins-by-code where the DB doesn't need a real join).
Async-first if the runtime is async; keep a single _client() factory function so connection setup lives in one place.
Put a "known keys" allowlist near settings-style tables so arbitrary keys can't silently pollute the schema.
8. Action / Tool Layer
Separate from the data layer. This is where the system does things to the outside world (send SMS, call an external API, transfer a call, hit a webhook).
Each action function: docstring stating exactly when to call it and what its inputs mean — write these docstrings as if an LLM (or a junior dev at 2am) is the only reader, because often it is.
Every external call wrapped in try/except with a graceful, human-readable fallback string — never let a tool call bring down the whole flow.
Group a build_tool_list(enabled: list) style filter function if the tool set needs to be configurable per deployment/persona/campaign.
9. Orchestration / Entrypoint
The one file that wires everything together for a single unit of work (a call, a job, a request lifecycle).
State explicit sequencing rules as inline comments where order-of-operations is non-obvious (e.g. "dial before starting the AI session, or the session times out during ring").
Load remote config into env before anything else initializes, if that pattern applies.
End with explicit lifecycle handling: how does this unit of work know it's done, and what happens on timeout vs normal completion vs disconnect.
10. API Layer
REST endpoints grouped by resource with # ── Resource ── headers, matching the data-layer groupings from Section 7 one-to-one — this symmetry makes the codebase navigable without a map.
Request/response models declared next to (or just above) the endpoints that use them, not in a separate file, unless the project is large enough to need it.
Background/scheduled work (cron-like jobs) documented with: how it's triggered, how it's rescheduled on restart, and how a "run now" override coexists with the schedule.
11. Frontend / UI Spec
Don't write full UI code in the spec — write the shape: nav structure, one line per screen/tab describing its contents, then a flat list of the JS functions/handlers needed with one-line responsibilities (loadX(), saveX(), deleteX() pattern).
Any reference data the UI needs (dropdown options, enums) gets its own labeled sub-block so it's copy-pasteable.
12. Critical Architecture Rules — DO NOT DEVIATE
This is the highest-value section. Format each rule as:

### Rule N: <one-line name>

<WRONG code block, commented why it's wrong>

<CORRECT code block>

Only include rules that came from an actual bug you hit (or a documented failure mode of a library/API you're using) — not hypothetical caution. A rules section full of "best practices" is noise; a rules section full of "this exact thing broke production" is gold.
13. Deployment
Numbered, copy-pasteable steps from empty VPS/environment to running system.
Include what a successful startup log looks like — so there's a clear "did it work" signal.
14. Known Gotchas Table
Symptom
Root Cause
Fix
Populate this as you build, not before — it's a living artifact. Every time you burn more than 15 minutes on a bug, it earns a row here.





15. Reference Data
Any enums, option lists, or lookup tables the system depends on (voice names, currency codes, status enums) — dumped flat with valid values, so nobody has to go re-discover them from an external API doc mid-build.
16. Cost / Ops Reference
If the system has variable operating cost (API calls, telephony minutes, compute), a simple per-unit cost table plus a worked example ("a typical X costs ≈ Y").
17. External API Integration Reference
For each third-party service used, a minimal, working code snippet for its core operations (create/read/update/delete or the equivalent verbs) — copy-paste ready, no explanation prose. This becomes your own private cheat-sheet independent of the vendor's docs.
18. One-Time Setup Sequence
The exact numbered steps a human runs after deploy, once, before the system is usable (run schema, fill in settings UI, create first config object, test end-to-end). This is different from Section 13 (deployment = getting the code running); this is "getting the product usable."


Quick-start: applying this to a new project
Copy this skeleton, keep the numbering.
Fill Sections 0–4 first and stop — get config/schema/deps agreed before writing a line of logic.
Write Sections 7 → 8 → 9 → 10 in that order (data → actions → orchestration → API) — each layer only depends on the ones before it, so building in this order means you never have to stub something out temporarily.
Leave Section 12 (rules) and Section 14 (gotchas) empty until you've actually built and broken things once — then backfill. Don't invent rules speculatively; they lose their teeth.
Sections 11, 15–18 can be filled in parallel with anyone else on the build, since they don't block the core logic.


Compressed one-page checklist (for fast reuse)
Mission + stack + "build exactly" framing
File tree with one-line responsibilities
.env grouped by service + settings precedence stated
Idempotent schema, access control stated per table
Pinned dependency list
Dockerfile + start script with visible config echo
Domain/prompt templates isolated from orchestration
Data layer: one function = one query, grouped by table
Action/tool layer: docstrings for when, graceful fallbacks
Orchestration: explicit sequencing + lifecycle handling
API layer: endpoints grouped to mirror data layer
UI spec: shape + function list, not full code
Rules section: wrong-vs-correct, only real bugs
Deployment steps + "what success looks like"
Gotchas table (living doc)
Reference data dumped flat
Cost table if applicable
Per-service API cheat-sheet
Post-deploy one-time setup checklist

