#!/usr/bin/env python3
"""Submit an intent-driven Pramana experiment and save a self-contained run."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_NOTEBOOK = Path(__file__).with_name("pramana_report.ipynb")
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "experiments/pramana/results/intent"
TERMINAL = {"complete", "completed", "failed"}


def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def find_values(value: Any, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for candidate, child in value.items():
            if candidate == key and child:
                found.append(str(child))
            found.extend(find_values(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_values(child, key))
    return found


def download_telemetry_artifacts(
    results: dict[str, Any], run_dir: Path, telemetry_url: str
) -> None:
    base = telemetry_url.rstrip("/")
    result_ids = sorted(set(find_values(results, "telemetry_result_id")))
    telemetry_dir = run_dir / "telemetry"
    telemetry_dir.mkdir(exist_ok=True)
    for result_id in result_ids:
        try:
            result = request_json("GET", f"{base}/results/{result_id}")
            (telemetry_dir / f"{result_id}.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
            listing = request_json("GET", f"{base}/results/{result_id}/artifacts")
            for artifact in listing.get("artifacts", []):
                artifact_id = artifact.get("artifact_id")
                if not artifact_id:
                    continue
                filename = Path(artifact.get("filename") or f"{artifact_id}.bin").name
                destination = run_dir / f"{result_id}-{filename}"
                with urllib.request.urlopen(
                    f"{base}/artifacts/{artifact_id}", timeout=240
                ) as response:
                    destination.write_bytes(response.read())
        except Exception as exc:
            (telemetry_dir / f"{result_id}-download-error.txt").write_text(
                str(exc) + "\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intent", help="Natural-language experiment intent")
    parser.add_argument("--orchestrator-url", default="http://localhost:8005")
    parser.add_argument("--telemetry-url", default="http://localhost:8004")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()

    submitted = request_json("POST", f"{args.orchestrator_url.rstrip('/')}/intent", {"intent": args.intent})
    orchestration_id = submitted["orchestration_id"]
    run_dir = args.output_root.resolve() / orchestration_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "intent.json").write_text(
        json.dumps({"intent": args.intent, "submission": submitted}, indent=2) + "\n"
    )

    while True:
        status = request_json(
            "GET", f"{args.orchestrator_url.rstrip('/')}/orchestration/{orchestration_id}"
        )
        (run_dir / "orchestration.json").write_text(json.dumps(status, indent=2) + "\n")
        if str(status.get("status", "")).lower() in TERMINAL:
            break
        time.sleep(args.poll_seconds)

    try:
        results = request_json(
            "GET", f"{args.orchestrator_url.rstrip('/')}/orchestration/{orchestration_id}/results"
        )
    except Exception as exc:
        results = {"error": str(exc), "results": []}
    (run_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    download_telemetry_artifacts(results, run_dir, args.telemetry_url)

    env = {
        **os.environ,
        "PRAMANA_RUN_DIR": str(run_dir),
        "PRAMANA_REPORT_TITLE": f"Pramana intent report — {orchestration_id}",
    }
    subprocess.run(
        [sys.executable, "-m", "jupyter", "nbconvert", "--to", "html", "--execute",
         str(REPORT_NOTEBOOK), "--output", "report.html", "--output-dir", str(run_dir),
         "--ExecutePreprocessor.timeout=300"],
        cwd=REPO_ROOT, env=env, check=True,
    )
    print(f"Pramana intent run: {run_dir}")
    return 0 if str(status.get("status", "")).lower() in {"complete", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
