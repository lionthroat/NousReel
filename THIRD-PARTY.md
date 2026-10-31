# Third-party programs in Nous Reel

The Windows download of Nous Reel includes these programs, unchanged, in its
`bin` folder. Nous Reel runs them as separate programs. On Linux they come
from your distribution's own packages instead.

## mpv (video playback)

- License: GNU GPL version 2 or later
- Source code: https://github.com/mpv-player/mpv
- The Windows build used is from https://github.com/shinchiro/mpv-winbuild-cmake
  (build scripts and the exact library versions are listed there)

## FFmpeg (ffmpeg and ffprobe: reading, converting, joining and checking files)

- License: GNU GPL version 3 (this build includes GPL components such as x264 and x265)
- Source code: https://ffmpeg.org/download.html and https://git.ffmpeg.org/ffmpeg.git
- The Windows build used is Gyan Doshi's "full shared" build from
  https://www.gyan.dev/ffmpeg/builds/ (https://github.com/GyanD/codexffmpeg),
  which lists every included library and its version

## Qt for Python (PySide6) and NumPy

- PySide6: GNU LGPL version 3, https://code.qt.io/cgit/pyside/pyside-setup.git
- Qt: GNU LGPL version 3, https://code.qt.io
- NumPy: BSD license, https://github.com/numpy/numpy

If you'd like a copy of the source code of any of these on a physical medium,
open an issue at https://github.com/lionthroat/NousReel.
