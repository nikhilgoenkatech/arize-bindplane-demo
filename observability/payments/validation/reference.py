#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

"""Offline contract model; this is NOT a DQL or OpenPipeline execution engine."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build', ROOT / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def convert(value, kind):
    if value is None:
        return None
    try:
        return {'string': str, 'double': float, 'long': int}[kind](value)
    except (ValueError, TypeError):
        return None


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    except (ValueError, AttributeError):
        return None


def normalize(source):
    result = dict(source)
    attempted = source.get('content') is not None and (any(source.get(k) is None for k in build.REQUIRED + ['trace_id', 'span_id']) or
        (source.get('stage') == 'PAYMENT_COMPLETED' and (source.get('totalDurationMs') is None or source.get('eventType') is None)))
    parsed = None
    if attempted:
        try:
            parsed = json.loads(source['content'])
            if not isinstance(parsed, dict):
                parsed = None
        except (ValueError, TypeError):
            pass
    payload = parsed or {}
    result.update({'demo.name': build.MARKER, 'demo.schema_version': '1', 'payment.raw_content': source.get('content')})
    for field, (target, kind) in build.FIELDS.items():
        value = convert(source.get(field), kind)
        result[target] = value if value is not None else convert(payload.get(field), kind)
    for field in ('transactionId', 'journeyId', 'trace_id', 'span_id'):
        result[field] = source.get(field) if source.get(field) is not None else payload.get(field)
    result['payment.source_timestamp'] = timestamp(payload.get('timestamp')) or timestamp(source.get('timestamp'))
    stage, status = result['payment.stage'], result['payment.status']
    kind = ('final' if result['payment.event_type'] == 'PAYMENT_COMPLETED' and stage == 'PAYMENT_COMPLETED' else
            'transport' if stage == 'DELIVERY_ERROR' or status == 'UNKNOWN' else 'stage' if stage is not None else 'other')
    result['payment.record_kind'] = kind
    result['payment.settlement_service'] = {'NPP': 'NPP Settlement', 'BPAY': 'BPAY Settlement', 'SWIFT': 'SWIFT Gateway', 'INTERNAL': 'Internal Ledger'}.get(result['payment.rail'], 'Unmapped')
    result['payment.outcome'] = status if kind == 'final' and status in ('SUCCESS', 'FAILED') else 'UNKNOWN' if kind in ('final', 'transport') else 'NOT_FINAL'
    if kind != 'stage' or stage == 'PAYMENT_GENERATED':
        result['payment.stage_duration_ms'] = None
    for field, size in [('trace', 32), ('span', 16)]:
        value = result[f'payment.{field}_id']
        valid = isinstance(value, str) and len(value) == size and all(c in '0123456789abcdefABCDEF' for c in value)
        result[f'{field}.id'] = source.get(f'{field}.id') or (value if valid else None)
    result['payment.quality.parse_attempted'] = attempted
    result['payment.quality.parse_failed'] = attempted and parsed is None
    missing = any(result[build.FIELDS[k][0]] is None for k in build.REQUIRED if k != 'durationMs')
    missing |= result['payment.source_timestamp'] is None
    missing |= kind == 'final' and result['payment.total_duration_ms'] is None
    missing |= kind == 'stage' and stage != 'PAYMENT_GENERATED' and result['payment.stage_duration_ms'] is None
    result['payment.quality.missing_required'] = kind != 'other' and missing
    result['payment.quality.unrecognized'] = kind == 'other'
    result['payment.quality.invalid_values'] = any(result[k] is not None and result[k] < threshold for k, threshold in
        [('payment.amount', 0.000000001), ('payment.total_duration_ms', 0), ('payment.stage_duration_ms', 0)]) or (payload.get('timestamp') is not None and timestamp(payload['timestamp']) is None)
    return result


def extract(records):
    config = json.loads((ROOT / 'openpipeline/ui-configuration.json').read_text())['businessEvent']
    return [dict({field: record.get(field) for field in config['fieldExtraction']['fields']},
                 **{'event.type': 'demo.payment.completed', 'event.provider': build.MARKER, 'timestamp': record['timestamp']})
            for record in records if record['payment.record_kind'] == 'final' and record.get('payment.transaction_id') is not None]


def canonical(records):
    finals = [r for r in records if r.get('payment.record_kind') == 'final' and r.get('payment.transaction_id') is not None]
    def key(record):
        return tuple((record.get('payment.' + k) is not None, record.get('payment.' + k)) for k in build.FINAL_SORT)
    selected = {}
    for record in sorted(finals, key=key, reverse=True):
        selected.setdefault(record['payment.transaction_id'], record)
    return list(selected.values())


def outcomes(records):
    finals = canonical(records)
    success = [r for r in finals if r['payment.outcome'] == 'SUCCESS']
    failed = [r for r in finals if r['payment.outcome'] == 'FAILED']
    completed = len(success) + len(failed)
    return {'completed': completed, 'success': len(success), 'failed': len(failed),
            'uncertain_final': sum(r['payment.outcome'] == 'UNKNOWN' for r in finals),
            'failure_rate_percent': 100 * len(failed) / completed if completed else None,
            'successful_value_AUD': round(sum(r['payment.amount'] for r in success if r['payment.currency'] == 'AUD' and (r['payment.amount'] or 0) > 0), 2) if success else None}
