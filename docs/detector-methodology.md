# Detector v2 — design methodology

*The design blueprint for Sentinel's detector v2. Generic, IP-clean anomaly-detection engineering
technique (independently redaction-checked; no proprietary specifics, no thresholds/numbers). It is
what the v1 holdout was missing — v1 fired on periodic bot noise and missed low-burst real
incidents; the techniques below (phase-matched baselines, rates-over-counts, robust locked
baselines, two-bar gating, persistence, parallel bot-excluded series) are the principled fixes.*

---


# Anomaly detection that earns trust — transferable methodology

These are generic engineering techniques for making a burst detector on time-series event data both precise (quiet on periodic noise) and sensitive (loud on real incidents). Everything below is written as method, applicable to any well-built detector on any event stream. Where useful, each section ends with a concrete mapping to per-entity hourly event counts of the kind a public archive of repository activity provides (issues opened, comments created, releases published per repository per hour; daily package download counts).

Two framing ideas before the techniques:

- **The trust criterion is external.** A detector earns trust the first time it catches a real problem before any human reports it — and keeps trust by publicly keeping score of its misses and false alarms afterward. Every technique below exists to serve that bar, not to improve an offline metric.
- **Precision and recall come from different layers.** Precision mostly comes from baselining, gating, and suppression (sections one through four and seven). Recall mostly comes from metric design, segmentation, and the learning loop (sections two, seven, eight). Confusing the layers — e.g., fixing noise by globally raising thresholds — buys precision by silently destroying recall.

## 1. Seasonality / periodicity — phase-matched baselines

**Principle.** Never compare an observation against a flat trailing baseline. Compare it against the same *phase* of the dominant cycles: daily-grain values baseline only against the same day-class (weekday vs weekend, or the same day-of-week), and hourly-grain values baseline against the same hour-of-day across prior days. The baseline window spans multiple weeks so each phase has enough samples.

**Why it earns trust.** Predictable periodic load is the single largest false-positive source in naive detectors. Phase matching makes "this hour is always busy" structurally invisible — the busy hour is compared only to itself on prior cycles — while a burst that is off-phase for that entity stands out immediately. Crucially, this also absorbs *periodic automation* without any knowledge of who the actors are: a scheduled job that fires at the same time every day or week becomes part of that phase's own baseline. Phase matching thus fixes a large share of the automation false-positive problem for free, before any actor classification exists.

**One operational consequence to design for:** phase-matched pools refill independently. After any baseline reset, the rarer phase (e.g., weekends) takes several calendar weeks longer to accumulate enough samples than the common phase. Treat each pool's warmup separately rather than assuming one global warmup date.

**Mapping.** Per (repository, event-type): score hour H against the distribution of hour H over the trailing several weeks, with weekday/weekend split. Nightly CI storms, scheduled dependency-update bots, and weekly triage sweeps all disappear into their own phase. Anything cron-shaped is handled here; only *aperiodic* automation still needs actor-level treatment (section seven).

## 2. Metric design — rates over counts, with an explicit denominator contract

**Principle.** Store every scored metric as a triple: numerator, denominator, rate. Score the rate; use the denominator for significance gating (section four); keep the raw count as a separate volume gauge with its own alert direction. Choose the denominator to match the actual question:

- *Outcome per attempt* (success/failure rates) — for "is the thing working".
- *Events per active entity* (activity per participant, per open item) — for "is behavior changing", scale-free across entities of different sizes.
- *Share of total* (one category's fraction of all events) — for composition shifts.

**The denominator contract:** exclude undecided/in-flight items from outcome-rate denominators — during busy or degraded periods, in-flight items pile up and artificially deflate the rate, manufacturing false alarms exactly when scrutiny is highest. Count attempts in a separate volume line instead.

**Why it earns trust.** Counts conflate popularity with health. A large entity's ordinary Monday dwarfs a small entity's total collapse; scoring counts means the detector is really a size detector. Rates make entities comparable and let one set of thresholds serve a heterogeneous population. Keeping the volume gauge *as well* matters because a rate and a volume anomaly mean different things: a rate break says quality changed; a volume collapse says supply died (which a pure rate metric can miss entirely if the numerator and denominator die together).

**Polarity belongs to the metric.** Every metric declares its harmful direction (spike-bad for errors and latency-like series, drop-bad for success and volume series), and severity is only assigned in the harmful direction. A drop in an error-like series is an improvement, not an incident.

**Mapping.** Instead of raw issue counts: issues opened per active participant that hour; comments per open issue; the share of events produced by first-time participants. Keep raw per-hour counts as drop-direction volume gauges — a normally-active repository going silent is its own failure mode. For daily download counts, score relative change against phase-matched history rather than absolute counts, and keep a separate collapse-direction gauge.

## 3. Robust baseline statistics — median centers, floored dispersion, locked pools

**Principle.** Compute the baseline center as the *median* of the phase-matched window, not the mean. Compute dispersion robustly (median-absolute-deviation style) and put a *floor* under it — a small fraction of the center value — so a near-constant series cannot produce astronomically inflated deviation scores from microscopic variance. The score is then a robust z: (observation − median) / floored dispersion.

**Baseline hygiene ("locking").** Exclude previously-flagged periods from the baseline pool. This prevents two failure modes: a past spike inflating the baseline so the follow-up regression looks normal, and a sustained regression dragging the baseline down until the incident "recovers" by redefining normal. Two refinements that matter in practice:

- Exclude *mildly* elevated periods too, not just the loudly flagged ones — gradual baseline poisoning happens through borderline days that individually never looked alarming.
- Guard the other direction: when the filtered pool gets too thin (too few clean samples remaining), fall back to the unfiltered pool. A baseline computed from almost nothing is worse than a mildly contaminated one — and for a genuine *permanent* regime shift, the fallback is what lets the detector eventually accept the new normal instead of alerting forever.

**Cold start / bootstrap.** On first build (or a full rebuild), you don't yet know which historical periods to exclude. Score the history once against unfiltered baselines, then use those verdicts as the exclusion set for the real pass. One rebuild converges.

**Warmup.** Entities or metrics without enough phase-matched history get an explicit "immature" state and are never alerted on — silence with a reason, not a verdict. When adding a new metric, backfill its history immediately: every day it runs without a baseline is a day the window fills with the *current* level, and if the current level is already broken, the detector is born blind to exactly the problem it was built for.

**Regime changes.** When a definition change or a legitimate product change moves a metric to a new normal, re-anchor the baseline start date explicitly rather than letting old-regime days pollute the pool — then expect per-phase warmup again (section one).

**Mapping.** Median and MAD-style dispersion per (repository, event-type, hour-phase) over the trailing weeks; flagged hours excluded from future baselines with a thin-pool fallback; new repositories sit in "immature" until they have enough matched history; a repository that visibly changes character (archived → revived, adopted by a large org) gets a re-anchor rather than a month of alerts.

## 4. Minimum-exposure / significance gating — two bars, both required

**Principle.** A deviation surfaces only if it clears *both* a statistical bar and an operational-materiality bar. Layer the gates:

- **Volume floor:** below a minimum denominator (or minimum events per interval), don't score at all. Thin streams produce garbage z-scores in both directions.
- **Absolute floor on rates:** for metrics whose baseline sits near zero, tiny absolute moves produce enormous relative deviations (near-zero dispersion inflates any z). Below a per-metric absolute level, force the verdict to "normal" *before* the statistical rules run.
- **Interval-based verdicts for comparisons:** when comparing two populations (before/after, cohort A/B), use confidence-interval-based tests (rate ratios, two-proportion tests) and emit an explicit "insufficient exposure" verdict when the data can't support a conclusion — silence and "not enough data" must be distinguishable.
- **Conclusive-effect override:** allow an overwhelming effect to override the volume floor (a total collapse on modest volume is decidable even where a mild drift is not).

**The hard-won insight worth internalizing:** at low baselines, statistical noise and real regressions are *numerically identical* — no variance trick separates them. Attempts to fix low-baseline noise generically (e.g., widening dispersion globally) were tested against history and rejected, because the same widening downgraded genuine regressions elsewhere. The separation is an operational judgment — "below this absolute level, nobody would act" — encoded per metric as a floor. Resist the one-global-fix temptation; the floors are small, explicit, reviewable config.

**Mapping.** Minimum events-per-hour before a (repository, event-type) is scoreable; an absolute change floor (in percentage-point-like terms, not just ratio terms) for thin streams; an explicit "insufficient data" verdict in the output schema rather than silent absence — the triage layer must be able to tell "quiet" from "unjudgeable".

## 5. Confirmation and the trailing verdict — persistence, settledness, revision

**Persistence before alerting.** Require more than one consecutive flagged interval before a fast-lane alert surfaces. This deliberately concedes the first interval(s) of a real incident in exchange for a large precision gain — the single-interval V-shaped dip is the classic noise shape, and paging on it destroys trust faster than the conceded lead time earns it. Reserve single-interval firing for the rare detector classes that carry independent corroboration built into their admission rule. Add — sparingly, and only backtested per class — a narrow "recurrent within a window" exception for bursty degradation that repeatedly flags without ever being consecutive.

**Settledness before verdicts.** Never issue a verdict from still-arriving data. Define "settled" mechanically: the value stops changing across consecutive recomputations, or the interval is older than the source's known settling window. Late-arriving data is the other classic false-positive source (incomplete denominators read as collapses), and it also produces false *recoveries*. Related: decide explicitly what *absence* means. A source that stops producing rows during an outage does not emit zeros — it emits nothing. Zero-fill scored series on a shared time spine so a dead source becomes a scoreable zero (a real alert) rather than a silently missing row (no alert). Distinguish "the entities went quiet" from "ingestion broke" — both matter, to different audiences.

**Trailing verdict revision.** Recompute recent intervals on every run within a trailing window, so verdicts self-revise as data settles. Treat every alert record as a living record through closure: a recovery claim requires the metric back at *its own* baseline, computed from settled data, held for consecutive intervals — never declared from inside a settling window, and never inferred from a related-but-different series.

**The scorecard.** Keep a running, version-controlled record of true positives, false positives, false negatives, and detection-to-response timing, updated through each episode's closure. The credit rules are what make it trustworthy: a detection reconstructed by backfill after the fact is recorded as coverage, never credited as a live catch; every win keeps its misses inline; the false-positive table is as load-bearing as the catch list. A scorecard that only records wins is marketing, and everyone can tell.

**Mapping.** Consecutive flagged hours before surfacing; for a source that publishes complete hourly archives, settledness is nearly free, but daily download counts settle late — gate verdicts on each source's maturity window. Publish the scorecard in the repository with the credit rules stated; re-run the frozen holdout after each detector change and record both improvements and regressions.

## 6. Severity tiering — impact times velocity, capped by confidence

**Principle.** Severity is computed from a handful of generic inputs, then mapped to *actions*, not adjectives:

- **Breadth** — how much of the population is affected: share of entities, share of participants, normalized by exposure (impact per active entity beats raw counts).
- **Depth** — deviation size vs the entity's own baseline, in ratio *and* absolute terms.
- **Velocity** — worsening across consecutive intervals outranks a static deviation.
- **Confidence** — data maturity, corroboration across independent series, actor-quality checks passed.
- **Asset tier** — a curated list of the entities/metrics that matter most gets a priority lane; everything else rides the digest.

Each severity level maps to a distinct action: note silently (recorded, queryable), surface in the periodic digest, or interrupt a human now. Structurally-lower-confidence classes are *capped* below the interrupt level no matter their scores. Finally, rank all candidates by an attention score and cap how many surface per cycle — with exemptions for the highest-tier classes, so that a flood of correlated echoes from one incident cannot crowd the genuinely distinct second incident out of the day's budget.

**Mapping.** Severity from affected-share (participants/threads touched) × deviation depth × persistence, with a hand-curated top tier of repositories that are allowed to interrupt; everything else lands in a ranked, capped daily digest. Cap automation-suspect candidates below interrupt level until an actor check clears them.

## 7. Noise suppression — the specific classes of move that create precision

Each of these is generic, cheap, and was load-bearing. Presented as a catalog; the first two directly attack automation floods.

- **Automated-actor exclusion as a first-class dimension.** Classify actors with transparent rules — account type where the platform exposes it, name patterns, payload signatures, hyper-regular timing, single-actor concentration — and compute every scored series twice: raw, and excluding classified actors. Score both. The *divergence between the pair is itself a diagnostic*: excluded-series flat while raw moves = automation artifact, close as noise; both move = real. Two design rules: never use one weak attribute alone as the classification boundary (require multiple concordant signals, or you'll misclassify real actors); and treat the classifier as versioned config whose changes trigger baseline re-scoring, because reclassification shifts every series it touches.
- **Concentration checks before flagging.** Compute the top-contributor share of any deviation before surfacing it. One actor producing the overwhelming majority of the delta is an actor story, not a population story — different audience, different severity, different route. This one check kills a startling fraction of naive alerts.
- **Segmentation, because aggregates both hide and fake anomalies.** Blends dilute real localized incidents below thresholds (a total outage in a small segment moves the blend a little) and manufacture fake ones via mix shift (segment proportions change, every segment's rate is stable, the blend moves anyway). Score segmented series alongside the blend, and decompose any blended move into mix-vs-rate before believing it.
- **Failure composition by design.** A blended failure rate is dominated by benign, ambient churn and its baseline is high and noisy — it can sit *below* baseline during a real outage confined to a small slice. Build reason-specific series (near-zero baselines, so any spike is signal) plus one composite-by-exclusion series: everything *not* on the explicit benign allowlist. Defining the composite by exclusion is what closes the "a brand-new failure mode appears and nothing counts it" gap.
- **Score everything; page on few.** In a mature deployment, a large fraction of plausible-looking metrics fire constantly — chronically-firing series are noise by construction, whatever their z says. The reliable interrupt-worthy core is the small set of quiet-until-broken outcome ratios (success/completion aggregates). Keep the noisy breakdowns *computing* as drill-down context; just don't let them page. Route by signal value, don't delete.
- **Cooldowns and dedup.** A per-identity cooldown window after each surfaced candidate, and deduplication by identity tuple with a deterministic tie-break, so a flapping series or a re-run cannot multi-page the same story.
- **Per-class calibration, never templated.** Copying gate values from a sibling metric family ships a blind detector — different families have different variance structure, and a gate tuned for outage shapes misses slow-burn shapes entirely. Every gate is backtested per class (section nine).
- **Known-cause carry with a closure criterion.** When a deviation is explained and owned, carry it forward annotated rather than re-alerting — but attach an explicit closure criterion (what settled evidence retires the carry), and require the carried explanation to still *fit the current slice* before it suppresses anything new. Dismissal-by-familiarity — "looks like the known thing" without re-checking the slice — is one of the most expensive triage failure modes there is: the real incident that resembles a resolved one gets waved off.

**Mapping.** Bot flags from account metadata + name patterns + timing regularity + concentration, applied as a parallel excluded series with divergence checks; top-contributor share computed on every candidate; per-repository and per-actor-class segmentation with mix-vs-rate decomposition on any aggregate move; changeset/stale/CI commenters absorbed by phase matching where periodic and by actor exclusion where not; carry known release-week storms with an expiry instead of re-flagging them daily.

## 8. The learning loop — deterministic config, contextual judgment, human grading

**The split that makes it work:**

- **Deterministic layer (config as code).** Floors, gates, exclusion lists, tiers, registries, re-anchor dates — all live in version control. Every change is a reviewed diff with a backtest attached. This layer is cheap, testable, and does not drift.
- **Contextual layer (the triage agent).** An agent — LLM or human — reads each surfaced candidate *plus context*: is an incident already known and owned? does an external monitor already cover this? does the shape match a documented past pattern? It decides surface / dismiss / carry, and writes its reasoning down. Its instructions are themselves versioned files, not tribal memory.
- **The recurrence library.** A curated "have we seen this before" document: signature → past cause → resolution → known false-flag patterns. The triage layer consults it before escalating; every resolved episode appends to it. This converts one-time debugging into permanent triage capability.
- **Human grading.** A recurring feedback channel where a human grades the triage output (over- and under-escalation both), which the agent reads back on its next cycle. The critical discipline: durable lessons get *folded into the versioned instructions and the recurrence library*, never left in the feedback thread — feedback that stays in chat evaporates.

**The flywheel:** every false positive becomes a config diff (a floor, a guard, a maturity rule, an exclusion) and every false negative becomes a coverage diff (a new metric, a segmentation, a gate exception) — shipped while the evidence is fresh, recorded in the scorecard with the episode that motivated it. Periodically run a coverage audit in both directions: sweep externally-known incidents against detector output (every uncredited incident is a named gap), and sample quiet stretches for silent misses.

**Why deterministic-first:** config changes compound and never regress silently; agent judgment is reserved for what config cannot encode — novelty, cross-source context, ownership. The failure mode to avoid is the inverse allocation: an agent memorizing threshold trivia while config tries to encode judgment.

**Mapping.** Detector config and its change history in the public repository; the triage agent gets a versioned prompt, a recurrence library file it both reads and appends to, and a grading loop from holdout reviews; the frozen public holdout re-run is the coverage audit.

## 9. Other load-bearing practices the questions didn't name

- **Backtest, don't invent — the meta-rule for every number.** Every threshold traces to two reproductions: the motivating real episode *fires* under it, and a healthy contrast window *stays silent* under it. That pair is the acceptance test. If a threshold's justification doesn't reproduce when re-derived from the raw data, stop and reconcile before shipping — alignment and rollup boundaries shift aggregates more than people expect.
- **Adversarial A/B for semantics changes.** Before shipping any change to matching or classification semantics, replay it against a *known-broken* historical case. A "fix" that also un-flags a confirmed true positive is rejected regardless of how much noise it removes. This has killed multiple obvious-looking cleanups that would have quietly lobotomized the detector.
- **Detection is not escalation — instrument the gap.** Track "first flagged" and "first human action" as separate timestamps and measure the lag. A correct signal sitting unread in a report is a silent alarm; the fixes are different from detection fixes (routing, ownership, making urgency legible). Express urgency in the stakeholder's units — affected entities per unit time, outcomes at risk — never in z-scores.
- **Corroborate onset across independent sources.** Before a high-severity claim, check that independent instrumentation paths agree on the onset interval. Concordance at the same timestamp across unrelated collection paths is powerful evidence the move is real and not a pipeline artifact.
- **Grain-match the failure mode.** Interval aggregation dilutes sub-interval bursts below any ratio gate. If the failure mode is shorter than the scoring grain, score run-length at the finer source grain (consecutive below-floor buckets) instead of hoping the coarse ratio moves.
- **Compare structurally different populations by their relationship, not their levels.** When one population is *expected* to differ from another (a debug cohort vs the main population), score the deviation of their ratio from that ratio's own history — "page the delta, not the level" — or the structural gap fires forever.
- **Self-documenting alert payloads.** Embed the metric's definition, baseline construction, and methodology notes in the alert payload itself, so the triage layer can never misread what fired or re-derive it wrong under time pressure.
- **A registration contract for anything that can page.** Every alert-capable series must be registered with an owner and a policy entry; a contract test fails the build when an unregistered series shows up armed. Unowned pagers rot into background noise and take the owned ones' credibility with them.
- **An independent second opinion.** Run one structurally different detector (different method, same raw series) as a cross-check lane. It catches your primary pipeline's blind spots and gives triage a cheap corroboration source.

---

## Redaction self-check (performed on this file before it entered the outbox)

All techniques in this document are stated as generic method ("baseline by phase", "floor the dispersion") with no instance values — textbook-level patterns, verified free of any organization-specific terms, identifiers, or figures before publication.

*Last updated: written on the work laptop and sanitized at the source per the handoff contract; safe to commit to the public project.*
