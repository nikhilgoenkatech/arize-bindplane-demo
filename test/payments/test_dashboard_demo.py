#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('dashboard_demo', Path(__file__).resolve().parents[2] / 'scripts/demo-payments-dashboard.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class DemoCoverageTests(unittest.TestCase):
    def test_incomplete_random_failure_coverage_is_bounded_and_saved(self):
        def send(index, body, args):
            failed = body['scenario'] == 'FAILED_PAYMENT'
            return {'status': 'FAILED' if failed else 'SUCCESS', 'result': {
                'transactionId': f'PAY-{index}', 'totalDurationMs': 4000,
                'errorCode': 'FRAUD_BLOCK' if failed else None}}
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(url='http://127.0.0.1:8080', timeout=1, max_failure_attempts=2, output=Path(folder) / 'evidence.json')
            with patch.object(demo.trigger, 'check_health', return_value=True), patch.object(demo.trigger, 'send', side_effect=send) as sender, redirect_stdout(io.StringIO()):
                self.assertEqual(demo.run(args), 1)
            manifest = json.loads(args.output.read_text())
            self.assertEqual(sender.call_count, 7)
            self.assertFalse(manifest['coverageComplete'])
            self.assertIn('processor-failure', manifest['examples'])
            self.assertNotIn('settlement-failure', manifest['examples'])

    def test_failed_preflight_sends_no_payments(self):
        with tempfile.TemporaryDirectory() as folder:
            args = SimpleNamespace(url='http://127.0.0.1:8080', timeout=1, max_failure_attempts=2, output=Path(folder) / 'evidence.json')
            with patch.object(demo.trigger, 'check_health', return_value=False), patch.object(demo.trigger, 'send') as sender:
                self.assertEqual(demo.run(args), 1)
                sender.assert_not_called()
            self.assertFalse(args.output.exists())


if __name__ == '__main__':
    unittest.main()
