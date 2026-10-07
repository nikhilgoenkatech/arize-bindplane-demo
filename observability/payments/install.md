# Installation and tenant verification

The files are ready for tenant setup, but have not been applied to Dynatrace. Keep the current payment image, Helm release and ingestion path. No image build or Helm upgrade is needed for these dashboard/ETL assets.

## 1. Permissions and ingestion prerequisites

Use the current Grail-based Dashboards app. The operator needs access to logs, business events and their buckets (`storage:logs:read`, `storage:bizevents:read`, `storage:buckets:read`); actual-span investigation also needs `storage:spans:read` and its bucket access. Dashboard creation needs document read/write access. Grant data access only to the required buckets/security contexts. Consult the [current default policies](https://docs.dynatrace.com/docs/manage/identity-access-management/permission-management/default-policies) rather than granting all tenant data.

OpenPipeline setup needs Settings schema/object read and object write permissions (`settings:schemas:read`, `settings:objects:read`, `settings:objects:write`) and authority over the new pipeline. Routing needs an administrator or a delegated routing policy; object write alone may not suffice. The [Settings migration reference](https://docs.dynatrace.com/docs/platform/openpipeline/migration-settings) explains current ownership/routing permissions and schema IDs.

Confirm existing log collection reads stdout from all five payment pods. The configured OTLP endpoint handles traces; it does not, by itself, prove container logs reach Grail. Reuse the existing Dynatrace/BindPlane collector, checking that it preserves JSON content, source timestamps, Kubernetes namespace and deployment names. Do not add a second collector that duplicates the same stdout. Keep credentials in the existing secret/credential store.

In Logs, inspect a fresh payment record before adding a route. It must have `k8s.namespace.name=llm-obs-demo` and one of the exact `k8s.deployment.name` values listed in [route.matcher](openpipeline/route.matcher). These values come from this repository's Helm names. Confirm these fields are available **at routing time**, using pipeline preview/ingest inspection; later Grail enrichment alone is insufficient. Do not substitute a payload-text-only route. If the collector emits different metadata, adapt the narrowly scoped matcher to the observed fields before activation. Avoid `k8s.cluster.name` as a routing prerequisite because enrichment can occur later.

The route deliberately fails closed if metadata is absent. In a tenant containing another cluster with identical namespace/deployment names, add a stable cluster or ingest-source identifier actually present before routing. Do not activate the namespace/deployment-only route until its uniqueness is confirmed.

## 2. Create the demo pipeline and preview

Open **Settings > Process and contextualize > OpenPipeline > Logs > Pipelines**. Create `Synthetic payments v1`. Existing matching routes, permission processors, redaction, retention and storage assignments must be inspected first: routing to a new pipeline must retain their required behavior. Leave all unrelated routes/pipelines unchanged.

The supported deliverable here is a precise UI configuration. [ui-configuration.json](openpipeline/ui-configuration.json) is a checklist, not an import schema. Under **Processing**, add five DQL processors in this order. Copy each `matcher` from the corresponding JSON entry and paste the entire referenced `.dql` file as its definition:

| Order/name | Definition | Purpose |
| --- | --- | --- |
| 01-mark-and-preserve | [01](openpipeline/01-mark-and-preserve.dql) | Marker, schema, content preservation, parse-attempt flag |
| 02-parse-if-needed | [02](openpipeline/02-parse-if-needed.dql) | Conditional JSON object parse |
| 03-normalize | [03](openpipeline/03-normalize.dql) | Numeric conversions, source time and identifiers |
| 04-classify-and-correlate | [04](openpipeline/04-classify-and-correlate.dql) | Stage/final/transport, outcome, rail mapping, trace/span UIDs |
| 05-quality | [05](openpipeline/05-quality.dql) | Missing, invalid, malformed and unrecognized records |

Every processor except the conditional parser uses matcher `true`. Use a real incoming record as preview input, plus one saved envelope from `fixtures/captured-logs.jsonl`. Confirm original content survives, numeric fields have the intended types, source IDs survive, final stage duration is null, and source timestamp parses. Also preview a structured record (no unnecessary parse), malformed content (flagged), processor-final failure (FAILED), and DELIVERY_ERROR (transport/UNKNOWN). Python reference fixtures demonstrate expected semantics; they are not proof the DQL preview passed.

Under **Data extraction > + > Business event**, use the `businessEvent` entry in the checklist:

- Name: `Extract authoritative payment completion`.
- Matching condition: `payment.record_kind == "final" and isNotNull(payment.transaction_id)`.
- Event type: **Static string**, `demo.payment.completed`.
- Event provider: **Static string**, `synthetic-payments-demo`.
- Field extraction: select **Fields to extract** and copy every name in `businessEvent.fieldExtraction.fields` (including demo markers, canonical business fields, quality flags and trace/span IDs).

This workflow follows [business-event extraction from logs](https://docs.dynatrace.com/docs/observe/business-observability/bo-events-capturing/bo-events-capturing-logs-and-spans). Preserve log storage: do not add Drop record or No storage assignment. Preserve existing permission/retention behavior for the payment logs, and verify the resulting business events are retained and accessible under your existing business-event configuration.

Save the pipeline. Under Logs **Dynamic routing**, add the named demo route with the exact `routeMatcher`, targeting this pipeline. Position it ahead of any catch-all route that would consume these records; preserve the relative order and content of unrelated routes. Verify a payment matches and an `otel-demo` record does not. Enable the demo route only after preview and storage/access checks pass.

Only newly ingested records receive the transformation. Existing Grail records are not retroactively changed. Generate fresh payments after activation.

Current OpenPipeline configuration can be automated through Settings API schemas such as `builtin:openpipeline.logs.pipelines` and `builtin:openpipeline.logs.routing`. No authenticated tenant schema/export was available here, so no guessed tenant-wide payload is supplied. For later automation, obtain the target tenant's current schema and exported objects, create the new pipeline, and merge only its route while retaining existing routing/ownership configuration. The UI path above is the verified deployment format for this package.

## 3. Generate observed examples

From the repository on CloudShell, keep this running in one terminal:

```bash
kubectl -n llm-obs-demo port-forward svc/payments-demo-payment-generator 8080:8080 --address 127.0.0.1
```

In another terminal on the **same CloudShell host**:

```bash
python3 scripts/demo-payments-dashboard.py --url http://127.0.0.1:8080
```

The driver preflights `/health`, then creates four normal payments (one per rail), one slow success, and bounded FAILED_PAYMENT attempts until both failure locations are observed. The generator chooses failure location randomly; there is no unsupported force-location field. Default maximum is 20 failure attempts. A nonzero exit or `coverageComplete: false` means coverage is incomplete; inspect the saved manifest. All attempts contribute traffic, including repeated failure locations. UNKNOWN requests are never automatically retried.

The output file contains actual transaction IDs under `examples`. There is no app run ID; use these IDs and the manifest start/end times to isolate the run. Background traffic continues at its existing configured rate. Do not expect tenant totals to equal the local fixture totals or the seven required examples.

For volume demonstrations, the existing CLI remains available:

```bash
python3 scripts/trigger-payments.py --count 12
python3 scripts/trigger-payments.py --count 50 --mix realistic --mode paced --rate 30
```

If the local health check fails, restore port forwarding before sending payments. Use IPv4 `127.0.0.1`; `localhost` previously produced connection errors on CloudShell. Do not interpret client UNKNOWN as confirmed business failure.

## 4. Import and check the dashboard

Open **Dashboards**, then **Upload file**, choosing [payments-dashboard.json](payments-dashboard.json). Alternatively, create a dashboard, open **Edit JSON**, paste the raw document and save. Set the title to **Payments — Activity, Outcomes & Journey**. Do not upload the `.document.json` envelope into the raw dashboard editor. The [documented version-21 structure](https://docs.dynatrace.com/docs/analyze-explore-automate/dashboards-and-notebooks/document-api/document-structure-dashboards) is the basis of the generated files.

Native Save/import validation is a required gate. Then run every data tile, check DQL errors and rendering, and save any tenant-required visualization settings. Local subset validation cannot substitute for this. The seven variables should appear; CSV defaults are their first input (`ALL`, or `3` for SlowSeconds), and TransactionId starts blank. The overview defaults to two hours.

If event extraction is unavailable, upload `payments-final-log-fallback.json` and identify it as fallback mode. It still requires normalization. Business-event example/reconciliation panels deliberately remain visible and can show missing extraction; they must not be presented as live event evidence.

To investigate: copy an ID from **Recent slow and failed payments** into **TransactionId**. Set other filters to ALL if they conflict. Scroll to the selected journey; its detail window is 24 hours regardless of overview timeframe. No table-click binding is assumed. Clear TransactionId afterwards to restore aggregate counts.

The actual-spans tile joins the selected logs' trace IDs to stored spans. If it shows data, copy the trace ID, open **Distributed Tracing**, choose the same 24-hour timeframe, and use the trace-ID search/filter to find and open that trace. Verify the payment services appear. If it is empty, show the log journey and state that trace ingestion/access has not been demonstrated. Do not manufacture a trace URL.

## 5. Acceptance and rollback

Allow ingestion to settle beyond the default two-minute reconciliation lag; extend the lag in both quality tiles together if necessary. Reset filters, clear TransactionId, then compare unique final-log payments with unique extracted-event payments. Missing/orphan counts should be zero for the settled, fully retained sample; duplicate counts expose ingestion replay. This measures extraction coverage, not payment success.

For each manifest ID, confirm six ordered records for normal/slow successes, five for processor failure, the correct terminating service/error, and one final event. Confirm the slow stage is settlement while the final remains SUCCESS. Compare amount once on a normal ID and verify intermediate SUCCESS cannot inflate final successes on a failed ID. Exercise every filter, a nonexistent transaction, empty timeframe and zero-denominator rate. Check malformed-record flags in preview rather than polluting live demo traffic.

Record the dashboard document ID, tenant version, pipeline/route IDs, preview outcomes, tile errors (if any), settled counts and trace-navigation result in a local deployment record. Do not store credentials. See [validation status](validation/README.md) for outstanding gates.

Rollback: disable/remove only the newly added demo route, restore its previous payment routing behavior if changed, and remove the demo dashboard/pipeline after traffic no longer uses it. Leave unrelated pipelines and the existing Helm releases intact. Stored logs/events remain governed by their original retention policy.
