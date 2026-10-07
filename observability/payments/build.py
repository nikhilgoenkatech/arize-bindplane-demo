#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Generate dashboard documents, DQL, and exact OpenPipeline UI specifications."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKER = 'synthetic-payments-demo'
TITLE = 'Payments — Activity, Outcomes & Journey'
SERVICES = ['payment-generator', 'payment-channel', 'payment-hub', 'payment-processor', 'payment-settlement']
FIELDS = {
    'transactionId': ('payment.transaction_id', 'string'), 'journeyId': ('payment.journey_id', 'string'),
    'paymentType': ('payment.type', 'string'), 'paymentRail': ('payment.rail', 'string'),
    'channel': ('payment.channel', 'string'), 'customerSegment': ('payment.customer_segment', 'string'),
    'direction': ('payment.direction', 'string'), 'currency': ('payment.currency', 'string'),
    'amount': ('payment.amount', 'double'), 'scenario': ('payment.scenario', 'string'),
    'service': ('payment.service', 'string'), 'stage': ('payment.stage', 'string'),
    'status': ('payment.status', 'string'), 'eventType': ('payment.event_type', 'string'),
    'stepSequence': ('payment.step_sequence', 'long'), 'durationMs': ('payment.stage_duration_ms', 'double'),
    'totalDurationMs': ('payment.total_duration_ms', 'double'), 'riskScore': ('payment.risk_score', 'long'),
    'fraudDecision': ('payment.fraud_decision', 'string'), 'errorCode': ('payment.error_code', 'string'),
    'errorMessage': ('payment.error_message', 'string'), 'trace_id': ('payment.trace_id', 'string'),
    'span_id': ('payment.span_id', 'string'),
}
REQUIRED = ['transactionId', 'journeyId', 'paymentType', 'paymentRail', 'channel', 'customerSegment',
            'direction', 'currency', 'amount', 'scenario', 'service', 'stage', 'status', 'stepSequence', 'durationMs']
ROUTE = 'matchesValue(k8s.namespace.name, "llm-obs-demo") and matchesValue(k8s.deployment.name, {' + ', '.join('"payments-demo-' + s + '"' for s in SERVICES) + '})'
FINAL_SORT = ['source_timestamp', 'status', 'service', 'total_duration_ms', 'error_code', 'trace_id', 'span_id', 'amount', 'type', 'rail', 'channel', 'customer_segment', 'scenario', 'journey_id', 'currency', 'direction', 'step_sequence', 'risk_score', 'fraud_decision', 'error_message']
DEDUPE = '| dedup payment.transaction_id, sort: {' + ', '.join('payment.' + field + ' desc' for field in FINAL_SORT) + '}'
FILTERS = '\n'.join('| filter $' + variable + ' == "ALL" or ' + field + ' == $' + variable for variable, field in [
    ('Rail', 'payment.rail'), ('PaymentType', 'payment.type'), ('Channel', 'payment.channel'),
    ('CustomerSegment', 'payment.customer_segment'), ('Scenario', 'payment.scenario')]) + '\n| filter $TransactionId == "" or payment.transaction_id == $TransactionId'
LOGS = 'fetch logs\n| filter demo.name == "synthetic-payments-demo" and demo.schema_version == "1"'
FINAL_LOGS = LOGS + '\n| filter payment.record_kind == "final" and isNotNull(payment.transaction_id)'
EVENTS = 'fetch bizevents\n| filter event.type == "demo.payment.completed" and event.provider == "synthetic-payments-demo"\n| filter demo.name == "synthetic-payments-demo" and demo.schema_version == "1"\n| filter isNotNull(payment.transaction_id)'


def write(path, content):
    file = ROOT / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content if isinstance(content, str) else json.dumps(content, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def pipeline():
    parse_match = 'isNotNull(content) and (' + ' or '.join('isNull(' + s + ')' for s in REQUIRED + ['trace_id', 'span_id']) + ' or (stage == "PAYMENT_COMPLETED" and (isNull(totalDurationMs) or isNull(eventType))))'
    definitions = [
        ('01-mark-and-preserve', 'true', 'fieldsAdd demo.name = "synthetic-payments-demo", demo.schema_version = "1", payment.raw_content = content, payment.quality.parse_attempted = ' + parse_match),
        ('02-parse-if-needed', parse_match, 'parse content, "JSON:dt.temp.payment"'),
    ]
    conversions = []
    for source, (target, kind) in FIELDS.items():
        fn = {'string': 'toString', 'double': 'toDouble', 'long': 'toLong'}[kind]
        conversions.append(f'{target} = coalesce({fn}({source}), {fn}(dt.temp.payment[{source}]))')
    conversions.append('payment.source_timestamp = toString(coalesce(toTimestamp(dt.temp.payment[timestamp]), toTimestamp(timestamp)))')
    preserve = ', '.join(f'{s} = coalesce({s}, dt.temp.payment[{s}])' for s in ('transactionId', 'journeyId', 'trace_id', 'span_id'))
    definitions.append(('03-normalize', 'true', 'fieldsAdd ' + ',\n  '.join(conversions) + '\n| fieldsAdd ' + preserve))
    classify = '''fieldsAdd payment.record_kind = if(payment.event_type == "PAYMENT_COMPLETED" and payment.stage == "PAYMENT_COMPLETED", "final", else: if(payment.stage == "DELIVERY_ERROR" or payment.status == "UNKNOWN", "transport", else: if(isNotNull(payment.stage), "stage", else: "other")))
| fieldsAdd payment.settlement_service = if(payment.rail == "NPP", "NPP Settlement", else: if(payment.rail == "BPAY", "BPAY Settlement", else: if(payment.rail == "SWIFT", "SWIFT Gateway", else: if(payment.rail == "INTERNAL", "Internal Ledger", else: "Unmapped"))))
| fieldsAdd payment.outcome = if(payment.record_kind == "final", if(payment.status == "SUCCESS", "SUCCESS", else: if(payment.status == "FAILED", "FAILED", else: "UNKNOWN")), else: if(payment.record_kind == "transport", "UNKNOWN", else: "NOT_FINAL"))
| fieldsAdd payment.stage_duration_ms = if(payment.record_kind == "stage" and payment.stage != "PAYMENT_GENERATED", payment.stage_duration_ms, else: null)
| fieldsAdd trace.id = coalesce(trace.id, toUid(payment.trace_id)), span.id = coalesce(span.id, toUid(payment.span_id))'''
    definitions.append(('04-classify-and-correlate', 'true', classify))
    missing = ' or '.join('isNull(' + FIELDS[s][0] + ')' for s in REQUIRED if s != 'durationMs')
    missing += ' or isNull(toTimestamp(payment.source_timestamp)) or (payment.record_kind == "final" and isNull(payment.total_duration_ms)) or (payment.record_kind == "stage" and payment.stage != "PAYMENT_GENERATED" and isNull(payment.stage_duration_ms))'
    quality = 'fieldsAdd payment.quality.parse_failed = payment.quality.parse_attempted == true and isNull(dt.temp.payment)\n'
    quality += '| fieldsAdd payment.quality.missing_required = (payment.record_kind != "other") and (' + missing + ')\n'
    quality += '| fieldsAdd payment.quality.unrecognized = payment.record_kind == "other"\n'
    quality += '| fieldsAdd payment.quality.invalid_values = (isNotNull(payment.amount) and payment.amount <= 0) or (isNotNull(payment.total_duration_ms) and payment.total_duration_ms < 0) or (isNotNull(payment.stage_duration_ms) and payment.stage_duration_ms < 0) or (isNotNull(dt.temp.payment[timestamp]) and isNull(toTimestamp(dt.temp.payment[timestamp])))'
    definitions.append(('05-quality', 'true', quality))
    extraction_match = 'payment.record_kind == "final" and isNotNull(payment.transaction_id)'
    extracted = ['demo.name', 'demo.schema_version', 'payment.source_timestamp', 'payment.record_kind',
                 'payment.outcome', 'payment.settlement_service', 'trace.id', 'span.id',
                 'payment.quality.missing_required', 'payment.quality.invalid_values'] + [v[0] for v in FIELDS.values()]
    # Source casing remains available in retained logs; business events use the canonical fields.
    spec = {'format': 'UI configuration checklist, NOT an API import payload', 'name': 'Synthetic payments v1',
            'scope': 'Logs', 'routeName': 'Synthetic payments / llm-obs-demo / payments-demo', 'routeMatcher': ROUTE,
            'processing': [], 'businessEvent': {'name': 'Extract authoritative payment completion', 'matcher': extraction_match,
                'eventType': {'mode': 'Static string', 'value': 'demo.payment.completed'},
                'eventProvider': {'mode': 'Static string', 'value': MARKER},
                'fieldExtraction': {'mode': 'Fields to extract', 'fields': extracted}},
            'storage': 'Retain source logs; preserve existing permissions, retention, and storage policy; do not add Drop record or No storage assignment.'}
    for name, matcher, dql in definitions:
        write('openpipeline/' + name + '.dql', dql + '\n')
        spec['processing'].append({'name': name, 'type': 'DQL', 'matcher': matcher, 'definitionFile': name + '.dql'})
    write('openpipeline/ui-configuration.json', spec)
    write('openpipeline/route.matcher', ROUTE + '\n')
    write('field-mapping.json', {'schemaVersion': '1', 'fields': {key: {'canonical': value[0], 'type': value[1]} for key, value in FIELDS.items()},
        'timestamp': {'canonical': 'payment.source_timestamp', 'type': 'normalized timestamp string', 'precedence': ['parsed JSON timestamp', 'existing timestamp'], 'ingestionTimestampPreserved': True}})


def csv(key, options, default='ALL'):
    options = [default] + [option for option in options if option != default]
    return {'version': 2, 'key': key, 'type': 'csv', 'visible': True, 'editable': True,
            'input': ','.join(options), 'multiple': False}


def dashboard(fallback=False):
    doc = {'version': 21, 'variables': [
        csv('Rail', ['ALL', 'NPP', 'BPAY', 'INTERNAL', 'SWIFT']),
        csv('PaymentType', ['ALL', 'PAYID', 'BPAY', 'ACCOUNT_TRANSFER', 'INTERNATIONAL_TRANSFER']),
        csv('Channel', ['ALL', 'ANZ_PLUS', 'MOBILE_BANKING', 'INTERNET_BANKING', 'CORPORATE_API']),
        csv('CustomerSegment', ['ALL', 'RETAIL', 'SMALL_BUSINESS', 'CORPORATE']),
        csv('Scenario', ['ALL', 'NORMAL', 'SLOW_SETTLEMENT', 'FAILED_PAYMENT']),
        {'version': 2, 'key': 'TransactionId', 'type': 'text', 'visible': True, 'editable': True, 'defaultValue': ''},
        csv('SlowSeconds', ['1', '2', '3', '5', '8'], '3'),
    ], 'tiles': {}, 'layouts': {}, 'settings': {'defaultTimeframe': {'value': {'from': 'now()-2h', 'to': 'now()'}, 'enabled': True}}}
    queries = []
    y = 0

    def tile(title, query=None, viz='table', x=0, width=24, height=5, text=None, detail=False, quality=False):
        nonlocal y
        key = str(len(doc['tiles']) + 1)
        if text is not None:
            content = {'type': 'markdown', 'content': text}
        else:
            content = {'type': 'data', 'title': title, 'query': query, 'visualization': viz,
                       'visualizationSettings': {}, 'querySettings': {}}
            if detail:
                content['timeframe'] = {'tileTimeframeEnabled': True, 'tileTimeframe': {'from': 'now()-24h', 'to': 'now()'}}
            if quality:
                content['timeframe'] = {'tileTimeframeEnabled': True, 'tileTimeframe': {'from': 'now()-2h', 'to': 'now()-2m'}}
            slug = title.lower().replace('—', '').replace('/', '-').replace(' ', '-')
            slug = ''.join(c for c in slug if c.isalnum() or c == '-')
            name = ('fallback/' if fallback else '') + key + '-' + slug + '.dql'
            write('queries/' + name, '// ' + title + '\n' + query + '\n')
            queries.append({'file': name, 'purpose': title, 'source': query.splitlines()[0], 'timeframe': 'last 24h' if detail else ('last 2h ending 2m ago' if quality else 'dashboard timeframe')})
        doc['tiles'][key] = content
        doc['layouts'][key] = {'x': x, 'y': y, 'w': width, 'h': height}
        if x + width == 24:
            y += height

    final = (FINAL_LOGS if fallback else EVENTS) + '\n' + DEDUPE + '\n' + FILTERS
    confirmed = final + '\n| filter payment.outcome == "SUCCESS" or payment.outcome == "FAILED"'
    filtered_logs = LOGS + '\n' + FILTERS
    tile('', text='# ' + TITLE + '\nSynthetic outbound AUD payments. ' + ('**Final-log fallback mode**: KPIs read normalized final logs.' if fallback else 'Business KPIs read extracted completion events; operational detail reads logs.') + '\nUse the timeframe and filters above. Completed = confirmed SUCCESS + FAILED. Failure rate uses that same denominator. Scripted coverage is deliberately failure-heavy; these are observed demo results, not production failure rates. SlowSeconds is a demo threshold, not a banking SLA.', height=4)
    kpis = [
        ('Completed payments', confirmed + '\n| summarize completed = count()'),
        ('Successful payments', final + '\n| summarize successful = countIf(payment.outcome == "SUCCESS")'),
        ('Failed payments', final + '\n| summarize failed = countIf(payment.outcome == "FAILED")'),
        ('Confirmed failure rate (%)', confirmed + '\n| summarize completed = count(), failed = countIf(payment.outcome == "FAILED")\n| fields failure_rate_percent = if(completed > 0, 100.0 * failed / completed, else: null)'),
        ('P95 end-to-end (seconds)', confirmed + '\n| filter payment.total_duration_ms >= 0\n| summarize p95_seconds = percentile(payment.total_duration_ms / 1000.0, 95)'),
        ('Successful payment value (AUD)', final + '\n| filter payment.outcome == "SUCCESS" and payment.amount > 0 and payment.currency == "AUD"\n| summarize successful_value_AUD = sum(payment.amount)'),
        ('Slow successful payments', final + '\n| summarize slow_success = countIf(payment.outcome == "SUCCESS" and payment.total_duration_ms >= toDouble($SlowSeconds) * 1000)'),
        ('Uncertain final outcomes', final + '\n| summarize uncertain_final = countIf(payment.outcome == "UNKNOWN")'),
    ]
    for i, (name, query) in enumerate(kpis):
        tile(name, query, 'singleValue', x=(i % 4) * 6, width=6, height=3)
    tile('Completed payment traffic', confirmed + '\n| fieldsAdd timestamp = toTimestamp(payment.source_timestamp)\n| makeTimeseries payments = count(default: 0), interval: 5m', 'lineChart', width=12)
    tile('Final outcome distribution', final + '\n| summarize payments = count(), by: {payment.outcome}', 'donutChart', x=12, width=12)
    tile('Payment mix by rail', confirmed + '\n| summarize payments = count(), by: {payment.rail}', 'categoricalBarChart', width=12)
    tile('Payment mix by channel', confirmed + '\n| summarize payments = count(), by: {payment.channel}', 'categoricalBarChart', x=12, width=12)
    tile('Payments with transport errors', filtered_logs + '\n| filter payment.record_kind == "transport" and isNotNull(payment.transaction_id)\n| dedup payment.transaction_id\n| summarize affected_payments = count()', 'singleValue', width=12, height=3)
    tile('No final outcome observed in selected window', filtered_logs + '\n| filter isNotNull(payment.transaction_id)\n| summarize finals = countIf(payment.record_kind == "final"), by: {payment.transaction_id}\n| filter finals == 0\n| summarize payments_without_observed_final = count()', 'singleValue', x=12, width=12, height=3)
    tile('', text='## Delays and failures\nStage durations are exclusive local simulated processing time. Final end-to-end duration is never included in stage latency. Transport errors may overlap completed payments; do not add those counts together. A missing final record can reflect ingestion delay or a timeframe boundary.', height=3)
    stages = filtered_logs + '\n| filter payment.record_kind == "stage" and payment.stage != "PAYMENT_GENERATED" and payment.stage_duration_ms >= 0\n| dedup payment.transaction_id, payment.stage, payment.step_sequence, payment.source_timestamp, payment.trace_id, payment.span_id'
    tile('Stage latency by service, stage and rail', stages + '\n| summarize avg_seconds = avg(payment.stage_duration_ms / 1000.0), p95_seconds = percentile(payment.stage_duration_ms / 1000.0, 95), records = count(), by: {payment.service, payment.stage, payment.rail}\n| sort p95_seconds desc')
    tile('End-to-end duration by rail and scenario', confirmed + '\n| summarize payments = count(), failed = countIf(payment.outcome == "FAILED"), avg_seconds = avg(payment.total_duration_ms / 1000.0), p95_seconds = percentile(payment.total_duration_ms / 1000.0, 95), by: {payment.rail, payment.scenario}')
    tile('Failures by error and terminating service', final + '\n| filter payment.outcome == "FAILED"\n| summarize failed_payments = count(), by: {payment.error_code, terminating_service = payment.service}\n| sort failed_payments desc')
    tile('Recent slow and failed payments', final + '\n| filter payment.outcome == "FAILED" or payment.total_duration_ms >= toDouble($SlowSeconds) * 1000\n| fields timestamp = toTimestamp(payment.source_timestamp), transactionId = payment.transaction_id, rail = payment.rail, channel = payment.channel, scenario = payment.scenario, outcome = payment.outcome, total_seconds = payment.total_duration_ms / 1000.0, errorCode = payment.error_code, terminating_service = payment.service\n| sort timestamp desc\n| limit 50', height=7)
    tile('', text='## Selected payment journey — last 24 hours\nCopy a transactionId from the table above into the TransactionId variable. Clear conflicting rail/type/channel/segment/scenario filters. All related tiles respect these filters. Detail tiles use a labelled 24-hour lookback, independent of the overview timeframe. No table-click selection or trace URL is assumed. A processor failure correctly has no settlement stage.', height=4)
    selected = filtered_logs + '\n| filter $TransactionId != "" and payment.transaction_id == $TransactionId'
    tile('Selected payment business context and termination', selected + '\n| summarize final_records = countIf(payment.record_kind == "final"), observed_stages = collectDistinct(payment.stage), rail = takeAny(payment.rail), channel = takeAny(payment.channel), amount_AUD = takeAny(payment.amount), by: {payment.transaction_id}\n| fieldsAdd observation = if(final_records > 0, "Final outcome observed: see completion row below", else: "No final outcome observed in detail window")', detail=True)
    tile('Selected authoritative final outcome', selected + '\n| filter payment.record_kind == "final"\n' + DEDUPE + '\n| fields transactionId = payment.transaction_id, outcome = payment.outcome, terminating_service = payment.service, total_seconds = payment.total_duration_ms / 1000.0, errorCode = payment.error_code, timestamp = toTimestamp(payment.source_timestamp)', detail=True)
    tile('Selected journey ordered records', selected + '\n| fields stepSequence = payment.step_sequence, timestamp = toTimestamp(payment.source_timestamp), service = payment.service, stage = payment.stage, stage_status = payment.status, stage_seconds = payment.stage_duration_ms / 1000.0, riskScore = payment.risk_score, fraudDecision = payment.fraud_decision, errorCode = payment.error_code, trace_id = payment.trace_id, span_id = payment.span_id\n| sort stepSequence asc, timestamp asc\n| limit 200', detail=True, height=8)
    trace = 'fetch spans\n| filter in(service.name, array("payment-generator", "payment-channel", "payment-hub", "payment-processor", "payment-settlement"))\n| join [\n' + selected + '\n| filter isNotNull(trace.id)\n| dedup trace.id\n| fields trace.id\n], on: {trace.id}, kind: inner\n| fields trace.id, span.id, service.name, span.name, start_time, duration\n| sort start_time asc\n| limit 100'
    tile('Actual spans for selected payment — if ingested', trace, detail=True)
    tile('', text='## From service logs to business insight\nContainer stdout → existing log collection → payment-scoped routing → typed fields and quality flags → retained logs + one extracted event per final record → DQL deduplication and analysis. OpenPipeline transforms individual records; DQL reconstructs the multi-service journey.\nSelect a transaction to compare raw and normalized records. Copy its trace ID into Distributed Tracing only when the actual-span table shows data. Trace IDs alone do not prove trace ingestion.', height=4)
    tile('Raw source log and normalized fields', selected + '\n| fields timestamp, raw_content = content, transactionId = payment.transaction_id, journeyId = payment.journey_id, amount = payment.amount, service = payment.service, stage = payment.stage, record_kind = payment.record_kind, outcome = payment.outcome, total_ms = payment.total_duration_ms, schema = demo.schema_version, trace.id\n| sort timestamp asc\n| limit 20', detail=True, height=7)
    tile('Extracted final business-event example', EVENTS + '\n' + FILTERS + '\n| filter $TransactionId != "" and payment.transaction_id == $TransactionId\n' + DEDUPE + '\n| fields timestamp, event.type, event.provider, demo.name, demo.schema_version, payment.transaction_id, payment.outcome, payment.total_duration_ms, payment.amount, payment.service, payment.error_code, trace.id', detail=True, height=5)
    quality = LOGS + '\n' + FILTERS + '\n| summarize relevant_logs = count(), recognized_final_logs = countIf(payment.record_kind == "final"), missing_required = countIf(payment.quality.missing_required == true), parse_failures = countIf(payment.quality.parse_failed == true), invalid_values = countIf(payment.quality.invalid_values == true), unrecognized = countIf(payment.quality.unrecognized == true)'
    tile('Transformation quality — settled 2-hour window', quality, quality=True)
    reconciliation = FINAL_LOGS + '\n' + FILTERS + '\n| fields payment.transaction_id, payment.source_timestamp, source = "final_log"\n| append [\n' + EVENTS + '\n' + FILTERS + '\n| fields payment.transaction_id, payment.source_timestamp, source = "business_event"\n]\n| summarize log_records = countIf(source == "final_log"), event_records = countIf(source == "business_event"), by: {payment.transaction_id}\n| summarize final_log_records = sum(log_records), extracted_event_records = sum(event_records), unique_final_log_payments = countIf(log_records > 0), unique_event_payments = countIf(event_records > 0), missing_event_payments = countIf(log_records > 0 and event_records == 0), events_without_log = countIf(event_records > 0 and log_records == 0), duplicate_final_logs = sum(if(log_records > 1, log_records - 1, else: 0)), duplicate_events = sum(if(event_records > 1, event_records - 1, else: 0))'
    tile('Extraction coverage — settled 2-hour window', reconciliation, quality=True)
    tile('', text='Quality panels use the same last-2-hours window ending 2 minutes ago to allow ingestion to settle. This is extraction coverage, not payment success. Increase the lag if your ingestion takes longer. Unrecognized/runtime logs are not payments. Filters may hide malformed records without those fields: reset filters to ALL and clear TransactionId for the full quality audit. No-data P95/rate/value remains empty; zero counts do not prove ingestion is healthy. In fallback mode, event panels can remain empty until extraction is enabled.', height=4)
    filename = 'payments-final-log-fallback.json' if fallback else 'payments-dashboard.json'
    write(filename, doc)
    write('queries/' + ('fallback-index.json' if fallback else 'index.json'), queries)
    if not fallback:
        write('payments-dashboard.document.json', {'name': TITLE, 'type': 'dashboard', 'content': doc})


if __name__ == '__main__':
    pipeline()
    dashboard()
    dashboard(fallback=True)
