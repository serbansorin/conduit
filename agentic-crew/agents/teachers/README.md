# Teacher agents

One agent per course. Each teacher owns a private knowledge base holding that
course's material — slides, lecture notes, problem sets.

## Adding a course

1. Copy `_template.yaml` and rename it to `<course-code>.yaml`.
2. Fill in `name`, `description` and `role`.
3. Point `rag.sources[0].path` at the folder with the course material.
4. Run:

   ```bash
   agentic-crew ingest --agent teacher-<course-code>
   ```

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
