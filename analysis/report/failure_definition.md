# HW5 failure definition: `uninformative_response`

Mode selected from Homework 4 (`analysis/state/patterns.json`). Requirement source: **RESP-3**.
Evaluator type: **LLM judge** — the right amount of detail depends on what the user asked for,
so a code check cannot decide it.

## Question

Does the final reply give the user the information they need for their next decision, without
burying it in detail they did not ask for?

## Fail rule (label 0 in HW5)

The reply makes the needed information hard to find:

- the fact or next step the user needs is missing, or
- it is obscured by policy text, caveats, or details the user did not ask for.

## Pass rule (label 1 in HW5)

The needed information is present and findable, and the information provided to the user is just
enough — not too much and not too little.

Follow Grice's maxims: 
* Quantity: include what is needed, and no more
* Quality: say what is true and supported by evidence. 
* Relation: stay relevant to the request. 
* Manner: Avoid ambiguity and present information in order. 

## Boundary

Judge the reply against what this user asked for. A short answer is not a failure when the user
requested a short answer. Extra detail is a failure only when it obscures the answer or the next
step. A reply that is wrong on the facts is a correctness problem, not this mode.

## Evidence needed to decide

- The user's request (and earlier turns when the reply depends on them).
- The final assistant reply.
- The tool calls and results the reply should have conveyed, so a missing fact can be told apart
  from a fact that was never available.

## Labels

- Human labels: 100, one per conversation, from Homework 4.
- Homework 4 file (`analysis/state/labels/uninformative_response.jsonl`) uses 1 = failure present:
  69 failure, 31 clean.
- Homework 5 export (`analysis/state/hw5_labels/uninformative_response.jsonl`) uses 1 = Pass:
  **31 Pass, 69 Fail**.
- Pass is the minority class, so TPR rests on roughly 12 development and 13 test cases and will
  carry a wide confidence interval.

## Notes carried into Part C

- `analysis/report/review_summary.md` states "31 traces with the failure and 69 without", which is
  the reverse of its own label file. The label file is authoritative: all three traces listed as
  positives in `patterns.json` carry label 1.
- Two labels looked inconsistent with this definition on first reading and are expected
  development disagreements: `full-029` (short, accurate, cited policy answer marked as a failure)
  and `full-036` (five product options with an offer to narrow, marked as a failure, while the
  similar `full-054` is marked clean).
- Label flips are allowed on the development split only, so the test split stays untouched until
  the judge is frozen.
