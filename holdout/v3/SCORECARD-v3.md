# Sentinel detector v3 — frozen test scorecard

> Model and threshold were committed before this whole-repository test split was opened.

## Headline test result

| Precision | Recall | ROC AUC | Median lead | Alerts | Test incidents |
| ---: | ---: | ---: | ---: | ---: | ---: |
| N/A | 0.0% | 0.455 | N/A | 0 | 19 |

## Burst magnitude versus coherence

| Frozen model | Test ROC AUC | Test precision | Test recall |
| --- | ---: | ---: | ---: |
| Burst-only ablation | 0.382 | 0.0% | 0.0% |
| Coherence v3 | 0.455 | N/A | 0.0% |

Coherence AUC delta: **+0.072**.

## Caught incidents

| Candidate | Repository / issue | Acknowledgment | Lead | Score |
| --- | --- | --- | ---: | ---: |
| none | — | — | — | — |

## Misses

| Candidate | Repository / issue | Acknowledgment | Score |
| --- | --- | --- | ---: |
| [2025-03-28T18:57:23.000001Z](https://github.com/microsoft/vscode/issues/244920#issuecomment-2762189403) | microsoft/vscode #244625 | [2025-03-28T20:00:20Z](https://github.com/microsoft/vscode/issues/244625#issuecomment-2762330454) | 0.000 |
| [2025-04-01T09:38:56.000001Z](https://github.com/microsoft/vscode/issues/245225#issuecomment-2768778428) | microsoft/vscode #244540 | [2025-04-01T13:17:45Z](https://github.com/microsoft/vscode/issues/244540#issuecomment-2769325895) | 0.000 |
| [2025-04-02T11:52:18.000001Z](https://github.com/microsoft/vscode/issues/245296#issuecomment-2772320055) | microsoft/vscode #244689 | [2025-04-05T00:58:14Z](https://github.com/microsoft/vscode/issues/244689#issuecomment-2779990412) | 0.000 |
| [2025-04-02T23:48:15.000001Z](https://github.com/microsoft/vscode/issues/245402#issuecomment-2773984608) | microsoft/vscode #245400 | [2025-04-03T06:06:55Z](https://github.com/microsoft/vscode/issues/245400#issuecomment-2774586006) | 0.000 |
| [2025-04-04T19:59:15.000001Z](https://github.com/microsoft/vscode/issues/245719) | microsoft/vscode #245719 | [2025-04-07T15:36:59Z](https://github.com/microsoft/vscode/issues/245719#issuecomment-2783788448) | 0.000 |
| [2025-04-05T10:36:25.000001Z](https://github.com/microsoft/vscode/issues/245338#issuecomment-2780651895) | microsoft/vscode #245759 | [2025-04-06T21:50:32Z](https://github.com/microsoft/vscode/issues/245759#issuecomment-2781689415) | 0.000 |
| [2025-04-05T15:31:21.000001Z](https://github.com/microsoft/vscode/issues/245773) | microsoft/vscode #245772 | [2025-04-07T20:50:40Z](https://github.com/microsoft/vscode/issues/245772#issuecomment-2784597312) | 0.000 |
| [2025-04-07T15:53:53.000001Z](https://github.com/microsoft/vscode/issues/245744#issuecomment-2783843444) | microsoft/vscode #245707 | [2025-04-08T16:46:34Z](https://github.com/microsoft/vscode/issues/245707#issuecomment-2787074401) | 0.868 |
| [2025-04-07T15:53:53.000001Z](https://github.com/microsoft/vscode/issues/245744#issuecomment-2783843444) | microsoft/vscode #245886 | [2025-04-08T09:51:20Z](https://github.com/microsoft/vscode/issues/245886#issuecomment-2785876632) | 0.868 |
| [2025-04-07T17:54:41.000001Z](https://github.com/microsoft/vscode/issues/245772#issuecomment-2784132856) | microsoft/vscode #244673 | [2025-04-07T18:54:31Z](https://github.com/microsoft/vscode/issues/244673#issuecomment-2784269215) | 0.000 |
| [2025-04-07T18:55:02.000001Z](https://github.com/webpack/webpack/issues/19394#issuecomment-2784270370) | webpack/webpack #19394 | [2025-04-07T20:14:04Z](https://github.com/webpack/webpack/issues/19394#issuecomment-2784517989) | 0.000 |
| [2025-04-07T20:46:02.000001Z](https://github.com/webpack/webpack/issues/19397#issuecomment-2784587713) | webpack/webpack #19397 | [2025-04-07T21:07:19Z](https://github.com/webpack/webpack/issues/19397#issuecomment-2784629892) | 0.000 |
| [2025-05-05T08:56:10.000001Z](https://github.com/microsoft/vscode/issues/248123) | microsoft/vscode #247928 | [2025-05-05T10:14:58Z](https://github.com/microsoft/vscode/issues/247928#issuecomment-2850537797) | 0.000 |
| [2025-05-06T09:46:43.000001Z](https://github.com/microsoft/vscode/issues/248194#issuecomment-2853929138) | microsoft/vscode #248195 | [2025-05-07T21:57:41Z](https://github.com/microsoft/vscode/issues/248195#issuecomment-2860486969) | 0.000 |
| [2025-05-09T08:58:11.000001Z](https://github.com/kubernetes/kubernetes/issues/131690#issuecomment-2865742606) | kubernetes/kubernetes #131688 | [2025-05-09T09:27:17Z](https://github.com/kubernetes/kubernetes/issues/131688#issuecomment-2865836295) | 0.000 |
| [2025-05-11T19:54:24.000001Z](https://github.com/microsoft/vscode/issues/248659) | microsoft/vscode #248656 | [2025-05-13T05:42:29Z](https://github.com/microsoft/vscode/issues/248656#issuecomment-2875110849) | 0.000 |
| [2025-05-12T14:59:08.000001Z](https://github.com/microsoft/vscode/issues/248735#issuecomment-2872922661) | microsoft/vscode #248732 | [2025-05-13T05:36:41Z](https://github.com/microsoft/vscode/issues/248732#issuecomment-2875102699) | 0.000 |
| [2025-05-13T07:53:02.000001Z](https://github.com/microsoft/vscode/issues/239626#issuecomment-2875426865) | microsoft/vscode #248784 | [2025-05-15T09:28:23Z](https://github.com/microsoft/vscode/issues/248784#issuecomment-2883165802) | 0.000 |
| [2025-05-16T15:57:59.000001Z](https://github.com/microsoft/vscode/issues/249089#issuecomment-2887118799) | microsoft/vscode #249108 | [2025-05-16T16:05:21Z](https://github.com/microsoft/vscode/issues/249108#issuecomment-2887134540) | 0.000 |

## Frozen identities

- Dataset: `08413df111d27a8c084c87b01b6be441f8138c178b610bb489ea73388c202d8b`
- Model: `6701d78dddf9e1bfe947bf9614ba6f63e3885e7f656a0a6c7b8dd914c2b176a8`
- Label audit: `05396daaf8babdef9dc00151d577350db2bc87bb7c5938a359115eebc7d4805d`
- Scorecard: `e16440bd60a590f64373a70302bddb5ffa214d36f9c6c0790624e0dbb61cec09`

V1/v2 and v3 use different frozen corpora. The progression is evidence of iteration, not a same-sample leaderboard.
