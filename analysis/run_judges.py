"""Homework 5: build judge inputs, split labels, run and score the judge.

Run everything from the repository root with ``uv run python``.

Part B provides:
    prepare_inputs()   -> analysis/state/hw5_trace_inputs.json
    split_data(mode)   -> analysis/state/splits.json (adds one key per mode)

The judge never sees a human label, an annotation, or scenario metadata:
those live outside the exported records on purpose, because including them
would hand the judge the answer it is supposed to work out.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACE_EXPORT = REPO_ROOT / "traces" / "support_traces.json"
STATE = REPO_ROOT / "analysis" / "state"
JUDGE_INPUTS = STATE / "hw5_trace_inputs.json"
MODE = "uninformative_response"
# The handout suggests gpt-4o-mini. We judge gpt-5.5 traces with a different
# model family, which register_judge's own default also does. Development and
# test use the same model.
JUDGE_MODEL = "claude-haiku-4-5-20251001"

# HW5 labels (Pass=1) when present, otherwise the HW4 file (failure=1). Only
# the trace ids are read here; the labels themselves stay out of the export.
HW5_LABELS = STATE / "hw5_labels" / f"{MODE}.jsonl"
HW4_LABELS = STATE / "labels" / f"{MODE}.jsonl"


def _text(value: Any) -> str:
    """Flatten Langfuse's message payloads to plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                for part in item.get("parts") or []:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(str(part.get("content", "")))
                if not item.get("parts") and item.get("content"):
                    parts.append(str(item["content"]))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p).strip()
    if isinstance(value, dict):
        if value.get("content"):
            return str(value["content"]).strip()
        return json.dumps(value, sort_keys=True)
    return str(value)


def _labelled_trace_ids() -> list[str]:
    path = HW5_LABELS if HW5_LABELS.exists() else HW4_LABELS
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    seen: dict[str, None] = {}
    for row in rows:  # keep file order, drop repeats from label flips
        seen.setdefault(str(row["trace_id"]), None)
    return list(seen)


def _turn_messages(trace: dict[str, Any], *, with_tools: bool) -> list[dict[str, Any]]:
    """One turn as messages: the user request, tool activity, the reply.

    Earlier turns of the same session are included without their tool calls,
    because they are context for the reply under evaluation, not the evidence
    being judged.
    """
    messages: list[dict[str, Any]] = []
    user = _text(trace.get("input"))
    if user:
        messages.append({"role": "user", "text": user})

    if with_tools:
        tools = [
            observation
            for observation in trace.get("observations") or []
            if observation.get("type") == "TOOL"
        ]
        tools.sort(key=lambda observation: observation.get("startTime") or "")
        for observation in tools:
            messages.append(
                {
                    "role": "tool_call",
                    "name": observation.get("name"),
                    "arguments": observation.get("input"),
                    "result": observation.get("output"),
                }
            )

    reply = _text(trace.get("output"))
    if reply:
        messages.append({"role": "assistant", "text": reply})
    return messages


def prepare_inputs(export: Path = TRACE_EXPORT, out: Path = JUDGE_INPUTS) -> list[dict[str, Any]]:
    """Write one judge input record per labelled conversation.

    Each record is ``{"trace_id": ..., "trace": [messages]}``. Earlier turns
    from the same Langfuse session are prepended so a reply that depends on
    them can still be judged.
    """
    traces = json.loads(export.read_text())["traces"]
    by_id = {trace["id"]: trace for trace in traces}

    by_session: dict[str, list[dict[str, Any]]] = {}
    for trace in traces:
        session = trace.get("sessionId") or trace.get("metadata", {}).get(
            "attributes", {}
        ).get("cartwheel.session_id")
        if session:
            by_session.setdefault(session, []).append(trace)
    for group in by_session.values():
        group.sort(key=lambda trace: trace.get("timestamp") or "")

    records: list[dict[str, Any]] = []
    missing: list[str] = []
    for trace_id in _labelled_trace_ids():
        trace = by_id.get(trace_id)
        if trace is None:
            missing.append(trace_id)
            continue

        messages: list[dict[str, Any]] = []
        session = trace.get("sessionId") or trace.get("metadata", {}).get(
            "attributes", {}
        ).get("cartwheel.session_id")
        for earlier in by_session.get(session, []):
            if earlier["id"] == trace_id:
                break
            messages.extend(_turn_messages(earlier, with_tools=False))
        messages.extend(_turn_messages(trace, with_tools=True))

        if not messages:
            missing.append(trace_id)
            continue
        records.append({"trace_id": trace_id, "trace": messages})

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=2) + "\n")

    turns = Counter(
        sum(1 for message in record["trace"] if message["role"] == "user")
        for record in records
    )
    tool_calls = sum(
        1 for record in records for message in record["trace"] if message["role"] == "tool_call"
    )
    print(f"wrote {len(records)} records to {out}")
    print(f"  user turns per record: {dict(sorted(turns.items()))}")
    print(f"  tool call messages:    {tool_calls}")
    if missing:
        print(f"  WARNING: {len(missing)} labelled traces had no content: {missing[:5]}")
    return records


def split_data(mode: str = MODE, inputs: Path = JUDGE_INPUTS) -> dict[str, list[str]]:
    """Split the human labels 20/40/40 and report the class counts.

    Only traces with a judge input record are eligible, so every split entry
    can actually be scored. Written to analysis/state/splits.json under
    ``mode``; other modes in that file are left alone.
    """
    from analysis.helpers import split_labels

    records = json.loads(inputs.read_text())
    eligible = [record["trace_id"] for record in records]
    splits = split_labels(
        mode,
        fractions=(0.20, 0.40, 0.40),
        seed=7,
        min_per_class=10,
        eligible_trace_ids=eligible,
    )

    # Report in HW5 terms (Pass=1, Fail=0); the stored files use failure flags.
    from analysis.helpers.tools import _load_labels

    failure_by_trace = {row["trace_id"]: row["label"] for row in _load_labels(mode)}
    print(f"split for '{mode}' (20/40/40, seed 7):")
    for name in ("train", "dev", "test"):
        ids = splits[name]
        fails = sum(failure_by_trace[trace_id] == 1 for trace_id in ids)
        print(f"  {name:5s} n={len(ids):3d}  Pass={len(ids) - fails:3d}  Fail={fails:3d}")
    return splits


def run_development(
    mode: str = MODE,
    prompt_path: str | Path = REPO_ROOT / "analysis" / "prompts" / f"{MODE}-v0.txt",
    judge_model: str = JUDGE_MODEL,
) -> dict[str, Any]:
    """Register a prompt version, score the dev split, save the metrics.

    Registers a new judge version, runs it over the development traces only,
    and writes analysis/report/dev-<judge_id>.json. The test split is never
    touched here.
    """
    from analysis.helpers import judge_alignment, register_judge, run_judge

    prompt_path = Path(prompt_path)
    record = register_judge(
        mode=mode,
        prompt_text=prompt_path.read_text(),
        judge_model=judge_model,
    )
    judge_id = record["judge_id"]
    print(f"registered {judge_id} from {prompt_path.name} on {judge_model}")

    run_judge(judge_id, split="dev", batch_size=10)
    metrics = judge_alignment(judge_id, split="dev")
    _save_metrics(metrics, f"dev-{judge_id}.json")
    _print_metrics(metrics)
    return metrics


def run_test(judge_id: str) -> dict[str, Any]:
    """Freeze the chosen judge, score the held out test split once, save it."""
    from analysis.helpers import freeze_judge, judge_alignment, run_judge

    freeze_judge(judge_id)
    run_judge(judge_id, split="test", batch_size=10)
    metrics = judge_alignment(judge_id, split="test")
    _save_metrics(metrics, f"test-{judge_id}.json")
    _print_metrics(metrics)
    return metrics


def _save_metrics(metrics: dict[str, Any], filename: str) -> Path:
    report = REPO_ROOT / "analysis" / "report"
    report.mkdir(parents=True, exist_ok=True)
    path = report / filename
    path.write_text(json.dumps(metrics, indent=2) + "\n")
    print(f"saved {path}")
    return path


def _print_metrics(metrics: dict[str, Any]) -> None:
    """Confusion counts and rates, with Pass as the positive class."""
    print(
        f"  n={metrics['n']}  TP={metrics['tp']} FN={metrics['fn']} "
        f"TN={metrics['tn']} FP={metrics['fp']}"
    )
    print(f"  TPR (agrees with human Pass) = {metrics['tpr']}  95% CI {metrics['tpr_interval']}")
    print(f"  TNR (agrees with human Fail) = {metrics['tnr']}  95% CI {metrics['tnr_interval']}")
    print(f"  agreement = {metrics['agreement']}  disagreements = {len(metrics['disagreements'])}")


def disagreement_sheet(judge_id: str, split: str = "dev") -> Path:
    """Write every disagreement to one readable Markdown file for review.

    Shows the user request, the tool calls, the assistant reply, the human
    label, the judge verdict, and the judge's critique, so each case can be
    adjudicated without starting the review app.
    """
    from analysis.helpers.tools import _load_judge

    judge = _load_judge(judge_id)
    critiques = judge.get("critiques", {}).get(judge["prompt_hash"], {})
    preds = judge["predictions"][judge["prompt_hash"]]

    inputs = {r["trace_id"]: r for r in json.loads(JUDGE_INPUTS.read_text())}
    labels = {
        json.loads(line)["trace_id"]: json.loads(line)["label"]
        for line in (STATE / "hw5_labels" / f"{judge['mode']}.jsonl").read_text().splitlines()
        if line.strip()
    }
    scenario_by_trace = {
        trace["id"]: trace.get("cartwheel_scenario_id")
        for trace in json.loads(TRACE_EXPORT.read_text())["traces"]
    }

    split_ids = json.loads((STATE / "splits.json").read_text())[judge["mode"]][split]
    scored = [tid for tid in split_ids if tid in preds]
    # labels are Pass=1 here, and so are predictions (label_convention pass_positive)
    disagreements = [tid for tid in scored if int(labels[tid]) != int(preds[tid])]

    lines = [
        f"# Disagreements: {judge_id} ({split} split)",
        "",
        f"{len(disagreements)} of {len(scored)} traces disagree. "
        "For each: judge wrong (fix prompt), label wrong (flip it), or definition unclear.",
        "",
    ]
    for trace_id in disagreements:
        record = inputs[trace_id]
        human = "Pass" if labels[trace_id] == 1 else "Fail"
        verdict = "Pass" if preds[trace_id] == 1 else "Fail"
        user = next((m["text"] for m in record["trace"] if m["role"] == "user"), "")
        reply = next((m["text"] for m in record["trace"] if m["role"] == "assistant"), "")
        tools = [m["name"] for m in record["trace"] if m["role"] == "tool_call"]
        lines += [
            f"## {scenario_by_trace.get(trace_id)} — human **{human}**, judge **{verdict}**",
            f"`{trace_id}`",
            "",
            f"**User:** {user}",
            "",
            f"**Tools:** {', '.join(tools) if tools else 'none'}",
            "",
            "**Reply:**",
            "",
            "> " + reply.replace("\n", "\n> "),
            "",
            "**Judge critique:**",
            "",
            "> " + str(critiques.get(trace_id, "(no critique captured)")).replace("\n", "\n> "),
            "",
            "**Your call:** judge wrong / label wrong / definition unclear",
            "",
            "---",
            "",
        ]
    path = REPO_ROOT / "analysis" / "report" / f"disagreements-{judge_id}-{split}.md"
    path.write_text("\n".join(lines))
    print(f"wrote {path}")
    return path


def main(argv: list[str] | None = None) -> None:
    """Command line entry point.

        uv run python -m analysis.run_judges prep          # inputs + split
        uv run python -m analysis.run_judges dev           # register v0, score dev, write sheet
        uv run python -m analysis.run_judges dev v1        # same for a revised prompt
        uv run python -m analysis.run_judges sheet <id>    # rewrite the disagreement sheet
        uv run python -m analysis.run_judges test <id>     # freeze and score test, once
    """
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "prep"

    if command == "prep":
        prepare_inputs()
        split_data()
    elif command == "dev":
        version = args[1] if len(args) > 1 else "v0"
        prompt = REPO_ROOT / "analysis" / "prompts" / f"{MODE}-{version}.txt"
        if not prompt.exists():
            raise SystemExit(f"no prompt at {prompt}")
        metrics = run_development(prompt_path=prompt)
        disagreement_sheet(metrics["judge_id"], split="dev")
    elif command == "sheet":
        if len(args) < 2:
            raise SystemExit("usage: sheet <judge_id> [dev|test]")
        disagreement_sheet(args[1], split=args[2] if len(args) > 2 else "dev")
    elif command == "test":
        if len(args) < 2:
            raise SystemExit("usage: test <judge_id>")
        run_test(args[1])
    else:
        raise SystemExit(f"unknown command '{command}'; see the docstring in {__file__}")


if __name__ == "__main__":
    main()
