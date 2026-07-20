#!/usr/bin/env bash
set -u

echo "Hakoniwa Business Pack doctor"
echo "platform: macOS"
echo "root: $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo

FAILURES=0
WARNINGS=0

ok() {
  echo "OK    $*"
}

warn() {
  echo "WARN  $*"
  WARNINGS=$((WARNINGS + 1))
}

fail() {
  echo "FAIL  $*"
  FAILURES=$((FAILURES + 1))
}

info() {
  echo "INFO  $*"
}

check_cmd() {
  local name="$1"
  local required="${2:-1}"
  local path
  path="$(command -v "${name}" 2>/dev/null || true)"
  if [[ -n "${path}" ]]; then
    ok "${name} found: ${path}"
  elif [[ "${required}" == "1" ]]; then
    fail "${name} not found in PATH"
  else
    warn "${name} not found in PATH"
  fi
}

check_path() {
  local path="$1"
  local label="$2"
  local required="${3:-1}"
  if [[ -e "${path}" ]]; then
    ok "${label}: ${path}"
  elif [[ "${required}" == "1" ]]; then
    fail "${label} missing: ${path}"
  else
    warn "${label} missing: ${path}"
  fi
}

find_python312() {
  if [[ -n "${HAKO_PYTHON:-}" ]]; then
    printf '%s\n' "${HAKO_PYTHON}"
    return
  fi
  if [[ -x "${HOME}/.pyenv/shims/python3.12" ]]; then
    printf '%s\n' "${HOME}/.pyenv/shims/python3.12"
    return
  fi
  if command -v pyenv >/dev/null 2>&1; then
    local pyenv_python
    pyenv_python="$(pyenv which python3.12 2>/dev/null || true)"
    if [[ -n "${pyenv_python}" && -x "${pyenv_python}" ]]; then
      printf '%s\n' "${pyenv_python}"
      return
    fi
  fi
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
}

check_python_runtime() {
  local py="$1"
  if [[ -z "${py}" ]]; then
    fail "Python 3.12 not found. Set HAKO_PYTHON to the intended interpreter."
    return
  fi

  if ! "${py}" - <<'PY' >/tmp/hakoniwa-business-pack-python-version.txt 2>&1
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)
PY
  then
    fail "Python interpreter is not 3.12: ${py} ($(cat /tmp/hakoniwa-business-pack-python-version.txt 2>/dev/null || true))"
    return
  fi
  ok "Python 3.12 found: ${py} ($(cat /tmp/hakoniwa-business-pack-python-version.txt))"

  if "${py}" -c "import hakopy" >/tmp/hakoniwa-business-pack-hakopy.txt 2>&1; then
    ok "hakopy import works for ${py}"
  else
    fail "hakopy import failed for ${py}. Install hakoniwa-core-pro into this same Python environment."
    sed 's/^/      /' /tmp/hakoniwa-business-pack-hakopy.txt
  fi

  if "${py}" -c "import hakoniwa_pdu" >/tmp/hakoniwa-business-pack-pdu.txt 2>&1; then
    ok "hakoniwa_pdu import works for ${py}"
  else
    fail "hakoniwa_pdu import failed for ${py}. Run: ${py} -m pip install hakoniwa-pdu"
    sed 's/^/      /' /tmp/hakoniwa-business-pack-pdu.txt
  fi

  if "${py}" -c "import hakoniwa_pdu.apps.launcher.hako_launcher" >/tmp/hakoniwa-business-pack-launcher.txt 2>&1; then
    ok "hako_launcher import works for ${py}"
  else
    fail "hako_launcher import failed for ${py}; hakoniwa-pdu may be too old or incomplete."
    sed 's/^/      /' /tmp/hakoniwa-business-pack-launcher.txt
  fi

  if "${py}" -m pip show hakoniwa-pdu >/tmp/hakoniwa-business-pack-pip-show.txt 2>&1; then
    local version
    version="$(awk '/^Version:/ { print $2 }' /tmp/hakoniwa-business-pack-pip-show.txt)"
    ok "hakoniwa-pdu package installed for ${py}: ${version:-unknown-version}"
  else
    fail "pip show hakoniwa-pdu failed for ${py}"
    sed 's/^/      /' /tmp/hakoniwa-business-pack-pip-show.txt
  fi
}

check_cmd ruby 0
check_cmd git 0
check_cmd curl 0

check_path /usr/local/hakoniwa "Hakoniwa install prefix"
check_path /usr/local/hakoniwa/bin/hako-cmd "hako-cmd"
check_path /usr/local/hakoniwa/lib "Hakoniwa library directory"
check_path /usr/local/hakoniwa/share/hakoniwa/offset "Hakoniwa PDU offset directory" 0
check_path /etc/hakoniwa/cpp_core_config.json "Hakoniwa core config" 0
check_path /usr/local/hakoniwa/bin/hakoniwa-pdu-web-bridge "installed web bridge binary" 0

PYTHON_BIN="$(find_python312)"
check_python_runtime "${PYTHON_BIN}"

echo
echo "Summary: ${FAILURES} failure(s), ${WARNINGS} warning(s)"
if [[ "${FAILURES}" -eq 0 ]]; then
  echo "Environment looks ready for Hakoniwa Business Pack Recipe preflight."
else
  echo "Environment is not ready. Fix failures before running SHM/PDU Recipes."
fi

exit "${FAILURES}"
