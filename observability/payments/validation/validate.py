#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Validate documented JSON shape, artifact integrity, and offline payment semantics."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('reference', Path(__file__).with_name('reference.py'))
ref = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ref)
RAW = [json.loads(line) for line in (ROOT / 'fixtures/captured-logs.jsonl').read_text().splitlines()]
LOGS = [ref.normalize(record) for record in RAW]
EVENTS = ref.extract(LOGS)
MANIFEST = json.loads((ROOT / 'fixtures/captured-manifest.json').read_text())


def journey(label):
    transaction = MANIFEST['examples'][label]
    return sorted((r for r in LOGS if r['payment.transaction_id'] == transaction), key=lambda r: (r['payment.step_sequence'], r['payment.source_timestamp']))


class ContractTests(unittest.TestCase):
    def test_normal_six_logs_one_payment_and_value_once(self):
        for kind in ['PAYID', 'BPAY', 'ACCOUNT_TRANSFER', 'INTERNATIONAL_TRANSFER']:
            records = journey('normal-' + kind)
            self.assertEqual(len(records), 6)
            result = ref.outcomes(records)
            self.assertEqual((result['completed'], result['success'], result['failed']), (1, 1, 0))
            self.assertEqual(result['successful_value_AUD'], records[-1]['payment.amount'])
            self.assertEqual([r['payment.step_sequence'] for r in records], list(range(6)))

    def test_slow_success_identifies_settlement(self):
        records = journey('slow-success')
        stages = [r for r in records if r['payment.stage_duration_ms'] is not None]
        longest = max(stages, key=lambda r: r['payment.stage_duration_ms'])
        self.assertEqual(longest['payment.stage'], 'SETTLEMENT_COMPLETED')
        self.assertGreaterEqual(longest['payment.stage_duration_ms'], 3000)
        self.assertLess(longest['payment.stage_duration_ms'], 8500)
        self.assertEqual(ref.outcomes(records)['failed'], 0)
        self.assertEqual(records[-1]['payment.outcome'], 'SUCCESS')
        self.assertIsNone(records[-1]['payment.stage_duration_ms'])
        self.assertGreater(records[-1]['payment.total_duration_ms'], longest['payment.stage_duration_ms'])

    def test_processor_failure_terminates_before_settlement(self):
        records = journey('processor-failure')
        self.assertEqual(len(records), 5)
        self.assertFalse(any(r['payment.stage'].startswith('SETTLEMENT') for r in records))
        self.assertEqual(records[-1]['payment.service'], 'Payment Processor')
        self.assertEqual(records[-1]['payment.error_code'], 'FRAUD_BLOCK')
        self.assertEqual(ref.outcomes(records)['failed'], 1)
        self.assertTrue(any(r['payment.status'] == 'SUCCESS' for r in records[:-1]))
        self.assertEqual(ref.outcomes(records)['success'], 0)

    def test_settlement_failure_has_service_and_code(self):
        records = journey('settlement-failure')
        self.assertEqual(records[-2]['payment.stage'], 'SETTLEMENT_FAILED')
        self.assertEqual(records[-1]['payment.service'], records[-1]['payment.settlement_service'])
        self.assertIsNotNone(records[-1]['payment.error_code'])
        self.assertEqual(ref.outcomes(records)['failed'], 1)

    def test_transport_and_uncertain_final_stay_separate(self):
        body = json.loads(RAW[0]['content'])
        body.update(stage='DELIVERY_ERROR', status='UNKNOWN', durationMs=0, errorCode='DOWNSTREAM_UNAVAILABLE')
        body.pop('eventType', None)
        transport = ref.normalize({'timestamp': body['timestamp'], 'content': json.dumps(body)})
        self.assertEqual(transport['payment.record_kind'], 'transport')
        self.assertEqual(ref.extract([transport]), [])
        self.assertEqual(ref.outcomes([transport])['completed'], 0)
        body.update(stage='PAYMENT_COMPLETED', eventType='PAYMENT_COMPLETED', totalDurationMs=0)
        uncertain = ref.normalize({'timestamp': body['timestamp'], 'content': json.dumps(body)})
        result = ref.outcomes([uncertain])
        self.assertEqual((result['completed'], result['failed'], result['uncertain_final']), (0, 0, 1))
        self.assertIsNone(result['failure_rate_percent'])

    def test_empty_and_missing_final_are_not_failures(self):
        for records in ([], journey('normal-PAYID')[:-1]):
            result = ref.outcomes(records)
            self.assertEqual((result['completed'], result['failed']), (0, 0))
            self.assertIsNone(result['failure_rate_percent'])

    def test_duplicate_final_does_not_multiply_counts_or_value(self):
        final = journey('normal-PAYID')[-1]
        self.assertEqual(ref.outcomes([final]), ref.outcomes([final, final, final]))
        later = dict(final, **{'payment.source_timestamp': '2026-10-07T23:00:00.000Z', 'payment.outcome': 'FAILED', 'payment.status': 'FAILED'})
        self.assertEqual(ref.canonical([later, final]), ref.canonical([final, later]))
        self.assertEqual(ref.outcomes([final, later])['failed'], 1)

    def test_structured_fields_are_preserved_and_not_parsed(self):
        final = next(json.loads(r['content']) for r in RAW if json.loads(r['content']).get('eventType') == 'PAYMENT_COMPLETED')
        final['content'] = 'not JSON, but all required fields are already structured'
        result = ref.normalize(final)
        self.assertFalse(result['payment.quality.parse_attempted'])
        self.assertFalse(result['payment.quality.parse_failed'])
        self.assertEqual(result['transactionId'], final['transactionId'])
        self.assertEqual(result['payment.amount'], final['amount'])
        self.assertEqual(result['trace.id'], final['trace_id'])

    def test_malformed_missing_and_invalid_are_visible(self):
        malformed = ref.normalize({'timestamp': '2026-10-07T10:00:00Z', 'content': '{broken'})
        self.assertTrue(malformed['payment.quality.parse_failed'])
        self.assertTrue(malformed['payment.quality.unrecognized'])
        body = json.loads(RAW[0]['content'])
        body.pop('journeyId')
        body['amount'] = -1
        missing = ref.normalize({'timestamp': body['timestamp'], 'content': json.dumps(body)})
        self.assertTrue(missing['payment.quality.missing_required'])
        self.assertTrue(missing['payment.quality.invalid_values'])
        self.assertEqual(ref.extract([malformed]), [])

    def test_reference_extraction_reconciles_captured_final_records(self):
        self.assertEqual(len(EVENTS), len(MANIFEST['payments']))
        self.assertEqual(ref.outcomes(LOGS), ref.outcomes(EVENTS))
        self.assertFalse(any(r['payment.quality.missing_required'] for r in LOGS))
        self.assertFalse(any(r['payment.quality.parse_failed'] for r in LOGS))
        self.assertEqual({r['payment.transaction_id'] for r in EVENTS}, {p['result']['transactionId'] for p in MANIFEST['payments']})


class ArtifactTests(unittest.TestCase):
    def test_documented_schema_layout_and_query_artifacts(self):
        schema = json.loads((ROOT / 'validation/dashboard-v21-documented-subset.schema.json').read_text(encoding='utf-8'))
        validator = Draft202012Validator(schema)
        for fallback, filename in [(False, 'payments-dashboard.json'), (True, 'payments-final-log-fallback.json')]:
            doc = json.loads((ROOT / filename).read_text(encoding='utf-8'))
            validator.validate(doc)
            self.assertEqual(set(doc['tiles']), set(doc['layouts']))
            rectangles = list(doc['layouts'].values())
            for i, a in enumerate(rectangles):
                self.assertLessEqual(a['x'] + a['w'], 24)
                for b in rectangles[i + 1:]:
                    self.assertFalse(a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w'] and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h'])
            index = json.loads((ROOT / 'queries' / ('fallback-index.json' if fallback else 'index.json')).read_text(encoding='utf-8'))
            data = [t for t in doc['tiles'].values() if t['type'] == 'data']
            self.assertEqual(len(data), len(index))
            for tile, entry in zip(data, index):
                text = (ROOT / 'queries' / entry['file']).read_text(encoding='utf-8')
                self.assertEqual(text.split('\n', 1)[1].strip(), tile['query'])

    def test_filters_scope_and_no_dashboard_reparsing(self):
        for filename in ('payments-dashboard.json', 'payments-final-log-fallback.json'):
            doc = json.loads((ROOT / filename).read_text(encoding='utf-8'))
            variables = {v['key'] for v in doc['variables']}
            for tile in doc['tiles'].values():
                if tile['type'] != 'data':
                    continue
                query = tile['query']
                self.assertIn(ref.build.FILTERS, query)
                self.assertIn('demo.name == "synthetic-payments-demo"', query)
                self.assertNotIn('| parse ', query)
                # Matches dashboard variable tokens, excluding ordinary field names.
                self.assertTrue(set(re.findall(r'\$([A-Za-z][A-Za-z0-9_]*)', query)) <= variables)
                if tile['title'].startswith('Selected') or tile['title'].startswith('Actual spans'):
                    self.assertEqual(tile['timeframe']['tileTimeframe']['from'], 'now()-24h')
            self.assertEqual(doc['variables'][-1]['input'].split(',')[0], '3')

    def test_pipeline_matchers_limits_order_and_final_only(self):
        config = json.loads((ROOT / 'openpipeline/ui-configuration.json').read_text())
        self.assertIn('NOT an API import payload', config['format'])
        self.assertEqual(len(config['processing']), 5)
        self.assertLess(len(config['routeMatcher']), 1500)
        self.assertIn('llm-obs-demo', config['routeMatcher'])
        for service in ref.build.SERVICES:
            self.assertIn('payments-demo-' + service, config['routeMatcher'])
        for processor in config['processing']:
            dql = (ROOT / 'openpipeline' / processor['definitionFile']).read_text()
            self.assertLess(len(dql), 8192)
            self.assertLess(len(processor['matcher']), 1500)
            self.assertNotIn('fetch ', dql)
        self.assertEqual(config['businessEvent']['matcher'], 'payment.record_kind == "final" and isNotNull(payment.transaction_id)')


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {'validationDate': '2026-10-07', 'testsRun': result.testsRun,
               'failures': len(result.failures), 'errors': len(result.errors),
               'capturedLogRecords': len(LOGS), 'capturedPayments': len(MANIFEST['payments']),
               'referenceProjectedBusinessEvents': len(EVENTS), 'observedOutcomes': ref.outcomes(LOGS),
               'coverageExamples': MANIFEST['examples'],
               'locallyValidated': ['Real local HTTP application run and business stdout', 'Documented v21 JSON subset, layouts, query consistency', 'Python reference model of counting and transformation contracts'],
               'NOTValidated': ['Native full vendor dashboard schema/import/rendering', 'DQL execution in Grail', 'Actual OpenPipeline transformation and extraction', 'Kubernetes incoming log metadata', 'Tenant log/event reconciliation', 'Actual span ingestion and trace navigation']}
    (ROOT / 'validation/results.json').write_text(json.dumps(summary, indent=2) + '\n')
    example = journey('slow-success')[-1]
    (ROOT / 'fixtures/reference-normalized-final.json').write_text(json.dumps(example, indent=2) + '\n')
    (ROOT / 'fixtures/reference-extracted-final.json').write_text(json.dumps(ref.extract([example])[0], indent=2) + '\n')
    sys.exit(0 if result.wasSuccessful() else 1)
