# Spoken Media Pause

Spoken Media Pause pauses desktop media while the [Spoken addon](https://www.curseforge.com/wow/addons/spoken) narrates in World of Warcraft. The Linux helper sends separate Pause and Play commands over D-Bus to players that expose MPRIS controls. It resumes an item only if the helper paused it and the same session still has that item selected and paused.

## Downloads

- [Linux AppImage and companion addon](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/latest)
- [Windows x64 beta](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/tag/v0.2.0-beta.1) and [Windows instructions](windows/README.md). The executable was built on Linux. Live Windows gameplay and media control are untested.

## Install the addon

Download the ZIP for your game client from the [companion addon 0.4.0 release](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/tag/addon-v0.4.0).

| WoW client | Companion ZIP |
| --- | --- |
| Forever, Classic Era, Burning Crusade Classic (Anniversary) | `SpokenMediaPause-0.4.0.zip` |
| Original Wrath of the Lich King 3.3.5a with the [Spoken WotLK port](https://github.com/PatchFoundry335/spoken-wow) | `SpokenMediaPause-0.4.0-WotLK-3.3.5a.zip` (experimental) |
| Original Vanilla 1.12 with [SoftSpoken](https://github.com/z1xel/SoftSpoken) | `SpokenMediaPause-0.4.0-Vanilla-1.12-SoftSpoken.zip` (experimental) |

Extract the chosen ZIP so `SpokenMediaPause` is directly inside that client's `Interface/AddOns` folder. Keep Spoken or the matching port installed with a voice pack. The official ZIP matches the editions advertised by [Spoken](https://www.curseforge.com/wow/addons/spoken) and uses its playback callback API. The legacy ZIPs match the older ports' loader requirements. The addon keeps the same marker for the available Linux and Windows desktop helpers.

## Install

1. Download the all-player AppImage from the [Linux release](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/tag/v0.1.0). The AppImage targets 64-bit Linux with glibc 2.35 or newer, including Ubuntu 22.04.
2. Install the [companion addon](#install-the-addon) for your WoW client.
3. Make the AppImage executable and start it. Start some media and click **Test Pause**, then **Test Play**. The buttons work with WoW closed. Test Play resumes media paused by Test Pause.

If AppImage mounting is unavailable, launch it with `--appimage-extract-and-run`.

## Capture and playback

- **X11 and XWayland.** The helper finds a visible WoW window and reads only the upper-left 200 × 160 pixels.
- **Native Wayland.** Click **Share WoW Window** and select the game. The helper uses the desktop's screen-sharing portal and PipeWire. For monitor sharing, launch with `--display` and keep WoW in the monitor's upper-left corner.
- **Toggle.** Click the colored marker in WoW to turn automatic pause on or off. Disabling it or closing WoW releases automatic pauses the helper still owns.
- **Players.** Playback controls work with MPRIS sessions that expose Pause, Play, and enough metadata to identify the current item. The helper talks directly to D-Bus. You can use it without `playerctl` or a browser extension.

The AppImage was tested with Firefox and VLC using local media, along with the 0.3.0 addon's marker rendered in a test window. Those tests covered X11, XWayland, and KDE's Wayland window and monitor sharing. The 0.4.0 addon was checked against published TOCs and simulated modern and legacy marker behavior. Live Classic and legacy gameplay remain untested.

[Build instructions and dependency sources](packaging/README.md)
