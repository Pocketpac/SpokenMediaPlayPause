Spoken Media Pause for Linux x86_64

Make the AppImage executable and launch it from your desktop. Close the
older desktop helper before launching the AppImage. This build works with
the existing SpokenMediaPause 0.3.0 WoW companion addon.

Start a player with MPRIS desktop media controls, then click Test Pause
and Test Play in the status window. Both buttons work with WoW closed.
Test Pause pauses eligible playing sessions, and Test Play resumes only
the same media paused by Test Pause. The helper sends separate Pause and
Play commands over D-Bus.

The app automatically finds WoW on X11 or XWayland. If WoW runs on native
Wayland, use Share WoW Window and pick the game. This uses your desktop's
screen-sharing portal and PipeWire service. For monitor sharing, run
with --display and keep the marker in the upper-left of that monitor.
Click the in-game marker to toggle automatic playback control. When
narration starts, the helper pauses each eligible playing session. It
resumes an item only if the same session still has it selected and paused.

If FUSE is unavailable, run the AppImage with --appimage-extract-and-run.

This x86_64 build targets Ubuntu 22.04 (glibc 2.35) and newer Linux
systems. It bundles the desktop helper and its app libraries and uses the
host desktop, graphics stack, and session D-Bus. Dependency versions and
licenses are in usr/share/licenses.
