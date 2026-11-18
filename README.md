# Spoken Media Pause

Spoken Media Pause pauses Linux desktop media while the [Spoken addon](https://www.curseforge.com/wow/addons/spoken) narrates in World of Warcraft Forever. It sends separate Pause and Play commands over D-Bus to players that expose MPRIS controls. It resumes an item only if the helper paused it and the same session still has that item selected and paused.

## Install

1. Download the all-player AppImage and the addon ZIP from [Releases](https://github.com/Pocketpac/SpokenMediaPlayPause/releases). The AppImage targets 64-bit Linux with glibc 2.35 or newer, including Ubuntu 22.04.
2. Extract the ZIP so `SpokenMediaPause` is directly inside WoW Forever's `Interface/AddOns` folder. Keep Spoken installed. If the 0.3.0 companion addon is already installed, you can use it with this release.
3. Make the AppImage executable and start it. Start some media and click **Test Pause**, then **Test Play**. The buttons work with WoW closed. Test Play resumes media paused by Test Pause.

If AppImage mounting is unavailable, launch it with `--appimage-extract-and-run`.

## Capture and playback

- **X11 and XWayland.** The helper finds a visible WoW window and reads only the upper-left 200 × 160 pixels.
- **Native Wayland.** Click **Share WoW Window** and select the game. The helper uses the desktop's screen-sharing portal and PipeWire. For monitor sharing, launch with `--display` and keep WoW in the monitor's upper-left corner.
- **Toggle.** Click the colored marker in WoW to turn automatic pause on or off. Disabling it or closing WoW releases automatic pauses the helper still owns.
- **Players.** Playback controls work with MPRIS sessions that expose Pause, Play, and enough metadata to identify the current item. The helper talks directly to D-Bus. You can use it without `playerctl` or a browser extension.

The AppImage was tested with Firefox and VLC using local media, along with the 0.3.0 addon's marker rendered in a test window. Those tests covered X11, XWayland, and KDE's Wayland window and monitor sharing.

[Build instructions and dependency sources](packaging/README.md)
