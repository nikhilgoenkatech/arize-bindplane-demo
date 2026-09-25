#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
#
# Run Arize AX experiments against the astronomy-shop-golden dataset.
#
# Three evaluators:
#   1. tool_selection  — did live agent call the right tool?  (code-based, exact match)
#   2. faithfulness    — is the response grounded in tool output, no hallucination? (LLM-as-judge)
#   3. completeness    — did the response cover all items returned by the tool? (LLM-as-judge)
#
# The task function calls the live agent (not VCR) and captures its response.
# Evaluator scores feed into Arize Signals for go/no-go production decisions.
#
# Usage:
#   pip install arize-phoenix[evals] openai requests pandas
#   export ARIZE_API_KEY=<from Arize AX → Settings → API Keys>
#   export OPENAI_API_KEY=<your-openai-key>  # used only by LLM evaluators
#   export AGENT_ENDPOINT=http://<frontend-proxy-elb>:8080
#       — or port-forward: kubectl port-forward svc/agent 8010:8010 -n llm-obs-demo
#         and set AGENT_ENDPOINT=http://localhost:8010
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
    """Send the golden input to the live agent and return its response + tool used."""
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
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "assistant" and msg.get("content"):
                output = msg["content"]
            if msg.get("tool_calls"):
                tool_called = msg["tool_calls"][0]["function"]["name"]

        return {"output": output, "tool_called": tool_called}
    except Exception as exc:
        return {"output": f"ERROR: {exc}", "tool_called": ""}


# ── Evaluator 1: tool selection (code-based) ─────────────────────────────────

def eval_tool_selection(output: dict, example: dict) -> dict:
    actual = (output or {}).get("tool_called", "")
    expected = (example.get("metadata") or {}).get("expected_tool", "")
    correct = actual == expected
    return {
        "score": 1.0 if correct else 0.0,
        "label": "correct" if correct else "wrong",
        "explanation": f"expected={expected!r}, got={actual!r}",
    }


# ── Evaluator 2: faithfulness (LLM-as-judge) ─────────────────────────────────

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
        return {"score": 0.0, "label": "hallucinated", "explanation": "no valid response to evaluate"}

    model = OpenAIModel(model="gpt-4o-mini")
    df = pd.DataFrame([{"tool_output": tool_output, "response": response}])
    result = llm_classify(
        dataframe=df,
        template=FAITHFULNESS_TEMPLATE,
        model=model,
        rails=["faithful", "hallucinated"],
    )
    label = result["label"].iloc[0]
    return {
        "score": 1.0 if label == "faithful" else 0.0,
        "label": label,
    }


# ── Evaluator 3: completeness (LLM-as-judge) ─────────────────────────────────

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

Did the assistant's response include all key items or data points from the system data,
without omitting important entries?
Answer with exactly one word: "complete" or "incomplete".
""".strip()

def eval_completeness(output: dict, example: dict) -> dict:
    tool_output = (example.get("metadata") or {}).get("tool_output", "")
    response = (output or {}).get("output", "")
    if not response or not tool_output or response.startswith("ERROR:"):
        return {"score": 0.0, "label": "incomplete", "explanation": "no valid response to evaluate"}

    model = OpenAIModel(model="gpt-4o-mini")
    df = pd.DataFrame([{"tool_output": tool_output, "response": response}])
    result = llm_classify(
        dataframe=df,
        template=COMPLETENESS_TEMPLATE,
        model=model,
        rails=["complete", "incomplete"],
    )
    label = result["label"].iloc[0]
    return {
        "score": 1.0 if label == "complete" else 0.0,
        "label": label,
    }


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

    print(f"Agent endpoint: {AGENT_ENDPOINT}")
    print(f"Arize endpoint: {ARIZE_ENDPOINT}")

    client = px.Client(endpoint=ARIZE_ENDPOINT, api_key=api_key)

    dataset = client.get_dataset(name=DATASET_NAME)
    print(f"Loaded dataset '{dataset.name}' ({len(dataset)} examples)")

    experiment = run_experiment(
        dataset=dataset,
        task=call_live_agent,
        evaluators=[eval_tool_selection, eval_faithfulness, eval_completeness],
        experiment_name=EXPERIMENT_NAME,
        project_name="astronomy-shop-agent",
        client=client,
    )

    print(f"\nExperiment '{experiment.name}' complete.")
    print(f"Results: {ARIZE_ENDPOINT}/projects/astronomy-shop-agent/experiments")


if __name__ == "__main__":
    main()
