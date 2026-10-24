# Improving the agent crew for coding work

This document is the plan for making the crew reliable on large, multi-repo
codebases. It is written from the pain points reported by developers who use
coding agents on projects like GetUBetter (mobile + dashboard + API backend
with cross-service calls).

## The five problems

1. **The project is not mapped.** An agent enters a 65-skill, four-subproject
   monorepo with no idea what exists. It searches, finds something
   plausible, and starts writing against it — often the wrong thing.

2. **No coding standards.** Each run produces code in a different style.
   The model picks whatever conventions it feels like, and the result drifts
   from the codebase it is supposed to match.

3. **Recreating instead of reusing.** The agent does not know that
   `UserService.create()` already exists, so it writes a new one. Two
   services now do the same job, and the next run picks the wrong one.

4. **Not seeing a folder.** A directory is missed because the glob was too
   narrow, or the agent never listed it. The agent then "creates" a file
   that already exists, or imports from a path that does not exist.

5. **Inconsistent across models.** The same question gets a different
   answer depending on the model. There is no shared contract for what
   "done" looks like.

## The fixes

### 1. A code map, generated and injected

`agentic-crew/tools/codemap.py` walks a project and produces a single
Markdown file: every directory, every source file, and every top-level
definition (class, function, method) with its signature. The file is meant
to be folded into an agent's skills, so the agent starts every task with
the answer to "what already exists?".

```bash
python3 tools/codemap.py ~/imc-dev/www/getubetter/app.gap \
    --out agents/maps/getubetter-angular.md
```

The map is regenerated, not hand-written. A cron or a git hook re-runs
it when the source changes, so the agent never works from a stale picture.

### 2. Coding standards as skills

A skill file is the right place for a project's conventions, because it is
read as text and folded into the system prompt. The agent always has it.

```yaml
skills:
  - name: coding standards
    path: ~/imc-dev/www/getubetter/.opencode/AGENTS.md
    include: "**/*"
    max_chars: 20000
```

`AGENTS.md` already exists for GetUBetter and holds the conventions. The
fix is to make sure every agent that touches the project has it loaded, and
to keep it accurate. A standards file that contradicts the code is worse
than no standards file.

### 3. Search before create

The agent's instructions should say, in the first line of every task:

> Before creating a file, search for a file that already does it. Before
> calling a service, confirm it exists. Before naming a variable, check
> how the codebase names the same concept.

This is a prompt-level rule, not a code change. It costs nothing and it is
the single most effective fix for problem 3.

### 4. A "what changed" brief per task

Before the agent writes code, it should produce a short brief:

- which files it will touch,
- which existing functions or services it will reuse,
- which new files it will create and why,
- what the coding standard says about each.

The brief is shown to the user before any code is written. This is the
"plan before you code" discipline, applied to agents.

### 5. A verification step after every change

The agent runs the project's own checks — `go test ./...`, `npm run build`,
`php -l` — and reports the result. If the checks fail, the agent fixes
them before claiming the task is done. No exceptions.

### 6. Multi-repo awareness

For a project like GetUBetter, the agent needs to know:

- which sub-project owns which concern,
- which services talk to which,
- where the API contract lives.

This is a single file — `AGENTS.md` at the root of the monorepo — that
maps the three sub-projects and their communication. The master agent reads
it; the specialists read their own scoped `AGENTS.md`.

### 7. Consistency across models

The fix is not a single model. It is a contract:

- the system prompt is the same for every model,
- the coding standards are the same for every model,
- the verification step is the same for every model,
- the plan-before-code brief is the same for every model.

The model only decides *how* to satisfy the contract. The contract itself
is fixed.

## What this looks like in the crew

A `getubetter-master` agent with a code map skill:

```yaml
name: getubetter-master
description: "Master engineer for the GetUBetter platform."
role: Master engineer
model: glm-5.3
capabilities: [getubetter, master, architecture, fullstack]

skills:
  - name: project map
    path: agents/maps/getubetter.md
    include: "**/*.md"
  - name: coding standards
    path: ~/imc-dev/www/getubetter/.opencode/AGENTS.md
    include: "**/*"
    max_chars: 20000

delegates:
  - getubetter-angular
  - getubetter-ionic
  - getubetter-laravel
  - getubetter-october

rag:
  collection: getubetter-master
  sources:
    - path: ~/imc-dev/www/getubetter/.opencode/skills
      include: "**/*.md"
```

The master reads the map, reads the standards, and delegates to the
specialist that owns the question. The specialist has its own map and its
own standards.

## What is already built

- `agentic-crew/tools/codemap.py` — the code map generator.
- `agentic-crew/tools/make_agent.py` — the agent generator.
- `agentic-crew/agents/getubetter-*.yaml` — five agents covering the
  platform.
- `agentic-crew/agents/teachers/` — 63 teacher agents.

## What is not built yet

- The "search before create" rule in the agent instructions.
- The "plan before code" brief step in the orchestrator.
- The verification step in the agent loop.
- A CI check that regenerates the code map and fails if it is stale.

These are prompt-level changes, not code changes, and they are the highest-
leverage work on the list.
