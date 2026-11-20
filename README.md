# Spoken Media Pause

<img src="assets/brass-badge-logo.png" alt="Spoken Media Pause brass badge logo with glowing amber bars" width="112">

Pause your music, video, or audiobook while [Spoken](https://www.curseforge.com/wow/addons/spoken) narrates in World of Warcraft. By default, the desktop helper sends separate Pause and Play commands and resumes only the items it paused.

Get the **companion addon** for your WoW client and the **Spoken Media Pause Companion** desktop app for your operating system from the [latest release](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/latest).

| Platform | Download | Requirements |
| --- | --- | --- |
| Windows | [Windows x64 EXE](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/latest/download/Spoken-Media-Pause-Companion-Windows-x64.exe) · [Windows setup](#windows-setup) | **Beta.** Tested on Windows 10 22H2 with Firefox and Chrome. |
| Linux | [Linux x64 AppImage](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/latest/download/Spoken-Media-Pause-Companion-Linux-x64.AppImage) · [Linux setup](#linux-setup) | glibc 2.35 or newer, including Ubuntu 22.04 |

On both platforms, use WoW in **Windowed** or **Windowed (Fullscreen)** mode. Exclusive fullscreen may make the in-game marker unreadable.

## Install the addon

1. Install Spoken or the matching port and a voice pack for your client.
2. Download the matching companion ZIP below. The same ZIP works with both desktop helpers.
3. With WoW closed, extract the ZIP into that client's `Interface/AddOns` folder. The resulting path should be `Interface/AddOns/SpokenMediaPause/SpokenMediaPause.lua`.
4. At the character selection screen, open **AddOns** and enable Spoken (or SoftSpoken) and **Spoken Media Pause**. After logging in, look for the [brass badge](#in-game-marker) in the top-left corner.

| WoW client | Companion download |
| --- | --- |
| WoW Forever, Classic Era, or Burning Crusade Classic (Anniversary) | [Standard addon 0.5.2](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/latest/download/SpokenMediaPause-0.5.2.zip) |
| Original WotLK 3.3.5a with the [Spoken WotLK port](https://github.com/PatchFoundry335/spoken-wow) | [WotLK addon 0.5.2](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/latest/download/SpokenMediaPause-0.5.2-WotLK-3.3.5a.zip) (experimental) |
| Original Vanilla 1.12 with [SoftSpoken](https://github.com/z1xel/SoftSpoken) | [Vanilla addon 0.5.2](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/latest/download/SpokenMediaPause-0.5.2-Vanilla-1.12-SoftSpoken.zip) (experimental) |

## Windows setup

1. Download and double-click `Spoken-Media-Pause-Companion-Windows-x64.exe`. The standalone download bundles the .NET runtime. The beta EXE is unsigned.
2. Start WoW in **Windowed** or **Windowed (Fullscreen)** mode. Keep the top-left corner of the game visible, with the helper window out of the way. The helper finds WoW and reads up to 200 × 160 pixels from that corner.
3. [Test playback and automatic pause](#test-playback-and-automatic-pause).

The default mode uses Windows system media sessions. It needs a compatible player that exposes playback controls and identifies the current item.

If the default mode has trouble with a player that responds to your keyboard’s media key, turn on **Use keyboard media key (fallback)**. It starts off and remembers your choice between launches. Start your media before testing, and keep the same player selected through narration. Windows chooses which player receives each **Play/Pause** press. [Windows fallback guide](windows/README.md#keyboard-media-key-fallback)

## Linux setup

1. Download the Linux x64 AppImage above. Open a terminal in the download folder, then make the AppImage executable and start it.

   ```sh
   chmod +x Spoken-Media-Pause-Companion-Linux-x64.AppImage
   ./Spoken-Media-Pause-Companion-Linux-x64.AppImage
   ```

2. Start WoW and choose the capture method that matches how the game is running.

   | WoW display mode | What to do |
   | --- | --- |
   | X11 or XWayland | Leave the helper open. It finds WoW automatically and reads only the upper-left 200 × 160 pixels of the game window. This also applies to WoW running under XWayland on a Wayland desktop. |
   | Native Wayland | Click **Share WoW Window** in the helper. Choose WoW in your desktop's screen-sharing picker and approve sharing. This uses the desktop's screen-sharing portal and PipeWire. |

3. [Test playback and automatic pause](#test-playback-and-automatic-pause).

If the AppImage reports a FUSE or mount error, use this launch command after making it executable. [AppImage FUSE help](https://docs.appimage.org/user-guide/troubleshooting/fuse.html)

```sh
./Spoken-Media-Pause-Companion-Linux-x64.AppImage --appimage-extract-and-run
```

If your Wayland picker offers only monitor sharing, close the helper and relaunch with `--display`. Select the monitor and position WoW so its marker is near that monitor's top-left corner.

```sh
./Spoken-Media-Pause-Companion-Linux-x64.AppImage --display
```

Linux playback works with players that expose Pause, Play, and track or item information through MPRIS. The helper uses D-Bus directly and needs neither `playerctl` nor a browser extension.

## In-game marker

![The brass Spoken Media Pause badge in the top-left corner of WoW, glowing amber during narration.](assets/in-game-marker.png)

Look for the small brass badge just inside the top-left corner of WoW. Click it to turn automatic pause off or back on.

- **Unlit bars** mean auto-pause is enabled and Spoken is idle.
- **Glowing amber bars**, as shown above, mean Spoken is narrating and the desktop helper should pause compatible media.
- **A diagonal slash** means auto-pause is off.

Use the desktop helper from the same release to read the brass badge. In WoW chat, enter `/smp classic` to use the six-color marker with an earlier helper, or `/smp brass` to return to the new badge.

If the marker disappears or freezes during a resolution or display mode change, the helper keeps its last pause decision until it can read a live marker again. When narration ends, you turn auto-pause off, or the helper detects that WoW or the capture session has closed, it releases any automatic pauses it still owns. If the marker stays unreadable, close the helper to release its pause.

## Test playback and automatic pause

1. **Check your player.** Start a song, video, or audiobook in your player. Click **Test Pause**, then **Test Play** in the helper. Both buttons work with WoW closed. In the default mode, **Test Play** resumes the same item paused by **Test Pause**. With Windows fallback enabled, each button sends one keyboard-style Play/Pause press.
2. **Check Spoken.** With your media playing and the helper running, trigger a voiced quest or NPC dialogue in WoW. Your media should pause while Spoken speaks and resume when narration ends.
3. **Check the toggle.** Click the [in-game badge](#in-game-marker) during narration. The slash should appear and the helper should release its pause. Click again to re-enable auto-pause.

If the test buttons have no effect, confirm your media is playing in a player that exposes your operating system's media controls. Try a regular track or video if a live stream fails. If the buttons work but automatic pause fails, check that both addons are enabled, Spoken is playing audible narration, and the marker is visible and enabled. On native Wayland, also check which window or monitor you shared.

If the status window says **Waiting for WoW’s marker**, the helper holds its last pause decision and waits for a fresh marker. In the default mode, **Media player is slow** means the helper is retrying a request after rechecking the selected item.

## Check for updates

The desktop companions check the public GitHub release in the background at startup and at most once a day. Click **Check for updates** to check immediately. If a newer download is available for your platform, **View release** opens the combined download page.

## Verification

- **Linux 0.5.4** uses the same Ubuntu 22.04 runtime and capture code as 0.5.3. Version 0.5.3 passed X11 capture and playback recovery with a simulated WoW window, both playback buttons, and the GitHub update check. Earlier builds were tested on XWayland and KDE Wayland window and monitor sharing.
- **Windows** was tested on a Windows 10 22H2 x64 VM. **Test Pause** and **Test Play** worked in Firefox and Chrome with both control modes. The compact window was tested in Chrome, including both playback modes and **Check for updates**. Beta 8 also passed fallback setting persistence and automatic key presses with a simulated WoW window on a Windows Server 2025 x64 runner. [Windows runner results](https://github.com/Pocketpac/SpokenMediaPlayPause/actions/runs/44668661133/job/136064070830)
- **Addon 0.5.2** was checked with simulated WoW APIs. Both companions passed rendered brass and classic marker checks. Live Classic and legacy gameplay remain untested.

[SHA-256 checksums](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/latest/download/SHA256SUMS-all.txt) · [Linux build instructions](packaging/README.md) · [Windows build instructions](windows/README.md#build-from-source)

## License

The original Spoken Media Pause code is available under the [MIT License](LICENSE). Third-party components are governed by their respective licenses.
