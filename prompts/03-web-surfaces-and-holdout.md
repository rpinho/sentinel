Work in /Users/ricardo/projects/sentinel (the working, validated Sentinel repo: adapters + detector + feedback store + GPT-5.6 triage with both ablations, all committed). If your working directory is not that path, stop and tell me. Read VALIDATION_REPORT.md, HANDOFF.md, TRIAGE.md, and the existing src/sentinel/ before writing anything — reuse what exists; do not re-scaffold or rewrite working modules. The OpenAI key is in .env (gitignored); load it with python-dotenv as the triage module already does. Commit as you go.

This session builds the two things deliberately deferred: (A) the frozen-config holdout run that produces the honest numbers, and (B) the three web surfaces that make the scorecard visible. Do (A) first — the web surfaces display its output, so the numbers must exist before the UI does.

=== PART A — THE FROZEN HOLDOUT (this is the credibility spine; do it first) ===

The single biggest risk to this project is a judge suspecting the demo was tuned until the webpack incident lit up. The holdout is the answer. Build a reproducible run, `scripts/run_holdout.sh` + a Python driver, that does exactly this and commits its outputs:

1. FREEZE the detector config at its current committed hash (it is already stamped onto firings — do not retune it for the holdout). Record the config hash and the current git commit in the output.
2. SNAPSHOT the feedback store at the moment the holdout begins, and record that snapshot's hash too — because triage learns from feedback, a holdout must freeze BOTH the config AND the feedback set or the "frozen" claim is not true.
3. Run the detector over a HOLDOUT WINDOW that was never inspected during development — pick a different, arbitrary week of GH Archive for a set of popular repos (e.g. a handful of high-traffic npm/JS repos over one full week that is NOT 2025-04-07). Do not hand-pick incidents; take whatever fires.
4. Triage every firing (live once to seed fixtures, then replay), then run the WHOLE holdout a second time with --no-feedback.
5. Emit a scorecard for the holdout: every firing with hypothesis, confidence, culprit, and — where a later maintainer acknowledgment exists in the archive — an auto-labelled verdict (TRUE / FALSE / UNRESOLVED). PUBLISH THE MISSES. Include false positives and any firing that stays UNRESOLVED. Compute precision, median lead time (firing → substantive ack), and false-positives-per-week, and print the same metrics for the --no-agent baseline and the --no-feedback ablation side by side.
6. Write the numbers, both hashes, the commit, and the holdout window into a committed HOLDOUT.md — unedited, whatever they are. A 55% precision you volunteered beats a 95% a judge suspects. If feedback does NOT improve precision, report that too; a project that honestly reports its learning loop didn't help is more credible than one that claims it did and never measured.

Report the raw numbers to me before writing any UI. If the holdout produces something surprising or ugly, stop and tell me rather than smoothing it.

=== PART B — THREE WEB SURFACES (self-contained, judge-testable) ===

A small web app (FastAPI + server-rendered HTML is fine; no heavy SPA) that a judge can open and test without rebuilding. Design is 25% of the score: dark, one accent colour, monospace timestamps, restraint — no chart junk, no gradient hero. Every timestamp on screen is a hyperlink to the real GitHub object.

1. /replay/<incident> — THE HERO. Timeline runs in compressed real event-time. Issue rate climbs, Sentinel fires with a UTC timestamp, the triage card shows cause + named culprit + confidence + the exact pre-firing evidence quotes. THEN the maintainer-acknowledgment marker drops, hyperlinked to the real comment, and the delta renders big: "named the culprit ~2h before the maintainers confirmed the cause." The replay must actually RECOMPUTE from cached raw archive + committed triage fixtures — never serve a stored screenshot. It must run with no API key (fixtures) and offline.
2. /scorecard — THE PRODUCT. Every firing from the holdout: timestamp, entity, hypothesis, confidence, verdict, mode. MISSES INCLUDED and visually marked. Headline row: precision, median lead time, false-positives/week, archive coverage. Show the frozen config hash + feedback snapshot hash. This page, not the alert, is what makes Sentinel a different object than observability tool #7.
3. /live — ingest the current GH Archive hour on demand (a button, not a background daemon judges can't see). Probably green. It is the honesty anchor: proof this is a running system, not a museum.

Provide a `sentinel serve` CLI command and a one-command way to run it. README a hosted-deploy note (the judge-testable instance).

=== CUT / DEFER (say so in the README) ===
Slack escalation (untestable → routing/severity shown IN the UI on each firing card instead). Auth. Multi-tenancy. A knowledge-base/RAG layer over past incidents (future work).

STANDING INSTRUCTIONS: tell me briefly about design decisions the brief doesn't cover; and if real data or the API contradicts the brief, stop and say so rather than working around it quietly. The credibility of this project is the whole product.
