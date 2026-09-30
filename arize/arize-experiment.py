#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
#
# Run experiments against the astronomy-shop-golden dataset.
# Uses Phoenix REST API directly — no arize-phoenix SDK required.
# LLM-as-judge evaluators use the openai package directly.
#
# Evaluators:
#   tool_selection  [code]  Did agent call the right tool?
#   tool_args_match [code]  Correct JSON arguments?
#   response_facts  [code]  All expected facts in response?
#   faithfulness    [LLM]   Any hallucinated claims?
#   completeness    [LLM]   Any important items omitted?
#
# After scoring every example, posts one aggregate business event to Dynatrace
# so a Site Reliability Guardian can qualify the release using this signal
# alongside the release's live operational telemetry. This script never makes
# the qualify/reject call itself — Dynatrace is the sole release authority.
#
# Usage:
#   pip install requests openai pyyaml
#   bash arize/port-forward.sh
#   export OPENAI_API_KEY=<your-key>
#   export DT_ENV_URL=https://<env-id>.live.dynatrace.com   # optional, skips bizevent if unset
#   export DT_API_TOKEN=<token with bizevents.ingest scope>  # optional
#   python3 arize/arize-experiment.py

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from openai import OpenAI

DATASET_NAME = "astronomy-shop-golden"
PHOENIX_ENDPOINT = os.getenv("ARIZE_ENDPOINT", "http://localhost:6006")
AGENT_ENDPOINT = os.getenv("AGENT_ENDPOINT", "http://localhost:8010")

# Dynatrace is the release-qualification authority: this script only reports the
# eval outcome as a business event, it never blocks or approves anything itself.
DT_ENV_URL = os.getenv("DT_ENV_URL")  # e.g. https://abc12345.live.dynatrace.com
DT_API_TOKEN = os.getenv("DT_API_TOKEN")
DT_AUTH_SCHEME = os.getenv("DT_AUTH_SCHEME", "Api-Token")  # "Api-Token" (classic) or "Bearer" (platform)
AGENT_SERVICE_NAME = os.getenv("AGENT_SERVICE_NAME", "agent")
RELEASE_VERSION = os.getenv("RELEASE_VERSION", "unknown")
EVAL_PASS_THRESHOLD = float(os.getenv("EVAL_PASS_THRESHOLD", "0.7"))


# ── Phoenix REST helpers ──────────────────────────────────────────────────────

def get_dataset(name: str) -> dict:
    resp = requests.get(f"{PHOENIX_ENDPOINT}/v1/datasets", timeout=10)
    resp.raise_for_status()
    for ds in resp.json().get("data", []):
        if ds["name"] == name:
            return ds
    raise ValueError(f"Dataset '{name}' not found. Run arize-dataset.py first.")


def get_examples(dataset_id: str) -> list[dict]:
    resp = requests.get(f"{PHOENIX_ENDPOINT}/v1/datasets/{dataset_id}/examples", timeout=10)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("examples", [])


def create_experiment(dataset_id: str, name: str) -> str:
    resp = requests.post(
        f"{PHOENIX_ENDPOINT}/v1/datasets/{dataset_id}/experiments",
        json={"name": name},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["data"]["id"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_run(experiment_id: str, example_id: str, output: dict, start_time: str, end_time: str) -> str:
    payload = {
        "dataset_example_id": example_id,
        "output": output,
        "repetition_number": 1,
        "start_time": start_time,
        "end_time": end_time,
    }
    resp = requests.post(
        f"{PHOENIX_ENDPOINT}/v1/experiments/{experiment_id}/runs",
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["data"]["id"]


def post_evaluation(run_id: str, name: str, annotator_kind: str, result: dict) -> None:
    timestamp = _now()
    payload = {
        "experiment_run_id": run_id,
        "name": name,
        "annotator_kind": annotator_kind,
        "start_time": timestamp,
        "end_time": timestamp,
        "result": {
            "label": result.get("label"),
            "score": result.get("score"),
            "explanation": result.get("explanation", ""),
        },
    }
    resp = requests.post(f"{PHOENIX_ENDPOINT}/v1/experiment_evaluations", json=payload, timeout=10)
    resp.raise_for_status()


# ── Task: call live agent ─────────────────────────────────────────────────────

def call_live_agent(message: str) -> dict:
    try:
        resp = requests.post(
            f"{AGENT_ENDPOINT}/prompt",
            json={"message": message, "history": []},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json().get("response", {})
        messages = data.get("messages", [])

        output = ""
        tools_called: list[dict] = []

        for msg in messages:
            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type") or msg.get("role")
            if msg_type in ("ai", "assistant") and msg.get("content"):
                output = msg["content"]

            tool_calls = msg.get("tool_calls")
            if not tool_calls or not isinstance(tool_calls, list):
                continue

            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                name = call.get("name", "")
                raw_args = call.get("args", call.get("arguments", {}))
                if isinstance(raw_args, dict):
                    args = raw_args
                else:
                    try:
                        args = json.loads(raw_args) if raw_args not in ("{}", "", None) else {}
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                tools_called.append({"name": name, "args": args})

        first = tools_called[0] if tools_called else {"name": "", "args": {}}
        return {
            "output": output,
            "tools_called": tools_called,
            "tool_called": first["name"],
            "tool_args": first["args"],
        }
    except Exception as exc:
        return {"output": f"ERROR: {exc}", "tools_called": [], "tool_called": "", "tool_args": {}}


# ── Code-based evaluators ─────────────────────────────────────────────────────

def eval_tool_selection(actual: dict, metadata: dict) -> dict:
    expected = metadata.get("expected_tool", "")
    called_names = [c.get("name", "") for c in actual.get("tools_called", [])]
    unexpected = [n for n in called_names if n != expected]
    correct = expected in called_names and not unexpected
    return {
        "score": 1.0 if correct else 0.0,
        "label": "correct" if correct else "wrong",
        "explanation": (
            f"expected={expected!r} matched, no extra calls"
            if correct
            else f"expected={expected!r} got={called_names!r}"
        ),
    }


def eval_tool_args_match(actual: dict, metadata: dict) -> dict:
    expected_tool = metadata.get("expected_tool", "")
    matching_call = next(
        (c for c in actual.get("tools_called", []) if c.get("name") == expected_tool), None
    )
    actual_args = matching_call.get("args", {}) if matching_call else {}

    try:
        expected_args = json.loads(metadata.get("expected_tool_args", "{}"))
    except (json.JSONDecodeError, TypeError):
        expected_args = {}

    if not expected_args:
        return {"score": 1.0, "label": "correct", "explanation": "tool takes no arguments"}

    if matching_call is None:
        return {
            "score": 0.0,
            "label": "wrong",
            "explanation": f"expected tool {expected_tool!r} was never called",
        }

    mismatches = [
        f"{k}: expected={v!r} got={actual_args.get(k)!r}"
        for k, v in expected_args.items()
        if actual_args.get(k) != v
    ]
    return {
        "score": 0.0 if mismatches else 1.0,
        "label": "wrong" if mismatches else "correct",
        "explanation": "; ".join(mismatches) if mismatches else f"args match: {json.dumps(actual_args)}",
    }


def eval_response_facts(actual: dict, metadata: dict) -> dict:
    response = actual.get("output", "")
    try:
        expected_facts: list[str] = json.loads(metadata.get("expected_facts", "[]"))
    except (json.JSONDecodeError, TypeError):
        expected_facts = []

    if not expected_facts or not response or response.startswith("ERROR:"):
        return {"score": 0.0, "label": "fail", "explanation": "no response or no facts"}

    missing = [f for f in expected_facts if f.lower() not in response.lower()]
    score = round(1.0 - len(missing) / len(expected_facts), 2)
    return {
        "score": score,
        "label": "pass" if not missing else "fail",
        "explanation": (
            f"all {len(expected_facts)} facts present"
            if not missing
            else f"missing {len(missing)}/{len(expected_facts)}: {missing[:3]}"
        ),
    }


# ── LLM-as-judge evaluators ───────────────────────────────────────────────────

def llm_judge(client: OpenAI, prompt: str, rails: list[str]) -> str:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=10,
        temperature=0,
    )
    answer = resp.choices[0].message.content.strip().lower()
    return answer if answer in rails else rails[-1]


def eval_faithfulness(actual: dict, metadata: dict, client: OpenAI) -> dict:
    tool_output = metadata.get("tool_output", "")
    response = actual.get("output", "")
    if not response or not tool_output or response.startswith("ERROR:"):
        return {"score": 0.0, "label": "hallucinated"}

    prompt = f"""Is every factual claim in the assistant response supported by the retrieved data?

Retrieved data:
{tool_output}

Assistant response:
{response}

Answer with exactly one word: "faithful" or "hallucinated"."""

    label = llm_judge(client, prompt, ["faithful", "hallucinated"])
    return {"score": 1.0 if label == "faithful" else 0.0, "label": label}


def eval_completeness(actual: dict, metadata: dict, client: OpenAI) -> dict:
    tool_output = metadata.get("tool_output", "")
    response = actual.get("output", "")
    if not response or not tool_output or response.startswith("ERROR:"):
        return {"score": 0.0, "label": "incomplete"}

    prompt = f"""Did the assistant response cover all key items from the system data without omitting important entries?

System data:
{tool_output}

Assistant response:
{response}

Answer with exactly one word: "complete" or "incomplete"."""

    label = llm_judge(client, prompt, ["complete", "incomplete"])
    return {"score": 1.0 if label == "complete" else 0.0, "label": label}


# ── Dynatrace business event ──────────────────────────────────────────────────
#
# Reports the aggregate eval outcome as one bizevent per experiment run. This
# script makes no qualification decision — a Site Reliability Guardian in
# Dynatrace is the single authority that combines this signal with the live
# operational telemetry from the same release to qualify or reject it.

def build_bizevent(experiment_id: str, experiment_name: str, dataset_id: str,
                    scores_by_evaluator: dict, latencies_ms: list[float],
                    start_time: str, end_time: str) -> dict:
    event = {
        "event.type": "ai_agent.eval.experiment_completed",
        "event.provider": "phoenix",
        "unique_id": experiment_id,
        "timeframe.from": start_time,
        "timeframe.to": end_time,
        "agent.service_name": AGENT_SERVICE_NAME,
        "release.version": RELEASE_VERSION,
        "dataset.name": DATASET_NAME,
        "dataset.id": dataset_id,
        "experiment.id": experiment_id,
        "experiment.name": experiment_name,
        "experiment.url": f"{PHOENIX_ENDPOINT}/experiments/{experiment_id}",
        # TODO: once traces are correlated per-example (CI/CD workflow phase), add a
        # per-failing-example Phoenix trace/span URL here so Dynatrace can deep-link
        # straight from the qualification failure to the trace that explains it.
    }

    if latencies_ms:
        event["agent.avg_latency_ms"] = round(sum(latencies_ms) / len(latencies_ms), 1)
        event["agent.max_latency_ms"] = round(max(latencies_ms), 1)
        event["agent.run_count"] = len(latencies_ms)

    overall_scores = []
    all_passed = True
    for name, scores in scores_by_evaluator.items():
        avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
        pass_count = sum(1 for s in scores if s >= EVAL_PASS_THRESHOLD)
        passed = pass_count == len(scores) and len(scores) > 0
        all_passed = all_passed and passed

        event[f"eval.{name}.avg_score"] = avg_score
        event[f"eval.{name}.pass_count"] = pass_count
        event[f"eval.{name}.total_count"] = len(scores)
        event[f"eval.{name}.passed"] = passed
        overall_scores.append(avg_score)

    event["eval.overall.avg_score"] = round(sum(overall_scores) / len(overall_scores), 4) if overall_scores else 0.0
    event["eval.overall.passed"] = all_passed
    event["eval.overall.threshold"] = EVAL_PASS_THRESHOLD
    return event


def send_bizevent(event: dict) -> None:
    if not DT_ENV_URL or not DT_API_TOKEN:
        print("\n[skip] DT_ENV_URL / DT_API_TOKEN not set — skipping Dynatrace bizevent.")
        return

    resp = requests.post(
        f"{DT_ENV_URL.rstrip('/')}/api/v2/bizevents/ingest",
        json=event,
        headers={
            "Authorization": f"{DT_AUTH_SCHEME} {DT_API_TOKEN}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    print(f"\nSent bizevent to Dynatrace: eval.overall.passed={event['eval.overall.passed']}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)

    print(f"Phoenix  : {PHOENIX_ENDPOINT}")
    print(f"Agent    : {AGENT_ENDPOINT}")

    try:
        requests.get(f"{PHOENIX_ENDPOINT}/healthz", timeout=5)
    except Exception:
        print("ERROR: Cannot reach Phoenix. Run: bash arize/port-forward.sh")
        sys.exit(1)

    openai_client = OpenAI(api_key=openai_key)

    dataset = get_dataset(DATASET_NAME)
    examples = get_examples(dataset["id"])
    print(f"\nLoaded dataset '{DATASET_NAME}' ({len(examples)} examples)")

    experiment_name = f"eval-{int(time.time())}"
    experiment_id = create_experiment(dataset["id"], experiment_name)
    print(f"Created experiment '{experiment_name}' (id={experiment_id})\n")

    experiment_start = _now()
    scores_by_evaluator: dict[str, list[float]] = {
        "tool_selection": [], "tool_args_match": [], "response_facts": [],
        "faithfulness": [], "completeness": [],
    }
    latencies_ms: list[float] = []

    for ex in examples:
        user_input = ex.get("input", {}).get("message", "")
        metadata = ex.get("metadata", {})
        print(f"  Running: {user_input[:60]}")

        run_start = _now()
        perf_start = time.perf_counter()
        actual = call_live_agent(user_input)
        latencies_ms.append((time.perf_counter() - perf_start) * 1000)
        run_end = _now()
        print(f"    tool_called={actual['tool_called']!r}  output={actual['output'][:50]!r}...")

        run_id = create_run(experiment_id, ex["id"], actual, run_start, run_end)

        evals = {
            "tool_selection": ("CODE", eval_tool_selection(actual, metadata)),
            "tool_args_match": ("CODE", eval_tool_args_match(actual, metadata)),
            "response_facts":  ("CODE", eval_response_facts(actual, metadata)),
            "faithfulness":    ("LLM", eval_faithfulness(actual, metadata, openai_client)),
            "completeness":    ("LLM", eval_completeness(actual, metadata, openai_client)),
        }

        for name, (annotator_kind, result) in evals.items():
            icon = "✓" if result["score"] == 1.0 else "✗"
            print(f"    {icon} {name}: {result['label']} ({result['score']})")
            post_evaluation(run_id, name, annotator_kind, result)
            scores_by_evaluator[name].append(result["score"])

        print()

    experiment_end = _now()

    print(f"Experiment complete.")
    print(f"View: {PHOENIX_ENDPOINT}/experiments/{experiment_id}")

    bizevent = build_bizevent(
        experiment_id, experiment_name, dataset["id"],
        scores_by_evaluator, latencies_ms, experiment_start, experiment_end,
    )
    send_bizevent(bizevent)


if __name__ == "__main__":
    main()
