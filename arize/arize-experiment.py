#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
#
# Run Arize AX experiments against the astronomy-shop-golden dataset.
#
# Validates live agent behaviour against the engineer-defined contract:
#
#   Evaluator              Type        What it checks
#   ─────────────────────  ──────────  ─────────────────────────────────────────
#   tool_selection         code        Did agent call the right tool?
#   tool_args_match        code        Did agent pass the correct JSON arguments?
#   response_facts         code        Does response contain all expected facts
#                                      (product names, currencies, promo text)?
#   faithfulness           LLM judge   Any hallucinated claims not in tool output?
#   completeness           LLM judge   Did response omit important items?
#
# The first three evaluators require no LLM — they assert exact structural
# correctness derived from the cassette contract.  The LLM judges catch
# subtler quality issues in the free-text response.
#
# Usage:
#   pip install -r arize/requirements.txt
#   export ARIZE_API_KEY=<from Arize AX → Settings → API Keys>
#   export OPENAI_API_KEY=<your-openai-key>
#   export AGENT_ENDPOINT=http://localhost:8010   # port-forward the agent svc
#       kubectl port-forward svc/agent 8010:8010 -n llm-obs-demo
#   export ARIZE_ENDPOINT=https://app.arize.com   # default
#   python arize/arize-experiment.py

import json
import os
import sys

import pandas as pd
import requests
import phoenix as px
from phoenix.evals import OpenAIModel, llm_classify
from phoenix.experiments import run_experiment

DATASET_NAME = "astronomy-shop-golden"
EXPERIMENT_NAME = "astronomy-shop-agent-eval"
ARIZE_ENDPOINT = os.getenv("ARIZE_ENDPOINT", "https://app.arize.com")
AGENT_ENDPOINT = os.getenv("AGENT_ENDPOINT", "http://localhost:8010")


# ── Task ─────────────────────────────────────────────────────────────────────

def call_live_agent(example: dict) -> dict:
    """Send the golden input to the live agent; return response + tool used + args."""
    url = f"{AGENT_ENDPOINT}/prompt"
    try:
        resp = requests.post(
            url,
            json={"message": example["input"], "history": []},
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

        return {
            "output": output,
            "tool_called": tool_called,
            "tool_args_actual": tool_args_actual,
        }
    except Exception as exc:
        return {"output": f"ERROR: {exc}", "tool_called": "", "tool_args_actual": {}}


# ── Evaluator 1: tool selection (code-based) ─────────────────────────────────

def eval_tool_selection(output: dict, example: dict) -> dict:
    actual = (output or {}).get("tool_called", "")
    expected = (example.get("metadata") or {}).get("expected_tool", "")
    correct = actual == expected
    return {
        "score": 1.0 if correct else 0.0,
        "label": "correct" if correct else "wrong",
        "explanation": f"expected={expected!r}  got={actual!r}",
    }


# ── Evaluator 2: tool argument match (code-based) ────────────────────────────

def eval_tool_args_match(output: dict, example: dict) -> dict:
    """
    Compare the actual tool call arguments against the engineer-defined contract.
    For tools with no arguments (list_products, get_supported_currencies) this
    passes trivially.  For get_ads it asserts category='binoculars', etc.
    """
    actual_args = (output or {}).get("tool_args_actual", {})
    expected_args_str = (example.get("metadata") or {}).get("expected_tool_args", "{}")
    try:
        expected_args = json.loads(expected_args_str)
    except (json.JSONDecodeError, TypeError):
        expected_args = {}

    if not expected_args:
        # Tool takes no arguments — trivially correct
        return {"score": 1.0, "label": "correct", "explanation": "tool takes no arguments"}

    # Check every expected key matches
    mismatches = []
    for key, expected_val in expected_args.items():
        actual_val = actual_args.get(key)
        if actual_val != expected_val:
            mismatches.append(f"{key}: expected={expected_val!r} got={actual_val!r}")

    if mismatches:
        return {
            "score": 0.0,
            "label": "wrong",
            "explanation": "; ".join(mismatches),
        }
    return {
        "score": 1.0,
        "label": "correct",
        "explanation": f"args match: {json.dumps(actual_args)}",
    }


# ── Evaluator 3: response facts (code-based) ─────────────────────────────────

def eval_response_facts(output: dict, example: dict) -> dict:
    """
    Assert that all engineer-defined expected facts appear in the response.
    Facts are derived from the actual tool output (product names, promo text,
    currency codes, product IDs) — so this validates end-to-end correctness
    without requiring an LLM judge.

    Example for binoculars prompt:
      expected_facts = ["Roof Binoculars for sale. 50% off.", "2ZYFJ3GM2N"]
      → both must appear somewhere in the agent response
    """
    response = (output or {}).get("output", "")
    facts_str = (example.get("metadata") or {}).get("expected_facts", "[]")
    try:
        expected_facts: list[str] = json.loads(facts_str)
    except (json.JSONDecodeError, TypeError):
        expected_facts = []

    if not expected_facts or not response or response.startswith("ERROR:"):
        return {"score": 0.0, "label": "missing", "explanation": "no response or no facts to check"}

    missing = [f for f in expected_facts if f.lower() not in response.lower()]
    score = 1.0 - (len(missing) / len(expected_facts))

    return {
        "score": round(score, 2),
        "label": "pass" if not missing else "fail",
        "explanation": (
            f"all {len(expected_facts)} facts present"
            if not missing
            else f"missing {len(missing)}/{len(expected_facts)}: {missing[:3]}"
        ),
    }


# ── Evaluator 4: faithfulness (LLM-as-judge) ─────────────────────────────────

FAITHFULNESS_TEMPLATE = """
You are evaluating whether an AI assistant's response is faithful to the data it retrieved.

Retrieved data (ground truth from the system):
[tool_output]
{tool_output}
[end tool_output]

Assistant response:
[response]
{response}
[end response]

Is every factual claim in the response supported by the retrieved data?
Answer with exactly one word: "faithful" or "hallucinated".
""".strip()

def eval_faithfulness(output: dict, example: dict) -> dict:
    tool_output = (example.get("metadata") or {}).get("tool_output", "")
    response = (output or {}).get("output", "")
    if not response or not tool_output or response.startswith("ERROR:"):
        return {"score": 0.0, "label": "hallucinated", "explanation": "no valid response"}

    model = OpenAIModel(model="gpt-4o-mini")
    df = pd.DataFrame([{"tool_output": tool_output, "response": response}])
    result = llm_classify(
        dataframe=df,
        template=FAITHFULNESS_TEMPLATE,
        model=model,
        rails=["faithful", "hallucinated"],
    )
    label = result["label"].iloc[0]
    return {"score": 1.0 if label == "faithful" else 0.0, "label": label}


# ── Evaluator 5: completeness (LLM-as-judge) ─────────────────────────────────

COMPLETENESS_TEMPLATE = """
You are evaluating whether an AI assistant's response covered all key information.

Data returned by the system:
[tool_output]
{tool_output}
[end tool_output]

Assistant response:
[response]
{response}
[end response]

Did the assistant include all key items or data points from the system data
without omitting important entries?
Answer with exactly one word: "complete" or "incomplete".
""".strip()

def eval_completeness(output: dict, example: dict) -> dict:
    tool_output = (example.get("metadata") or {}).get("tool_output", "")
    response = (output or {}).get("output", "")
    if not response or not tool_output or response.startswith("ERROR:"):
        return {"score": 0.0, "label": "incomplete", "explanation": "no valid response"}

    model = OpenAIModel(model="gpt-4o-mini")
    df = pd.DataFrame([{"tool_output": tool_output, "response": response}])
    result = llm_classify(
        dataframe=df,
        template=COMPLETENESS_TEMPLATE,
        model=model,
        rails=["complete", "incomplete"],
    )
    label = result["label"].iloc[0]
    return {"score": 1.0 if label == "complete" else 0.0, "label": label}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("ARIZE_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: ARIZE_API_KEY not set")
        sys.exit(1)
    if not openai_key:
        print("ERROR: OPENAI_API_KEY not set (required for faithfulness and completeness evaluators)")
        sys.exit(1)

    print(f"Agent endpoint : {AGENT_ENDPOINT}")
    print(f"Arize endpoint : {ARIZE_ENDPOINT}")

    client = px.Client(endpoint=ARIZE_ENDPOINT, api_key=api_key)

    dataset = client.get_dataset(name=DATASET_NAME)
    print(f"Loaded dataset '{dataset.name}' ({len(dataset)} examples)\n")

    print("Running experiment with 5 evaluators:")
    print("  [code] tool_selection   — right tool called?")
    print("  [code] tool_args_match  — correct JSON arguments?")
    print("  [code] response_facts   — all expected facts in response?")
    print("  [LLM]  faithfulness     — any hallucinations?")
    print("  [LLM]  completeness     — anything omitted?\n")

    experiment = run_experiment(
        dataset=dataset,
        task=call_live_agent,
        evaluators=[
            eval_tool_selection,
            eval_tool_args_match,
            eval_response_facts,
            eval_faithfulness,
            eval_completeness,
        ],
        experiment_name=EXPERIMENT_NAME,
        project_name="astronomy-shop-agent",
        client=client,
    )

    print(f"\nExperiment '{experiment.name}' complete.")
    print(f"Results: {ARIZE_ENDPOINT}/projects/astronomy-shop-agent/experiments")


if __name__ == "__main__":
    main()
