# HW5 results: an LLM judge for `uninformative_response`

Mode: **`uninformative_response`** (RESP-3). Definition, Pass and Fail rules, and
evidence requirements are in [failure_definition.md](failure_definition.md).

Judge model: **`claude-haiku-4-5-20251001`**, for development and test alike.
This departs from the handout's suggested `gpt-4o-mini`. The traces under
evaluation were produced by `gpt-5.5`, so judging with a different model family
avoids shared blind spots, which is also what `register_judge`'s own default
does. `analysis/helpers/scale.py` passes unrecognised model names to LiteLLM
verbatim, so no code change was needed; `OPENAI_API_KEY` still has to be present
because `scale._backend()` gates on it.

## Labels and splits

- 100 human labels from Homework 4, one per conversation.
- HW4 file records 1 = failure present. The HW5 export
  (`analysis/state/hw5_labels/uninformative_response.jsonl`) records 1 = Pass.
- Three labels were flipped to Pass during development review, appended rather
  than overwritten, and all three are in the **development** split, so the test
  split was never touched: `full-015` ("shipping time? just the number of days"
  answered in one line), `full-049` (a requested 20-row order table, while the
  identical shape in `full-025` was labelled Pass), and `full-029` (a short,
  cited policy answer).
- Split: 20 / 40 / 40, seed 7, `min_per_class=10`.

| Split | n | Pass | Fail |
| --- | ---: | ---: | ---: |
| Train | 20 | 6 | 14 |
| Development | 40 | 15 | 25 |
| Test | 40 | 13 | 27 |

Training traces supplied few-shot examples only and were never scored.

## Version history

| Judge | Prompt file | Dev TPR | Dev TNR | Character |
| --- | --- | ---: | ---: | --- |
| v0 | `uninformative_response-v0.txt` | 0.917 | 0.286 | Too lenient: accepted "answer first, then a little policy" |
| v1 | `uninformative_response-v1.txt` | 0.333 | 1.000 | Too strict: treated every closing sentence as padding |
| v2 | `uninformative_response-v2.txt` | — | — | Same rules as v3; the run aborted when the model returned a verdict that was not exactly `Pass` or `Fail` |
| **v3** | `uninformative_response-v2.txt` | **0.867** | **0.560** | Selected. v2's rules plus a strict one-bare-word verdict contract |

v0 → v1 and v1 → v3 are the two revisions the handout permits. v2 → v3 changed
only the output format, not a single judging rule.

### What each revision responded to

**v0 → v1.** All 20 of v0's misses had one shape: the reply answered the
question, then added a sentence or two of policy. v0's prompt called that a
Pass. v1 stated the strict rule outright — after the answer and its
identifiers, any further policy, threshold, or eligibility explanation is a
Fail.

**v1 → v3.** v1 overshot, failing 35 of 40 dev traces. Its misses showed which
closing material the labels accept: a refusal plus a brief line naming what the
assistant can help with (`full-024`, `full-044`), one offer of one next action
(`full-056`, `full-090`), a list the user asked to see (`full-020`, `full-049`),
and one clause of context answering something the user raised (`full-029`, the
trace whose label had just been flipped — v1's rule contradicted that flip).
v3 keeps Fail for policy, threshold, and eligibility explanation and for buried
next steps, and names those four shapes as Pass.

## Final results, frozen judge on the held out test split

`uninformative_response-v3`, frozen before the test split was scored once.
Pass is the positive class.

| | Human Pass | Human Fail |
| --- | ---: | ---: |
| Judge Pass | TP 13 | FP 11 |
| Judge Fail | FN 0 | TN 16 |

- **TPR 1.000**, 95% Wilson interval **[0.772, 1.000]** (13 of 13 Pass labels).
- **TNR 0.593**, 95% Wilson interval **[0.407, 0.755]** (16 of 27 Fail labels).
- Agreement 0.725, n = 40.

Development metrics were TPR 0.867 and TNR 0.560, so the held out split did not
come out worse — evidence that v3 was not overfitted to the development traces,
though with 13 Pass cases the TPR interval is wide and a TPR of exactly 1.0
should not be read as perfect agreement.

## Would I use this judge?

Not as an automatic metric. Not yet as a gate.

- **It never wrongly flags a good reply.** In the test split it produced no false
  Fail verdicts, so anything it calls a failure is worth a human's attention.
- **It misses roughly four in ten real failures** (TNR 0.593, and the interval
  runs from 0.41 to 0.76). Any prevalence estimate built on it would understate
  the failure rate substantially.
- **The ceiling here is label noise, not prompt quality.** The labels disagree
  with each other on the same reply shapes: `full-025` and `full-049` are the
  same kind of requested table with opposite labels, and `full-050` and
  `full-084` are padded replies labelled Pass while briefer replies were
  labelled Fail. No prompt can agree with a standard that is inconsistent, which
  is why v0 and v1 could trade TPR against TNR but neither could hold both.

Useful now as a **screening filter**: let it surface candidate failures for human
review, where its high precision on Fail verdicts pays off. Before trusting a
rate from it, the next step is re-labelling a sample against the sharpened
definition so the boundary is applied consistently, then re-measuring.

## Note on the Homework 4 review summary

`analysis/report/review_summary.md` states that the review found "31 traces with
the failure and 69 without". Its own label file records the reverse: 69 rows
carry label 1, and all three traces that `patterns.json` names as positives
carry label 1. The label file is authoritative, so the reviewed sample is 69
failures and 31 clean, and HW5's Pass count (31, before flips) follows from that.

## Files

| Artifact | Path |
| --- | --- |
| HW5 labels (Pass=1, with flips appended) | `analysis/state/hw5_labels/uninformative_response.jsonl` |
| Splits and judge inputs | `analysis/state/splits.json`, `analysis/state/hw5_trace_inputs.json` |
| Prompts | `analysis/prompts/uninformative_response-v{0,1,2}.txt` |
| Judge records, predictions, critiques | `analysis/state/judges/uninformative_response-v{0,1,2,3}.json` |
| Code | `analysis/run_judges.py` |
| Metrics | `analysis/report/dev-uninformative_response-v{0,1,3}.json`, `analysis/report/test-uninformative_response-v3.json` |
| Disagreement sheets | `analysis/report/disagreements-uninformative_response-v{0,1,3}-dev.md` |

Video: not recorded.
