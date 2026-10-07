#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "payments-demo"
PRESET = ROOT / "k8s-values-payments.yaml"
HELM = os.environ.get("HELM_BIN", "helm")
ROLES = {
    "payment-generator": ("generator", "payment-channel"),
    "payment-channel": ("channel", "payment-hub"),
    "payment-hub": ("hub", "payment-processor"),
    "payment-processor": ("processor", "payment-settlement"),
    "payment-settlement": ("settlement", None),
}


def render(release="payments-demo", *overrides):
    result = subprocess.run(
        [HELM, "template", release, str(CHART), "-n", "llm-obs-demo",
         "-f", str(PRESET), *overrides],
        text=True, capture_output=True, check=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


class PaymentsChartTest(unittest.TestCase):
    def test_lint(self):
        subprocess.run(
            [HELM, "lint", str(CHART), "-f", str(PRESET), "--strict"],
            check=True, capture_output=True, text=True,
        )

    def test_five_roles_and_http_routes(self):
        docs = render()
        deployments = [doc for doc in docs if doc["kind"] == "Deployment"]
        services = {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "Service"}
        configs = [doc for doc in docs if doc["kind"] == "ConfigMap"]
        self.assertEqual(len(docs), 11)
        self.assertEqual(len(deployments), 5)
        self.assertEqual(len(services), 5)
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["data"]["TRANSACTIONS_PER_MINUTE"], "10")
        self.assertEqual(
            configs[0]["data"]["OTEL_EXPORTER_OTLP_ENDPOINT"],
            "http://otel-collector.llm-obs-demo.svc.cluster.local:4318",
        )
        for deployment in deployments:
            name = deployment["metadata"]["name"]
            pod = deployment["spec"]["template"]
            container = pod["spec"]["containers"][0]
            component = pod["metadata"]["labels"]["app.kubernetes.io/component"]
            role, next_component = ROLES[component]
            env = {entry["name"]: entry.get("value") for entry in container["env"]}
            self.assertEqual(env["SERVICE"], role)
            self.assertEqual(env["PORT"], "8080")
            self.assertEqual(container["envFrom"][0]["configMapRef"]["name"], configs[0]["metadata"]["name"])
            if next_component:
                destination = f"payments-demo-{next_component}"
                self.assertIn(destination, services)
                self.assertEqual(env["NEXT_SERVICE_URL"], f"http://{destination}:8080")
            else:
                self.assertNotIn("NEXT_SERVICE_URL", env)
            selector = services[name]["spec"]["selector"]
            self.assertEqual(selector, deployment["spec"]["selector"]["matchLabels"])
            for key, value in selector.items():
                self.assertEqual(pod["metadata"]["labels"][key], value)
            self.assertEqual(services[name]["spec"]["type"], "ClusterIP")
            self.assertEqual(services[name]["spec"]["ports"][0]["targetPort"], "http")
            self.assertEqual(container["ports"][0]["name"], "http")
            self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/health")
            self.assertEqual(container["livenessProbe"]["httpGet"]["path"], "/health")
            self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
            self.assertTrue(pod["spec"]["securityContext"]["runAsNonRoot"])

    def test_image_and_secret_overrides_apply_to_every_service(self):
        docs = render(
            "payments-demo",
            "--set-string", "image.repository=registry.example/payments-demo",
            "--set-string", "image.tag=v2",
            "--set", "imagePullSecrets[0].name=registry-credentials",
            "--set", "otel.headersSecretName=collector-headers",
            "--set", "otel.headersSecretKey=otlp",
        )
        for doc in docs:
            if doc["kind"] != "Deployment":
                continue
            spec = doc["spec"]["template"]["spec"]
            self.assertEqual(spec["imagePullSecrets"], [{"name": "registry-credentials"}])
            container = spec["containers"][0]
            self.assertEqual(container["image"], "registry.example/payments-demo:v2")
            secret = next(e for e in container["env"] if e["name"] == "OTEL_EXPORTER_OTLP_HEADERS")
            self.assertEqual(secret["valueFrom"]["secretKeyRef"], {"name": "collector-headers", "key": "otlp"})

    def test_configuration_rolls_pods_and_zero_disables_traffic(self):
        before = render()
        after = render("payments-demo", "--set", "generator.transactionsPerMinute=0")
        before_sums = {d["spec"]["template"]["metadata"]["annotations"]["checksum/config"] for d in before if d["kind"] == "Deployment"}
        after_sums = {d["spec"]["template"]["metadata"]["annotations"]["checksum/config"] for d in after if d["kind"] == "Deployment"}
        self.assertEqual(len(before_sums), 1)
        self.assertEqual(len(after_sums), 1)
        self.assertNotEqual(before_sums, after_sums)
        self.assertEqual(next(d for d in after if d["kind"] == "ConfigMap")["data"]["TRANSACTIONS_PER_MINUTE"], "0")

    def test_releases_are_isolated(self):
        before = render("payments-a")
        after = render("payments-b")
        self.assertTrue(
            {d["metadata"]["name"] for d in before}.isdisjoint(
                {d["metadata"]["name"] for d in after}
            )
        )
        for doc in after:
            if doc["kind"] == "Deployment":
                self.assertEqual(doc["spec"]["selector"]["matchLabels"]["app.kubernetes.io/instance"], "payments-b")
                for env in doc["spec"]["template"]["spec"]["containers"][0]["env"]:
                    if env["name"] == "NEXT_SERVICE_URL":
                        self.assertTrue(env["value"].startswith("http://payments-b-"))
        for doc in render("payments-" + "x" * 44):
            self.assertLessEqual(len(doc["metadata"]["name"]), 63)

    def test_invalid_configuration_is_rejected(self):
        for value in [
            "generator.transactionsPerMinute=-1",
            "generator.transactionsPerMinute=601",
            "replicaCount=0",
            "image.repository=",
            "otel.endpoint=not-an-http-url",
            "generator.scenarioWeights=0\\,0\\,0",
            "generator.paymentTypeWeights=1\\,2",
        ]:
            with self.subTest(value=value):
                result = subprocess.run(
                    [HELM, "template", "payments-demo", str(CHART), "--set", value],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("schema", result.stderr)


if __name__ == "__main__":
    unittest.main()
