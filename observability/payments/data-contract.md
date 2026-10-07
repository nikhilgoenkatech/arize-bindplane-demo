# Payment data contract, version 1

Inspected application: the existing Python `payments-demo/common/model.py`, `common/runtime.py`, and five service implementations; actual HTTP responses/stdout are saved in [fixtures](fixtures/). The Helm chart in this repository deploys the same image to five roles. All payments are synthetic, OUTBOUND, AUD. No application `runId`, forced failure location, direct business-event exporter, or retry is implemented.

The app's common record contains the fields below. Error/fraud fields are optional. Each structured source field takes precedence over the corresponding parsed JSON field; numeric conversion is applied first. Original source fields remain, and original correlation identifiers are promoted from JSON when needed. `content` remains intact and is also copied to `payment.raw_content`.

| Source | Canonical field | Type / meaning |
| --- | --- | --- |
| transactionId | payment.transaction_id | string; PAY-prefixed unique transaction ID |
| journeyId | payment.journey_id | string; JRN-prefixed journey ID |
| paymentType | payment.type | string |
| paymentRail | payment.rail | string |
| channel | payment.channel | string |
| customerSegment | payment.customer_segment | string |
| direction | payment.direction | string; OUTBOUND |
| currency | payment.currency | string; AUD |
| amount | payment.amount | double; amount repeated across stages |
| scenario | payment.scenario | string; demo annotation |
| service | payment.service | string; logical emitting service, not pod name |
| stage | payment.stage | string |
| status | payment.status | string; PROCESSING, SUCCESS, FAILED, UNKNOWN |
| eventType | payment.event_type | string; present on authoritative final record |
| stepSequence | payment.step_sequence | long |
| durationMs | payment.stage_duration_ms | double milliseconds; cleared for final, transport and generator records |
| totalDurationMs | payment.total_duration_ms | double milliseconds; authoritative final end-to-end time |
| riskScore | payment.risk_score | long; processor/fraud context |
| fraudDecision | payment.fraud_decision | string; optional |
| errorCode | payment.error_code | string or null |
| errorMessage | payment.error_message | string or null |
| trace_id | payment.trace_id and trace.id | preserved string plus `toUid` correlation field, preferring existing trace.id |
| span_id | payment.span_id and span.id | preserved string plus `toUid` correlation field, preferring existing span.id |
| JSON timestamp, else envelope timestamp | payment.source_timestamp | normalized timestamp rendered as string for extraction; queries use toTimestamp |

The ingestion `timestamp` is preserved. Configure the existing collector to honor the JSON event timestamp; OpenPipeline is not used to overwrite the mandatory log timestamp. The parser reads JSON only when required fields are absent. Fully structured records therefore use their existing timestamp. If a raw timestamp cannot be converted, envelope time is used and invalid-values is flagged. This flag cannot identify an upstream timestamp substitution made before this pipeline.

Enrichment: `demo.name=synthetic-payments-demo`, `demo.schema_version=1` (string), `payment.record_kind`, `payment.outcome`, `payment.settlement_service`, and boolean `payment.quality.*` fields. Parsing failure means JSON parsing was attempted and produced no object. Missing-required counts recognized records lacking required canonical data. Unrecognized/runtime records stay visible but do not count as payments. These flags are observability checks, not comprehensive schema validation.

| Type | Rail | Logical settlement |
| --- | --- | --- |
| PAYID | NPP | NPP Settlement |
| BPAY | BPAY | BPAY Settlement |
| ACCOUNT_TRANSFER | INTERNAL | Internal Ledger |
| INTERNATIONAL_TRANSFER | SWIFT | SWIFT Gateway |

The normal sequence is 0 GENERATED, 1 INITIATED, 2 ROUTED, 3 PROCESSING, 4 SETTLEMENT_COMPLETED, 5 PAYMENT_COMPLETED. A processor failure has a failed PROCESSING record at 3 and final failure at 4; no settlement is expected. Settlement failure has SETTLEMENT_FAILED at 4 and final failure at 5. The full stage constants remain in the logs and dashboard.

Classification checks final first: both `eventType` and `stage` must be `PAYMENT_COMPLETED`. SUCCESS/FAILED become confirmed outcomes; any other final status becomes UNKNOWN. Otherwise, DELIVERY_ERROR or status UNKNOWN becomes transport; other named stages become stage; the remainder becomes other. Extraction requires final classification and a non-null transaction ID. Invalid/missing fields remain flagged rather than silently becoming a success or failure.

`DELIVERY_ERROR`, error `DOWNSTREAM_UNAVAILABLE`, represents an HTTP forwarding exception. It emits UNKNOWN and returns HTTP 502 without inventing a final business outcome. The downstream operation may already have completed. Multiple upstream transport errors, and a real final record, may share one transaction. The transport counter is distinct affected transactions and must not be added to completed counts. Missing final means only **No final outcome observed in selected window**.

There were no duplicate finals in the 46 captured records. The app and driver do not retry uncertain requests. Collection replay can still duplicate records: every final KPI first deduplicates by transaction ID, preferring the latest normalized source timestamp. Ties use the ordered descending fields in `build.py: FINAL_SORT`, covering outcome, service, duration, errors, correlation, amount and business dimensions. Identical business records are equivalent; ingestion time is not a winner criterion. This is a defensive deterministic policy, not an application update/version protocol. Sorting assumes the app's UTC timestamp contract. Conflicting finals warrant investigation.

Completed = SUCCESS + FAILED. Confirmed failure rate = FAILED / completed; zero denominator returns null. Successful value sums amount only on deduplicated successful AUD finals. Slow success is successful final total time >= SlowSeconds * 1000, independent of scenario. Empty counts are zero; undefined rates/percentiles are no-data. Value on no records is a no-data/empty sum, not evidence of zero traffic health.

Stage `durationMs` measures only the local simulated work, not cumulative downstream time. The final record repeats end-to-end duration in both durationMs and totalDurationMs, so its normalized stage duration is deliberately null. End-to-end includes HTTP/serialization overhead and must not be reconstructed by summing the log durations. Stage aggregation suppresses identical replays by transaction/stage/sequence/source-time/trace/span.

OpenPipeline is per-record: each final log can extract one `demo.payment.completed` event, provider `synthetic-payments-demo`. Extraction itself does not deduplicate or join journeys. DQL deduplicates the result. The application must not send another copy directly to business-event ingestion. The fallback uses the same canonical final rules against retained logs.

All overview queries use the selected timeframe and six business filters. Journey/ETL examples use the labelled 24-hour override. Quality and reconciliation use the identical last-two-hours window ending two minutes ago; increase that lag if needed. Reset filters for an audit because malformed records may lack filter dimensions. Missing/orphan events can also reflect access, retention or timestamp differences; they are not payment failures.
