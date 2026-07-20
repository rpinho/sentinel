# Sentinel detector v4 — powered frozen test scorecard

> Model and threshold were frozen at commit `a90f276e0cc6b886999506aa7e3b174afc3775aa` before the v4 test repositories were extracted.

## Statistical power

| Split | Candidate hours | Positive hours | Confirmed incidents | Target met |
| --- | ---: | ---: | ---: | :---: |
| Train | 11280 | 407 | 425 | yes |
| Test | 5229 | 267 | 287 | yes |

## Headline test result

| Precision | Recall | ROC AUC | Median lead | Alerts |
| ---: | ---: | ---: | ---: | ---: |
| 13.6% | 88.0% | 0.841 | 806.9 min | 1724 |

Whole-repository bootstrap 95% intervals: precision [8.8%, 18.1%], recall [83.4%, 94.2%], ROC AUC [0.807, 0.882].

## Burst magnitude versus coherence

| Frozen model | ROC AUC | Precision | Recall |
| --- | ---: | ---: | ---: |
| Burst-only ablation | 0.794 | 15.2% | 55.1% |
| Coherence v4 | 0.841 | 13.6% | 88.0% |

Coherence AUC delta: **+0.047**. The frozen test metrics are descriptive, not confirmatory: the predeclared label-noise ceiling was exceeded by substantive_maintainer_comment.

## Population correction

**64** test incidents (22.3%) fell outside the old five-event spike gate. V3 could not learn or detect them.

## Label sources and held-out audit

| Source | Test incidents | Audited | Rejected | Obvious noise |
| --- | ---: | ---: | ---: | ---: |
| `completed_bug_closure` | 77 | 20 | 1 | 5.0% |
| `merged_fixing_pr` | 79 | 20 | 1 | 5.0% |
| `substantive_maintainer_comment` | 131 | 20 | 6 | 30.0% |

Predeclared per-source noise ceiling: 20.0%. Held-out label-quality target met: **no**.

## Recall by confirmation source

| Source | Incidents | Caught | Recall |
| --- | ---: | ---: | ---: |
| `completed_bug_closure` | 77 | 70 | 90.9% |
| `merged_fixing_pr` | 79 | 55 | 69.6% |
| `substantive_maintainer_comment` | 131 | 128 | 97.7% |

## Sample caught incidents

| Candidate | Repository / issue | Source | Confirmation | Lead | Score |
| --- | --- | --- | --- | ---: | ---: |
| [2025-03-28T02:00:00Z](https://github.com/home-assistant/core/issues/141624) | home-assistant/core #141624 | `merged_fixing_pr` | [2025-03-28T19:32:01Z](https://github.com/home-assistant/core/pull/141680) | 1052.0 min | 0.744 |
| [2025-03-28T06:00:00Z](https://github.com/python/cpython/issues/131820) | python/cpython #131820 | `substantive_maintainer_comment` | [2025-03-28T18:37:41Z](https://github.com/python/cpython/issues/131820#issuecomment-2762146610) | 757.7 min | 0.833 |
| [2025-03-28T07:00:00Z](https://github.com/home-assistant/core/issues/141631) | home-assistant/core #141631 | `substantive_maintainer_comment` | [2025-03-30T10:28:23Z](https://github.com/home-assistant/core/issues/141631#issuecomment-2764491965) | 3088.4 min | 0.738 |
| [2025-03-28T08:00:00Z](https://github.com/home-assistant/core/issues/141636) | home-assistant/core #141636 | `merged_fixing_pr` | [2025-03-30T12:58:48Z](https://github.com/home-assistant/core/pull/141805) | 3178.8 min | 0.783 |
| [2025-03-28T09:00:00Z](https://github.com/home-assistant/core/issues/141638) | home-assistant/core #141638 | `merged_fixing_pr` | [2025-03-30T19:34:51Z](https://github.com/home-assistant/core/pull/141722) | 3514.8 min | 0.821 |
| [2025-03-28T12:00:00Z](https://github.com/flutter/flutter/issues/166129) | flutter/flutter #166129 | `substantive_maintainer_comment` | [2025-03-31T06:33:24Z](https://github.com/flutter/flutter/issues/166129#issuecomment-2765264700) | 3993.4 min | 0.772 |
| [2025-03-28T12:00:00Z](https://github.com/home-assistant/core/issues/141643) | home-assistant/core #141643 | `merged_fixing_pr` | [2025-03-28T13:01:14Z](https://github.com/home-assistant/core/pull/141647) | 61.2 min | 0.637 |
| [2025-03-28T13:00:00Z](https://github.com/rust-lang/rust/issues/139055) | rust-lang/rust #139055 | `completed_bug_closure` | [2025-03-29T00:02:09Z](https://github.com/rust-lang/rust/issues/139055) | 662.1 min | 0.820 |
| [2025-03-28T15:00:00Z](https://github.com/home-assistant/core/issues/141657) | home-assistant/core #141657 | `merged_fixing_pr` | [2025-03-28T19:38:17Z](https://github.com/home-assistant/core/pull/141674) | 278.3 min | 0.827 |
| [2025-03-28T18:00:00Z](https://github.com/llvm/llvm-project/issues/133473) | llvm/llvm-project #133473 | `merged_fixing_pr` | [2025-03-28T23:28:57Z](https://github.com/llvm/llvm-project/pull/133520) | 328.9 min | 0.750 |
| [2025-03-28T19:00:00Z](https://github.com/home-assistant/core/issues/141675) | home-assistant/core #141675 | `merged_fixing_pr` | [2025-03-31T09:40:15Z](https://github.com/home-assistant/core/pull/141839) | 3760.2 min | 0.803 |
| [2025-03-28T20:00:00Z](https://github.com/llvm/llvm-project/issues/133509) | llvm/llvm-project #133509 | `substantive_maintainer_comment` | [2025-03-31T16:06:45Z](https://github.com/llvm/llvm-project/issues/133509#issuecomment-2766706840) | 4086.8 min | 0.799 |
| [2025-03-28T22:00:00Z](https://github.com/dotnet/runtime/issues/114026) | dotnet/runtime #114026 | `substantive_maintainer_comment` | [2025-03-28T22:04:26Z](https://github.com/dotnet/runtime/issues/114026#issuecomment-2762678533) | 4.4 min | 0.792 |
| [2025-03-29T01:00:00Z](https://github.com/python/cpython/issues/131865) | python/cpython #131865 | `completed_bug_closure` | [2025-03-31T18:19:53Z](https://github.com/python/cpython/issues/131865) | 3919.9 min | 0.775 |
| [2025-03-29T04:00:00Z](https://github.com/rust-lang/rust/issues/139095) | rust-lang/rust #139095 | `substantive_maintainer_comment` | [2025-03-30T04:24:58Z](https://github.com/rust-lang/rust/issues/139095#issuecomment-2764377911) | 1465.0 min | 0.782 |
| [2025-03-29T08:00:00Z](https://github.com/home-assistant/core/issues/141717) | home-assistant/core #141717 | `merged_fixing_pr` | [2025-03-30T19:34:51Z](https://github.com/home-assistant/core/pull/141722) | 2134.8 min | 0.747 |
| [2025-03-29T09:00:00Z](https://github.com/llvm/llvm-project/issues/133573) | llvm/llvm-project #133573 | `substantive_maintainer_comment` | [2025-03-29T14:06:18Z](https://github.com/llvm/llvm-project/issues/133573#issuecomment-2763382295) | 306.3 min | 0.750 |
| [2025-03-29T12:00:00Z](https://github.com/rails/rails/issues/54832) | rails/rails #54832 | `substantive_maintainer_comment` | [2025-03-29T13:13:20Z](https://github.com/rails/rails/issues/54832#issuecomment-2763359004) | 73.3 min | 0.878 |
| [2025-03-29T21:00:00Z](https://github.com/rust-lang/rust/issues/139120) | rust-lang/rust #139120 | `substantive_maintainer_comment` | [2025-03-30T02:41:05Z](https://github.com/rust-lang/rust/issues/139120#issuecomment-2764351094) | 341.1 min | 0.753 |
| [2025-03-30T12:00:00Z](https://github.com/home-assistant/core/issues/141808) | home-assistant/core #141808 | `substantive_maintainer_comment` | [2025-03-30T12:39:35Z](https://github.com/home-assistant/core/issues/141808#issuecomment-2764545368) | 39.6 min | 0.713 |

## Frozen identities

- Train dataset: `5d94c941eedba3f90de12aa135220da3609bf48c11330739cb3e895867951881`
- Test dataset: `895395b1b41a733996ceecd7c1c33913e01ffe9482db68a6f8f2ce85c7c0732d`
- Model: `57021d95ec13ef5d8c1f52f95e6008152b7c1a6ddccbe7fe5f77aa042a2a3f9d`
- Label audit: `117b48e112a0323e6cc8e55026e21664a578fb79b7e32ce1c3f5d7c19ba57b34`
- Scorecard: `7a823f275f6be144cd9c5eb90b65ce913781c709017075437cad67738d3825ea`

V1/v2/v3/v4 use different frozen corpora. Their progression documents iteration; it is not a same-sample leaderboard.
