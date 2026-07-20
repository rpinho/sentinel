# Detector v4 powered study

V4 asks whether pre-confirmation coherence signals rank repository-hours that later
receive maintainer-confirmed software-incident evidence. It is a frozen observational
study, not the packaged production detector.

## Frozen design

- Unit: every repository UTC hour with at least one public issue-opened or
  issue-comment-created event. No spike gate is required.
- Split: 60 training repositories and 15 unseen test repositories across two frozen
  2025 windows. Repositories, not rows, are the separation boundary.
- Labels: future-only completed bug closure, merged fixing pull request, or substantive
  maintainer comment within 72 hours. Every positive records its source.
- Features: the v3 coherence family, with a separately selected burst-only ablation.
- Selection: deterministic standardized logistic regression, leave-one-repository-out
  predictions on training repositories, equal repository/class weights, and train-only
  F1 threshold selection.
- Uncertainty: 2,000 deterministic whole-repository bootstrap replicates.
- Audit: a deterministic SHA-256 sample of 20 incidents per source in each split.

The protocol is `holdout/v4/study-protocol.json`. Train data, model, threshold, and
selection report were frozen at commit
`a90f276e0cc6b886999506aa7e3b174afc3775aa` before the test archive was extracted.
The scorecard records zero test rows used for selection and binds every artifact with a
content hash.

## Power and result

| Split | Active hours | Positive hours | Confirmed incidents |
| --- | ---: | ---: | ---: |
| Train | 11,280 | 407 | 425 |
| Test | 5,229 | 267 | 287 |

| Frozen arm | Precision | Recall | ROC AUC | Median lead |
| --- | ---: | ---: | ---: | ---: |
| Coherence | 13.6% | 88.0% | 0.841 | 806.9 min |
| Burst only | 15.2% | 55.1% | 0.794 | 1,111.5 min |

The coherence AUC bootstrap interval is 0.807–0.882, and its AUC exceeds the burst arm
by 0.047. Sixty-four test incidents (22.3%) fall outside the old five-event spike
population, so the earlier gate could neither learn nor detect them.

These are descriptive results, not a confirmatory claim. The held-out audit rejected
1/20 closure labels and 1/20 fixing-PR labels, but 6/20 maintainer-comment labels. The
30% comment-source noise exceeded the predeclared 20% ceiling. The largest identified
failure class is LLVM's `llvmbot`: GH Archive presents it as a `User` with `MEMBER`
association, and its name evades the committed `[bot]`/`-bot` suffix rule, allowing
automated issue-body mirrors to look like substantive maintainer confirmations.

V4 remains frozen as evidence. The next study should add identity-specific and
payload-duplication bot checks, freeze a new protocol, and use new repository/time
holdouts; it must not relabel or retune this test split.

## Reproduce without network or model calls

The committed train/test datasets, audits, models, and protocol are sufficient to rerun
selection and scoring locally:

```bash
uv run python scripts/train_detector_v4.py
uv run python scripts/evaluate_detector_v4.py \
  --model-commit a90f276e0cc6b886999506aa7e3b174afc3775aa
uv run pytest -q
```

Expected artifact identities:

- Train dataset: `5d94c941eedba3f90de12aa135220da3609bf48c11330739cb3e895867951881`
- Test dataset: `895395b1b41a733996ceecd7c1c33913e01ffe9482db68a6f8f2ce85c7c0732d`
- Coherence model: `57021d95ec13ef5d8c1f52f95e6008152b7c1a6ddccbe7fe5f77aa042a2a3f9d`
- Burst model: `ae275eb90bffe7ee9ffefb3d9e3810f30c599f49bb82c822b6e6fd48284632a8`
- Combined label audit: `117b48e112a0323e6cc8e55026e21664a578fb79b7e32ce1c3f5d7c19ba57b34`
- Scorecard: `7a823f275f6be144cd9c5eb90b65ce913781c709017075437cad67738d3825ea`

The primary publication artifacts are `holdout/v4/SCORECARD-v4.md` and
`holdout/v4/scorecard.json`; `holdout/v4/dataset.json` maps the compressed split files
to their content hashes, and `holdout/v4/label-audit.json` preserves every reviewed
sample and decision.
