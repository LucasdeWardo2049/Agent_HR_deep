#!/bin/bash

############################################################################
#
#    Agno Docker Image Builder (maintainer-only)
#
#    Usage: ./scripts/build_image.sh
#
#    Publishes the official AgentOS image the template family references
#    (the helm chart's default, and the fast path for cloud deploys).
#    Pushes two tags: `latest` and `agno-<pin>` (read from
#    requirements.txt), so deploys can reference the exact runtime.
#
#    Run it from agentos-railway (the family reference) as part of the
#    agno bump ritual, after the verify gates pass.
#
#    Prerequisites:
#      - Docker Buildx installed
#      - Run 'docker buildx create --use' first (unless the containerd
#        image store is enabled — Docker Desktop default these days)
#      - `docker login` with push access to the target org
#
#    Overrides: IMAGE_NAME (default agnohq/agentos), IMAGE_TAG (default
#    latest; the agno-<pin> tag is always added when the pin is found).
#
############################################################################

set -e

CURR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS_ROOT="$(dirname "${CURR_DIR}")"
DOCKER_FILE="Dockerfile"
IMAGE_NAME="${IMAGE_NAME:-agnohq/agentos}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# Colors
ORANGE='\033[38;5;208m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

TAG_ARGS=(-t "${IMAGE_NAME}:${IMAGE_TAG}")
AGNO_PIN="$(sed -nE 's/^agno==(.*)$/\1/p' "${OS_ROOT}/requirements.txt" | head -1)"
if [[ -n "$AGNO_PIN" ]]; then
    TAG_ARGS+=(-t "${IMAGE_NAME}:agno-${AGNO_PIN}")
fi

echo ""
echo -e "    ${ORANGE}▸${NC} ${BOLD}Building + publishing Docker image${NC}"
echo -e "    ${DIM}Image: ${IMAGE_NAME}:${IMAGE_TAG}${AGNO_PIN:+  (+ :agno-${AGNO_PIN})}${NC}"
echo -e "    ${DIM}Platforms: linux/amd64, linux/arm64${NC}"
echo ""

echo -e "    ${DIM}> docker buildx build --platform=linux/amd64,linux/arm64 ${TAG_ARGS[*]} -f ${DOCKER_FILE} ${OS_ROOT} --push${NC}"
docker buildx build --platform=linux/amd64,linux/arm64 "${TAG_ARGS[@]}" -f "${DOCKER_FILE}" "${OS_ROOT}" --push

echo ""
echo -e "    ${BOLD}Done.${NC}"
echo ""
