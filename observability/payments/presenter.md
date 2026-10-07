# Six-minute customer walkthrough

Before presenting, complete the live gates in [install.md](install.md). Run `python3 scripts/demo-payments-dashboard.py`, confirm `coverageComplete: true`, and keep its `examples` IDs available. Allow ingestion and extraction to settle. Choose the current dashboard, a recent timeframe containing that run, all business filters ALL, TransactionId blank, SlowSeconds 3. Background traffic means totals differ from the small local validation sample.

| Time | Show | Say / demonstrate |
| --- | --- | --- |
| 0:00–1:00 | Activity and outcome KPIs, rail/channel mix | “These are synthetic outbound AUD payments across NPP, BPAY, internal ledger and SWIFT. Completed includes confirmed success and failure. Value includes successful payments only. Six service logs can represent one payment.” Point out observed scripted results are not production failure rates. Filter one rail, then reset it. |
| 1:00–2:00 | Slow successful payments, stage latency, recent slow/failed table | Choose SLOW_SETTLEMENT. Show successful outcome with elevated end-to-end time; the settlement stage carries the delay. “Scenario tells us what was injected; duration above our three-second demo threshold detects slowness.” Change SlowSeconds to 8 briefly, then restore 3. Reset scenario. |
| 2:00–3:15 | Selected journey for `examples.processor-failure` | Paste its ID into TransactionId. Show ordered stages and final FRAUD_BLOCK at Payment Processor. Earlier successes remain intermediate. “Processing terminated here; absent settlement is expected.” Point to the authoritative final row and error. Detail explicitly uses the last 24 hours. |
| 3:15–4:00 | `examples.settlement-failure`, then optional slow-success | Replace the ID, show SETTLEMENT_FAILED and the rail's logical terminating service/error. For slow-success, show SETTLEMENT_COMPLETED followed by successful PAYMENT_COMPLETED. Stage time and final total are separate. |
| 4:00–5:15 | From service logs to business insight | Show raw content beside canonical typed fields, then the extracted final event. “Existing collection brings stdout in. A narrow payment route selects it. OpenPipeline normalizes each record and extracts only the final outcome. Grail queries reconstruct the journey and deduplicate final KPIs.” Open the saved pipeline's five processing steps if useful. |
| 5:15–6:00 | Reconciliation, optional actual trace | Clear TransactionId and reset all filters. Compare final-log payment IDs with extracted-event IDs over the settled window. Explain these counts prove extraction coverage, not payment success. If stored spans were verified, reselect an ID and open its actual trace using Distributed Tracing. Otherwise finish with the log journey and state trace ingestion is not demonstrated. |

Useful responses during discussion:

- **“Why isn't an intermediate success a successful payment?”** Routing or fraud processing may succeed before settlement rejects the payment; only the terminal outcome is authoritative.
- **“Why is there a missing final?”** It means no final was observed in this window. It can reflect in-flight work, ingestion delay, a time boundary or transport uncertainty. It is not automatically a failure.
- **“Why can transport errors coexist with completion?”** A downstream action can finish before its caller loses the response. They describe different observations of the same transaction.
- **“Does OpenPipeline calculate the journey?”** No. It transforms individual records. The application supplies final total duration; DQL groups and orders the logs by transaction.
- **“Does a trace ID prove tracing is working?”** No. The actual-span tile verifies stored spans; a log-only ID is insufficient.

Use the manifest from the current tenant run, not the IDs in the committed local fixtures. There is no run-ID filter in this application. Do not show the deliberately failure-heavy coverage run as a production failure-rate model.
