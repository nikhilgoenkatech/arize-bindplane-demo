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
        PLATFORM_CLIENT        = credentials('vkt_client_id')
        PLATFORM_CLIENT_SECRET = credentials('vkt_client_secret')
        PLATFORM_CLIENT_URN    = credentials('vkt_client_urn')
        DT_ENV_URL             = 'https://vkt82601.apps.dynatrace.com'   // platform API (/platform/...)
        DT_CLASSIC_URL         = 'https://vkt82601.live.dynatrace.com'   // classic API  (/api/v2/...)
        DT_API_TOKEN           = credentials('vkt_dt_api_token')          // classic DT API token for /api/v2/bizevents/ingest

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

                    // Wait for Phoenix to actually accept connections before running evals.
                    sh '''
                        echo "Waiting for Phoenix on localhost:6006..."
                        for i in $(seq 1 15); do
                            if curl -sf http://localhost:6006/healthz > /dev/null 2>&1; then
                                echo "Phoenix ready."
                                break
                            fi
                            echo "  attempt $i/15 — retrying in 2s..."
                            sleep 2
                        done
                    '''

                    sh '''
                        python3 -m pip install --no-cache-dir --break-system-packages -r arize/requirements.txt
                        python3 arize/arize-dataset.py

                        RELEASE_VERSION="${RELEASE_ID}" \
                        AGENT_SERVICE_NAME=agent \
                        DT_ENV_URL="${DT_CLASSIC_URL}" \
                        DT_AUTH_SCHEME=Api-Token \
                        python3 arize/arize-experiment.py
                    '''
                }
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

                    // Each build gets a unique input ID so concurrent pipeline
                    // runs each wait independently — Dynatrace targets the right
                    // build via the proceedUrl / abortUrl in the trigger bizevent.
                    def inputId = "srg-${env.BUILD_ID}"
                    triggerGuardianValidation(inputId)

                    try {
                        // Pipeline blocks here. Dynatrace workflow calls:
                        //   proceedUrl → pipeline continues to Production
                        //   abortUrl   → pipeline fails here (caught below)
                        input(
                            id: inputId,
                            message: "Waiting for Dynatrace SRG verdict on release ${env.RELEASE_ID}",
                            ok: 'Proceed to Production',
                        )
                        sendSdlcEvent([
                            'event.type': 'validation',
                            'event.status': 'finished',
                            'task.name': 'Release Gate (SRG Approval)',
                            'task.outcome': 'Success',
                        ])
                    } catch (err) {
                        sendSdlcEvent([
                            'event.type': 'validation',
                            'event.status': 'finished',
                            'task.name': 'Release Gate (SRG Approval)',
                            'task.outcome': 'Failure',
                        ])
                        error("Dynatrace SRG did not approve release ${env.RELEASE_ID}: ${err.message}")
                    }
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
            curl -sS -X POST "${DT_CLASSIC_URL}/platform/ingest/v1/events.sdlc" \
                -H "Authorization: Bearer ${SDLC_TOKEN}" \
                -H "Content-Type: application/json" \
                -d "${SDLC_BODY}"
            set -x
        '''
    }
}

// Fires the bizevent that triggers the Dynatrace workflow/guardian.
// Includes the Jenkins input callback URLs so the Dynatrace workflow knows
// exactly which build to approve or abort — critical when multiple pipeline
// runs are waiting simultaneously (each has a unique inputId via BUILD_ID).
//
// Dynatrace workflow contract:
//   On guardian pass → POST proceedUrl  (no body required)
//   On guardian fail → POST abortUrl    (no body required)
//
// Both URLs require a Jenkins user/token with Job/Build permission.
void triggerGuardianValidation(String inputId) {
    def proceedUrl = "${env.BUILD_URL}input/${inputId}/proceedEmpty"
    def abortUrl   = "${env.BUILD_URL}input/${inputId}/abort"
    echo "SRG gate input id : ${inputId}"
    echo "  Proceed URL : ${proceedUrl}"
    echo "  Abort URL   : ${abortUrl}"

    def payload = [
        'event.type'         : 'guardian.validation.triggered',
        'event.provider'     : 'jenkins',
        'service'            : 'astronomy-shop-agent',
        'stage'              : 'staging',
        'release.version'    : env.RELEASE_ID,
        'jenkins.job'        : env.JOB_NAME,
        'jenkins.build'      : env.BUILD_ID,
        'jenkins.input.id'   : inputId,
        'jenkins.proceed.url': proceedUrl,
        'jenkins.abort.url'  : abortUrl,
    ]
    def token = getAccessToken()
    def body = writeJSON(json: payload, returnText: true)
    withEnv(["TRIGGER_TOKEN=${token}", "TRIGGER_BODY=${body}"]) {
        sh '''
            set +x
            curl -sS -X POST "${DT_CLASSIC_URL}/api/v2/bizevents/ingest" \
                -H "Authorization: Bearer ${TRIGGER_TOKEN}" \
                -H "Content-Type: application/json" \
                -d "${TRIGGER_BODY}"
            set -x
        '''
    }
}

// Copied verbatim from the easytrade pipeline — same OAuth2 client-credentials
// exchange, same SSO endpoint. Scope extended with openpipeline:events.sdlc:ingest
// for sendSdlcEvent on top of the original bizevents scope.
String getAccessToken() {
    print("Getting OAuth2 token")
    // Debug: print what we're sending (secret stays masked by Jenkins)
    sh(script: '''
        set +x
        echo "[oauth] SSO endpoint   : https://sso.dynatrace.com/sso/oauth2/token"
        echo "[oauth] client_id      : ${PLATFORM_CLIENT}"
        echo "[oauth] client_secret  : (masked)"
        echo "[oauth] client_urn     : ${PLATFORM_CLIENT_URN}"
        set -x
    ''')
    final String tokenResponse = sh(script: '''
        set +x
        curl -vLX POST "https://sso.dynatrace.com/sso/oauth2/token" \
            --header "Content-Type: application/x-www-form-urlencoded" \
            --data-urlencode "grant_type=client_credentials" \
            --data-urlencode "client_id=${PLATFORM_CLIENT}" \
            --data-urlencode "client_secret=${PLATFORM_CLIENT_SECRET}" \
            --data-urlencode "resource=urn:dtaccount:${PLATFORM_CLIENT_URN}" \
            --data-urlencode "scope=storage:buckets:read storage:bizevents:read storage:events:write storage:events:read openpipeline:events.sdlc:ingest"
        set -x
    ''', returnStdout: true).trim()
    echo("[oauth] Raw response: ${tokenResponse}")
    def parsed = readJSON(text: tokenResponse)
    if (!parsed.access_token) {
        error("getAccessToken() failed — response: ${tokenResponse}")
    }
    return parsed.access_token
}
