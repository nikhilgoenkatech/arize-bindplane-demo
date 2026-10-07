#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Capture actual service stdout locally; Kubernetes envelope fields are simulated."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, required=True)
    args = parser.parse_args()
    app_root = args.app_root.resolve()
    if not (app_root / 'run.py').is_file():
        parser.error('--app-root must point to the payments-demo application directory')
    root = Path(__file__).resolve().parents[1]
    repo = root.parents[1]
    fixtures = root / 'fixtures'
    fixtures.mkdir(exist_ok=True)
    roles = ['generator', 'channel', 'hub', 'processor', 'settlement']
    services = ['payment-generator', 'payment-channel', 'payment-hub', 'payment-processor', 'payment-settlement']
    sockets = [socket.socket() for _ in roles]
    for sock in sockets:
        sock.bind(('127.0.0.1', 0))
    ports = [sock.getsockname()[1] for sock in sockets]
    for sock in sockets:
        sock.close()
    processes, handles = [], []
    with tempfile.TemporaryDirectory() as directory:
        try:
            for index, role in reversed(list(enumerate(roles))):
                env = {k: v for k, v in os.environ.items() if not k.startswith('OTEL_')}
                env.update(SERVICE=role, PORT=str(ports[index]), TRANSACTIONS_PER_MINUTE='0')
                if index < 4:
                    env['NEXT_SERVICE_URL'] = f'http://127.0.0.1:{ports[index + 1]}'
                handle = (Path(directory) / f'{role}.jsonl').open('w')
                handles.append(handle)
                processes.append(subprocess.Popen([sys.executable, 'run.py'], cwd=app_root, env=env,
                    stdout=handle, stderr=handle, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
            for port in ports:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        with urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                            assert json.load(response) == {'status': 'UP'}
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.1)
            manifest_path = Path(directory) / 'demo-manifest.json'
            subprocess.run([sys.executable, str(repo / 'scripts/demo-payments-dashboard.py'), '--url',
                            f'http://127.0.0.1:{ports[0]}', '--output', str(manifest_path)], check=True)
            manifest = json.loads(manifest_path.read_text())
            manifest['captureNotes'] = 'Application responses/stdout are real local HTTP execution. Kubernetes metadata was attached by the capture script, not observed from Dynatrace. No tenant/collector was used.'
            raw = []
            for role, service in zip(roles, services):
                for line in (Path(directory) / f'{role}.jsonl').read_text().splitlines():
                    record = json.loads(line)
                    if 'transactionId' in record:
                        raw.append({'timestamp': record['timestamp'], 'content': line,
                                    'k8s.namespace.name': 'llm-obs-demo',
                                    'k8s.deployment.name': 'payments-demo-' + service})
            raw.sort(key=lambda r: (json.loads(r['content'])['transactionId'], json.loads(r['content'])['stepSequence']))
            (fixtures / 'captured-logs.jsonl').write_text('\n'.join(json.dumps(r, separators=(',', ':')) for r in raw) + '\n')
            (fixtures / 'captured-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            print(f'Captured {len(raw)} real business log records into {fixtures}')
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                process.wait(timeout=10)
            for handle in handles:
                handle.close()


if __name__ == '__main__':
    main()
