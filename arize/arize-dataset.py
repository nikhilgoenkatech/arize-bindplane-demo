#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
#
# Upload golden examples from VCR cassettes to Arize AX as a labeled dataset.
#
# Each row captures the full engineer-defined contract for a user prompt:
#   - expected_tool        : which tool should be called
#   - expected_tool_args   : exact JSON arguments the tool should receive
#   - tool_output          : what the tool actually returned (ground truth from API)
#   - expected_facts       : key factual strings the response MUST contain
#   - output               : the golden final answer from the cassette
#
# The experiment then validates live agent behaviour against this contract.
#
# Usage:
#   pip install -r arize/requirements.txt
#   export ARIZE_API_KEY=<from Arize AX → Settings → API Keys>
#   export ARIZE_ENDPOINT=https://app.arize.com   # default
#   python arize/arize-dataset.py

import json
import os
import sys
from pathlib import Path

import pandas as pd
import yaml
import phoenix as px

CASSETTE_DIR = Path(__file__).parent.parent / "src/agent/fixtures/vcr_cassettes"
DATASET_NAME = "astronomy-shop-golden"
# Self-hosted Phoenix (already running in cluster): port-forward svc/phoenix 6006:6006 -n phoenix
# Arize cloud: set to https://app.arize.com and also set ARIZE_API_KEY
ARIZE_ENDPOINT = os.getenv("ARIZE_ENDPOINT", "http://localhost:6006")


def extract_expected_facts(tool_name: str, tool_output_str: str) -> list[str]:
    """
    Derive key factual strings the agent's response must contain.
    These are extracted from the actual tool output — not invented.
    """
    try:
        data = json.loads(tool_output_str)
    except (json.JSONDecodeError, TypeError):
        return []

    facts: list[str] = []

    if tool_name == "list_products" and isinstance(data, list):
        # Response must name every product returned by the API
        for product in data:
            if name := product.get("name"):
                facts.append(name)

    elif tool_name == "get_supported_currencies" and isinstance(data, list):
        # Response must include the major currencies at minimum
        major = {"USD", "EUR", "GBP", "JPY", "CAD", "AUD"}
        facts = [c for c in data if c in major]

    elif tool_name == "get_ads" and isinstance(data, list):
        # Response must surface every promotion text and linked product ID
        for ad in data:
            if text := ad.get("text"):
                facts.append(text)
            if url := ad.get("redirectUrl"):
                # extract product ID from /product/<id>
                product_id = url.rstrip("/").split("/")[-1]
                if product_id:
                    facts.append(product_id)

    return facts


def parse_cassette(path: Path) -> list[dict]:
    with open(path) as f:
        cassette = yaml.safe_load(f)

    interactions = cassette.get("interactions", [])
    examples = []
    i = 0

    while i < len(interactions):
        req_body = json.loads(interactions[i]["request"]["body"])
        messages = req_body["messages"]

        # New conversation: only [system, user] — no prior assistant/tool turns
        non_system = [m for m in messages if m["role"] != "system"]
        if len(non_system) != 1 or non_system[0]["role"] != "user":
            i += 1
            continue

        user_input = non_system[0]["content"]

        resp_body = json.loads(interactions[i]["response"]["body"]["string"])
        llm_choice = resp_body["choices"][0]["message"]

        if not llm_choice.get("tool_calls"):
            i += 1
            continue

        tool_call = llm_choice["tool_calls"][0]["function"]
        tool_name = tool_call["name"]
        raw_args = tool_call.get("arguments", "{}")
        tool_args = json.loads(raw_args) if raw_args not in ("{}", "") else {}

        if i + 1 >= len(interactions):
            i += 1
            continue

        next_req = json.loads(interactions[i + 1]["request"]["body"])
        tool_output = next(
            (m["content"] for m in next_req["messages"] if m["role"] == "tool"), ""
        )

        final_resp = json.loads(interactions[i + 1]["response"]["body"]["string"])
        final_answer = final_resp["choices"][0]["message"].get("content", "")

        if final_answer:
            expected_facts = extract_expected_facts(tool_name, tool_output)
            examples.append({
                "input": user_input,
                "output": final_answer,
                "expected_tool": tool_name,
                "expected_tool_args": json.dumps(tool_args),
                "tool_output": tool_output,
                "expected_facts": json.dumps(expected_facts),
                "source_model": req_body.get("model", "unknown"),
            })

        i += 2

    return examples


def main():
    api_key = os.environ.get("ARIZE_API_KEY")  # optional for self-hosted Phoenix
    client = px.Client(endpoint=ARIZE_ENDPOINT, **( {"api_key": api_key} if api_key else {}))

    all_examples: list[dict] = []
    for cassette_file in sorted(CASSETTE_DIR.glob("*.yaml")):
        print(f"Parsing {cassette_file.name}...")
        examples = parse_cassette(cassette_file)
        print(f"  {len(examples)} examples found")
        all_examples.extend(examples)

    # Keep first occurrence of each unique user prompt
    seen: set[str] = set()
    unique = []
    for ex in all_examples:
        if ex["input"] not in seen:
            seen.add(ex["input"])
            unique.append(ex)

    df = pd.DataFrame(unique)

    print(f"\n{'='*60}")
    print(f"Golden dataset: {len(df)} examples\n")
    for _, row in df.iterrows():
        print(f"  INPUT : {row['input']}")
        print(f"  TOOL  : {row['expected_tool']}({row['expected_tool_args']})")
        facts = json.loads(row["expected_facts"])
        print(f"  FACTS : {facts[:3]}{'...' if len(facts) > 3 else ''}")
        print()
    print("="*60)

    print(f"\nUploading to Arize AX as '{DATASET_NAME}'...")
    dataset = client.upload_dataset(
        dataset_name=DATASET_NAME,
        dataframe=df,
        input_keys=["input"],
        output_keys=["output"],
        metadata_keys=[
            "expected_tool",
            "expected_tool_args",
            "tool_output",
            "expected_facts",
            "source_model",
        ],
    )
    print(f"Done. Dataset '{dataset.name}' uploaded ({len(df)} rows).")
    print(f"View: {ARIZE_ENDPOINT}/projects/astronomy-shop-agent/datasets")


if __name__ == "__main__":
    main()
