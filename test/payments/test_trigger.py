#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

from contextlib import redirect_stdout, redirect_stderr
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'trigger-payments.py'
SPEC = importlib.util.spec_from_file_location('trigger', SCRIPT)
trigger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trigger)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'status': getattr(self.server, 'health_status', 'UP')}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with self.server.lock:
            self.server.requests.append((time.monotonic(), self.path, body))
            index = len(self.server.requests)
            self.server.active += 1
            self.server.peak = max(self.server.peak, self.server.active)
        time.sleep(0.08)
        if self.server.mode == 'http_error':
            self.send_response(503)
            raw = b'Unavailable'
        elif self.server.mode == 'malformed':
            self.send_response(200)
            raw = b'not json'
        else:
            self.send_response(200)
            raw = json.dumps({'eventType': 'PAYMENT_COMPLETED',
                              'transactionId': f'PAY-{index}',
                              'status': 'FAILED' if body.get('scenario') == 'FAILED_PAYMENT' else 'SUCCESS',
                              'paymentType': body.get('paymentType', 'PAYID'),
                              'scenario': body.get('scenario', 'NORMAL')}).encode()
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
        with self.server.lock:
            self.server.active -= 1

    def log_message(self, *args):
        pass


class TriggerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.requests = []
        self.server.lock = threading.Lock()
        self.server.active = self.server.peak = 0
        self.server.mode = 'normal'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def args(self, *extra):
        return trigger.arguments(['--count', '12', '--url', f'http://127.0.0.1:{self.server.server_port}',
                                  '--output', str(Path(self.temp.name) / 'results.jsonl'), *extra])

    def run_script(self, args):
        with redirect_stdout(io.StringIO()):
            status = trigger.run(args)
        records = [json.loads(line) for line in args.output.read_text().splitlines()]
        return status, records

    def test_coverage_makes_exactly_twelve_payments(self):
        status, records = self.run_script(self.args())
        self.assertEqual(status, 0)
        self.assertEqual(len(records), 12)
        self.assertEqual(len(self.server.requests), 12)
        self.assertEqual(self.server.peak, 1)
        pairs = {(body['paymentType'], body['scenario']) for _, _, body in self.server.requests}
        self.assertEqual(pairs, {(kind, scenario) for kind in trigger.TYPES for scenario in trigger.SCENARIOS})
        self.assertTrue(all(path == '/generate' for _, path, _ in self.server.requests))
        self.assertEqual(sum(r['status'] == 'FAILED' for r in records), 4)

    def test_burst_limits_inflight_and_keeps_request_indexes(self):
        status, records = self.run_script(self.args('--count', '9', '--mode', 'burst', '--concurrency', '3'))
        self.assertEqual(status, 0)
        self.assertEqual(len(records), 9)
        self.assertEqual(sorted(r['requestIndex'] for r in records), list(range(1, 10)))
        self.assertGreater(self.server.peak, 1)
        self.assertLessEqual(self.server.peak, 3)

    def test_pacing_spaces_request_starts(self):
        status, records = self.run_script(self.args('--count', '4', '--mode', 'paced', '--rate', '600'))
        self.assertEqual(status, 0)
        self.assertEqual(len(records), 4)
        starts = [timestamp for timestamp, _, _ in self.server.requests]
        self.assertTrue(all(b - a >= 0.07 for a, b in zip(starts, starts[1:])), starts)

    def test_realistic_and_forced_payloads(self):
        args = self.args('--mix', 'realistic')
        self.assertEqual(trigger.payload(0, args), {})
        args = self.args('--payment-type', 'payid')
        self.assertEqual([trigger.payload(i, args)['scenario'] for i in range(3)], list(trigger.SCENARIOS))
        args = self.args('--scenario', 'normal')
        self.assertEqual([trigger.payload(i, args)['paymentType'] for i in range(4)], list(trigger.TYPES))
        args = self.args('--mix', 'realistic', '--payment-type', 'BPAY', '--scenario', 'FAILED_PAYMENT')
        self.assertEqual(trigger.payload(9, args), {'paymentType': 'BPAY', 'scenario': 'FAILED_PAYMENT'})

    def test_errors_are_unknown_and_never_retried(self):
        for mode in ('http_error', 'malformed'):
            with self.subTest(mode=mode):
                self.server.mode = mode
                self.server.requests.clear()
                args = self.args('--count', '2', '--output', str(Path(self.temp.name) / f'{mode}.jsonl'))
                status, records = self.run_script(args)
                self.assertEqual(status, 1)
                self.assertEqual(len(self.server.requests), 2)
                self.assertTrue(all(r['status'] == 'UNKNOWN' for r in records))

    def test_prompt_validation_and_existing_output(self):
        with patch('builtins.input', return_value='7'):
            args = trigger.arguments([])
            self.assertEqual(args.count, 7)
            self.assertEqual(args.url, 'http://127.0.0.1:8080')
        for argv in (['--count', '0'], ['--count', '2', '--concurrency', '51'],
                     ['--count', '2', '--rate', 'nan'], ['--count', '2', '--url', 'ftp://host']):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                trigger.arguments(argv)
        args = self.args()
        args.output.write_text('existing data')
        with self.assertRaises(FileExistsError):
            trigger.run(args)
        self.assertEqual(args.output.read_text(), 'existing data')
        self.assertEqual(len(self.server.requests), 0)

    def test_unhealthy_generator_sends_no_payments(self):
        self.server.health_status = 'DOWN'
        args = self.args()
        with redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(trigger.run(args), 1)
        self.assertIn('No payments sent', errors.getvalue())
        self.assertFalse(args.output.exists())
        self.assertEqual(len(self.server.requests), 0)

    def test_missing_listener_fails_before_creating_results(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            args = self.args('--url', f'http://127.0.0.1:{sock.getsockname()[1]}', '--timeout', '0.2')
            with redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(trigger.run(args), 1)
        self.assertIn('port-forward', errors.getvalue())
        self.assertFalse(args.output.exists())
        self.assertEqual(len(self.server.requests), 0)

    def test_loopback_ignores_proxy_environment(self):
        with patch.dict(os.environ, {'http_proxy': 'http://127.0.0.1:1', 'HTTP_PROXY': 'http://127.0.0.1:1',
                                     'no_proxy': '', 'NO_PROXY': ''}):
            status, records = self.run_script(self.args('--count', '1'))
        self.assertEqual(status, 0)
        self.assertEqual(len(records), 1)


if __name__ == '__main__':
    unittest.main()
