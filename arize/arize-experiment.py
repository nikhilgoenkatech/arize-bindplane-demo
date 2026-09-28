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
# Usage:
#   pip install requests openai pyyaml
#   bash arize/port-forward.sh
#   export OPENAI_API_KEY=<your-key>
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
        tool_called = ""
        tool_args_actual: dict = {}

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant" and msg.get("content"):
                output = msg["content"]
            if msg.get("tool_calls"):
                fn = msg["tool_calls"][0]["function"]
                tool_called = fn.get("name", "")
                raw = fn.get("arguments", "{}")
                try:
                    tool_args_actual = json.loads(raw) if raw not in ("{}", "") else {}
                except json.JSONDecodeError:
                    tool_args_actual = {}

        return {"output": output, "tool_called": tool_called, "tool_args": tool_args_actual}
    except Exception as exc:
        return {"output": f"ERROR: {exc}", "tool_called": "", "tool_args": {}}


# ── Code-based evaluators ─────────────────────────────────────────────────────

def eval_tool_selection(actual: dict, metadata: dict) -> dict:
    got = actual.get("tool_called", "")
    expected = metadata.get("expected_tool", "")
    correct = got == expected
    return {
        "score": 1.0 if correct else 0.0,
        "label": "correct" if correct else "wrong",
        "explanation": f"expected={expected!r} got={got!r}",
    }


def eval_tool_args_match(actual: dict, metadata: dict) -> dict:
    actual_args = actual.get("tool_args", {})
    try:
        expected_args = json.loads(metadata.get("expected_tool_args", "{}"))
    except (json.JSONDecodeError, TypeError):
        expected_args = {}

    if not expected_args:
        return {"score": 1.0, "label": "correct", "explanation": "tool takes no arguments"}

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

    for ex in examples:
        user_input = ex.get("input", {}).get("message", "")
        metadata = ex.get("metadata", {})
        print(f"  Running: {user_input[:60]}")

        run_start = _now()
        actual = call_live_agent(user_input)
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

        print()

    print(f"Experiment complete.")
    print(f"View: {PHOENIX_ENDPOINT}/experiments/{experiment_id}")


if __name__ == "__main__":
    main()
