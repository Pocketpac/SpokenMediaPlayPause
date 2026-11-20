# Check both control modes in the published Windows EXE against audible playback in real Firefox.
param([Parameter(Mandatory)][string]$Exe)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

function Wait-For([string]$Description, [scriptblock]$Probe, [int]$Seconds = 20) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $result = & $Probe
        if ($result) { return $result }
        Start-Sleep -Milliseconds 150
    } while ($timer.Elapsed.TotalSeconds -lt $Seconds)
    throw "Timed out: $Description"
}

function Find-Control($Window, [string]$Name) {
    # Resolve by accessibility name so scaling or window position doesn't affect the test.
    $condition = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::NameProperty, $Name)
    $element = $Window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $condition)
    if ($element) { return $element }
}

function Invoke-Button($Window, [string]$Name) {
    $button = Wait-For "$Name enabled" {
        $candidate = Find-Control $Window $Name
        if ($candidate -and $candidate.Current.IsEnabled) { $candidate }
    }
    $pattern = $button.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $pattern.Invoke()
}

function Driver-Call([string]$Method, [string]$Route, $Data = $null) {
    # Send test commands to the runner's loopback WebDriver.
    $parameters = @{ Uri = "$driverUrl$Route"; Method = $Method; TimeoutSec = 12; UseBasicParsing = $true }
    if ($null -ne $Data) {
        $parameters.ContentType = 'application/json'
        $parameters.Body = ConvertTo-Json -InputObject $Data -Depth 12 -Compress
    }
    (Invoke-RestMethod @parameters).value
}

function Video-State {
    Driver-Call POST "/session/$driverSession/execute/sync" @{
        script = 'const a = document.querySelector("video"); return { paused: a.paused, time: a.currentTime, status: navigator.mediaSession.playbackState, pauses: window.pauses, plays: window.plays };'
        args = @()
    }
}

function Window-Text($Window) {
    # Include only our app's labels when a test fails, including playback feedback.
    foreach ($control in $Window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition)) {
        if ($control.Current.ControlType -eq [System.Windows.Automation.ControlType]::Text) {
            $control.Current.Name
        }
    }
}

# The page reports the actual HTML video state and event counts. Only the app sends playback commands after startup.
$fixture = Join-Path $env:RUNNER_TEMP 'spoken-firefox-check'
$null = New-Item -ItemType Directory -Path $fixture
$page = Join-Path $fixture 'player.html'
@'
<!doctype html>
<meta charset="utf-8">
<title>Spoken Firefox verification</title>
<video src="test-video.webm" width="320" height="240" loop controls></video>
<button id="start">Play video</button>
<script>
const audio = document.querySelector("video");
window.plays = window.pauses = 0;
navigator.mediaSession.metadata = new MediaMetadata({title: "Spoken Firefox test video", artist: "Spoken fixtures"});
navigator.mediaSession.setActionHandler("pause", () => audio.pause());
navigator.mediaSession.setActionHandler("play", () => audio.play());
audio.onplay = () => { window.plays++; navigator.mediaSession.playbackState = "playing"; };
audio.onpause = () => { window.pauses++; navigator.mediaSession.playbackState = "paused"; };
document.querySelector("#start").onclick = () => audio.play();
</script>
'@ | Set-Content -LiteralPath $page -Encoding UTF8

function Wait-Playback([string]$Description, [bool]$Paused, $Before) {
    # Require a new media event as well as the final paused or playing state.
    Wait-For $Description {
        $now = Video-State
        if ($now.paused -eq $Paused -and
            (($Paused -and $now.pauses -eq ($Before.pauses + 1)) -or
            (-not $Paused -and $now.plays -eq ($Before.plays + 1)))) { $now }
    } 12
}

function Close-Companion {
    # Release any owned pause normally before testing a different playback mode.
    if ($script:app -and -not $script:app.HasExited) {
        $null = $script:app.CloseMainWindow()
        if (-not $script:app.WaitForExit(6500)) { throw 'The companion did not close cleanly' }
    }
}

$app = $null
$game = $null
$driver = $null
$driverSession = $null
$driverUrl = 'http://127.0.0.1:4455'
$env:MOZ_LOG = 'MediaControl:4'
$env:MOZ_LOG_FILE = Join-Path $fixture 'firefox-media.log'
$failed = @()
$summary = @()
try {
    foreach ($name in 'AudioEndpointBuilder', 'Audiosrv') {
        if ((Get-Service $name).Status -ne 'Running') { Start-Service $name }
    }
    # Use a normal VP8/Opus video with a generated tone and no external website dependencies.
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\hosted-windows\test-video.webm') -Destination $fixture
    $probeProject = Join-Path $PSScriptRoot '..\hosted-windows\MediaProbe\MediaProbe.csproj'
    $probeOut = Join-Path $fixture 'probe'
    & dotnet build $probeProject -c Release -o $probeOut --nologo
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the Windows media session diagnostic' }
    $probe = Join-Path $probeOut 'MediaProbe.dll' 

    # UI Automation exercises the real compiled companion, and WebDriver only observes the page after initial playback.
    $driverExe = Join-Path $env:GECKOWEBDRIVER 'geckodriver.exe'
    $driver = Start-Process -FilePath $driverExe -ArgumentList '--host 127.0.0.1 --port 4455' -PassThru
    $null = Wait-For 'geckodriver startup' { try { (Driver-Call GET '/status').ready } catch { } }
    $session = Driver-Call POST '/session' @{
        capabilities = @{ alwaysMatch = @{
            browserName = 'firefox'
            'moz:firefoxOptions' = @{ prefs = @{ 'media.hardwaremediakeys.enabled' = $true } }
        } }
    }
    $driverSession = $session.sessionId
    Write-Host "Firefox $($session.capabilities.browserVersion), Windows build $([Environment]::OSVersion.Version)"
    $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    $gameExe = Join-Path $fixture 'wow.exe'
    & $csc /nologo /target:winexe "/out:$gameExe" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll (Join-Path $PSScriptRoot '..\hosted-windows\HostedMarkerFixture.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the simulated WoW window' }

    $pref = 'HKCU:\Software\SpokenMediaPause'
    Remove-ItemProperty $pref MediaKeyFallback -ErrorAction SilentlyContinue
    foreach ($mode in 'Default Windows controls', 'Keyboard Play/Pause fallback') {
        $window = $null
        try {
            $app = Start-Process -FilePath $Exe -PassThru
            $window = Wait-For 'companion window' {
                $app.Refresh()
                if ($app.HasExited) { throw "Companion exited with status $($app.ExitCode)" }
                if ($app.MainWindowHandle -and $app.MainWindowTitle -like 'Spoken Media Pause*') {
                    [System.Windows.Automation.AutomationElement]::FromHandle($app.MainWindowHandle)
                }
            } 35
            $checkbox = Wait-For 'the fallback checkbox' { Find-Control $window 'Use keyboard media key (fallback)' }
            $toggle = $checkbox.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
            if ($toggle.Current.ToggleState -ne 'Off') { throw 'Fallback unexpectedly enabled on startup' }
            if ($mode -like 'Keyboard*') {
                $toggle.Toggle()
                $null = Wait-For 'fallback checkbox checked' { $toggle.Current.ToggleState -eq 'On' }
            }

            # Reload the same fixture for each mode so previous tests cannot leave it paused.
            $null = Driver-Call POST "/session/$driverSession/url" @{ url = ([uri]$page).AbsoluteUri }
            $button = Driver-Call POST "/session/$driverSession/element" @{ using = 'css selector'; value = '#start' }
            $buttonId = $button.'element-6066-11e4-a52e-4f735466cecf'
            $null = Driver-Call POST "/session/$driverSession/element/$buttonId/click" @{}
            $playing = Wait-For 'Firefox video clock running' {
                $now = Video-State
                if (-not $now.paused -and $now.time -gt 1) { $now }
            }
            Write-Host "Firefox video playing before $mode (position $($playing.time))."
            # List what Firefox registered with Windows. This observer never creates or commands a session.
            & dotnet $probe list
            if ($LASTEXITCODE -ne 0) { Write-Warning 'The Windows media session diagnostic failed' }
            Invoke-Button $window 'Test Pause'
            $paused = Wait-Playback 'Test Pause paused the Firefox video' $true $playing
            Invoke-Button $window 'Test Play'
            $playing = Wait-Playback 'Test Play resumed the same Firefox video' $false $paused
            Write-Host "PASS: $mode - Firefox paused and resumed with both test buttons."
            $summary += "- $mode - Firefox paused and resumed with both test buttons."

            # A topmost test window exercises playback while the game has focus, without using an installed WoW client.
            $marker = Join-Path $fixture 'marker.txt'
            Set-Content -LiteralPath $marker -Value 'idle'
            $game = Start-Process -FilePath $gameExe -ArgumentList ('"{0}"' -f $marker) -PassThru
            $null = Wait-For 'the simulated WoW marker' { Find-Control $window 'Ready' }
            Set-Content -LiteralPath $marker -Value 'narrating'
            $paused = Wait-Playback 'simulated narration paused Firefox' $true $playing
            Set-Content -LiteralPath $marker -Value 'idle'
            $null = Wait-Playback 'end of simulated narration resumed Firefox' $false $paused
            Write-Host "PASS: $mode - Firefox paused and resumed automatically with the simulated marker."
            $summary += "- $mode - Firefox paused and resumed automatically with the simulated marker."
        } catch {
            $detail = "$mode - $($_.Exception.Message)"
            $failed += $detail
            $summary += "- $detail"
            Write-Warning $detail
            if ($window) { Write-Host ('Companion labels: ' + ((Window-Text $window) -join ' | ')) }
            try { Write-Host ('Firefox video state: ' + (Video-State | ConvertTo-Json -Compress)) } catch { }
        } finally {
            if ($game -and -not $game.HasExited) { Stop-Process -Id $game.Id }
            Close-Companion
        }
    }
} finally {
    if ($driverSession) { try { $null = Driver-Call DELETE "/session/$driverSession" } catch { } }
    if ($driver -and -not $driver.HasExited) { Stop-Process -Id $driver.Id }
    # Print only media registration and command traces from the synthetic browser profile.
    Get-ChildItem -Path "$env:MOZ_LOG_FILE*" -ErrorAction SilentlyContinue |
        Select-String -Pattern 'SystemMediaTransportControls|WindowSMTCProvider|Set controller|Activate|HandleMediaKey|for monitoring media keys' |
        Select-Object -Last 35 | ForEach-Object { Write-Host $_.Line }
}
$summary | Out-File -LiteralPath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
if ($failed) { throw ($failed -join ' | ') }
