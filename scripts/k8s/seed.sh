#!/usr/bin/env bash
# Seed synthetic demo data into the local Kubernetes stack.
#
#   scripts/k8s/seed.sh
#
# Applies k8s/seed-job.yaml (one-shot Job running
# python /app/scripts/seed_demo_data.py in the app image) and follows its logs.
# Run ONLY after the app Deployment is Ready — migrations run on app start
# (scripts/docker-entrypoint.sh) and the seed needs a migrated schema.
#
# The seed is idempotent: re-running never duplicates rows. If DEMO_*_PASSWORD
# values are missing from k8s/secret.yaml, generated passwords are printed
# ONCE in the log below — copy them out.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
K8S_DIR="${REPO_ROOT}/k8s"
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

if [ ! -f "${K8S_DIR}/secret.yaml" ]; then
    echo "error: ${K8S_DIR}/secret.yaml not found (run scripts/k8s/gen-secret.sh first)." >&2
    exit 1
fi

# Re-runnable: drop any previous job before applying (a Job is immutable).
kubectl --context "${KUBE_CONTEXT}" delete job seed-demo-data \
    -n "${NAMESPACE}" --ignore-not-found --wait=true

kubectl --context "${KUBE_CONTEXT}" apply -f "${K8S_DIR}/seed-job.yaml"

# Wait for the job's pod to appear so log streaming cannot race scheduling.
pod=""
for _ in $(seq 1 30); do
    pod="$(kubectl --context "${KUBE_CONTEXT}" get pods -n "${NAMESPACE}" \
        -l job-name=seed-demo-data \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    [ -n "${pod}" ] && break
    sleep 1
done
if [ -z "${pod}" ]; then
    echo "error: seed job pod did not appear; check: kubectl --context ${KUBE_CONTEXT} describe job seed-demo-data -n ${NAMESPACE}" >&2
    exit 1
fi

kubectl --context "${KUBE_CONTEXT}" logs -f "job/seed-demo-data" -n "${NAMESPACE}"

echo
kubectl --context "${KUBE_CONTEXT}" get job seed-demo-data -n "${NAMESPACE}"
echo "Note: the Job object stays around; next run of this script replaces it."
