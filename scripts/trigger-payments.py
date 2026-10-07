#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Send synthetic payments using Python 3.9+ and the standard library only."""

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener, urlopen


TYPES = ('PAYID', 'BPAY', 'ACCOUNT_TRANSFER', 'INTERNATIONAL_TRANSFER')
SCENARIOS = ('NORMAL', 'SLOW_SETTLEMENT', 'FAILED_PAYMENT')


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be at least 1')
    return number


def positive_float(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be a positive finite number')
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-n', '--count', type=positive_int, help='number of requests; prompts when omitted')
    parser.add_argument('--url', default='http://127.0.0.1:8080', help='generator base URL (default: http://127.0.0.1:8080)')
    parser.add_argument('--mix', choices=('coverage', 'realistic'), default='coverage',
                        help='coverage cycles through 12 type/scenario pairs; realistic uses generator weights')
    parser.add_argument('--payment-type', type=str.upper, choices=TYPES)
    parser.add_argument('--scenario', type=str.upper, choices=SCENARIOS)
    parser.add_argument('--mode', choices=('sequential', 'paced', 'burst'), default='sequential')
    parser.add_argument('--concurrency', type=positive_int, default=5, help='maximum in-flight requests for paced/burst modes (1-50)')
    parser.add_argument('--rate', type=positive_float, default=60, help='target requests/minute in paced mode')
    parser.add_argument('--timeout', type=positive_float, default=30, help='HTTP timeout in seconds')
    parser.add_argument('--output', type=Path, help='new JSONL file; defaults to payments-results-TIMESTAMP.jsonl')
    args = parser.parse_args(argv)
    parsed = urlparse(args.url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment:
        parser.error('--url must be an HTTP(S) base URL without a query or fragment')
    if args.concurrency > 50:
        parser.error('--concurrency must be between 1 and 50')
    if args.count is None:
        try:
            args.count = positive_int(input('Number of transactions: ').strip())
        except (ValueError, argparse.ArgumentTypeError, EOFError):
            parser.error('enter a positive whole number, or pass --count N')
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        args.output = Path(f'payments-results-{stamp}.jsonl')
    return args


def payload(index, args):
    body = {}
    if args.mix == 'coverage':
        types = (args.payment_type,) if args.payment_type else TYPES
        scenarios = (args.scenario,) if args.scenario else SCENARIOS
        body = {'paymentType': types[index % len(types)],
                'scenario': scenarios[(index // len(types)) % len(scenarios)]}
    if args.payment_type:
        body['paymentType'] = args.payment_type
    if args.scenario:
        body['scenario'] = args.scenario
    return body


def open_url(request, timeout):
    url = request.full_url if isinstance(request, Request) else request
    if urlparse(url).hostname in ('127.0.0.1', 'localhost', '::1'):
        return build_opener(ProxyHandler({})).open(request, timeout=timeout)
    return urlopen(request, timeout=timeout)


def check_health(args):
    url = args.url.rstrip('/') + '/health'
    try:
        with open_url(url, min(args.timeout, 5)) as response:
            if json.load(response) != {'status': 'UP'}:
                raise ValueError('Expected health response {"status":"UP"}')
    except (URLError, OSError, ValueError) as error:
        if isinstance(error, HTTPError):
            error.close()
        print(f'Generator health check failed at {url}: {error}', file=sys.stderr)
        print('No payments sent. Start kubectl port-forward in this same CloudShell environment, '
              'verify /health, then retry. Use --url http://127.0.0.1:8080 for IPv4 loopback.', file=sys.stderr)
        return False
    return True


def send(index, body, args):
    record = {'requestIndex': index + 1, 'request': body, 'status': 'UNKNOWN'}
    started = time.monotonic()
    req = Request(args.url.rstrip('/') + '/generate', data=json.dumps(body).encode(),
                  headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with open_url(req, args.timeout) as response:
            result = json.load(response)
        if (not isinstance(result, dict) or result.get('eventType') != 'PAYMENT_COMPLETED'
                or result.get('status') not in ('SUCCESS', 'FAILED')
                or not isinstance(result.get('transactionId'), str)):
            raise ValueError('Response is not a completed payment event')
        record.update(status=result['status'], result=result)
    except HTTPError as error:
        record.update(httpStatus=error.code, error=f'HTTP {error.code}: {error.reason}')
        error.close()
    except (URLError, OSError, ValueError) as error:
        record['error'] = str(error)
    record['clientDurationMs'] = round((time.monotonic() - started) * 1000, 2)
    return record


def run(args):
    if not check_health(args):
        return 1
    workers = 1 if args.mode == 'sequential' else args.concurrency
    interval = 60 / args.rate if args.mode == 'paced' else 0
    counts = Counter()
    started = time.monotonic()
    interrupted = False
    with args.output.open('x', encoding='utf-8') as output, ThreadPoolExecutor(max_workers=workers) as executor:
        print(f'Sending {args.count} payments: mix={args.mix}, mode={args.mode}, concurrency={workers}', flush=True)
        print(f'Results: {args.output.resolve()}', flush=True)
        pending = set()
        submitted = 0
        next_send = time.monotonic()

        def collect(done):
            for future in done:
                record = future.result()
                counts[record['status']] += 1
                output.write(json.dumps(record, separators=(',', ':')) + '\n')
                output.flush()
                result = record.get('result', {})
                print(f"[{record['requestIndex']}/{args.count}] {record['status']:8} "
                      f"{result.get('transactionId', '-')} "
                      f"{result.get('paymentType', record['request'].get('paymentType', 'random'))} "
                      f"{result.get('scenario', record['request'].get('scenario', 'random'))} "
                      f"{record['clientDurationMs']:.0f}ms "
                      f"{result.get('errorCode') or record.get('error', '')}", flush=True)

        try:
            while submitted < args.count or pending:
                now = time.monotonic()
                if submitted < args.count and len(pending) < workers and now >= next_send:
                    pending.add(executor.submit(send, submitted, payload(submitted, args), args))
                    submitted += 1
                    next_send = now + interval
                    continue
                delay = max(0, next_send - now) if submitted < args.count and len(pending) < workers else None
                if pending:
                    done, pending = wait(pending, timeout=delay, return_when=FIRST_COMPLETED)
                    collect(done)
                elif delay:
                    time.sleep(delay)
        except KeyboardInterrupt:
            interrupted = True
            print('\nStopped sending. Waiting for in-flight requests to finish...', flush=True)
            done, _ = wait(pending)
            collect(done)

    print(f"Summary: requested={args.count}, sent={submitted}, success={counts['SUCCESS']}, "
          f"failed={counts['FAILED']}, unknown={counts['UNKNOWN']}, elapsed={time.monotonic() - started:.1f}s")
    if counts['UNKNOWN']:
        print('UNKNOWN requests were not retried; the server may still have completed them.')
    return 130 if interrupted else (1 if counts['UNKNOWN'] else 0)


def main():
    try:
        return run(arguments())
    except OSError as error:
        print(f'Cannot write results: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
