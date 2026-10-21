// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

const opentelemetry = require('@opentelemetry/sdk-node');
const {getNodeAutoInstrumentations} = require('@opentelemetry/auto-instrumentations-node');
const {OTLPTraceExporter} = require('@opentelemetry/exporter-trace-otlp-grpc');
const {OTLPMetricExporter} = require('@opentelemetry/exporter-metrics-otlp-grpc');
const {OTLPLogExporter} = require('@opentelemetry/exporter-logs-otlp-grpc');
const {BatchLogRecordProcessor} = require('@opentelemetry/sdk-logs');
const {PeriodicExportingMetricReader} = require('@opentelemetry/sdk-metrics');
const {alibabaCloudEcsDetector} = require('@opentelemetry/resource-detector-alibaba-cloud');
const {awsEc2Detector, awsEksDetector} = require('@opentelemetry/resource-detector-aws');
const {containerDetector} = require('@opentelemetry/resource-detector-container');
const {gcpDetector} = require('@opentelemetry/resource-detector-gcp');
const {envDetector, hostDetector, osDetector, processDetector} = require('@opentelemetry/resources');


const sdk = new opentelemetry.NodeSDK({
  traceExporter: new OTLPTraceExporter(),
  logRecordProcessors: [new BatchLogRecordProcessor({ exporter: new OTLPLogExporter() })],
  instrumentations: [
    getNodeAutoInstrumentations({
      '@opentelemetry/instrumentation-fs': {
        enabled: false,
      },
      '@opentelemetry/instrumentation-http': {
        applyCustomAttributesOnSpan: (span, request) => {
          const headers = request.headers || {};
          const mapping = {
            'x-load-test-id'      : 'load_test.id',
            'x-load-test-scenario': 'load_test.scenario',
            'x-load-test-tool'    : 'load_test.tool',
            'x-load-test-vusers'  : 'load_test.vusers',
            'x-release-version'   : 'release.version',
          };
          for (const [header, attr] of Object.entries(mapping)) {
            const val = headers[header];
            if (val) {
              span.setAttribute(attr, val);
              console.log('[loadTest] set', attr, '=', val, 'on span:', span.name);
            }
          }
        },
      },
    })
  ],
  metricReader: new PeriodicExportingMetricReader({
    exporter: new OTLPMetricExporter(),
  }),
  resourceDetectors: [
    containerDetector,
    envDetector,
    hostDetector,
    osDetector,
    processDetector,
    alibabaCloudEcsDetector,
    awsEksDetector,
    awsEc2Detector,
    gcpDetector,
  ],
});

sdk.start();
