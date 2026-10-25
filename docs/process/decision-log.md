# Decisions

### Purpose

Explain consequential choices and alternatives

Append-only. One entry per meaningful choice, including choices about process. Superseded entries stay in place and gain `Status: superseded by ADR-NNN`. Schema is in `../README.md`. Prose is not hard-wrapped: one paragraph per line.

---

## 2026-10-24 ADR-001 — Stable prefixed IDs plus a `Refs:` commit trailer

**Status:** accepted

**Context.** The case study must be supported by actual evidence gathered during development. That only works if the evidence is machine-findable, which rules out prose that refers to "the chunking decision" or "that experiment in week 3."

**Options.**

- Free prose cross-references between documents.
- Per-document local numbering (`D1`, `E3`).
- Globally unique prefixed IDs (`ADR-001`, `EXP-007`, `INC-003`).

**Decision.** Globally unique prefixed IDs, never reused, referenced from git commit messages via a `Refs:` trailer. Local numbering rejected: it collides across documents and with shorthand used in other contexts. Same ID is reused nowhere, so it is greppable from any context.

**Consequences.** Entry headings are fixed as `## YYYY-MM-DD <ID> — <title>` (ISO dates) so a shell one-liner can pull a milestone's evidence. Cost is a namespace to maintain by hand.
