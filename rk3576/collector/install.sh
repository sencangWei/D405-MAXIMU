#!/usr/bin/env bash
# Install one verified immutable application release and atomically select it.
set -euo pipefail

UMI_SOURCE=$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
UMI_INSTALL_ROOT=${1:?Usage: bash install.sh /absolute/path/to/umi-collector}
case "$UMI_INSTALL_ROOT" in /*) ;; *) echo 'Install path must be absolute' >&2; exit 1;; esac
test "$UMI_INSTALL_ROOT" != /
cd "$UMI_SOURCE"
sha256sum --strict --check SHA256SUMS
bash "$UMI_SOURCE/check-host.sh"
UMI_VERSION=$(python3 -c 'import json; print(json.load(open("release-manifest.json", encoding="utf-8"))["version"])')
[[ "$UMI_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
UMI_TARGET="$UMI_INSTALL_ROOT/releases/$UMI_VERSION"
test ! -e "$UMI_TARGET" || { echo "Version already installed: $UMI_TARGET" >&2; exit 1; }
if [ -e "$UMI_INSTALL_ROOT/current" ] && [ ! -L "$UMI_INSTALL_ROOT/current" ]; then
  echo 'Refusing to replace a non-symlink current path' >&2
  exit 1
fi
mkdir -p -- "$UMI_INSTALL_ROOT/releases"
UMI_STAGE=$(mktemp -d "$UMI_INSTALL_ROOT/releases/.install-XXXXXX")
trap 'test ! -d "$UMI_STAGE" || rm -rf -- "$UMI_STAGE"' EXIT
cp -a -- "$UMI_SOURCE/." "$UMI_STAGE/"
(cd "$UMI_STAGE" && sha256sum --strict --check SHA256SUMS)
chmod 755 \
  "$UMI_STAGE/check-host.sh" \
  "$UMI_STAGE/install.sh" \
  "$UMI_STAGE/bin/recorderctl" \
  "$UMI_STAGE/bin/umi-admin-service" \
  "$UMI_STAGE/bin/umi-preview-service" \
  "$UMI_STAGE/native/bin/umi-record-native" \
  "$UMI_STAGE/native/bin/umi-rsusb-probe"
mv -T -- "$UMI_STAGE" "$UMI_TARGET"
UMI_LINK="$UMI_INSTALL_ROOT/.current-$$"
ln -s -- "$UMI_TARGET" "$UMI_LINK"
mv -Tf -- "$UMI_LINK" "$UMI_INSTALL_ROOT/current"
trap - EXIT
printf 'Installed and selected: %s\n' "$UMI_TARGET"
