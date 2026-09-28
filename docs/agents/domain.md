# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This repo is **single-context**: one `CONTEXT.md` and one `docs/adr/` at the root.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-....md
│   └── 0002-....md
├── frontend/       ← the React app
├── backend/        ← the Flask API
├── worker/         ← the collection scheduler
└── design/         ← read-only design mirror
```

If this repo ever splits into multiple bounded contexts, add a `CONTEXT-MAP.md` at the root pointing at one `CONTEXT.md` per context, and update this file.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_

## Record material decisions

Create the next sequential file in `docs/adr/` in the same change whenever a
decision is costly to reverse, surprising to a future maintainer, and has a real
alternative or trade-off. Record the context, decision, reason, consequences and
rejected alternatives; link any ADR that it extends, supersedes or contradicts.

Routine bug fixes, dependency bumps and behavior-preserving refactors do not need
an ADR. If a later decision changes an accepted one, add a new ADR and update the
old ADR's status or cross-reference instead of rewriting history.
