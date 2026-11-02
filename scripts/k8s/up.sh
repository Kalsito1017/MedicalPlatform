#!/usr/bin/env bash
# Bring the Clinical AI Platform up on the local Kubernetes cluster
# (Docker Desktop Kubernetes). Applies namespace, config, secret, database,
# app Deployment and Service; waits for rollouts; prints the local URL.
#
#   scripts/k8s/up.sh
#
# Prerequisites: image built/tagged as medicalplatform-app:local and
# k8s/secret.yaml generated (scripts/k8s/gen-secret.sh). See docs/KUBERNETES.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
K8S_DIR="${REPO_ROOT}/k8s"
KUBE_CONTEXT="${KUBE_CONTEXT:-docker-desktop}"
NAMESPACE="${NAMESPACE:-clinical}"

# --- Preflight ---------------------------------------------------------------
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
    echo "error: ${K8S_DIR}/secret.yaml not found." >&2
    echo "Generate it with scripts/k8s/gen-secret.sh (or copy k8s/secret.example.yaml" >&2
    echo "and replace every REPLACE_ME value). Never commit k8s/secret.yaml." >&2
    exit 1
fi

# --- Apply -------------------------------------------------------------------
kubectl --context "${KUBE_CONTEXT}" apply \
    -f "${K8S_DIR}/namespace.yaml" \
    -f "${K8S_DIR}/configmap.yaml" \
    -f "${K8S_DIR}/secret.yaml" \
    -f "${K8S_DIR}/postgres.yaml" \
    -f "${K8S_DIR}/app-deployment.yaml" \
    -f "${K8S_DIR}/app-service.yaml"

# --- Wait for rollout --------------------------------------------------------
echo "waiting for the database (StatefulSet db)..."
kubectl --context "${KUBE_CONTEXT}" rollout status statefulset/db \
    -n "${NAMESPACE}" --timeout=300s

echo "waiting for the application (Deployment app)..."
kubectl --context "${KUBE_CONTEXT}" rollout status deployment/app \
    -n "${NAMESPACE}" --timeout=300s

# --- Result ------------------------------------------------------------------
echo
kubectl --context "${KUBE_CONTEXT}" get pods,svc -n "${NAMESPACE}"
echo
echo "App URL: http://localhost:8081"
echo "Next: seed demo data with scripts/k8s/seed.sh (only after the app is Ready)."
