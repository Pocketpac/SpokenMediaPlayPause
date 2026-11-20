# Spoken Media Pause Companion for Windows

## Use the beta

The standalone x64 executable is a **Beta** built with the Windows 11 SDK. On a Windows 10 22H2 x64 VM, **Test Pause** and **Test Play** worked in Firefox and Chrome with both the default Windows controls and the keyboard fallback. Follow the [Windows setup guide](../README.md#windows-setup) and [playback test](../README.md#test-playback-and-automatic-pause) on the main page.

The helper keeps its screen capture in memory. When you close the helper, it attempts to resume any sessions it still owns that have the same media selected and paused.

The status window keeps **Test Pause**, **Test Play**, and **Check for updates** on one row and grows to fit feedback. Use **Check for updates** for an immediate check. Daily background checks use the same combined GitHub release. **View release** opens the downloads when a newer Windows build is available.

### Keyboard media key fallback

Select **Use keyboard media key (fallback)** to send the same Play/Pause toggle as a multimedia keyboard. The checkbox is saved for your Windows user and starts unchecked on a fresh install.

Start playback before triggering Spoken or clicking **Test Pause**. The helper sends a Play/Pause press when narration starts and a second press when narration ends, you turn auto-pause off, or WoW closes. A brief loss of the marker preserves the pending press.

Windows chooses the player for each press. If you switch players or change playback during narration, the resume key may go to a different item or toggle it the other way. When a test pause is pending, click **Test Play** before changing modes.

Beta 8 passed the checkbox restart test and sent the expected keyboard presses with a native player and a simulated WoW window on Windows Server 2025 x64. [Windows runner results](https://github.com/Pocketpac/SpokenMediaPlayPause/actions/runs/44668661133/job/136064070830)

## Build from source

Use the .NET 10 SDK. Run these optional commands from the repository root on Linux or Windows. End users can run the download directly.

```sh
dotnet publish windows/SpokenMediaPause.Windows.csproj -c Release -o dist/windows
dotnet run --project tests/windows/SpokenMediaPause.Windows.Checks.csproj -c Release
```

The publish command writes `Spoken-Media-Pause-Companion-Windows-x64.exe` to `dist/windows/`. The checks exercise the marker decoder and pause ownership against synthetic frames and media sessions.
