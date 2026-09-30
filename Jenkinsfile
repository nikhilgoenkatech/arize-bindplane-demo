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

        OPENAI_API_KEY = credentials('openai-api-key')
    }

    stages {

        stage('Build') {
            steps {
                script {
                    git branch: 'main',
                        credentialsId: 'GITCREDENTIALS',
                        url: 'https://github.com/nikhilgoenkatech/arize-bindplane-demo.git'

                    env.TAG = sh(script: 'git log --pretty=format:"%h" -n 1', returnStdout: true).trim()
                    env.GIT_BRANCH = sh(script: 'git rev-parse --abbrev-ref HEAD', returnStdout: true).trim()
                    env.RELEASE_ID = "1.0.${BUILD_ID}"
                    echo "Release ${env.RELEASE_ID} — commit ${env.TAG} on ${env.GIT_BRANCH}"
                }
            }
        }

        stage('Deploy') {
            steps {
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

                        helm upgrade --install otel-demo open-telemetry/opentelemetry-demo \
                          -f k8s-values-bindplane.yaml \
                          -n ${K8S_NAMESPACE} --create-namespace

                        # NOTE: assumes the chart names this deployment exactly "agent" —
                        # verify with `kubectl get deployment -n ${K8S_NAMESPACE}` if this fails.
                        kubectl -n ${K8S_NAMESPACE} set env deployment/agent \
                          DT_RELEASE_VERSION=${RELEASE_ID} \
                          DT_RELEASE_PRODUCT=astronomy-shop-agent \
                          DT_RELEASE_STAGE=staging \
                          DT_RELEASE_BUILD_VERSION=${TAG}

                        kubectl -n ${K8S_NAMESPACE} rollout status deployment/agent --timeout=300s
                    '''
                }
            }
        }

        stage('Validation (Agent Eval)') {
            steps {
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
            }
        }
    }
}

// Copied verbatim from the easytrade pipeline — same OAuth2 client-credentials
// exchange, same scope, same SSO endpoint. Do not fork this per-app; if the
// scope or endpoint ever changes it should change for both pipelines at once.
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
            --data-urlencode "scope=storage:buckets:read storage:bizevents:read storage:events:write"
        set -x
    ''', returnStdout: true).trim()
    return readJSON(text: tokenResponse).access_token
}
