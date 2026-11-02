#!/usr/bin/env bash
# Show the state of the local Kubernetes stack: workloads, pods, services,
# storage, per-pod probe configuration/status, and recent events.
#
#   scripts/k8s/status.sh
set -euo pipefail

KUBE_CONTEXT="${KUBE_CONTEXT:-docker-desktop}"
NAMESPACE="${NAMESPACE:-clinical}"

if ! command -v kubectl >/dev/null 2>&1; then
    echo "error: kubectl not found on PATH." >&2
    exit 1
fi

if ! kubectl config get-contexts "${KUBE_CONTEXT}" >/dev/null 2>&1; then
    echo "error: kubectl context '${KUBE_CONTEXT}' not found." >&2
    echo "Enable Kubernetes in Docker Desktop -> Settings -> Kubernetes -> Apply," >&2
    echo "then re-run this script (or set KUBE_CONTEXT to your cluster's context)." >&2
    exit 1
fi

k() {
    kubectl --context "${KUBE_CONTEXT}" -n "${NAMESPACE}" "$@"
}

echo "== Workloads"
k get statefulsets,deployments,jobs 2>/dev/null || echo "(namespace '${NAMESPACE}' not found — run scripts/k8s/up.sh first)"
echo
echo "== Pods"
k get pods -o wide
echo
echo "== Services"
k get svc
echo
echo "== PersistentVolumeClaims"
k get pvc
echo
echo "== Probes and readiness"
for pod in $(k get pods -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true); do
    echo "-- ${pod}"
    k get pod "${pod}" -o jsonpath='{range .status.containerStatuses[*]}   container {.name}: ready={.ready} restarts={.restartCount}{"\n"}{end}'
    k get pod "${pod}" -o jsonpath='{range .spec.containers[*]}{"   "}{.name}{": liveness="}{.livenessProbe.httpGet.path}{.livenessProbe.exec.command}{" every "}{.livenessProbe.periodSeconds}s x{.livenessProbe.failureThreshold}{"; readiness="}{.readinessProbe.httpGet.path}{.readinessProbe.exec.command}{" every "}{.readinessProbe.periodSeconds}s x{.readinessProbe.failureThreshold}{"; startup="}{.startupProbe.httpGet.path}{.startupProbe.exec.command}{" every "}{.startupProbe.periodSeconds}s x{.startupProbe.failureThreshold}{"\n"}{end}'
done
echo
echo "== Recent events"
k get events --sort-by=.lastTimestamp 2>/dev/null | tail -n 15 || true
