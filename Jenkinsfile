// Astronomy Shop agent: build metadata -> deploy -> agent-quality validation.
//
// This pipeline never qualifies or rejects the release itself. The
// Validation stage runs the Phoenix golden-dataset experiment against the
// freshly deployed agent and reports the aggregate result to Dynatrace as a
// business event (see arize/arize-experiment.py) — a Site Reliability
// Guardian in Dynatrace is the sole authority that combines this signal with
// the release's live operational telemetry to qualify it.
//
// The Build stage checks out this repo explicitly (this Jenkins job does not
// do an implicit SCM checkout before the pipeline runs — same as easytrade).
//
// New Jenkins credentials needed (secret text):
//   openai-api-key   OpenAI key used by the LLM-as-judge evaluators
// Reused as-is from the easytrade pipeline (same Jenkins credential store,
// same OAuth2 client-credentials flow via getAccessToken() below — nothing
// about Dynatrace auth is reinvented here):
//   aws-access-key, aws-secret-key, aws-session-token
//   client_id, client_secret, client_urn
//   GITCREDENTIALS (used for the repo checkout below)

pipeline {
    parameters {
        booleanParam(
            name: 'AI_EXTRA_TOOL_CALL',
            defaultValue: false,
            description: 'Force the agent to call an unnecessary extra tool (aiExtraToolCall demo flag) — turn on for the degraded-agent demo scenario, leave off for the normal one'
        )
    }

    // No Kubernetes cloud is configured on this Jenkins, so yamlFile is inert
    // here (confirmed: builds run directly on the "Jenkins" controller node,
    // not in a pod) — kept only to match the easytrade pipeline's agent
    // declaration exactly, same as there.
    agent {
        any {
            idleMinutes '0'
            yamlFile 'pipeline/jenkins/pod.yaml'
        }
    }

    environment {
        AWS_REGION       = 'us-east-2'
        EKS_CLUSTER_NAME = 'myekscluster'
        K8S_NAMESPACE    = 'llm-obs-demo'
        KUBECONFIG       = "${WORKSPACE}/.kube/config"

        // Same OAuth2 client-credentials app + same Dynatrace tenant already
        // used by the easytrade pipeline's getAccessToken()/ingestBizEvents().
        PLATFORM_CLIENT        = credentials('client_id')
        PLATFORM_CLIENT_SECRET = credentials('client_secret')
        PLATFORM_CLIENT_URN    = credentials('client_urn')
        DT_ENV_URL             = 'https://ykd61701.sprint.dynatracelabs.com'

        // How long to wait before the guardian's evaluation could possibly be done,
        // and how often to re-check after that. Tune once you know how long your
        // guardian's own objectives actually take to evaluate.
        SRG_INITIAL_DELAY_SECONDS = '60'
        SRG_POLL_INTERVAL_SECONDS = '30'
        SRG_MAX_POLL_ATTEMPTS     = '10'

        OPENAI_API_KEY = credentials('openai-api-key')
    }

    stages {

        stage('Build') {
            steps {
                script {
                    sendSdlcEvent([
                        'event.type': 'build',
                        'event.status': 'started',
                        'task.name': 'Build',
                    ])

                    git branch: 'main',
                        credentialsId: 'GITCREDENTIALS',
                        url: 'https://github.com/nikhilgoenkatech/arize-bindplane-demo.git'

                    env.TAG = sh(script: 'git log --pretty=format:"%h" -n 1', returnStdout: true).trim()
                    env.GIT_BRANCH = sh(script: 'git rev-parse --abbrev-ref HEAD', returnStdout: true).trim()
                    env.RELEASE_ID = "1.0.${BUILD_ID}"
                    echo "Release ${env.RELEASE_ID} — commit ${env.TAG} on ${env.GIT_BRANCH}"
                }
            }
            post {
                success {
                    script {
                        sendSdlcEvent([
                            'event.type': 'build',
                            'event.status': 'finished',
                            'task.name': 'Build',
                            'task.outcome': 'Success',
                            'artifact.id': 'astronomy-shop-agent',
                            'artifact.version': env.RELEASE_ID,
                            'vcs.repository.name': 'arize-bindplane-demo',
                            'vcs.ref.head.name': env.GIT_BRANCH,
                            'vcs.ref.head.revision': env.TAG,
                        ])
                    }
                }
                failure {
                    script {
                        sendSdlcEvent([
                            'event.type': 'build',
                            'event.status': 'finished',
                            'task.name': 'Build',
                            'task.outcome': 'Failure',
                        ])
                    }
                }
            }
        }

        stage('Deploy') {
            steps {
                script {
                    sendSdlcEvent([
                        'event.type': 'deployment',
                        'event.status': 'started',
                        'task.name': 'Deploy',
                        'cicd.deployment.name': 'astronomy-shop-agent',
                        'cicd.deployment.namespace': env.K8S_NAMESPACE,
                        'cicd.deployment.release_stage': 'staging',
                    ])
                }
                withCredentials([
                    string(credentialsId: 'aws-access-key', variable: 'AWS_ACCESS_KEY_ID'),
                    string(credentialsId: 'aws-secret-key', variable: 'AWS_SECRET_ACCESS_KEY'),
                    string(credentialsId: 'aws-session-token', variable: 'AWS_SESSION_TOKEN'),
                ]) {
                    sh '''
                        export AWS_DEFAULT_REGION=${AWS_REGION}
                        aws sts get-caller-identity
                        aws eks update-kubeconfig --region ${AWS_REGION} --name ${EKS_CLUSTER_NAME} --kubeconfig ${KUBECONFIG}

                        helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
                        helm repo update

                        # env[11]/env[12] here must match the DT_RELEASE_VERSION/
                        # DT_RELEASE_BUILD_VERSION positions in k8s-values-bindplane.yaml's
                        # components.agent.env list — update both places together.
                        helm upgrade --install otel-demo open-telemetry/opentelemetry-demo \
                          -f k8s-values-bindplane.yaml \
                          --set components.agent.env[11].value="${RELEASE_ID}" \
                          --set components.agent.env[12].value="${TAG}" \
                          -n ${K8S_NAMESPACE} --create-namespace

                        # NOTE: assumes the chart names this deployment exactly "agent" —
                        # verify with `kubectl get deployment -n ${K8S_NAMESPACE}` if this fails.
                        kubectl -n ${K8S_NAMESPACE} rollout status deployment/agent --timeout=300s

                        # The published chart's flagd-config ConfigMap is generated purely from
                        # a file bundled inside the chart package itself (no Helm values hook),
                        # so it's reset to the chart's own defaults on every helm upgrade above —
                        # this step has to reapply our copy (with this run's flag value patched
                        # in) every single time, not just once.
                        python3 -c "
import json
with open('src/flagd/demo.flagd.json') as f:
    data = json.load(f)
data['flags']['aiExtraToolCall']['defaultVariant'] = 'on' if '${AI_EXTRA_TOOL_CALL}' == 'true' else 'off'
with open('/tmp/demo.flagd.json', 'w') as f:
    json.dump(data, f, indent=2)
"
                        kubectl create configmap flagd-config \
                          --from-file=demo.flagd.json=/tmp/demo.flagd.json \
                          -n ${K8S_NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -

                        # NOTE: assumes the chart names this deployment exactly "flagd" —
                        # verify with `kubectl get deployment -n ${K8S_NAMESPACE}` if this fails.
                        # A rollout restart is required: the pod's already-mounted ConfigMap
                        # volume doesn't pick up new content without one.
                        kubectl -n ${K8S_NAMESPACE} rollout restart deployment/flagd
                        kubectl -n ${K8S_NAMESPACE} rollout status deployment/flagd --timeout=120s
                    '''
                }
            }
            post {
                success {
                    script {
                        sendSdlcEvent([
                            'event.type': 'deployment',
                            'event.status': 'finished',
                            'task.name': 'Deploy',
                            'task.outcome': 'Success',
                            'cicd.deployment.status': 'succeeded',
                            'cicd.deployment.name': 'astronomy-shop-agent',
                            'cicd.deployment.namespace': env.K8S_NAMESPACE,
                            'cicd.deployment.release_stage': 'staging',
                            'artifact.version': env.RELEASE_ID,
                        ])
                    }
                }
                failure {
                    script {
                        sendSdlcEvent([
                            'event.type': 'deployment',
                            'event.status': 'finished',
                            'task.name': 'Deploy',
                            'task.outcome': 'Failure',
                            'cicd.deployment.status': 'failed',
                            'cicd.deployment.name': 'astronomy-shop-agent',
                            'cicd.deployment.namespace': env.K8S_NAMESPACE,
                        ])
                    }
                }
            }
        }

        stage('Validation (Agent Eval)') {
            steps {
                script {
                    sendSdlcEvent([
                        'event.type': 'validation',
                        'event.status': 'started',
                        'task.name': 'Validation (Agent Eval)',
                    ])
                }

                // kubectl (via the kubeconfig aws eks update-kubeconfig writes) calls out to
                // `aws eks get-token` on every invocation rather than embedding static
                // credentials — so update-kubeconfig AND the kubectl port-forward calls
                // inside port-forward.sh both need AWS creds present, hence one shared block.
                withCredentials([
                    string(credentialsId: 'aws-access-key', variable: 'AWS_ACCESS_KEY_ID'),
                    string(credentialsId: 'aws-secret-key', variable: 'AWS_SECRET_ACCESS_KEY'),
                    string(credentialsId: 'aws-session-token', variable: 'AWS_SESSION_TOKEN'),
                ]) {
                    sh '''
                        export AWS_DEFAULT_REGION=${AWS_REGION}
                        aws eks update-kubeconfig --region ${AWS_REGION} --name ${EKS_CLUSTER_NAME} --kubeconfig ${KUBECONFIG}
                    '''

                    // JENKINS_NODE_COOKIE=dontKillMe stops Jenkins' process-tree killer from
                    // reaping the backgrounded kubectl port-forward processes once this step
                    // ends — without it they die before the next steps can reach Phoenix/agent.
                    // The AWS creds only need to be present at launch — kubectl's exec-plugin
                    // caches/refreshes tokens from the env this backgrounded process already has.
                    sh 'JENKINS_NODE_COOKIE=dontKillMe bash arize/port-forward.sh'
                }

                script {
                    env.DT_API_TOKEN = getAccessToken()
                }

                sh '''
                    python3 -m pip install --no-cache-dir --break-system-packages -r arize/requirements.txt
                    python3 arize/arize-dataset.py

                    RELEASE_VERSION="${RELEASE_ID}" \
                    AGENT_SERVICE_NAME=agent \
                    DT_AUTH_SCHEME=Bearer \
                    python3 arize/arize-experiment.py
                '''
            }
            post {
                always {
                    sh 'bash arize/port-forward.sh stop || true'
                }
                success {
                    script {
                        sendSdlcEvent([
                            'event.type': 'validation',
                            'event.status': 'finished',
                            'task.name': 'Validation (Agent Eval)',
                            'task.outcome': 'Success',
                        ])
                    }
                }
                failure {
                    script {
                        sendSdlcEvent([
                            'event.type': 'validation',
                            'event.status': 'finished',
                            'task.name': 'Validation (Agent Eval)',
                            'task.outcome': 'Failure',
                        ])
                    }
                }
            }
        }

        stage('Release Gate (SRG Approval)') {
            steps {
                script {
                    sendSdlcEvent([
                        'event.type': 'validation',
                        'event.status': 'started',
                        'task.name': 'Release Gate (SRG Approval)',
                    ])

                    triggerGuardianValidation()
                    def verdict = pollForGuardianResult()
                    echo "Guardian verdict for release ${env.RELEASE_ID}: ${verdict}"

                    if (verdict != 'pass') {
                        sendSdlcEvent([
                            'event.type': 'validation',
                            'event.status': 'finished',
                            'task.name': 'Release Gate (SRG Approval)',
                            'task.outcome': 'Failure',
                        ])
                        error("Dynatrace Site Reliability Guardian did not approve release ${env.RELEASE_ID} (verdict: ${verdict})")
                    }

                    sendSdlcEvent([
                        'event.type': 'validation',
                        'event.status': 'finished',
                        'task.name': 'Release Gate (SRG Approval)',
                        'task.outcome': 'Success',
                    ])
                }
            }
        }

        // Demo-only: this is a narrative beat, not a real deployment. There is no
        // separate production environment/cluster in this setup — the point is to
        // show the shape of the story (guardian approves -> release proceeds),
        // not to actually stand up a second environment.
        stage('Proceed to Production') {
            steps {
                script {
                    sendSdlcEvent([
                        'event.type': 'deployment',
                        'event.status': 'started',
                        'task.name': 'Proceed to Production',
                        'cicd.deployment.name': 'astronomy-shop-agent',
                        'cicd.deployment.release_stage': 'production',
                    ])

                    echo "Release ${env.RELEASE_ID} approved by Dynatrace SRG -- proceeding to production."

                    sendSdlcEvent([
                        'event.type': 'deployment',
                        'event.status': 'finished',
                        'task.name': 'Proceed to Production',
                        'task.outcome': 'Success',
                        'cicd.deployment.status': 'succeeded',
                        'cicd.deployment.name': 'astronomy-shop-agent',
                        'cicd.deployment.release_stage': 'production',
                        'artifact.version': env.RELEASE_ID,
                    ])
                }
            }
        }
    }
}

// Sends one SDLC event (task or pipeline, per Dynatrace's semantic dictionary:
// https://docs.dynatrace.com/docs/semantic-dictionary/model/sdlc-events) to the
// built-in ingest endpoint. Fetches its own fresh token per call rather than
// reusing one across the pipeline's lifetime, since Deploy/Validation can run
// long enough for an earlier token to expire — same reasoning as why
// getAccessToken() is called fresh before every ingestBizEvents() in easytrade.
// Requires openpipeline:events.sdlc:ingest scope on the OAuth client; if this
// 403s, that scope needs to be granted to the client in Dynatrace's IAM first —
// not something this pipeline can grant itself.
void sendSdlcEvent(Map fields) {
    def payload = [
        'event.provider'          : 'jenkins',
        'cicd.pipeline.id'        : env.JOB_NAME,
        'cicd.pipeline.run.id'    : env.BUILD_ID,
        'cicd.pipeline.run.url.full': env.BUILD_URL,
    ] + fields

    def token = getAccessToken()
    def body = writeJSON(json: payload, returnText: true)
    withEnv(["SDLC_TOKEN=${token}", "SDLC_BODY=${body}"]) {
        sh '''
            set +x
            curl -sS -X POST "${DT_ENV_URL}/platform/ingest/v1/events.sdlc" \
                -H "Authorization: Bearer ${SDLC_TOKEN}" \
                -H "Content-Type: application/json" \
                -d "${SDLC_BODY}"
            set -x
        '''
    }
}

// Fires the bizevent that triggers the Dynatrace workflow/guardian configured
// on the Dynatrace side (that workflow config is out of scope here). Contract:
// event.type == "guardian.validation.triggered", correlated on release.version
// with both the arize-experiment.py bizevent and the result event the workflow
// is expected to emit back (event.type == "guardian.validation.result",
// same release.version, a "result" field of "pass"/"fail").
void triggerGuardianValidation() {
    def payload = [
        'event.type'      : 'guardian.validation.triggered',
        'event.provider'  : 'jenkins',
        'service'         : 'astronomy-shop-agent',
        'stage'           : 'staging',
        'release.version' : env.RELEASE_ID,
    ]
    def token = getAccessToken()
    def body = writeJSON(json: payload, returnText: true)
    withEnv(["TRIGGER_TOKEN=${token}", "TRIGGER_BODY=${body}"]) {
        sh '''
            set +x
            curl -sS -X POST "${DT_ENV_URL}/api/v2/bizevents/ingest" \
                -H "Authorization: Bearer ${TRIGGER_TOKEN}" \
                -H "Content-Type: application/json" \
                -d "${TRIGGER_BODY}"
            set -x
        '''
    }
}

// Polls Grail via DQL for the guardian's result event (see triggerGuardianValidation
// for the expected contract) until found or SRG_MAX_POLL_ATTEMPTS is exhausted.
// Returns "pass", "fail", or "timeout". Waits SRG_INITIAL_DELAY_SECONDS before the
// first check (the guardian can't possibly be done before its own evaluation
// window elapses, so there's no point checking sooner), then re-checks every
// SRG_POLL_INTERVAL_SECONDS.
String pollForGuardianResult() {
    int initialDelay = (env.SRG_INITIAL_DELAY_SECONDS ?: '60') as int
    int pollInterval = (env.SRG_POLL_INTERVAL_SECONDS ?: '30') as int
    int maxAttempts = (env.SRG_MAX_POLL_ATTEMPTS ?: '10') as int

    echo "Waiting ${initialDelay}s before checking for a guardian result..."
    sleep(time: initialDelay, unit: 'SECONDS')

    def dql = "fetch bizevents | filter event.type == \"guardian.validation.result\" and " +
        "`release.version` == \"${env.RELEASE_ID}\" | limit 1"

    for (int attempt = 1; attempt <= maxAttempts; attempt++) {
        def token = getAccessToken()
        def queryBody = writeJSON(json: ['query': dql], returnText: true)
        String response
        withEnv(["POLL_TOKEN=${token}", "POLL_BODY=${queryBody}"]) {
            response = sh(script: '''
                set +x
                curl -sS -X POST "${DT_ENV_URL}/platform/storage/query/v1/query:execute" \
                    -H "Authorization: Bearer ${POLL_TOKEN}" \
                    -H "Content-Type: application/json" \
                    -d "${POLL_BODY}"
                set -x
            ''', returnStdout: true).trim()
        }

        def parsed = readJSON(text: response)
        def records = parsed?.result?.records
        if (records && records.size() > 0) {
            return records[0]['result']
        }

        echo "Guardian result not yet available (attempt ${attempt}/${maxAttempts})"
        if (attempt < maxAttempts) {
            sleep(time: pollInterval, unit: 'SECONDS')
        }
    }
    return 'timeout'
}

// Copied verbatim from the easytrade pipeline — same OAuth2 client-credentials
// exchange, same SSO endpoint. Do not fork this per-app; if the endpoint ever
// changes it should change for both pipelines at once. Scope was widened here
// (openpipeline:events.sdlc:ingest, storage:events:read added) to cover
// sendSdlcEvent's and pollForGuardianResult's needs on top of the original
// bizevents scope — that's additive, not a behavior change for the existing
// bizevents usage.
String getAccessToken() {
    print("Getting OAuth2 token")
    final String tokenResponse = sh(script: '''
        set +x
        curl -sLX POST "https://sso-sprint.dynatracelabs.com/sso/oauth2/token" \
            --header "Content-Type: application/x-www-form-urlencoded" \
            --data-urlencode "grant_type=client_credentials" \
            --data-urlencode "client_id=${PLATFORM_CLIENT}" \
            --data-urlencode "client_secret=${PLATFORM_CLIENT_SECRET}" \
            --data-urlencode "resource=urn:dtaccount:${PLATFORM_CLIENT_URN}" \
            --data-urlencode "scope=storage:buckets:read storage:bizevents:read storage:events:write storage:events:read openpipeline:events.sdlc:ingest"
        set -x
    ''', returnStdout: true).trim()
    return readJSON(text: tokenResponse).access_token
}
