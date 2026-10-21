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

// Promotes x-load-test-* and x-release-version HTTP request headers to span
// attributes on every server-side span so Dynatrace can filter load test traces.
const loadTestSpanProcessor = {
  onStart(span) {
    const headers = span.attributes;
    const mapping = {
      'http.request.header.x-load-test-id'      : 'load_test.id',
      'http.request.header.x-load-test-scenario': 'load_test.scenario',
      'http.request.header.x-load-test-tool'    : 'load_test.tool',
      'http.request.header.x-load-test-vusers'  : 'load_test.vusers',
      'http.request.header.x-release-version'   : 'release.version',
    };
    let promoted = false;
    for (const [src, dest] of Object.entries(mapping)) {
      const val = headers[src];
      if (val !== undefined) {
        span.setAttribute(dest, Array.isArray(val) ? val[0] : val);
        promoted = true;
      }
    }
    if (promoted) {
      console.log('[loadTestSpanProcessor] promoted load test attrs on span:', span.name,
        '| load_test.id:', headers['http.request.header.x-load-test-id'],
        '| release.version:', headers['http.request.header.x-release-version']);
    }
  },
  onEnd() {},
  shutdown() { return Promise.resolve(); },
  forceFlush() { return Promise.resolve(); },
};

const sdk = new opentelemetry.NodeSDK({
  traceExporter: new OTLPTraceExporter(),
  spanProcessors: [loadTestSpanProcessor],
  logRecordProcessors: [new BatchLogRecordProcessor({ exporter: new OTLPLogExporter() })],
  instrumentations: [
    getNodeAutoInstrumentations({
      // disable fs instrumentation to reduce noise
      '@opentelemetry/instrumentation-fs': {
        enabled: false,
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
