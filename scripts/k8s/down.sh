#!/usr/bin/env bash
# Tear down the Clinical AI Platform local Kubernetes stack.
#
#   scripts/k8s/down.sh
#
# Deletes the entire `clinical` namespace. WARNING: this DESTROYS THE DATABASE
# (the PVC created by StatefulSet db lives in the namespace and is deleted with
# it). All seeded/demo data is lost. This is the documented "full reset" path —
# see docs/KUBERNETES.md, "Data persistence and full reset".
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

echo "WARNING: this deletes namespace '${NAMESPACE}' INCLUDING the database PVC."
echo "All data in the local Kubernetes database will be permanently lost."
printf "Type 'yes' to continue: "
read -r answer
if [ "${answer}" != "yes" ]; then
    echo "aborted; nothing was deleted."
    exit 0
fi

kubectl --context "${KUBE_CONTEXT}" delete namespace "${NAMESPACE}" --ignore-not-found
kubectl --context "${KUBE_CONTEXT}" wait --for=delete "namespace/${NAMESPACE}" --timeout=180s
echo "namespace '${NAMESPACE}' deleted."
