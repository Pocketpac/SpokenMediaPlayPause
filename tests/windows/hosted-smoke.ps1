# Exercise the packaged WinForms UI and Firefox through real Windows desktop APIs.
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

function New-Tone([string]$Path) {
    # Generate a small loopable PCM sample. No external media or accounts are used.
    $rate = 16000
    $samples = 2 * $rate
    $writer = [IO.BinaryWriter]::new([IO.File]::Create($Path))
    try {
        $writer.Write([Text.Encoding]::ASCII.GetBytes('RIFF'))
        $writer.Write([int](36 + 2 * $samples))
        $writer.Write([Text.Encoding]::ASCII.GetBytes('WAVEfmt '))
        $writer.Write([int]16)
        $writer.Write([int16]1)
        $writer.Write([int16]1)
        $writer.Write([int]$rate)
        $writer.Write([int](2 * $rate))
        $writer.Write([int16]2)
        $writer.Write([int16]16)
        $writer.Write([Text.Encoding]::ASCII.GetBytes('data'))
        $writer.Write([int](2 * $samples))
        for ($n = 0; $n -lt $samples; $n++) {
            $writer.Write([int16](2000 * [Math]::Sin(2 * [Math]::PI * 440 * $n / $rate)))
        }
    } finally { $writer.Dispose() }
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

function Audio-State {
    Driver-Call POST "/session/$driverSession/execute/sync" @{
        script = 'const a = document.querySelector("audio"); return { paused: a.paused, time: a.currentTime, status: navigator.mediaSession.playbackState };'
        args = @()
    }
}

$fixture = Join-Path $env:RUNNER_TEMP 'spoken-media-fixture'
$null = New-Item -ItemType Directory -Path $fixture -Force
$app = $null
$driver = $null
$driverSession = $null
try {
    $app = Start-Process -FilePath $Exe -PassThru
    $window = Wait-For 'the Spoken Media Pause status window' {
        $app.Refresh()
        if ($app.HasExited) { throw "Application quit during startup with status $($app.ExitCode)" }
        if ($app.MainWindowHandle -ne 0 -and $app.MainWindowTitle -like 'Spoken Media Pause*') {
            [System.Windows.Automation.AutomationElement]::FromHandle($app.MainWindowHandle)
        }
    } 35
    foreach ($name in 'Test Pause', 'Test Play', 'Check for updates') {
        if (-not (Find-Control $window $name)) { throw "Status window is missing $name" }
    }
    Write-Host 'PASS: packaged EXE launched with all three controls'

    Invoke-Button $window 'Check for updates'
    $null = Wait-For 'result of the manual GitHub check' {
        if (Find-Control $window 'Could not check GitHub.') { throw 'The app reported a GitHub request failure' }
        Find-Control $window 'No update available.'
    } 25
    Write-Host 'PASS: manual GitHub check returned No update available'

    # The ephemeral runner may have Windows Audio disabled at boot.
    foreach ($name in 'AudioEndpointBuilder', 'Audiosrv') {
        $service = Get-Service $name -ErrorAction SilentlyContinue
        if ($service -and $service.Status -ne 'Running') {
            try { Start-Service $name } catch { Write-Warning "Could not start $name on the runner" }
        }
    }
    $wave = Join-Path $fixture 'tone.wav'
    New-Tone $wave
    $page = Join-Path $fixture 'player.html'
    @'
<!doctype html>
<meta charset="utf-8">
<title>Spoken runner test tone</title>
<audio src="tone.wav" loop></audio>
<button id="start">Start tone</button>
<script>
const audio = document.querySelector("audio");
navigator.mediaSession.metadata = new MediaMetadata({title: "Spoken runner test tone", artist: "Spoken fixtures"});
navigator.mediaSession.setActionHandler("pause", () => audio.pause());
navigator.mediaSession.setActionHandler("play", () => audio.play());
audio.onplay = () => navigator.mediaSession.playbackState = "playing";
audio.onpause = () => navigator.mediaSession.playbackState = "paused";
document.getElementById("start").onclick = () => audio.play();
</script>
'@ | Set-Content -LiteralPath $page -Encoding UTF8

    $driverExe = Join-Path $env:GECKOWEBDRIVER 'geckodriver.exe'
    $driverUrl = 'http://127.0.0.1:4455'
    $driver = Start-Process -FilePath $driverExe -ArgumentList '--host 127.0.0.1 --port 4455' -PassThru
    $null = Wait-For 'geckodriver startup' {
        try { (Driver-Call GET '/status').ready } catch { }
    }
    $driverSession = (Driver-Call POST '/session' @{
        capabilities = @{ alwaysMatch = @{
            browserName = 'firefox'
            'moz:firefoxOptions' = @{ prefs = @{ 'media.hardwaremediakeys.enabled' = $true } }
        } }
    }).sessionId
    $null = Driver-Call POST "/session/$driverSession/url" @{ url = ([uri]$page).AbsoluteUri }
    $button = Driver-Call POST "/session/$driverSession/element" @{ using = 'css selector'; value = '#start' }
    $buttonId = $button.'element-6066-11e4-a52e-4f735466cecf'
    $null = Driver-Call POST "/session/$driverSession/element/$buttonId/click" @{}
    $null = Wait-For 'Firefox tone playback' {
        $audio = Audio-State
        (-not $audio.paused) -and ($audio.time -gt 0.1)
    } 25
    Start-Sleep -Milliseconds 800

    Invoke-Button $window 'Test Pause'
    $null = Wait-For 'Firefox tone paused by the EXE' { (Audio-State).paused } 25
    Write-Host 'PASS: Test Pause paused the real Firefox media session'
    Invoke-Button $window 'Test Play'
    $null = Wait-For 'the same Firefox tone resumed by the EXE' { -not (Audio-State).paused } 25
    Write-Host 'PASS: Test Play resumed the same Firefox media session'
} finally {
    if ($driverSession) {
        try { $null = Driver-Call DELETE "/session/$driverSession" } catch { }
    }
    if ($driver -and -not $driver.HasExited) { Stop-Process -Id $driver.Id -ErrorAction SilentlyContinue }
    if ($app -and -not $app.HasExited) {
        $null = $app.CloseMainWindow()
        if (-not $app.WaitForExit(5000)) { Stop-Process -Id $app.Id -ErrorAction SilentlyContinue }
    }
}
