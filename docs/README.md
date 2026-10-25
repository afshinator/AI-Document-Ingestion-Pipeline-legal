# Docs

Two kinds of document live here, split by lifecycle.

- **Evergreen** (top level): edited in place, always describes current truth.
- **Process** (`process/`): append-only, describes how the work happened. Never rewrite an entry; correct it with a new entry that supersedes it.

## Layout

```
docs/
  README.md                this map
  process/
    engineering-journal.md   append-only, dated events
    decision-log.md          ADR-NNN
    experiment-log.md        EXP-NNN
    failure-log.md           INC-NNN      (created at first real bug)
    milestones/              NN-slug.md   (created at first milestone boundary)
    case-study.md                         (created at end of implementation)
```

`spec.md`, `architecture.md`, `implementation.md`, `evaluation.md` go at the top level when they are written.

## When to write what

| Activity | What to capture | When |
|---|---|---|
| Journal | What you attempted, what happened, what surprised you | Throughout |
| Decision log | Options, tradeoffs, and why one was chosen | On every meaningful choice |
| Experiment log | Hypothesis, configuration, measurements, conclusion | Every experiment |
| Failure analysis | Incorrect output, root cause, fixes attempted | When investigating a failure |
| Milestone report | What works, what doesn't, what was learned | End of each milestone |
| Case study | The engineering story, supported by evidence | After implementation |

Keep the split honest:

- **Journal records events. Decision log records conclusions.** A journal entry links to the ADR it produced and never restates the tradeoff.
- **Links point backward only.** Journal cites the ADR. ADR cites the experiment that justified it. Nothing cites forward.
- **Reference IDs from commits:** `Refs: ADR-NNN, EXP-NNN`. This is what lets the case study be assembled from the logs instead of written from memory.
- IDs are unique for the life of the project and never reused. Prefixes `ADR-`, `EXP-`, `INC-` are reserved for this namespace.

## Format rules

- Prose is not hard-wrapped: one paragraph per line. Renderers reflow, so hard wraps only show in the raw source, at one unambiguous width.
- Omit any section that is empty. An ADR with no related experiment has no `Refs:` line rather than an empty one.
- Every entry starts with an `## YYYY-MM-DD <ID> — <title>` heading, ISO dates only, so the logs stay greppable. Use `YYYY-MM-DD` literally in templates so they cannot match an evidence grep.

### engineering-journal.md

```
## YYYY-MM-DD — <what you set out to do>
Did / Happened / Surprised by
Refs: ADR-NNN, EXP-NNN
```

### decision-log.md

```
## YYYY-MM-DD ADR-NNN — <the decision, stated as a decision>
Status: accepted | rejected | superseded by ADR-NNN
Context / Options / Decision / Consequences
Refs: EXP-NNN
```

### experiment-log.md

```
## YYYY-MM-DD EXP-NNN — <hypothesis, one line>
Hypothesis / Configuration / Measurements / Conclusion
Refs: ADR-NNN, commit or script path
```

### failure-log.md

```
## YYYY-MM-DD INC-NNN — <observed symptom>
Observed / Scope (how many docs, which) / Root cause / Fix attempted / Status
Refs: INC-NNN, commit
```
