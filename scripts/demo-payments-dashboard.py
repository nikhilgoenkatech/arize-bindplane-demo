#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Collect normal/slow payments and observed failures for a dashboard walkthrough."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('trigger', Path(__file__).with_name('trigger-payments.py'))
trigger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trigger)


def run(args):
    if not trigger.check_health(args):
        return 1
    manifest = {'startedAt': datetime.now(timezone.utc).isoformat(), 'generatorUrl': args.url,
                'applicationRunIdSupported': False, 'payments': [], 'examples': {}, 'coverageComplete': False}
    with args.output.open('x', encoding='utf-8') as output:
        def submit(kind, scenario, label):
            result = trigger.send(len(manifest['payments']), {'paymentType': kind, 'scenario': scenario}, args)
            result['label'] = label
            manifest['payments'].append(result)
            event = result.get('result', {})
            print(f"{label}: {event.get('transactionId', 'NO RESPONSE')} {result['status']} {event.get('errorCode') or ''}", flush=True)
            return result

        try:
            for kind in trigger.TYPES:
                result = submit(kind, 'NORMAL', f'normal-{kind}')
                if result['status'] == 'SUCCESS':
                    manifest['examples'][f'normal-{kind}'] = result['result']['transactionId']
            result = submit('PAYID', 'SLOW_SETTLEMENT', 'slow-success')
            if result['status'] == 'SUCCESS' and result['result'].get('totalDurationMs', 0) >= 3000:
                manifest['examples']['slow-success'] = result['result']['transactionId']
            for attempt in range(args.max_failure_attempts):
                if all(key in manifest['examples'] for key in ('processor-failure', 'settlement-failure')):
                    break
                result = submit(trigger.TYPES[attempt % 4], 'FAILED_PAYMENT', f'failure-attempt-{attempt + 1}')
                event = result.get('result', {})
                if result['status'] == 'FAILED':
                    location = 'processor-failure' if event.get('errorCode') == 'FRAUD_BLOCK' else 'settlement-failure'
                    manifest['examples'].setdefault(location, event['transactionId'])
            manifest['coverageComplete'] = len(manifest['examples']) == 7
        finally:
            manifest['finishedAt'] = datetime.now(timezone.utc).isoformat()
            json.dump(manifest, output, indent=2)
            output.write('\n')
    print(f"Coverage complete: {manifest['coverageComplete']}. Evidence: {args.output}")
    if not manifest['coverageComplete']:
        print('The generator randomly chooses failure location. Coverage is incomplete; inspect the manifest before rerunning.')
    return 0 if manifest['coverageComplete'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--timeout', type=trigger.positive_float, default=30)
    parser.add_argument('--max-failure-attempts', type=trigger.positive_int, default=20)
    parser.add_argument('--output', type=Path, default=Path('payments-demo-evidence-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json'))
    try:
        return run(parser.parse_args())
    except OSError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
