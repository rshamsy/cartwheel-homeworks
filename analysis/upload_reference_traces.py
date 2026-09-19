"""Upload the HW3 reference trace export into your own Langfuse project.

Homework 4 labels traces as Langfuse scores, so the reviewed traces must exist
in the Langfuse project named by ``.env``. The reference bundle
(``homework/module-2/hw3-reference.patch``) ships only a JSON export, so this
script re-creates each trace and observation with its original identifiers,
timestamps, parent links, and metadata. Every uploaded trace carries the tag
``hw4-reference`` (or ``--tag``) so the Module 2 loader can separate it from
other traces in the same project (set ``CARTWHEEL_TRACE_TAG`` in ``.env``).

Standard library only. Credentials come from ``LANGFUSE_PUBLIC_KEY``,
``LANGFUSE_SECRET_KEY``, and ``LANGFUSE_HOST`` in ``.env`` and are never printed.

    python3 analysis/upload_reference_traces.py            # dry run, sends nothing
    python3 analysis/upload_reference_traces.py --send     # upload
    python3 analysis/upload_reference_traces.py --verify   # check what Langfuse holds

Re-running ``--send`` is safe: Langfuse upserts by identifier.

The ingestion API accepts only SPAN, GENERATION, and EVENT observations, so
AGENT and TOOL steps (the tool calls the review depends on) are sent through
Langfuse's OpenTelemetry endpoint, the route by which they were first recorded.
"""

from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import json
import struct
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPORT = ROOT / "traces" / "support_traces.json"
DEFAULT_TAG = "hw4-reference"

TRACE_FIELDS = {
    "id": "id",
    "timestamp": "timestamp",
    "name": "name",
    "userId": "userId",
    "sessionId": "sessionId",
    "input": "input",
    "output": "output",
    "metadata": "metadata",
    "release": "release",
    "version": "version",
    "environment": "environment",
}
OBSERVATION_FIELDS = [
    "id", "traceId", "type", "name", "startTime", "endTime",
    "completionStartTime", "model", "modelParameters", "input", "output",
    "metadata", "level", "statusMessage", "parentObservationId", "version",
    "environment",
]
# Observation types the ingestion API rejects; sent over OpenTelemetry instead.
OTEL_TYPES = {"AGENT", "TOOL"}


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


def request(
    method: str,
    path: str,
    body: object | None = None,
    content_type: str = "application/json",
) -> tuple[int, object]:
    host = os.environ["LANGFUSE_HOST"].rstrip("/")
    token = base64.b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
    ).decode()
    if isinstance(body, bytes):
        data = body
    else:
        data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        host + path,
        data=data,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw.decode(errors="replace")[:300]


def build_batch(trace: dict, tag: str) -> list[dict]:
    """One trace-create event followed by one observation-create per step."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    body = {out: trace.get(src) for out, src in TRACE_FIELDS.items()}
    body["tags"] = sorted(set(trace.get("tags") or []) | {tag})
    events = [{
        "id": str(uuid.uuid4()),
        "timestamp": now,
        "type": "trace-create",
        "body": {k: v for k, v in body.items() if v is not None},
    }]
    for obs in sorted(trace["observations"], key=lambda o: o.get("startTime") or ""):
        if obs["type"] in OTEL_TYPES:
            continue
        obody = {k: obs.get(k) for k in OBSERVATION_FIELDS}
        obody["traceId"] = trace["id"]
        events.append({
            "id": str(uuid.uuid4()),
            "timestamp": now,
            "type": "observation-create",
            "body": {k: v for k, v in obody.items() if v is not None},
        })
    return events


# Minimal OTLP protobuf encoding (opentelemetry/proto/trace/v1/trace.proto), so
# the script needs no dependencies.


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(number: int, payload: bytes | str) -> bytes:
    if isinstance(payload, str):
        payload = payload.encode()
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def _fixed64(number: int, value: int) -> bytes:
    return _varint(number << 3 | 1) + struct.pack("<Q", value)


def _key_value(key: str, value: object) -> bytes:
    text = value if isinstance(value, str) else json.dumps(value)
    return _field(1, key) + _field(2, _field(1, text))


def _unix_nano(stamp: str) -> int:
    parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1_000_000_000)


def build_otel(trace: dict) -> bytes | None:
    """Encode the trace's AGENT and TOOL observations as one OTLP request."""
    spans = []
    resource = scope = None
    for obs in trace["observations"]:
        if obs["type"] not in OTEL_TYPES:
            continue
        meta = obs.get("metadata") or {}
        resource = resource or meta.get("resourceAttributes") or {}
        scope = scope or meta.get("scope") or {}
        attrs = dict(meta.get("attributes") or {})
        attrs["langfuse.observation.type"] = obs["type"].lower()
        if obs.get("input") is not None:
            attrs["langfuse.observation.input"] = json.dumps(obs["input"])
        if obs.get("output") is not None:
            attrs["langfuse.observation.output"] = json.dumps(obs["output"])
        if obs.get("level") and obs["level"] != "DEFAULT":
            attrs["langfuse.observation.level"] = obs["level"]
        if obs.get("statusMessage"):
            attrs["langfuse.observation.status_message"] = obs["statusMessage"]
        span = _field(1, bytes.fromhex(trace["id"])) + _field(2, bytes.fromhex(obs["id"]))
        if obs.get("parentObservationId"):
            span += _field(4, bytes.fromhex(obs["parentObservationId"]))
        span += _field(5, obs.get("name") or obs["type"].lower())
        span += _varint(6 << 3) + _varint(1)  # SPAN_KIND_INTERNAL
        start = _unix_nano(obs["startTime"])
        span += _fixed64(7, start)
        span += _fixed64(8, _unix_nano(obs["endTime"]) if obs.get("endTime") else start)
        for key, value in attrs.items():
            span += _field(9, _key_value(key, value))
        spans.append(span)
    if not spans:
        return None
    resource_msg = b"".join(_field(1, _key_value(k, v)) for k, v in (resource or {}).items())
    scope_msg = _field(1, (scope or {}).get("name") or "cartwheel-reference-upload")
    if (scope or {}).get("version"):
        scope_msg += _field(2, scope["version"])
    scope_spans = _field(1, scope_msg) + b"".join(_field(2, s) for s in spans)
    resource_spans = _field(1, resource_msg) + _field(2, scope_spans)
    return _field(1, resource_spans)


def attributes(trace: dict) -> dict:
    meta = trace.get("metadata") or {}
    attrs = meta.get("attributes") if isinstance(meta, dict) else None
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except ValueError:
            attrs = None
    return attrs if isinstance(attrs, dict) else {}


def summarize(traces: list[dict], label: str) -> None:
    scenarios = {attributes(t).get("cartwheel.scenario_id") for t in traces} - {None}
    sessions = {attributes(t).get("cartwheel.session_id") for t in traces} - {None}
    types = collections.Counter(o["type"] for t in traces for o in t.get("observations") or [])
    print(f"{label}: {len(traces)} traces, {len(scenarios)} scenarios, "
          f"{len(sessions)} sessions, observations {dict(types)}")


def send(traces: list[dict], tag: str) -> int:
    failures = 0
    for i, trace in enumerate(traces, 1):
        status, resp = request("POST", "/api/public/ingestion", {"batch": build_batch(trace, tag)})
        errors = resp.get("errors", []) if isinstance(resp, dict) else [resp]
        otel = build_otel(trace)
        if otel and status < 300 and not errors:
            status, resp = request(
                "POST", "/api/public/otel/v1/traces", otel, "application/x-protobuf"
            )
            if status >= 300:
                errors = [resp]
        if status >= 300 or errors:
            failures += 1
            print(f"  [{i}/{len(traces)}] {trace['id']} HTTP {status}: {str(errors)[:300]}")
        elif i % 20 == 0 or i == len(traces):
            print(f"  [{i}/{len(traces)}] sent")
    return failures


def verify(traces: list[dict], tag: str) -> int:
    problems = 0
    found = []
    for trace in traces:
        status, live = request("GET", f"/api/public/traces/{trace['id']}")
        if status != 200 or not isinstance(live, dict):
            problems += 1
            print(f"  missing {trace['id']} (HTTP {status})")
            continue
        found.append(live)
        want = collections.Counter(o["type"] for o in trace["observations"])
        got = collections.Counter(o.get("type") for o in live.get("observations") or [])
        if tag not in (live.get("tags") or []) or want != got:
            problems += 1
            print(f"  mismatch {trace['id']}: tags={live.get('tags')} "
                  f"expected {dict(want)} got {dict(got)}")
    summarize(found, "in Langfuse")
    print(f"export ids found in Langfuse: {len(found)} of {len(traces)}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--send", action="store_true", help="upload the traces")
    mode.add_argument("--verify", action="store_true", help="compare Langfuse with the export")
    parser.add_argument("--tag", default=DEFAULT_TAG)
    args = parser.parse_args()

    traces = json.loads(EXPORT.read_text())["traces"]
    summarize(traces, "export")
    load_env()
    missing = [k for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST") if not os.environ.get(k)]

    if not (args.send or args.verify):
        sizes = [len(json.dumps({"batch": build_batch(t, args.tag)})) for t in traces]
        print(f"dry run: would send {len(traces)} batches tagged '{args.tag}', "
              f"largest {max(sizes) // 1024} KB")
        print("credentials:", "missing " + ", ".join(missing) if missing else "present")
        return 0
    if missing:
        print("missing in .env:", ", ".join(missing))
        return 1
    status, _ = request("GET", "/api/public/health")
    if status != 200:
        print(f"Langfuse not reachable at LANGFUSE_HOST (HTTP {status})")
        return 1

    if args.send:
        failures = send(traces, args.tag)
        print(f"done: {len(traces) - failures} sent, {failures} failed")
        print("Langfuse processes uploads asynchronously; wait a minute, then run --verify.")
        return 1 if failures else 0

    problems = verify(traces, args.tag)
    print("OK" if not problems else f"{problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
