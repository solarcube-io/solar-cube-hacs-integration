#!/usr/bin/env bash
# Fetch the Font Awesome Free desktop package and install the Solid face that
# the LCD renderer uses.
#
# Why the *desktop* zip and not the web one: the web package only ships .woff2,
# which Pillow can open only when its bundled FreeType was compiled with
# brotli/woff2 support -- not guaranteed in the Home Assistant container images.
# The desktop package ships .otf, which always works.
#
# The 45 MB web bundle is intentionally NOT committed (see .gitignore); run this
# script when you need it locally.
#
# Font Awesome Free is licensed CC BY 4.0 (icons), SIL OFL 1.1 (fonts) and
# MIT (code). See tools/fontawesome-free-*-web/LICENSE.txt after downloading.

set -euo pipefail

VERSION="${1:-7.2.0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FONTS_DIR="${REPO_ROOT}/custom_components/solar_cube/fonts"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

base="https://use.fontawesome.com/releases/v${VERSION}"

echo "==> Downloading Font Awesome Free ${VERSION} (desktop)"
curl -fsSL "${base}/fontawesome-free-${VERSION}-desktop.zip" \
  -o "${TMP_DIR}/desktop.zip"
unzip -q "${TMP_DIR}/desktop.zip" -d "${TMP_DIR}"

otf="$(find "${TMP_DIR}" -name 'Font Awesome*Solid*.otf' -print -quit)"
if [[ -z "${otf}" ]]; then
  echo "ERROR: could not find the Solid .otf in the desktop package" >&2
  exit 1
fi

mkdir -p "${FONTS_DIR}"
cp "${otf}" "${FONTS_DIR}/fa-solid-900.otf"
echo "==> Installed ${FONTS_DIR}/fa-solid-900.otf"

echo "==> Downloading Font Awesome Free ${VERSION} (web, for reference/dev only)"
curl -fsSL "${base}/fontawesome-free-${VERSION}-web.zip" -o "${TMP_DIR}/web.zip"
unzip -q "${TMP_DIR}/web.zip" -d "${REPO_ROOT}/tools"
echo "==> Extracted tools/fontawesome-free-${VERSION}-web/ (git-ignored)"

echo ""
echo "Verify Pillow can read the face:"
echo "  python3 -c \"from PIL import ImageFont; \\"
echo "    ImageFont.truetype('${FONTS_DIR}/fa-solid-900.otf', 12); print('ok')\""
