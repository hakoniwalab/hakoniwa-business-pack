#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNAME_S="$(uname -s)"

case "${UNAME_S}" in
  Darwin)
    exec bash "${SCRIPT_DIR}/doctor-mac.bash" "$@"
    ;;
  Linux)
    echo "Hakoniwa Business Pack doctor"
    echo "platform: Linux"
    echo
    echo "FAIL  Linux doctor is not implemented yet."
    echo "      Add tools/doctor-linux.bash when Linux Recipes are validated."
    exit 1
    ;;
  *)
    echo "Hakoniwa Business Pack doctor"
    echo "platform: ${UNAME_S}"
    echo
    echo "FAIL  Unsupported platform: ${UNAME_S}"
    exit 1
    ;;
esac
