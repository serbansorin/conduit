# agents

Agent definitions, one YAML file per agent. The orchestrator loads every
`*.yaml` in this directory and its subdirectories; a file whose name starts
with `_` is treated as a template and is not registered.

- `getubetter-*.yaml` — the GetUBetter crew (master + Angular, Ionic,
  Laravel, October).
- `teachers/` — the EPiC teacher crew, one agent per course.
- `teachers/_template.yaml` — the template for adding a course by hand.
