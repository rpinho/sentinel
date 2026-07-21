# Triage architecture

Sentinel's triage boundary is a signal-scoped claim, not a time bucket. One
candidate states that one series, for one segment and one window, deviated from
its own phase-matched baseline in a harmful direction. Its stable identity is
`series × segment × window`; it must be adjudicable without asking a judge to
discover which part of a mixed repository-hour mattered.

The detector settles the statistical question before triage. The judge decides
whether the scoped deviation is real, benign, known, or unresolved. Separate
scoped candidates caused by one incident are expected. They are grouped into a
single story after adjudication; downstream code never tries to un-mix a bag.

## Evidence flow

Triage is explicitly two phase:

1. The light screen sees every eligible candidate as a compact row: identity,
   claim, current value, numerator and denominator, phase baseline and quality,
   deviation, absolute-impact fields, concentration, one bounded excerpt, and
   matching feedback lessons.
2. Only candidates elected for investigation receive a bounded heavy pack. The
   pack embeds its metric definition, methodology, baseline caveats,
   segmentation, sibling pointers, and attribution checks. It carries normalized
   excerpts rather than raw event dumps and never includes post-cutoff evidence.

Snapshot claims that can become stale—current ownership, open-incident state,
or whether a fix shipped—must be checked at verdict time in a production
deployment. They are not assumed from old context.

## Verdicts and stories

Every generated candidate receives an auditable disposition. Automation,
below-floor signals, capped echoes, and cooldown duplicates are deterministically
dismissed with reason codes. The light judge returns `investigate`,
`benign_dismiss`, `watch`, or `insufficient_data`. The investigation judge
returns `finding` with severity, `benign_dismiss`, `known_carry`, `watch`, or
`insufficient_data`.

Severity is derived from breadth, absolute magnitude, velocity, and confidence.
Deviation score only admits a candidate; it does not determine severity.
Near-zero baselines are marked because they can create impressive scores from
trivial changes. High-severity claims require corroboration. Release or change
attribution requires a separate verification drill—temporal correlation is not
causation. Published findings must cite exact text from the bounded evidence.

Sibling findings group into one story using a stable signal identity, with exact
issue number preferred when present. A story is the alert. Watch and carry are
lower-interruption outputs, not hidden findings.

## Rate discipline

The intended funnel compresses at every stage: many scored series-windows,
fewer detector flags, fewer eligible scoped candidates, a small investigation
set, and very few published stories. Each live 15–60 minute cycle screens its
complete compact candidate set in one invocation when it fits the configured
cap. Historical replay may partition the census into equivalently sized exact
requests. Candidate caps have deterministic class ordering so lower-trust echo
floods cannot starve issue-thread signals. Per-identity cooldowns prevent one
flapping signal from producing multiple alerts.

The interrupt tier should be quiet on most days and the digest should be
scannable in minutes. If either is noisy, candidate generation and gates tighten
upstream.

## Feedback and learning

At the start of a run, the judge receives active, matching feedback as untrusted
historical hypotheses to verify. Human grading, incident outcomes, follow-up
resolution, and the versioned scorecard are complementary label sources. Fast
feedback is not durable learning by itself: validated lessons are folded into
versioned instructions and a curated recurrence library. Deterministic lessons
move farther upstream into metric definitions, floors, exclusions, and gates.

Feedback is append-only and superseding corrections determine active state.
Blanket suppressions are avoided; known conditions use explicit bands and
closure criteria. Carries must re-check live state, fixes count when shipped,
streaks are recomputed idempotently from underlying data, and definition changes
re-anchor affected history. Reviewer text is data, never executable instruction.

## Guardrails learned from failure

- Candidate bags corrupt both judgment and feedback labels; scope by signal.
- Evidence flooding causes truncation and weak, vibe-based grading; fetch heavy
  evidence only after election.
- Evidence starvation cannot be solved by prompting; repeated unknowns identify
  missing instrumentation.
- Familiar-looking known issues require signature concordance, live coverage,
  and a recovery probe before suppression.
- Dismissal reasons must be valid for the metric construction; normalized rates
  cannot be explained away by raw traffic growth.
- Slow-burn recurrence must accumulate across cycles so locally modest signals
  do not reset to benign forever.

This design is implemented and frozen for the v7 architectural study in
`holdout/v7/protocol.json`.
