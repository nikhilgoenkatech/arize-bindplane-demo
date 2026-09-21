# BindPlane Setup — Kubernetes

BindPlane is an OpenTelemetry-native pipeline manager by observIQ.
Dynatrace is a supported destination. Two components to deploy:

  1. BindPlane Server  — management/control plane (UI + API)
  2. BindPlane Gateway Collector — data plane (receives OTLP, routes to Dynatrace)

---

## Prerequisites

- Kubernetes cluster with kubectl configured
- Helm 3+
- A Dynatrace environment ID (e.g. `abc12345`)
- A Dynatrace API token with scopes:
    - `metrics.ingest`
    - `logs.ingest`
    - `openTelemetryTrace.ingest`
- A BindPlane license key (obtain from bindplane.com)
- PostgreSQL available in-cluster (see note below)

> **PostgreSQL note**: BindPlane requires Postgres as its backend. Quickest option for a demo:
> deploy the Bitnami Postgres chart alongside it, or use an existing cluster DB.

---

## Step 1 — Deploy BindPlane Server

```bash
# Create the BindPlane secret (replace placeholder values)
kubectl create namespace bindplane

kubectl -n bindplane create secret generic bindplane \
  --from-literal=username=admin \
  --from-literal=password=<your-password> \
  --from-literal=sessions_secret=$(uuidgen | tr '[:upper:]' '[:lower:]') \
  --from-literal=license=<your-license-key>

# Add Helm repo and install
helm repo add bindplane https://observiq.github.io/bindplane-op-helm
helm repo update

helm upgrade --install bindplane bindplane/bindplane \
  --namespace bindplane \
  --set config.server.secretName=bindplane \
  --set postgresql.enabled=true \
  --set postgresql.auth.postgresPassword=<pg-password>
```

After install, the BindPlane server is reachable inside the cluster at:

```
Service: bindplane.bindplane.svc.cluster.local
Port:    3001
OpAMP:   ws://bindplane.bindplane.svc.cluster.local:3001/v1/opamp
```

Access the UI via port-forward:

```bash
kubectl -n bindplane port-forward svc/bindplane 3001
# Open http://localhost:3001  (login with admin / <your-password>)
```

---

## Step 2 — Create a Dynatrace Destination in BindPlane UI

1. Open http://localhost:3001 → **Destinations** → **Add Destination**
2. Search for **Dynatrace**
3. Fill in:
   - **Deployment Type**: SaaS
   - **Environment ID**: `<your-env-id>` (e.g. `abc12345`)
   - **API Token**: `<your-dynatrace-api-token>`
   - **Telemetry**: Metrics + Logs + Traces
4. Save

---

## Step 3 — Create a Pipeline Configuration

1. **Configurations** → **New Configuration**
2. **Source**: Add → search **OTLP** → select "OTLP" receiver (gRPC port 4317)
3. **Destination**: select the Dynatrace destination from Step 2
4. Save as e.g. `otel-demo-pipeline`

---

## Step 4 — Install BindPlane Gateway Collector

1. In BindPlane UI → **Collectors** → **Install Collectors**
2. Platform: **Kubernetes Gateway**
3. Configuration: `otel-demo-pipeline`
4. Copy the generated YAML manifest and save as `bindplane-gateway.yaml`

   The manifest creates these resources in namespace `bindplane-agent`:
   ```
   Service:    bindplane-gateway-agent        (ClusterIP, port 4317)
   Deployment: bindplane-gateway-agent
   HPA:        bindplane-gateway-agent
   ```

   Verify the `OPAMP_ENDPOINT` in the manifest points to your server:
   ```yaml
   - name: OPAMP_ENDPOINT
     value: "ws://bindplane.bindplane.svc.cluster.local:3001/v1/opamp"
   ```

5. Apply it:
   ```bash
   kubectl apply -f bindplane-gateway.yaml
   ```

6. Verify the collector is running and visible in BindPlane UI under **Collectors**:
   ```bash
   kubectl -n bindplane-agent get pods
   ```

---

## Step 5 — Deploy OTel Demo

Now deploy the OTel Demo pointing its collector at the BindPlane gateway:

```bash
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo update

kubectl create secret generic llm-secret \
  --from-literal=api-key=<your-openai-api-key>

helm upgrade --install otel-demo open-telemetry/opentelemetry-demo \
  -f k8s-values.yaml
```

---

## Architecture Summary

```
OTel Demo services (11+ microservices + chatbot)
        │  OTLP gRPC :4317
        ▼
OTel Demo built-in collector
        │  OTLP gRPC :4317
        ▼
BindPlane Gateway Collector          (bindplane-gateway-agent.bindplane-agent:4317)
│  managed by BindPlane Server via OpAMP
│  OTLP HTTP → Dynatrace
        ▼
Dynatrace  (https://<env-id>.live.dynatrace.com/api/v2/otlp)
```

> **Note**: Dynatrace accepts OTLP over **HTTP only** (not gRPC).
> BindPlane handles this translation internally when you configure the Dynatrace destination.

---

## Swapping the Chatbot Model (Demo: Bad → Good)

No code changes needed — just a Helm upgrade:

```bash
# "Bad" model
helm upgrade otel-demo open-telemetry/opentelemetry-demo \
  -f k8s-values.yaml \
  --set "components.chatbot.env[0].value=gpt-3.5-turbo"

# "Good" model
helm upgrade otel-demo open-telemetry/opentelemetry-demo \
  -f k8s-values.yaml \
  --set "components.chatbot.env[0].value=gpt-4o"
```
