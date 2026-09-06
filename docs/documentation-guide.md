# Documentation rules

For all project documentation:

1. **Be concise.** Lead with purpose; use plain language, short paragraphs, and mapping tables. Link instead of repeating detail.
2. **Label status.** Separate requirements, current behavior, gaps, and proposals. Planned is not implemented.
3. **Ground claims.** Check/link the code; distinguish source review, reproduction, and automated coverage. Passing tests establish only what they cover.
4. **Explain data transformations.** For each rule, identify raw inputs, field paths, transformation, output, units, defaults, missing-data behavior, and information loss. Include a small worked example where useful.
5. **Use consistent terminology.** Follow the [lineage reference](data-lineage.md) for RFC 3339 timestamps and Normalized, Calculated, Inferred, and Model-generated origins. Keep origin separate from timing quality.
6. **Show uncertainty.** State unsupported cases and decision impact. Missing evidence is not zero, success, or certainty.
7. **Preserve decisions.** Flag conflicting requirements and ask for clarification before changing those points. Routine wording and formatting need no approval. Keep deferred wishes separate from current correctness gaps.
8. **Maintain the evidence trail.** When behavior changes, update affected docs, examples, and relevant tests together. State version/date where material and explain effects on existing data, reimports, and backfills. Label synthetic or redacted examples.
9. **Verify.** Check links, examples, arithmetic, and implementation consistency. Include private content only when necessary and authorized.

## Where information belongs

| Document | Purpose |
| --- | --- |
| [README](../README.md) | Quick start and navigation |
| [Specification](specification.md) | Requirements, status, and acceptance criteria |
| [Architecture](architecture.md) | Components, boundaries, and design choices |
| [Data lineage](data-lineage.md) | Exact mappings and metric semantics |
| [Data-quality gaps](data-quality-gaps.md) | Current correctness concerns, evidence, and verification cases |
| [Backlog](backlog.md) | Deferred capabilities and future wishes |

Keep each detailed rule in one primary document; cross-link elsewhere.
