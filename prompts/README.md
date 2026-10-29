# prompts/ — how Sentinel was actually built

Sentinel was built by driving **Codex (GPT-5.6)** with a sequence of deliberate prompts,
each independently validated before the next. This directory is the real, unedited log —
committed on purpose. Sentinel grades itself in public and reports its own misses; it seemed
only right to build it in public too.

The prompts were authored with the help of **Claude**, which also acted as the *independent
adversarial validator* between sessions (a different model checking the builder's work — see
`VALIDATION_REPORT.md`). That cross-model loop — one model builds, another tries to break the
claims from raw data — is itself part of the method.

| # | File | Drove |
|---|---|---|
| 1 | `01-scaffold-detector-adapters.md` | Event schema, two adapters (GH Archive + npm), detector, feedback store, CLI |
| 2 | `02-triage-and-ablations.md` | GPT-5.6 pre-firing triage, exact-request caching/fixtures, `--no-agent` + `--no-feedback` |
| 3 | `03-web-surfaces-and-holdout.md` | Frozen-config holdout run, then the replay / scorecard / live web surfaces |

Notes:
- Prompt 1 is **reconstructed** (faithful, not byte-exact) — it predated the decision to log
  prompts. Prompts 2 and 3 are verbatim.
- No prompt contains a secret. The OpenAI key lives only in `.env` (gitignored) and is
  referenced by path, never inline.
- Recurring instruction across all sessions: *if real data contradicts the brief, stop and
  report it rather than working around it.* Session 2 did exactly that when GPT-5.6 rejected
  `temperature: 0`.
