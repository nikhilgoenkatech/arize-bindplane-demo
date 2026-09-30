// Astronomy Shop agent: build metadata -> deploy -> agent-quality validation.
//
// This pipeline never qualifies or rejects the release itself. The
// Validation stage runs the Phoenix golden-dataset experiment against the
// freshly deployed agent and reports the aggregate result to Dynatrace as a
// business event (see arize/arize-experiment.py) — a Site Reliability
// Guardian in Dynatrace is the sole authority that combines this signal with
// the release's live operational telemetry to qualify it.
//
// Assumes this job is configured as "Pipeline script from SCM" against this
// repo, so the checkout Jenkins does before running the pipeline already
// gives every stage the working tree — the Build stage below only reads git
// metadata, it does not re-clone.
//
// New Jenkins credentials needed (secret text):
//   openai-api-key   OpenAI key used by the LLM-as-judge evaluators
// Reused as-is from the easytrade pipeline (same Jenkins credential store,
// same OAuth2 client-credentials flow via getAccessToken() below — nothing
// about Dynatrace auth is reinvented here):
//   aws-access-key, aws-secret-key, aws-session-token
//   client_id, client_secret, client_urn

pipeline {
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
                    container('awscli') {
                        sh '''
                            export AWS_DEFAULT_REGION=${AWS_REGION}
                            aws sts get-caller-identity
                            aws eks update-kubeconfig --region ${AWS_REGION} --name ${EKS_CLUSTER_NAME} --kubeconfig ${KUBECONFIG}
                        '''
                    }
                    container('helm') {
                        sh '''
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
        }

        stage('Validation (Agent Eval)') {
            steps {
                withCredentials([
                    string(credentialsId: 'aws-access-key', variable: 'AWS_ACCESS_KEY_ID'),
                    string(credentialsId: 'aws-secret-key', variable: 'AWS_SECRET_ACCESS_KEY'),
                    string(credentialsId: 'aws-session-token', variable: 'AWS_SESSION_TOKEN'),
                ]) {
                    container('awscli') {
                        sh '''
                            export AWS_DEFAULT_REGION=${AWS_REGION}
                            aws eks update-kubeconfig --region ${AWS_REGION} --name ${EKS_CLUSTER_NAME} --kubeconfig ${KUBECONFIG}
                        '''
                    }
                }

                // Port-forwards run in the helm container (it has kubectl). The python
                // container shares this pod's network namespace, so localhost:6006 /
                // localhost:8010 are reachable from there once these come up.
                container('helm') {
                    sh 'bash arize/port-forward.sh'
                }

                // Same getAccessToken() as the easytrade pipeline — curl needs to run
                // somewhere with it; amazon/aws-cli ships curl, so reuse that container.
                container('awscli') {
                    script {
                        env.DT_API_TOKEN = getAccessToken()
                    }
                }

                container('python') {
                    sh '''
                        pip install --no-cache-dir -r arize/requirements.txt
                        python3 arize/arize-dataset.py

                        RELEASE_VERSION="${RELEASE_ID}" \
                        AGENT_SERVICE_NAME=agent \
                        DT_AUTH_SCHEME=Bearer \
                        python3 arize/arize-experiment.py
                    '''
                }
            }
            post {
                always {
                    container('helm') {
                        sh 'bash arize/port-forward.sh stop || true'
                    }
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
