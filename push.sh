#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOYMENT_FILE="${SCRIPT_DIR}/k8s/prd/srvapp-deployment.yaml"
export AWS_PROFILE="${AWS_PROFILE:-ih_ecr_prd}"

usage() {
    echo "Usage: $0 [tag]" >&2
    echo "Push the locally built srvapp image to the production ECR repository." >&2
}

if (( $# == 1 )) && [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit 0
fi

if (( $# > 1 )); then
    usage
    exit 2
fi

for command in aws docker; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "Error: required command not found: ${command}" >&2
        exit 1
    fi
done

if [[ ! -f "${DEPLOYMENT_FILE}" ]]; then
    echo "Error: deployment file not found: ${DEPLOYMENT_FILE}" >&2
    exit 1
fi

DEPLOYMENT_IMAGE="$({
    awk '$1 == "image:" { print $2; exit }' "${DEPLOYMENT_FILE}"
} || true)"

if [[ -z "${DEPLOYMENT_IMAGE}" || "${DEPLOYMENT_IMAGE}" != *:* ]]; then
    echo "Error: unable to read the ECR image from ${DEPLOYMENT_FILE}" >&2
    exit 1
fi

ECR_REPOSITORY="${DEPLOYMENT_IMAGE%:*}"
SOURCE_TAG="${DEPLOYMENT_IMAGE##*:}"
TARGET_TAG="${1:-${SOURCE_TAG}}"
REGISTRY="${ECR_REPOSITORY%%/*}"

if [[ ! "${TARGET_TAG}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
    echo "Error: invalid Docker image tag: ${TARGET_TAG}" >&2
    exit 2
fi

if [[ "${REGISTRY}" =~ ^[0-9]+\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com$ ]]; then
    AWS_REGION="${BASH_REMATCH[1]}"
else
    echo "Error: unsupported ECR registry in deployment image: ${REGISTRY}" >&2
    exit 1
fi

SOURCE_IMAGE="${ECR_REPOSITORY}:${SOURCE_TAG}"
TARGET_IMAGE="${ECR_REPOSITORY}:${TARGET_TAG}"

if ! docker image inspect "${SOURCE_IMAGE}" >/dev/null 2>&1; then
    echo "Error: local image not found: ${SOURCE_IMAGE}" >&2
    echo "Build it first with: docker compose -f docker-compose.prd.yml build srvapp" >&2
    exit 1
fi

echo "Logging in to ${REGISTRY} (${AWS_REGION})..."
aws ecr get-login-password --region "${AWS_REGION}" \
    | docker login --username AWS --password-stdin "${REGISTRY}"

if [[ "${TARGET_IMAGE}" != "${SOURCE_IMAGE}" ]]; then
    echo "Tagging ${SOURCE_IMAGE} as ${TARGET_IMAGE}..."
    docker tag "${SOURCE_IMAGE}" "${TARGET_IMAGE}"
fi

echo "Pushing ${TARGET_IMAGE}..."
docker push "${TARGET_IMAGE}"
echo "Pushed ${TARGET_IMAGE}"
