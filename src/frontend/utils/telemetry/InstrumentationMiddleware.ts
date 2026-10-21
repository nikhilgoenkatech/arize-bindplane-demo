// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import { NextApiHandler } from 'next';
import {context, Exception, Span, SpanStatusCode, trace} from '@opentelemetry/api';
import {
  ATTR_HTTP_REQUEST_METHOD,
  ATTR_HTTP_RESPONSE_STATUS_CODE,
  ATTR_URL_PATH,
  SemanticAttributes,
} from '@opentelemetry/semantic-conventions';
import logger from './logger';

const LOAD_TEST_HEADERS: Record<string, string> = {
  'x-load-test-id'      : 'load_test.id',
  'x-load-test-scenario': 'load_test.scenario',
  'x-load-test-tool'    : 'load_test.tool',
  'x-load-test-vusers'  : 'load_test.vusers',
  'x-release-version'   : 'release.version',
};

const InstrumentationMiddleware = (handler: NextApiHandler): NextApiHandler => {
  return async (request, response) => {
    const span = trace.getSpan(context.active()) as Span;
    const urlPath = request.url?.split('?', 1)[0];

    for (const [header, attrKey] of Object.entries(LOAD_TEST_HEADERS)) {
      const value = request.headers[header];
      logger.info({ header, value: value ?? null }, 'load-test-header-check');
      if (value) span.setAttribute(attrKey, value as string);
    }

    let httpStatus = 200;
    try {
      await runWithSpan(span, async () => handler(request, response));
      httpStatus = response.statusCode;
      logger.info(
        {
          [ATTR_HTTP_REQUEST_METHOD]: request.method,
          [ATTR_URL_PATH]: urlPath,
          [ATTR_HTTP_RESPONSE_STATUS_CODE]: httpStatus,
        },
        'API request completed'
      );
    } catch (error) {
      span.recordException(error as Exception);
      span.setStatus({ code: SpanStatusCode.ERROR });
      httpStatus = 500;
      logger.error(
        {
          err: error,
          [ATTR_HTTP_REQUEST_METHOD]: request.method,
          [ATTR_URL_PATH]: urlPath,
          [ATTR_HTTP_RESPONSE_STATUS_CODE]: httpStatus,
        },
        'API request failed'
      );
      throw error;
    } finally {
      span.setAttribute(SemanticAttributes.HTTP_STATUS_CODE, httpStatus);
    }
  };
};

async function runWithSpan(parentSpan: Span, fn: () => Promise<unknown>) {
  const ctx = trace.setSpan(context.active(), parentSpan);
  return await context.with(ctx, fn);
}

export default InstrumentationMiddleware;
