# Spoken Media Pause for Windows

**Beta and untested on Windows.** This standalone x64 executable targets Windows 11. The executable was cross-built on Linux, where automated marker and playback logic checks also passed. The Windows capture and media APIs, live WoW gameplay, and individual players still need Windows testing.

## Use the beta

1. Download `Spoken-Media-Pause-Windows-x64-Beta.exe` from the [Windows beta release](https://github.com/Pocketpac/SpokenMediaPlayPause/releases/tag/v0.2.0-beta.1).
2. Install Spoken and the [Spoken Media Pause companion addon for your WoW client](../README.md#install-the-addon).
3. Run the executable. Start media and use **Test Pause**, then **Test Play**, to check playback. The buttons work with WoW closed.
4. Use WoW in windowed or borderless mode and keep its upper-left corner on-screen and visible. Click the colored in-game marker to toggle automatic pause.

The helper reads up to 200 × 160 pixels of the visible WoW client. It keeps the crop in memory. It uses Windows system media controls to send separate Pause and Play requests. Eligible sessions must expose both controls and identify their current media. Some apps, live streams, and exclusive fullscreen configurations may fall outside those requirements.

The helper checks the Windows session and item metadata before resuming. **Test Play** releases media paused by **Test Pause**. Stopping narration, disabling auto-pause, or closing WoW releases automatic pauses the helper still owns. Closing the helper also attempts to release its remaining pauses. The beta executable is unsigned.

## Build from source

Use the .NET 10 SDK. Run these optional commands from the repository root on Linux or Windows. End users can run the download directly.

```sh
dotnet publish windows/SpokenMediaPause.Windows.csproj -c Release -o dist/windows
dotnet run --project tests/windows/SpokenMediaPause.Windows.Checks.csproj -c Release
```

The publish command writes the standalone x64 executable to `dist/windows/`. The checks exercise the marker decoder and pause ownership against synthetic frames and media sessions. Keep the Windows binary labeled beta until it has been tried with WoW Forever, Spoken, and Windows media playback.
