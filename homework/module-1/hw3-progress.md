# HW3 progress

## Start the environment (droplet + Mac)
1. Droplet: `docker compose -f observability/docker-compose.yml up -d` (check `ps`: 6 services up)
2. Droplet, own terminal: `uv run uvicorn server.app:app --port 8010`
   check: `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8010/docs` → 200
3. Mac: `ssh -N -L 3000:localhost:3000 rahim@devbox` (Mac port 8010 is taken; add
   `-L 18010:localhost:8010` if API docs are needed)
4. Mac browser: http://localhost:3000 (must be localhost; NEXTAUTH_URL)
Never run `docker compose ... down -v` (deletes traces). Claude session runs in a container: don't stop it.

## Settings
- Model: `claude-opus-4-6` (CARTWHEEL_MODEL in .env; pass the same to --model)
- HW2: own implementation (no reference patch)
- Claude's container has no `uv`, `sqlite3`, or `docker`, and can't reach :8010. Claude reads
  the DB with python3 (read-only) and writes files; Rahim runs `uv run ...` commands on the droplet.

## Part A: approved dimension plan (2026-09-16)
Seeded "today" = 2026-07-01. Default runner users: shopper 1, merchant 9001, support 9501;
set `tuple.user_id` whenever the record belongs to someone else.
1. `role`: shopper, merchant, support
2. `intent`: order_status, return_deadline, refund_request, cancel_order, find_order,
   product_search, policy_question, dispute, account_change, out_of_scope
3. `record_state`: order_delivered_in_window, order_delivered_window_expired, order_shipped,
   order_placed (only 35), order_cancelled, order_refunded, order_damaged, product,
   product_damaged, policy_page, none
4. `applicable_policy`: platform cw-* id; store override (Juniper 14d, Saltbox 7d, Meridian 21d,
   Northwind 45d; Cascade Audio + Second Stitch restocking fee); none
5. `tools_needed`: none, one_lookup, several
6. `difficulty`: ordinary, boundary, hard
7. `access_scope` (added; reason SPEC AUTH-1): own_record, outside_scope
- `turn_count`: 1–3 = 1 + len(followups)

Damaged records (5 challenge scenarios each in final; user must be allowed to access):
| case_id | entity | allowed users |
| dq-order-missing-delivery-date | order 8002 (store 20) | shopper 392, merchant 9020, support |
| dq-order-reversed-dates | order 8001 (store 16) | shopper 174, merchant 9016, support |
| dq-order-store-mismatch | order 8003 (store 1; product 553 is store 14) | shopper 119, merchant 9001, support (not 9014) |
| dq-product-duplicate-title | product 2 (dup of 1) | any role |
| dq-product-invalid-price | product 4 (−$5.00) | any role |
| dq-product-missing-title | product 3 (empty title) | any role |

Rahim's example tuples (approved):
1. merchant 9002, refund_request, order 6974 (Juniper, delivered 06-14, 17d), store override 14d,
   own_record, several, boundary → do_not_refund (eligibility_function, window=14) [challenge]
2. merchant 9001, refund_request, order 7623 ($285, 8d), cw-refunds, own_record, several, ordinary
   → refund_queued_for_approval (sql + eligibility_function; ESC-1, RESP-2) [coverage]
3. merchant 9001, order_status, order 3880 (Juniper store 2), none, outside_scope, one_lookup,
   hard → no_order_details_disclosed (sql store_id=2; AUTH-1, RESP-4) [challenge]

## Done
- [x] Setup: HW1/HW2 tests pass offline (21 passed, 9 xpassed, 10 xfailed = later modules)
- [x] Data seeded (as of 2026-07-01); Langfuse reachable from Mac
- [x] Server answering on :8010
- [x] Concepts: scenario, expected result from ground truth, dimension, tuple, coverage vs challenge
- [x] Part A: damaged records read; dimension plan approved; example tuples chosen

## Next
- Part B: explain the pilot; propose pilot composition (30); generate `scenarios/pilot_scenarios.jsonl`

## Deliverables (Files to commit)
- [ ] scenarios/pilot_scenarios.jsonl (30)
- [ ] scenarios/pilot-results.jsonl
- [ ] scenarios/pilot_review.jsonl (≥10 reviewed, ≥5 confirmed failures)
- [ ] scenarios/support_scenarios.jsonl (175 coverage + 75 challenge, 5 per damaged record, new ids)
- [ ] scenarios/support_review.jsonl (15: both groups, 3 roles, every intent)
- [ ] scenarios/monitoring_scenarios.jsonl (50: both groups, 3 roles)
- [ ] scenarios/final-results.jsonl (250 completed)
- [ ] reports/smoke-output.txt
- [ ] traces/support_traces.json (250 scenario ids)

## Checks
- [ ] `scenarios.validate` pilot file passes
- [ ] Pilot review points: Rahim decides validity/failures
- [ ] `scenarios.validate ... --final` passes
- [ ] `seed.generate` run again before the final run
- [ ] jq status count: 250 completed
- [ ] Export succeeds; 3 traces checked (1 challenge, 1 multi-turn)

## Rahim's (not Claude's)
- [ ] Review decisions and assessments
- [ ] Video (≤5 min)

## Live model usage log
- (none yet; all checks so far offline)
