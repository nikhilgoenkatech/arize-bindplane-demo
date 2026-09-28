#!/usr/bin/env python3
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
#
# Upload golden examples from VCR cassettes to Phoenix as a labeled dataset.
# Uses Phoenix REST API directly — no arize-phoenix SDK required.
#
# Usage:
#   pip install requests pyyaml
#   # port-forward first: bash arize/port-forward.sh
#   python3 arize/arize-dataset.py

import json
import os
import sys
from pathlib import Path

import requests
import yaml

CASSETTE_DIR = Path(__file__).parent.parent / "src/agent/fixtures/vcr_cassettes"
DATASET_NAME = "astronomy-shop-golden"
PHOENIX_ENDPOINT = os.getenv("ARIZE_ENDPOINT", "http://localhost:6006")


def extract_expected_facts(tool_name: str, tool_output_str: str) -> list[str]:
    try:
        data = json.loads(tool_output_str)
    except (json.JSONDecodeError, TypeError):
        return []

    facts: list[str] = []
    if tool_name == "list_products" and isinstance(data, list):
        facts = [p["name"] for p in data if p.get("name")]
    elif tool_name == "get_supported_currencies" and isinstance(data, list):
        major = {"USD", "EUR", "GBP", "JPY", "CAD", "AUD"}
        facts = [c for c in data if c in major]
    elif tool_name == "get_ads" and isinstance(data, list):
        for ad in data:
            if text := ad.get("text"):
                facts.append(text)
            if url := ad.get("redirectUrl"):
                product_id = url.rstrip("/").split("/")[-1]
                if product_id:
                    facts.append(product_id)
    return facts


def _parse_json_body(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_response_string(interaction: dict) -> dict:
    try:
        body = interaction.get("response", {}).get("body", {})
        raw = body.get("string", "") if isinstance(body, dict) else ""
        return _parse_json_body(raw)
    except (AttributeError, TypeError):
        return {}


def parse_cassette(path: Path) -> list[dict]:
    with open(path) as f:
        cassette = yaml.safe_load(f)

    interactions = cassette.get("interactions", [])
    examples = []
    i = 0

    while i < len(interactions):
        try:
            req_body = _parse_json_body(interactions[i].get("request", {}).get("body", ""))
            messages = req_body.get("messages", [])

            non_system = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
            if len(non_system) != 1 or non_system[0].get("role") != "user":
                i += 1
                continue

            user_input = non_system[0].get("content", "")
            resp_body = _parse_response_string(interactions[i])
            choices = resp_body.get("choices", [])
            if not choices or not isinstance(choices, list):
                i += 1
                continue

            llm_choice = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            if not llm_choice.get("tool_calls"):
                i += 1
                continue

            tool_calls = llm_choice["tool_calls"]
            if not tool_calls or not isinstance(tool_calls, list):
                i += 1
                continue

            fn = tool_calls[0].get("function", {}) if isinstance(tool_calls[0], dict) else {}
            tool_name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                tool_args = json.loads(raw_args) if raw_args not in ("{}", "", None) else {}
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            if i + 1 >= len(interactions):
                i += 1
                continue

            next_req = _parse_json_body(interactions[i + 1].get("request", {}).get("body", ""))
            tool_output = next(
                (m.get("content", "") for m in next_req.get("messages", [])
                 if isinstance(m, dict) and m.get("role") == "tool"),
                "",
            )

            final_resp = _parse_response_string(interactions[i + 1])
            final_choices = final_resp.get("choices", [])
            if not final_choices or not isinstance(final_choices, list):
                i += 2
                continue

            final_msg = final_choices[0].get("message", {}) if isinstance(final_choices[0], dict) else {}
            final_answer = final_msg.get("content", "")

            if final_answer and tool_name and user_input:
                examples.append({
                    "input": user_input,
                    "output": final_answer,
                    "expected_tool": tool_name,
                    "expected_tool_args": json.dumps(tool_args),
                    "tool_output": tool_output,
                    "expected_facts": json.dumps(extract_expected_facts(tool_name, tool_output)),
                    "source_model": req_body.get("model", "unknown"),
                })

            i += 2

        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            print(f"  [warn] skipping interaction {i} in {path.name}: {exc}")
            i += 1

    return examples


def create_or_get_dataset(name: str) -> str:
    resp = requests.get(f"{PHOENIX_ENDPOINT}/v1/datasets", timeout=10)
    resp.raise_for_status()
    for ds in resp.json().get("data", []):
        if ds["name"] == name:
            print(f"Dataset '{name}' already exists (id={ds['id']}), will append examples.")
            return ds["id"]

    resp = requests.post(
        f"{PHOENIX_ENDPOINT}/v1/datasets",
        json={"name": name, "description": "Golden examples from VCR cassettes — engineer-defined contracts"},
        timeout=10,
    )
    resp.raise_for_status()
    dataset_id = resp.json()["data"]["id"]
    print(f"Created dataset '{name}' (id={dataset_id})")
    return dataset_id


def upload_examples(dataset_id: str, examples: list[dict]) -> None:
    payload = {
        "examples": [
            {
                "input": {"message": ex["input"]},
                "output": {"response": ex["output"]},
                "metadata": {
                    "expected_tool": ex["expected_tool"],
                    "expected_tool_args": ex["expected_tool_args"],
                    "tool_output": ex["tool_output"],
                    "expected_facts": ex["expected_facts"],
                    "source_model": ex["source_model"],
                },
            }
            for ex in examples
        ]
    }
    resp = requests.post(
        f"{PHOENIX_ENDPOINT}/v1/datasets/{dataset_id}/examples",
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Uploaded {len(examples)} examples.")


def main():
    print(f"Phoenix endpoint: {PHOENIX_ENDPOINT}")

    # Verify Phoenix is reachable
    try:
        requests.get(f"{PHOENIX_ENDPOINT}/healthz", timeout=5)
    except Exception:
        print("ERROR: Cannot reach Phoenix. Run: bash arize/port-forward.sh")
        sys.exit(1)

    all_examples: list[dict] = []
    for cassette_file in sorted(CASSETTE_DIR.glob("*.yaml")):
        print(f"Parsing {cassette_file.name}...")
        found = parse_cassette(cassette_file)
        print(f"  {len(found)} examples")
        all_examples.extend(found)

    # Deduplicate by input
    seen: set[str] = set()
    unique = [ex for ex in all_examples if not (ex["input"] in seen or seen.add(ex["input"]))]

    print(f"\nGolden contract ({len(unique)} examples):")
    for ex in unique:
        facts = json.loads(ex["expected_facts"])
        print(f"  [{ex['expected_tool']}({ex['expected_tool_args']})] {ex['input'][:50]}")
        print(f"    facts: {facts[:2]}{'...' if len(facts) > 2 else ''}")

    dataset_id = create_or_get_dataset(DATASET_NAME)
    upload_examples(dataset_id, unique)

    print(f"\nDone. View at: {PHOENIX_ENDPOINT}/datasets")


if __name__ == "__main__":
    main()
