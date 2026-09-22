"""Draw the HW4 review batches and record them in the sample manifest.

The review unit is a conversation (all turns of one ``cartwheel.session_id``),
but the handout counts traces, so every batch records the trace ids of each
picked conversation and its trace total. Batches are appended to
``analysis/state/sample_manifest.json``; a conversation already in an earlier
batch is never drawn again, so no trace counts toward two batches.

Selection never looks at outcomes: random draws use a fixed seed, and cluster
representatives come from k-means over the course helper's trace features
(turn count, tool calls, distinct tools, retrieval, length).

    uv run python analysis/sample_batches.py batch1   # 1a: ~15 random traces, 1b: 15 cluster reps
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis.helpers.normalization import _metadata, normalize_traces  # noqa: E402
from analysis.helpers.selection import _feature_vector, _kmeans, _standardize  # noqa: E402

EXPORT = ROOT / "traces" / "support_traces.json"
MANIFEST = ROOT / "analysis" / "state" / "sample_manifest.json"
SEED = 7


def conversations() -> list[dict]:
    """One record per conversation: session, scenario, trace ids, features."""
    raw = json.loads(EXPORT.read_text())["traces"]
    groups: dict[str, list[dict]] = defaultdict(list)
    for trace in raw:
        groups[_metadata(trace)["cartwheel.session_id"]].append(trace)
    features = {t["meta"]["scenario_id"]: t for t in normalize_traces(raw)}
    out = []
    for session_id, group in sorted(groups.items()):
        group.sort(key=lambda t: t["timestamp"])
        meta = _metadata(group[0])
        scenario = meta["cartwheel.scenario_id"]
        out.append({
            "session_id": session_id,
            "scenario_id": scenario,
            "role": meta.get("cartwheel.user_role"),
            "trace_ids": [t["id"] for t in group],
            "features": features[scenario]["features"],
        })
    return sorted(out, key=lambda c: c["scenario_id"])


def load_manifest() -> dict:
    if MANIFEST.exists():
        data = json.loads(MANIFEST.read_text())
        if "batches" in data:
            return data
    return {
        "source": str(EXPORT.relative_to(ROOT)),
        "unit": "conversation (all turns of one cartwheel.session_id); counts are traces",
        "batches": [],
    }


def batch_record(batch_id: str, name: str, method: str, picks: list[dict]) -> dict:
    trace_ids = [tid for p in picks for tid in p["trace_ids"]]
    return {
        "id": batch_id,
        "name": name,
        "method": method,
        "seed": SEED,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "conversation_count": len(picks),
        "trace_count": len(trace_ids),
        "trace_ids": trace_ids,
        "picks": picks,
    }


def pick(conv: dict, reason: str) -> dict:
    return {k: conv[k] for k in ("session_id", "scenario_id", "role", "trace_ids")} | {"reason": reason}


def random_batch(pool: list[dict], target_traces: int) -> list[dict]:
    """Uniform random conversations until their traces reach the target."""
    order = pool[:]
    random.Random(SEED).shuffle(order)
    picks, total = [], 0
    for conv in order:
        if total >= target_traces:
            break
        picks.append(pick(conv, "uniform random draw"))
        total += len(conv["trace_ids"])
    return picks


def cluster_batch(pool: list[dict], k: int) -> list[dict]:
    """The conversation nearest each k-means centroid over trace features."""
    vectors = _standardize([_feature_vector(c) for c in pool])
    assign = _kmeans(vectors, k=k, seed=SEED)
    picks = []
    for cluster in sorted(set(assign)):
        members = [i for i, a in enumerate(assign) if a == cluster]
        centre = [sum(vectors[i][d] for i in members) / len(members) for d in range(len(vectors[0]))]
        best = min(members, key=lambda i: math.dist(vectors[i], centre))
        f = pool[best]["features"]
        picks.append(pick(pool[best], (
            f"nearest the centre of cluster {cluster} ({len(members)} conversations); "
            f"{f['turn_count']} messages, {f['tool_call_count']} tool calls, "
            f"retrieval={'yes' if f['has_retrieval'] else 'no'}"
        )))
    return picks


def batch1(force: bool) -> None:
    manifest = load_manifest()
    if any(b["id"] in ("1a", "1b") for b in manifest["batches"]) and not force:
        raise SystemExit("batch 1 already exists in the manifest; pass --force to redraw it")
    manifest["batches"] = [b for b in manifest["batches"] if b["id"] not in ("1a", "1b")]
    used = {s for b in manifest["batches"] for s in (p["session_id"] for p in b["picks"])}
    pool = [c for c in conversations() if c["session_id"] not in used]

    a = random_batch(pool, target_traces=15)
    taken = {p["session_id"] for p in a}
    b = cluster_batch([c for c in pool if c["session_id"] not in taken], k=15)
    manifest["batches"] += [
        batch_record("1a", "Batch 1a · random", "uniform random conversations until 15 traces", a),
        batch_record("1b", "Batch 1b · cluster representatives",
                     "k-means (k=15) over turn count, tool calls, distinct tools, retrieval, length; "
                     "conversation nearest each centroid", b),
    ]
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    for batch in manifest["batches"][-2:]:
        print(f"{batch['name']}: {batch['conversation_count']} conversations, {batch['trace_count']} traces")
        for p in batch["picks"]:
            print(f"  {p['scenario_id']:9} {p['role']:9} {len(p['trace_ids'])} turn(s)  {p['reason']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("batch", choices=["batch1"])
    parser.add_argument("--force", action="store_true", help="redraw a batch that already exists")
    args = parser.parse_args()
    {"batch1": batch1}[args.batch](args.force)


if __name__ == "__main__":
    main()
