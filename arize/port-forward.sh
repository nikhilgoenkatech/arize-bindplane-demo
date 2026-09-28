#!/usr/bin/env bash
# Start port-forwards for Phoenix and agent in the background.
# Usage: source arize/port-forward.sh        (to also export env vars)
#        bash arize/port-forward.sh           (port-forwards only)
# Stop:  bash arize/port-forward.sh stop

PHOENIX_PF_PID_FILE="/tmp/phoenix-pf.pid"
AGENT_PF_PID_FILE="/tmp/agent-pf.pid"

stop() {
    for pid_file in "$PHOENIX_PF_PID_FILE" "$AGENT_PF_PID_FILE"; do
        if [[ -f "$pid_file" ]]; then
            pid=$(cat "$pid_file")
            kill "$pid" 2>/dev/null && echo "Stopped PID $pid" || echo "PID $pid already gone"
            rm -f "$pid_file"
        fi
    done
}

if [[ "${1}" == "stop" ]]; then
    stop
    exit 0
fi

stop  # kill any existing port-forwards before starting fresh

echo "Starting port-forwards..."
kubectl port-forward svc/phoenix 6006:6006 -n phoenix &>/tmp/pf-phoenix.log &
echo $! > "$PHOENIX_PF_PID_FILE"
echo "  Phoenix  → http://localhost:6006  (PID $(cat $PHOENIX_PF_PID_FILE))"

kubectl port-forward svc/agent 8010:8010 -n llm-obs-demo &>/tmp/pf-agent.log &
echo $! > "$AGENT_PF_PID_FILE"
echo "  Agent    → http://localhost:8010  (PID $(cat $AGENT_PF_PID_FILE))"

# Give kubectl a moment to establish connections
sleep 2

# Export env vars when sourced
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    export ARIZE_ENDPOINT=http://localhost:6006
    export AGENT_ENDPOINT=http://localhost:8010
    echo "Env vars set: ARIZE_ENDPOINT, AGENT_ENDPOINT"
fi

echo "Done. Logs: /tmp/pf-phoenix.log  /tmp/pf-agent.log"
echo "Stop with: bash arize/port-forward.sh stop"
