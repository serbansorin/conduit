# Teacher agents

One agent per course. Each teacher owns a private knowledge base holding that
course's material — slides, lecture notes, problem sets.

## Adding a course

The EPiC courses are generated, not written by hand:

```bash
# The UPB plan, from the faculty's AQ portal.
python3 tools/extract_upb_plan.py

# The Erasmus partner tracks, from the programme handbook.
python3 tools/extract_epic_curriculum.py ~/university/epic/_programme/E-PiCo+-Handbook.pdf
```

For a course of your own, copy `_template.yaml`, fill in `name`,
`description` and `role`, point `rag.sources[0].path` at the material folder
and run:

```bash
agentic-crew ingest --agent teacher-<course-code>
```

See `CURRICULUM.md` for the programme reference.

## A note on the material

PDF text extraction uses `pypdf`, which reads the text layer. A **scanned**
PDF has no text layer and will ingest as empty — run those through OCR first.
Lecture slides exported as PDF usually have a text layer and are fine.

## A note on the model

`deepseek-v4.1-flash` is the default. It is cheap, and because the teacher
answers from retrieved material rather than from memory, that is enough for
tutoring questions. A course with heavy proof-based content — signal
processing, electromagnetism — may be worth a stronger model; set `model` in
the agent's YAML and nothing else changes.

## How a teacher answers

The teacher's `instructions` are the system prompt. Everything it cites comes
from its own collection, so it cannot accidentally quote another course's
material.
