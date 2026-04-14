#!/usr/bin/env bash
#
# Install HALucinator VSCode extensions from a Docker image.
# Usage: ./vscode-extension-installer.sh <docker_image>
#
set -e

VSIX_DIR="/halucinator/vsix_files"

# Extension name -> vsix filename (without .vsix suffix)
declare -A EXTENSIONS=(
    [grammatech.gview-extension]="gview-extension-0.0.3"
    [grammatech.gtirb-vscode]="gtirb-vscode-0.0.10"
    [grammatech.halucinator-vscode]="halucinator-vscode-0.0.8"
    [grammatech.halucinator-project-creator]="halucinator-project-creator-0.0.13"
)

if [ "$#" -ne 1 ]; then
    echo -e "\n\tUsage: $0 <docker_image>\n"
    exit 1
fi
DOCKER_IMAGE=$1

# Check VSCode
if ! code -h 2>/dev/null | grep -q "Visual Studio Code"; then
    echo -e "\n\tVisual Studio Code not found. Install it first.\n"
    exit 1
fi

# Start a temporary container to extract vsix files
echo "Extracting vsix files from ${DOCKER_IMAGE}"
docker run -dt --rm --name halucinator-tmp --network=none "$DOCKER_IMAGE"

for ext_id in "${!EXTENSIONS[@]}"; do
    vsix="${EXTENSIONS[$ext_id]}"
    docker cp "halucinator-tmp:${VSIX_DIR}/${vsix}.vsix" .

    installed=$(code --list-extensions --show-versions 2>/dev/null | grep "$ext_id" || true)
    if [ -z "$installed" ]; then
        echo "Installing ${ext_id}"
        code --install-extension "${vsix}.vsix"
    elif [[ "$installed" == *"${vsix##*-}"* ]]; then
        echo "${ext_id} is already up to date"
    else
        echo "${ext_id} version mismatch (installed: ${installed})"
        echo "  Consider uninstalling and re-running this script."
    fi
done

docker stop halucinator-tmp
echo -e "\nDone."
