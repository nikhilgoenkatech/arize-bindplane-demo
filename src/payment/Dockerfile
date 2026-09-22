# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM ghcr.io/open-telemetry/opentelemetry-operator/autoinstrumentation-nodejs:0.78.0@sha256:690e9e063686b4d5f368039872d72b1d379367906195fb2b6881500d044e3f55 AS otelagent

# -----------------------------------------------------------------------------

FROM docker.io/library/node:26.8.2-slim@sha256:f7bb8247fdb16250dbec7fd0e24f091c6f5f0a29d256f3aef5816a7a369166b2 AS builder

WORKDIR /usr/src/app/

COPY ./src/payment/package.json package.json
COPY ./src/payment/package-lock.json package-lock.json

RUN npm ci --omit=dev

# -----------------------------------------------------------------------------

FROM gcr.io/distroless/nodejs22-debian12:nonroot@sha256:13593b7570658e8477de39e2f4a1dd25db2f836d68a0ba771251572d23bb4f8e

WORKDIR /usr/src/app/

COPY --from=builder /usr/src/app/node_modules/ node_modules/

# Path and NODE_OPTIONS value match what the OpenTelemetry Operator injects, so a
# statically built image and an operator-injected pod behave identically. Changing
# either breaks that equivalence.
COPY --from=otelagent /autoinstrumentation /otel-auto-instrumentation-nodejs

COPY ./pb/demo.proto demo.proto

COPY ./src/payment/charge.js charge.js
COPY ./src/payment/index.js index.js
COPY ./src/payment/logger.js logger.js

ARG PAYMENT_PORT=50051
EXPOSE ${PAYMENT_PORT}

ENV NODE_OPTIONS="--require /otel-auto-instrumentation-nodejs/autoinstrumentation.js"

CMD ["index.js"]
