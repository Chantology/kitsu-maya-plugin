#!/bin/bash
# Installs the Kitsu Maya Plugin into Maya's user modules folder on Linux.
#
# Usage: ./install_linux.sh [custom modules folder]
#   Run with no arguments to install to the standard location
#   (~/maya/modules), which every Maya version scans at startup. If you
#   use a custom MAYA_MODULE_PATH, pass that modules folder as an
#   argument to this script instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/modules"

if [ "${1:-}" != "" ]; then
    TARGET="$1"
else
    TARGET="$HOME/maya/modules"
fi

echo "Installing Kitsu Maya Plugin"
echo "  From: $SOURCE"
echo "  To:   $TARGET"
echo

if [ ! -f "$SOURCE/kitsu_maya_plugin.mod" ]; then
    echo "ERROR: $SOURCE/kitsu_maya_plugin.mod not found."
    echo "Run this script from the repository root, or check your checkout."
    exit 1
fi

mkdir -p "$TARGET"

# Remove any previous install of this plugin's own folder first, for a
# clean, idempotent install that never leaves stale files from an older
# version behind.
rm -rf "$TARGET/kitsu_maya_plugin"

cp "$SOURCE/kitsu_maya_plugin.mod" "$TARGET/kitsu_maya_plugin.mod"
cp -R "$SOURCE/kitsu_maya_plugin" "$TARGET/kitsu_maya_plugin"

echo "Done. Restart Maya, then enable \"kitsuMayaPlugin.py\" in"
echo "Windows > Settings/Preferences > Plug-in Manager (check both"
echo "\"Loaded\" and \"Auto load\")."
