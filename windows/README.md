# Spoken Media Pause for Windows

## Use the beta

The standalone x64 executable is a **Beta** targeting Windows 11. Windows 10 compatibility is unverified. Follow the [Windows setup guide](../README.md#windows-setup) and [playback test](../README.md#test-playback-and-automatic-pause) on the main page.

The helper keeps its screen capture in memory. When you close the helper, it attempts to resume any sessions it still owns that have the same media selected and paused.

## Build from source

Use the .NET 10 SDK. Run these optional commands from the repository root on Linux or Windows. End users can run the download directly.

```sh
dotnet publish windows/SpokenMediaPause.Windows.csproj -c Release -o dist/windows
dotnet run --project tests/windows/SpokenMediaPause.Windows.Checks.csproj -c Release
```

The publish command writes the standalone x64 beta executable to `dist/windows/`. The checks exercise the marker decoder and pause ownership against synthetic frames and media sessions.
