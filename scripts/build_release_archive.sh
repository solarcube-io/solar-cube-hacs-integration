#!/usr/bin/env bash
# Build a tarball for installing the integration onto a target device.
#
#   ./scripts/build_release_archive.sh [output-dir]
#
# The archive contains exactly custom_components/solar_cube/, so it extracts
# straight into a Home Assistant config directory:
#
#   tar -xzf solar_cube-<version>.tar.gz -C /config
#
# File list comes from `git ls-files`, so build artefacts, __pycache__, tests,
# previews and dev tooling cannot leak in by accident. A dirty or untracked
# working tree is reported rather than silently shipped.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${REPO_ROOT}/dist}"
COMPONENT="custom_components/solar_cube"

cd "${REPO_ROOT}"

VERSION="$(python3 -c "
import json, pathlib
print(json.loads(pathlib.Path('${COMPONENT}/manifest.json').read_text())['version'])
")"
ARCHIVE="${OUT_DIR}/solar_cube-${VERSION}.tar.gz"

# --- refuse to ship something that is not what is committed --------------------
if ! git diff --quiet -- "${COMPONENT}" || ! git diff --cached --quiet -- "${COMPONENT}"; then
  echo "ERROR: ${COMPONENT} has uncommitted changes; commit or stash first." >&2
  git status --short -- "${COMPONENT}" >&2
  exit 1
fi

UNTRACKED="$(git ls-files --others --exclude-standard -- "${COMPONENT}")"
if [[ -n "${UNTRACKED}" ]]; then
  echo "ERROR: untracked files under ${COMPONENT}:" >&2
  echo "${UNTRACKED}" >&2
  echo "Add them or add them to .gitignore; they would be missing from the archive." >&2
  exit 1
fi

# --- build --------------------------------------------------------------------
mkdir -p "${OUT_DIR}"
rm -f "${ARCHIVE}"

# COPYFILE_DISABLE stops macOS tar from embedding ._ AppleDouble entries.
COPYFILE_DISABLE=1 git ls-files -z -- "${COMPONENT}" \
  | tar --null --files-from=- \
        --uid 0 --gid 0 \
        -czf "${ARCHIVE}"

# --- verify -------------------------------------------------------------------
EXPECTED="$(git ls-files -- "${COMPONENT}" | wc -l | tr -d ' ')"
ACTUAL="$(tar -tzf "${ARCHIVE}" | grep -vc '/$' || true)"

if [[ "${EXPECTED}" != "${ACTUAL}" ]]; then
  echo "ERROR: expected ${EXPECTED} files, archive holds ${ACTUAL}." >&2
  exit 1
fi

if tar -tzf "${ARCHIVE}" | grep -qE '__pycache__|\.pyc$|\.DS_Store|^\._|/\._'; then
  echo "ERROR: archive contains build or OS cruft:" >&2
  tar -tzf "${ARCHIVE}" | grep -E '__pycache__|\.pyc$|\.DS_Store|^\._|/\._' >&2
  exit 1
fi

if ! tar -tzf "${ARCHIVE}" | grep -q "^${COMPONENT}/manifest.json$"; then
  echo "ERROR: manifest.json is not at ${COMPONENT}/manifest.json in the archive." >&2
  exit 1
fi

SHA_FILE="${ARCHIVE}.sha256"
if command -v shasum >/dev/null 2>&1; then
  (cd "${OUT_DIR}" && shasum -a 256 "$(basename "${ARCHIVE}")" > "$(basename "${SHA_FILE}")")
else
  (cd "${OUT_DIR}" && sha256sum "$(basename "${ARCHIVE}")" > "$(basename "${SHA_FILE}")")
fi

echo "Built ${ARCHIVE}"
echo "  version : ${VERSION}"
echo "  commit  : $(git rev-parse --short HEAD)"
echo "  files   : ${ACTUAL}"
echo "  size    : $(du -h "${ARCHIVE}" | cut -f1)"
echo "  sha256  : $(cut -d' ' -f1 "${SHA_FILE}")"
echo
echo "Install on the target:"
echo "  tar -xzf $(basename "${ARCHIVE}") -C /config && restart Home Assistant"
