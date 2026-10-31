#!/usr/bin/env bash
# Builds nous-reel_<version>_amd64.deb for Linux Mint / Ubuntu / Debian.
# Run on Linux from the project folder, with the venv set up
# (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt):
#   bash build_linux.sh
# GitHub builds it on version tags (see .github/workflows/build.yml).
#
# The package installs the app to /opt/nous-reel, a `nous-reel` command, a menu
# entry and icons, and offers it in Open With for video files. mpv and ffmpeg
# come from the distribution (Depends), not bundled.
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
VERSION=$(tr -d '[:space:]' < VERSION)
PKG=nous-reel

QT_QPA_PLATFORM=offscreen "$PY" make_icon.py

# No --strip: it breaks libraries that pip wheels ship pre-patched (numpy's
# OpenBLAS). libpython is stripped by hand below. QtNetwork stays: the player
# talks to mpv over a local socket.
"$PY" -m PyInstaller --noconfirm --clean --windowed --name NousReel \
    --add-data "$PWD/icon.ico:." --add-data "$PWD/VERSION:." --add-data "$PWD/THIRD-PARTY.md:." \
    --distpath dist --workpath build --specpath build \
    --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtPdf \
    "$PWD/nousreel.py"

# Qt pieces this app never uses (Wayland, EGLFS/framebuffer/VNC platforms, SVG,
# OpenGL helpers, translations). Kept: X11 (xcb), which mpv needs to draw into
# the window, the GTK and portal themes, input methods, ICU, and a few image
# formats. Under a Wayland session Qt falls back to X11 (XWayland).
INT=dist/NousReel/_internal
QT=$INT/PySide6/Qt
rm -rf "$QT/translations" \
    "$QT"/lib/libQt6{OpenGL,Svg,WaylandClient,WlShellIntegration}.so* \
    "$QT"/lib/libQt6{EglFS,EglFs,Quick,Qml,Pdf,VirtualKeyboard}*.so* \
    "$QT"/plugins/{egldeviceintegrations,generic,iconengines,tls,networkinformation,wayland-*}
find "$QT/plugins/platforms" -type f ! -name libqxcb.so -delete
find "$QT/plugins/imageformats" -type f ! -name libqico.so ! -name libqjpeg.so ! -name libqgif.so ! -name libqwebp.so -delete

# Use the system's own GTK, GLib, X11, fontconfig, OpenSSL and C++ runtime
# (listed in Depends) rather than copies of the build machine's
find "$INT" -maxdepth 1 -name 'lib*.so*' ! -name 'libpython3*' -delete
find "$INT" -maxdepth 1 -name 'libpython3*.so*' -exec strip --strip-debug {} +

MISSING=$(find dist/NousReel -type f \( -name '*.so*' -o -name NousReel \) -print0 |
    xargs -0 ldd 2>/dev/null | awk '/:$/ { f = $0 } /not found/ { print f, $0 }' || true)
if [ -n "$MISSING" ]; then echo "$MISSING"; echo "Pruned too much"; exit 1; fi

STAGE=build/deb
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/opt" "$STAGE/usr/bin" "$STAGE/usr/share/applications"
cp -a dist/NousReel "$STAGE/opt/$PKG"
ln -s "/opt/$PKG/NousReel" "$STAGE/usr/bin/$PKG"

QT_QPA_PLATFORM=offscreen "$PY" - "$STAGE" "$PKG" <<'EOF'
import os, sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage
app = QGuiApplication(sys.argv[:1])
stage, pkg = sys.argv[1], sys.argv[2]
src = QImage("icon.png")
for size in (48, 64, 128, 256, 512):
    d = os.path.join(stage, f"usr/share/icons/hicolor/{size}x{size}/apps")
    os.makedirs(d, exist_ok=True)
    src.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(os.path.join(d, pkg + ".png"))
EOF

cat > "$STAGE/usr/share/applications/$PKG.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Nous Reel
GenericName=Video player
Comment=Video player that can shrink, join and fix files, step through frames and make memes
Exec=$PKG %f
Icon=$PKG
Terminal=false
Categories=AudioVideo;Video;Player;
MimeType=video/x-matroska;video/mp4;video/x-m4v;video/x-msvideo;video/quicktime;video/x-ms-wmv;video/webm;video/mpeg;video/mp2t;video/x-flv;video/ogg;video/dvd;
Keywords=video;player;movie;mkv;subtitles;shrink;compress;meme;plex;
StartupWMClass=NousReel
EOF

SIZE_KB=$(du -sk "$STAGE" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: video
Priority: optional
Architecture: amd64
Maintainer: lionthroat <lionthroat@users.noreply.github.com>
Homepage: https://nousreel.lionthroat.com
Installed-Size: $SIZE_KB
Depends: mpv, ffmpeg, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libxcb-xkb1, libxcb-util1, libxcb-shm0, libxcb-sync1, libxcb-xfixes0, libxcb-render0, libxcb1, libx11-6, libx11-xcb1, libxkbcommon0, libxkbcommon-x11-0, libegl1, libgl1, libfontconfig1, libfreetype6, libdbus-1-3, libglib2.0-0, libgtk-3-0, libstdc++6, libssl3, libffi8, zlib1g, libzstd1, libbz2-1.0, liblzma5
Description: Video player that can also shrink, join and fix video files
 Nous Reel plays anything mpv can, steps through frames one at a time, and
 turns any frame into a deliberately ruined meme. Its tools shrink big files
 (presets or every dial, explained), join CD1/CD2 parts, fix tracks and build
 subtitles in, and check a file for damage. Free and open source (AGPL-3.0).
EOF

chmod -R u+rwX,go+rX,go-w "$STAGE"
OUT="${PKG}_${VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
echo "Built: $OUT ($(du -h "$OUT" | cut -f1))"
