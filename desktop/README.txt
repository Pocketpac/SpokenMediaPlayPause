Spoken Media Pause for Linux x86_64

Make the AppImage executable and launch it from your desktop. Close the
older desktop helper before launching the AppImage. This build works with
the existing SpokenMediaPause 0.3.0 WoW companion addon.

Play YouTube or Audible in Firefox, then click Test Pause and Test Play
in the status window. The test buttons work with WoW closed. Firefox
exposes its selected media session over D-Bus, and the app sends explicit
Pause and Play commands directly to it.

The app automatically finds WoW on X11 or XWayland. If WoW runs on native
Wayland, use Share WoW Window and pick the game. This uses your desktop's
screen-sharing portal and PipeWire service. For monitor sharing, run
with --display and keep the marker in the upper-left of that monitor.
Click the in-game marker to
toggle automatic playback control. Automatic Play runs only for the media
the helper paused, provided it is still selected.

If FUSE is unavailable, run the AppImage with --appimage-extract-and-run.

This x86_64 build targets Ubuntu 22.04 (glibc 2.35) and newer Linux
systems. It bundles the desktop helper and its app libraries and uses the
host desktop, graphics stack, and session D-Bus. Dependency versions and
licenses are in usr/share/licenses.
