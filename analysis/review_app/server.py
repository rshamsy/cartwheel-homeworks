"""Cartwheel HW4 review app: conversation-grouped trace review with comments.

Adapted from the reference interface (``analysis/server.py``) after
inspecting the Module 1 traces. Changes from the reference:

- Traces are grouped into conversations by ``cartwheel.session_id`` and every
  turn (one Langfuse trace) is shown as its own section, in order.
- Every observation (span, agent, model call, tool call) is its own card, so
  the order of calls and their inputs and outputs are readable without
  clicking through a tree.
- Comments attach to a whole section (turn or card) or to selected text, and
  appear in a right margin next to their anchor, Google Docs style. Edits keep
  the previous text in ``history`` so the original open code stays inspectable.

Traces come from Langfuse (tagged ``CARTWHEEL_TRACE_TAG``) and are cached in
``analysis/review_app/.cache/traces.json``. Comments are saved to
``analysis/state/annotations.json``.

Run from the repository root:

    uv run python analysis/review_app/server.py                 # Langfuse, cached
    uv run python analysis/review_app/server.py --refresh       # re-pull Langfuse
    uv run python analysis/review_app/server.py --source export # offline export

Then open http://localhost:8030/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from analysis.helpers.normalization import _data, _metadata  # noqa: E402

STATE_DIR = ROOT / "analysis" / "state"
ANNOTATIONS = STATE_DIR / "annotations.json"
MANIFEST = STATE_DIR / "sample_manifest.json"
CACHE = HERE / ".cache" / "traces.json"
EXPORT = ROOT / "traces" / "support_traces.json"
RESULTS = ROOT / "scenarios" / "final-results.jsonl"
SPEC = ROOT / "SPEC.md"

# Tools whose results are help-center retrieval rather than order actions.
RETRIEVAL_TOOLS = {"search_help_center", "get_policy"}


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_env() -> None:
    """Read KEY=VALUE lines from .env without overriding the shell environment."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def fetch_langfuse(tag: str | None) -> list[dict[str, Any]]:
    """Pull full raw traces from Langfuse, restricted to ``tag`` when given."""
    from analysis.helpers import langfuse_io

    lf = langfuse_io._client()
    summaries: list[Any] = []
    page = 1
    while True:
        resp = lf.api.trace.list(tags=[tag] if tag else None, page=page, limit=100)
        batch = list(resp.data or [])
        summaries.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    traces = []
    for i, summary in enumerate(summaries, 1):
        # The SDK's own JSON encoding keeps camelCase keys and ISO timestamps,
        # matching the trace export.
        full = lf.api.trace.get(summary.id)
        traces.append(json.loads(full.json()) if hasattr(full, "json") else _data(full))
        if i % 20 == 0:
            print(f"  fetched {i}/{len(summaries)} traces")
    return traces


def load_traces(source: str, refresh: bool, tag: str | None) -> list[dict[str, Any]]:
    if source == "export":
        return json.loads(EXPORT.read_text())["traces"]
    if CACHE.exists() and not refresh:
        print(f"using cached traces from {CACHE.relative_to(ROOT)} (--refresh to re-pull)")
        return json.loads(CACHE.read_text())
    print(f"pulling traces from Langfuse (tag={tag or 'none'}) ...")
    traces = fetch_langfuse(tag)
    if not tag:
        traces = [t for t in traces if _metadata(t).get("cartwheel.scenario_id")]
    if not traces:
        raise SystemExit("Langfuse returned no traces; check the tag and project.")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(traces))
    return traces


def load_run_status() -> dict[str, dict[str, Any]]:
    """Scenario run status (completed or error) from the HW3 results file."""
    out: dict[str, dict[str, Any]] = {}
    if not RESULTS.exists():
        return out
    for line in RESULTS.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["scenario_id"]] = {"status": row.get("status"), "error": row.get("error")}
    return out


# ---------------------------------------------------------------------------
# shaping traces for the page
# ---------------------------------------------------------------------------


def _text_parts(messages: Any) -> list[str]:
    out = []
    for item in messages if isinstance(messages, list) else []:
        for part in (item or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("type") == "text" and str(part.get("content", "")).strip():
                out.append(str(part["content"]))
    return out


def _tool_requests(messages: Any) -> list[dict[str, Any]]:
    out = []
    for item in messages if isinstance(messages, list) else []:
        for part in (item or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("type") == "tool_call":
                out.append({"name": part.get("name"), "arguments": part.get("arguments")})
    return out


def _duration_ms(obs: dict[str, Any]) -> int | None:
    from datetime import datetime

    start, end = obs.get("startTime"), obs.get("endTime")
    if not start or not end:
        return None
    parse = lambda s: datetime.fromisoformat(str(s).replace("Z", "+00:00"))  # noqa: E731
    return int((parse(end) - parse(start)).total_seconds() * 1000)


def shape_turn(trace: dict[str, Any]) -> dict[str, Any]:
    """Turn one raw trace into an ordered list of display steps."""
    observations = sorted(
        trace.get("observations") or [], key=lambda o: o.get("startTime") or ""
    )
    by_id = {o["id"]: o for o in observations}

    def depth(obs: dict[str, Any]) -> int:
        d, parent = 0, obs.get("parentObservationId")
        while parent in by_id and d < 20:
            d, parent = d + 1, by_id[parent].get("parentObservationId")
        return d

    steps: list[dict[str, Any]] = []
    user_text = "\n".join(_text_parts(trace.get("input"))) or (
        trace.get("input") if isinstance(trace.get("input"), str) else ""
    )
    steps.append({"id": "user", "kind": "user", "depth": 0, "text": user_text})

    generation_no = 0
    for obs in observations:
        base = {
            "id": obs["id"],
            "type": obs.get("type"),
            "name": obs.get("name"),
            "depth": depth(obs),
            "start": obs.get("startTime"),
            "duration_ms": _duration_ms(obs),
            "level": obs.get("level"),
            "status_message": obs.get("statusMessage"),
        }
        otype = obs.get("type")
        if otype == "GENERATION":
            generation_no += 1
            out = obs.get("output")
            steps.append({
                **base,
                "kind": "generation",
                "number": generation_no,
                "model": obs.get("model"),
                "texts": _text_parts(out),
                "tool_requests": _tool_requests(out),
                "prompt": obs.get("input"),
            })
        elif otype == "TOOL":
            out = obs.get("output")
            summary = {}
            if isinstance(out, dict):
                for key in ("ok", "error", "reason", "status", "refund_eligible",
                            "amount_usd", "count", "ticket_id", "refund_id"):
                    if key in out:
                        summary[key] = out[key]
            steps.append({
                **base,
                "kind": "tool",
                "retrieval": obs.get("name") in RETRIEVAL_TOOLS,
                "input": obs.get("input"),
                "output": out,
                "summary": summary,
            })
        else:
            steps.append({**base, "kind": "span"})

    # The final reply is the text of the last model call that requested no tool.
    generations = [s for s in steps if s["kind"] == "generation"]
    if generations and generations[-1]["texts"] and not generations[-1]["tool_requests"]:
        generations[-1]["final"] = True
    else:
        reply = "\n".join(_text_parts(trace.get("output")))
        steps.append({"id": "reply", "kind": "missing_reply", "depth": 0, "text": reply})

    meta = _metadata(trace)
    return {
        "trace_id": trace["id"],
        "timestamp": trace.get("timestamp"),
        "user_id": meta.get("cartwheel.user_id"),
        "steps": steps,
    }


def build_conversations(traces: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    runs = load_run_status()
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        meta = _metadata(trace)
        key = meta.get("cartwheel.session_id") or trace.get("sessionId") or trace["id"]
        groups[str(key)].append(trace)
    conversations = {}
    for session_id, group in groups.items():
        group.sort(key=lambda t: t.get("timestamp") or "")
        meta = _metadata(group[0])
        scenario = meta.get("cartwheel.scenario_id")
        models = sorted({
            o.get("model") for t in group for o in t.get("observations") or [] if o.get("model")
        })
        conversations[session_id] = {
            "session_id": session_id,
            "scenario_id": scenario,
            "role": meta.get("cartwheel.user_role"),
            "prompt_version": meta.get("cartwheel.prompt_version"),
            "models": models,
            "run": runs.get(scenario or "", {}),
            "trace_ids": [t["id"] for t in group],
            "turns": [shape_turn(t) for t in group],
        }
    return conversations


def parse_spec() -> list[dict[str, str]]:
    """Split SPEC.md into requirement blocks keyed by their identifiers."""
    blocks: list[dict[str, str]] = []
    section = ""
    current: dict[str, str] | None = None
    for line in SPEC.read_text().splitlines():
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            current = None
            continue
        row = re.match(r"\|\s*(TOOL-\d+)\s*\|(.*)", line)
        match = re.search(r"\*\*([A-Z]+-\d+)\.\*\*\s*(.*)", line)
        if row:
            cells = [c.strip() for c in row.group(2).split("|") if c.strip()]
            blocks.append({"id": row.group(1), "section": section, "text": " · ".join(cells)})
            current = None
        elif match:
            current = {"id": match.group(1), "section": section, "text": match.group(2)}
            blocks.append(current)
        elif current is not None and line.strip():
            current["text"] += "\n" + line
    return blocks


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def read_annotations() -> dict[str, Any]:
    if not ANNOTATIONS.exists():
        return {"annotations": []}
    try:
        data = json.loads(ANNOTATIONS.read_text())
    except json.JSONDecodeError:
        return {"annotations": []}
    if isinstance(data, list):
        data = {"annotations": data}
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def read_manifest(conversations: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Batches of session ids, from sample_manifest.json when present."""
    by_trace = {tid: sid for sid, c in conversations.items() for tid in c["trace_ids"]}
    ordered = sorted(conversations, key=lambda s: conversations[s]["scenario_id"] or s)
    if not MANIFEST.exists():
        return [{"name": "All conversations (no sample yet)", "sessions": ordered}]
    manifest = json.loads(MANIFEST.read_text())
    raw_batches = manifest.get("batches") or [
        {"name": "Sample", "trace_ids": [p["trace_id"] for p in manifest.get("picks", [])]}
    ]
    batches = []
    for batch in raw_batches:
        sessions: list[str] = []
        for tid in batch.get("trace_ids") or []:
            sid = by_trace.get(tid)
            if sid and sid not in sessions:
                sessions.append(sid)
        for sid in batch.get("sessions") or []:
            if sid in conversations and sid not in sessions:
                sessions.append(sid)
        batches.append({"name": batch.get("name", "Batch"), "method": batch.get("method"), "sessions": sessions})
    return batches


def make_handler(conversations: dict[str, dict[str, Any]], spec: list[dict[str, str]]):
    index = [
        {k: c[k] for k in ("session_id", "scenario_id", "role", "trace_ids", "run")}
        for c in conversations.values()
    ]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
            return

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, status: int = 200) -> None:
            self._send(json.dumps(data).encode(), "application/json", status)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                self._send((HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path in ("/app.js", "/app.css"):
                kind = "text/javascript" if url.path.endswith(".js") else "text/css"
                self._send((HERE / url.path[1:]).read_bytes(), kind + "; charset=utf-8")
            elif url.path == "/api/index":
                self._json({"conversations": index, "batches": read_manifest(conversations)})
            elif url.path == "/api/conversation":
                sid = parse_qs(url.query).get("id", [""])[0]
                if sid in conversations:
                    self._json(conversations[sid])
                else:
                    self._json({"error": f"unknown conversation {sid}"}, 404)
            elif url.path == "/api/annotations":
                self._json(read_annotations())
            elif url.path == "/api/spec":
                self._json(spec)
            else:
                self._json({"error": f"unknown path {url.path}"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/annotations":
                self._json({"error": "not found"}, 404)
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json({"error": "expected JSON"}, 400)
                return
            if not isinstance(data, dict) or not isinstance(data.get("annotations"), list):
                self._json({"error": "expected {annotations: [...]}"}, 400)
                return
            write_json(ANNOTATIONS, data)
            self._json({"ok": True, "count": len(data["annotations"])})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--source", choices=["langfuse", "export"], default="langfuse")
    parser.add_argument("--refresh", action="store_true", help="re-pull traces from Langfuse")
    args = parser.parse_args()

    load_env()
    tag = os.environ.get("CARTWHEEL_TRACE_TAG") or None
    traces = load_traces(args.source, args.refresh, tag)
    conversations = build_conversations(traces)
    turns = sum(len(c["turns"]) for c in conversations.values())
    print(f"{len(conversations)} conversations, {turns} traces (source: {args.source})")

    server = ThreadingHTTPServer((args.host, args.port), make_handler(conversations, parse_spec()))
    print(f"review app on http://{args.host}:{args.port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")


if __name__ == "__main__":
    main()
