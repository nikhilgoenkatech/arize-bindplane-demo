# Synthetic payments on the existing cluster

Install the five payments services as a separate Helm release in the same
namespace as the existing Astronomy Shop. This repository supplies the chart
and a preset for your existing collector. The application source is in
[self-service-/payments-demo at commit 2b450ac](https://github.com/nikhilgoenkatech/self-service-/tree/2b450ac/payments-demo).

The original `k8s-values-bindplane.yaml` belongs to
`open-telemetry/opentelemetry-demo`, release `otel-demo`, namespace
`llm-obs-demo`. Its chart schema restricts component names to the shop services.
The new `charts/payments-demo` chart owns only the five payments Deployments,
five ClusterIP Services, and one ConfigMap. It has no chart dependencies.
The shop's existing `payment` service and its Jenkins pipeline remain independent.

## 1. Build and publish the image

Build the already verified application from the source repository. Run these
commands in a parent directory where `self-service-` is not already checked out;
otherwise use your existing checkout at the indicated commit.

```sh
git clone --branch feat/synthetic-payments-demo https://github.com/nikhilgoenkatech/self-service-.git
cd self-service-
git checkout 2b450ac
docker build -t YOUR_REGISTRY/payments-demo:v1 ./payments-demo
docker push YOUR_REGISTRY/payments-demo:v1
```

Replace `YOUR_REGISTRY` with a registry your cluster can pull from. Build for
your worker-node architecture. The image is not published by creating this
branch or running Helm. One image runs all five roles.

## 2. Get the Helm changes

In your existing `arize-bindplane-demo` checkout:

```sh
git fetch origin
git switch --track origin/feat/payments-demo-helm
kubectl config current-context
helm list -n llm-obs-demo
kubectl -n llm-obs-demo get svc otel-collector
```

Use `git switch feat/payments-demo-helm` if the branch already exists locally.
The commands below use the release and namespace documented in the original
values file. Change the namespace and `otel.endpoint` if your deployment differs.

## 3. Install or upgrade the payments release

Create a local `payments-image.local.yaml` with your image:

```yaml
image:
  repository: YOUR_REGISTRY/payments-demo
  tag: v1
```

For a private registry, also set an existing image-pull Secret in the payments
namespace:

```yaml
imagePullSecrets:
  - name: registry-credentials
```

Render and inspect the new release, then install it:

```sh
helm lint ./charts/payments-demo -f k8s-values-payments.yaml -f payments-image.local.yaml
helm template payments-demo ./charts/payments-demo -n llm-obs-demo -f k8s-values-payments.yaml -f payments-image.local.yaml
helm upgrade --install payments-demo ./charts/payments-demo -n llm-obs-demo -f k8s-values-payments.yaml -f payments-image.local.yaml --wait --timeout 5m
kubectl -n llm-obs-demo get pods -l app.kubernetes.io/instance=payments-demo
```

The existing `otel-demo` release does not need an upgrade. Use
`k8s-values-payments.yaml` with the local payments chart only, and continue
using `k8s-values-bindplane.yaml` with the upstream shop chart. Do not apply the
raw manifests from the application repository for this Helm deployment.

On later application changes, build and push a new tag, update
`payments-image.local.yaml`, and rerun the same `helm upgrade --install`
command. Keep custom configuration in the same values files and pass them on
every upgrade. Generator or collector configuration changes automatically
roll the pods through a ConfigMap checksum.

## 4. Generate a payment

```sh
kubectl -n llm-obs-demo port-forward svc/payments-demo-payment-generator 8080:8080
```

Keep that terminal open. In another terminal, use PowerShell:

```powershell
Invoke-RestMethod http://localhost:8080/generate -Method Post -ContentType 'application/json' -Body '{"paymentType":"PAYID","scenario":"NORMAL"}'
Invoke-RestMethod http://localhost:8080/generate -Method Post -ContentType 'application/json' -Body '{"paymentType":"PAYID","scenario":"SLOW_SETTLEMENT"}'
Invoke-RestMethod http://localhost:8080/generate -Method Post -ContentType 'application/json' -Body '{"paymentType":"INTERNATIONAL_TRANSFER","scenario":"FAILED_PAYMENT"}'
```

Or Bash:

```sh
curl -X POST http://localhost:8080/generate -H 'Content-Type: application/json' -d '{"paymentType":"PAYID","scenario":"NORMAL"}'
```

Copy the returned `transactionId` and find it across all five services:

```sh
kubectl -n llm-obs-demo logs -l app.kubernetes.io/instance=payments-demo --all-containers=true --prefix=true --tail=200
```

Health endpoints are available at `/health`. Full scenarios, business fields,
sample logs, and DQL queries are documented in the
[application README](https://github.com/nikhilgoenkatech/self-service-/blob/2b450ac/payments-demo/README.md).

## Telemetry and configuration

The preset sends OTLP HTTP traces to:

```text
payments services -> otel-collector.llm-obs-demo:4318 -> existing exporters
```

Your existing collector configuration fans out all traces to BindPlane
(and then Dynatrace), Arize, and Phoenix. Payment traces follow that same route.
To send payments elsewhere, change `otel.endpoint` to an existing OTLP HTTP
collector. The BindPlane destination documented in the original values file
uses gRPC port 4317; do not use that gRPC address as an HTTP exporter endpoint.

Business JSON logs are written to container stdout. An OTLP receiver alone
does not read those logs: enable Kubernetes container log collection through
your existing Dynatrace/BindPlane installation. This chart installs no log
agent, collector, database, or credentials.

| Value | Default | Purpose |
| --- | --- | --- |
| `image.repository` / `image.tag` | `payments-demo` / `local` | Set to your published image |
| `imagePullSecrets` | `[]` | Optional registry Secret references |
| `replicaCount` | `1` | Replicas per service; traffic rate applies per generator replica |
| `generator.transactionsPerMinute` | `10` | 0 disables automatic traffic; maximum 600 |
| `generator.paymentTypeWeights` | `40,25,25,10` | PayID, BPAY, internal, international |
| `generator.scenarioWeights` | `95,3,2` | Normal, slow settlement, failed payment |
| `otel.endpoint` | Empty in chart; existing collector in preset | OTLP HTTP base URL |
| `otel.headersSecretName` / `otel.headersSecretKey` | Empty / `headers` | Optional Secret field containing exporter headers |
| `resources` | 25m CPU / 64Mi requests per pod | Adjustable requests and limits |

Service names are prefixed by the Helm release name. All resources belong to
the payments release, so later shop Jenkins deployments do not remove them.
No change to the existing Jenkins job is needed.

## Rollback or remove

```sh
helm history payments-demo -n llm-obs-demo
helm rollback payments-demo PREVIOUS_REVISION -n llm-obs-demo --wait --timeout 5m
helm uninstall payments-demo -n llm-obs-demo
```

Run rollback or uninstall only when wanted. These commands target the payments
release; the shop and existing collector remain running.

## Local chart verification

```sh
python -m pip install -r test/payments/requirements.txt
python -m unittest discover -s test/payments -v
```

Tests require Helm on PATH (or `HELM_BIN`) and Python with PyYAML. They render the
actual chart and check all five roles, service routing, release isolation, image
overrides, probes, ConfigMap rollout checksums, Secret references, and invalid
configuration rejection. Cluster deployment and telemetry delivery still need
verification in your environment.
