# Official references and verification boundaries

Checked 2026-10-07. No authenticated Dynatrace/DQL knowledge-base tools or tenant credentials were available in this session. Official documentation was used to verify the supported formats and language constructs. This is documentation review, not execution against a tenant.

| Capability | Official source | Decision |
| --- | --- | --- |
| Current dashboard document | [Document structure](https://docs.dynatrace.com/docs/analyze-explore-automate/dashboards-and-notebooks/document-api/document-structure-dashboards) (updated 2026-09-18) | Version 21; data/markdown tiles; 24-column layout; typed timeframe object; CSV/text variables |
| Current app import | [Dashboards](https://docs.dynatrace.com/docs/analyze-explore-automate/dashboards-and-notebooks/dashboards-new) | Raw document upload or Edit JSON; no Classic API payload |
| Single-select variable defaults | [Dynatrace's dashboard variable reference](https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-app-dashboards/references/variables.md) | Omit single-select CSV defaultValue; first option supplies default |
| Per-record DQL | [OpenPipeline commands](https://docs.dynatrace.com/docs/platform/openpipeline/reference/openpipeline-dql-commands), [functions](https://docs.dynatrace.com/docs/platform/openpipeline/reference/dql/openpipeline-dql-functions) | parse/fieldsAdd and supported conversions; no aggregation/join in ingestion processing |
| Matcher syntax | [DQL matcher](https://docs.dynatrace.com/docs/platform/openpipeline/reference/dql/dql-matcher-in-openpipeline) | matchesValue, boolean conditions, null checks |
| Extraction UI | [Logs/spans to business events](https://docs.dynatrace.com/docs/observe/business-observability/bo-events-capturing/bo-events-capturing-logs-and-spans), [field extraction options](https://docs.dynatrace.com/docs/observe/business-observability/bo-event-processing/bo-processing-openpipeline) | Data extraction > Business event; static type/provider; Fields to extract |
| Processing order/storage | [Processing](https://docs.dynatrace.com/docs/platform/openpipeline/concepts/processing), [data flow](https://docs.dynatrace.com/docs/platform/openpipeline/concepts/data-flow) | Retain logs; verify routing-time metadata and existing storage/access behavior |
| Configuration APIs | [Settings migration](https://docs.dynatrace.com/docs/platform/openpipeline/migration-settings) | Modern Settings schemas exist; use UI without target tenant schema/export/credentials |
| Processor limits | [Limits](https://docs.dynatrace.com/docs/platform/openpipeline/reference/limits) | Definitions <8192 chars; matchers below even legacy 1500-char limit |
| Deduplication | [Filtering commands](https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/filtering-commands) | dedup ID with deterministic sort fields |
| Aggregation | [Aggregation commands](https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/aggregation-commands), [aggregation functions](https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/functions/aggregation-functions) | summarize counts, sums, percentile; makeTimeseries |
| Combining logs/events/spans | [Correlation and join commands](https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/correlation-and-join-commands) | append for reconciliation; trace-ID inner join for actual spans |
| Trace correlation | [Traces and DQL](https://docs.dynatrace.com/docs/observe/application-observability/distributed-tracing/use-traces-and-dql-to-spot-patterns) | toUid correlation IDs; no fabricated trace URLs |
| Permissions | [Default policies](https://docs.dynatrace.com/docs/manage/identity-access-management/permission-management/default-policies) | Logs/business-events/spans/buckets/document access as needed |

The public dashboard page documents the JSON contract and native Save-time validation. A complete publicly installable vendor JSON Schema package was not obtained; the vendor package referenced by official extension tooling was unavailable from the public npm registry. `validation/dashboard-v21-documented-subset.schema.json` is explicitly a **local encoding of the documented subset**, not a vendor schema. A successful native import/save is required before claiming full schema compatibility.

Supported visualization identifiers are used with result shapes matching their documented slots. Their visual rendering and automatic field assignment still require a tenant check. Transaction selection uses the documented text variable; table-click bindings are not assumed. UI/configuration names can differ on older tenants; the target is the current Grail app and current OpenPipeline Settings model.
