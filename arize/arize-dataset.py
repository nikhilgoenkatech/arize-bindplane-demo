#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
#
# Upload golden examples from VCR cassettes to Arize AX as a labeled dataset.
# Each cassette interaction (user prompt → tool call → tool result → final answer)
# becomes one row in the dataset, which serves as the ground truth for experiments.
#
# Usage:
#   pip install arize-phoenix pandas pyyaml
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
ARIZE_ENDPOINT = os.getenv("ARIZE_ENDPOINT", "https://app.arize.com")


def parse_cassette(path: Path) -> list[dict]:
    with open(path) as f:
        cassette = yaml.safe_load(f)

    interactions = cassette.get("interactions", [])
    examples = []
    i = 0

    while i < len(interactions):
        req_body = json.loads(interactions[i]["request"]["body"])
        messages = req_body["messages"]

        # A new conversation begins with only [system, user] — no prior assistant/tool turns
        non_system = [m for m in messages if m["role"] != "system"]
        if len(non_system) != 1 or non_system[0]["role"] != "user":
            i += 1
            continue

        user_input = non_system[0]["content"]

        resp_body = json.loads(interactions[i]["response"]["body"]["string"])
        llm_choice = resp_body["choices"][0]["message"]

        # Expect a tool call as the first LLM action
        if not llm_choice.get("tool_calls"):
            i += 1
            continue

        tool_call = llm_choice["tool_calls"][0]["function"]
        tool_name = tool_call["name"]
        raw_args = tool_call.get("arguments", "{}")
        tool_args = json.loads(raw_args) if raw_args not in ("{}", "") else {}

        # Next interaction: tool result fed back → final LLM answer
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
            examples.append({
                "input": user_input,
                "output": final_answer,
                "tool_called": tool_name,
                "tool_args": json.dumps(tool_args),
                "tool_output": tool_output,
                "expected_tool": tool_name,
                "source_model": req_body.get("model", "unknown"),
            })

        i += 2

    return examples


def main():
    api_key = os.environ.get("ARIZE_API_KEY")
    if not api_key:
        print("ERROR: ARIZE_API_KEY not set")
        sys.exit(1)

    client = px.Client(endpoint=ARIZE_ENDPOINT, api_key=api_key)

    all_examples: list[dict] = []
    for cassette_file in sorted(CASSETTE_DIR.glob("*.yaml")):
        print(f"Parsing {cassette_file.name}...")
        examples = parse_cassette(cassette_file)
        print(f"  {len(examples)} examples found")
        all_examples.extend(examples)

    # Keep first occurrence of each unique user prompt across cassettes
    seen: set[str] = set()
    unique = []
    for ex in all_examples:
        if ex["input"] not in seen:
            seen.add(ex["input"])
            unique.append(ex)

    df = pd.DataFrame(unique)
    print(f"\nDataset: {len(df)} unique golden examples")
    print(df[["input", "tool_called"]].to_string(index=False))

    print(f"\nUploading to Arize AX as '{DATASET_NAME}'...")
    dataset = client.upload_dataset(
        dataset_name=DATASET_NAME,
        dataframe=df,
        input_keys=["input"],
        output_keys=["output"],
        metadata_keys=["tool_called", "tool_args", "tool_output", "expected_tool", "source_model"],
    )
    print(f"Done. Dataset '{dataset.name}' uploaded ({len(df)} rows).")
    print(f"View: {ARIZE_ENDPOINT}/projects/astronomy-shop-agent/datasets")


if __name__ == "__main__":
    main()
