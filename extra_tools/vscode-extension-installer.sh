#!/usr/bin/env bash
#
# Download and install HALucinator VSCode extensions from GitHub releases.
# Usage: ./vscode-extension-installer.sh [tag]
#   tag: optional release tag (default: "latest")
#
# Releases: https://github.com/GrammaTech/halucinator-vscode/releases
#
set -e

REPO="GrammaTech/halucinator-vscode"
TAG="${1:-latest}"

# Check VSCode
if ! code -h 2>/dev/null | grep -q "Visual Studio Code"; then
    echo -e "\n\tVisual Studio Code not found. Install it first.\n"
    exit 1
fi

# Check curl
if ! command -v curl >/dev/null 2>&1; then
    echo "Error: 'curl' is required but not installed."
    exit 1
fi

# Resolve the release API URL
if [ "$TAG" = "latest" ]; then
    api_url="https://api.github.com/repos/${REPO}/releases/latest"
else
    api_url="https://api.github.com/repos/${REPO}/releases/tags/${TAG}"
fi

echo "Fetching release info from ${api_url}"
release_json=$(curl -fsSL "$api_url")

# Extract the list of .vsix asset download URLs
vsix_urls=$(echo "$release_json" \
    | grep -oE '"browser_download_url"[[:space:]]*:[[:space:]]*"[^"]*\.vsix"' \
    | sed -E 's/.*"([^"]+)"$/\1/')

if [ -z "$vsix_urls" ]; then
    echo "Error: no .vsix assets found in release '${TAG}' of ${REPO}"
    exit 1
fi

# Download and install
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

installed_list=$(code --list-extensions --show-versions 2>/dev/null || true)

for url in $vsix_urls; do
    fname=$(basename "$url")
    echo "Downloading ${fname}"
    curl -fsSL -o "${tmp_dir}/${fname}" "$url"

    # Best-effort idempotency: skip if the exact file name (which encodes
    # the version) matches an already-installed extension.
    version_suffix="${fname%.vsix}"
    version_suffix="${version_suffix##*-}"
    if echo "$installed_list" | grep -q "@${version_suffix}$"; then
        echo "  ${fname} already installed, skipping"
        continue
    fi

    echo "Installing ${fname}"
    code --install-extension "${tmp_dir}/${fname}"
done

echo -e "\nDone."
