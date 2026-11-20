# Verify the exact Beta EXE in a clean Windows runner desktop.
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
    # Firefox excludes clips of three seconds or less from desktop media controls.
    $samples = 12 * $rate
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

function Open-Companion {
    $script:app = Start-Process -FilePath $Exe -PassThru
    Wait-For 'the companion status window' {
        $app.Refresh()
        if ($app.HasExited) { throw "Companion exited with code $($app.ExitCode)" }
        if ($app.MainWindowHandle -ne 0 -and $app.MainWindowTitle -like 'Spoken Media Pause*') {
            [System.Windows.Automation.AutomationElement]::FromHandle($app.MainWindowHandle)
        }
    } 35
}

function Close-Companion {
    if ($app -and -not $app.HasExited) {
        $null = $app.CloseMainWindow()
        if (-not $app.WaitForExit(6000)) { throw 'Companion did not close cleanly' }
    }
}

function Fallback-Control($Window) {
    Find-Control $Window 'Use keyboard media key (fallback)'
}

function Fallback-Pattern($Window) {
    (Fallback-Control $Window).GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
}

function Key-Events {
    @(Get-Content -LiteralPath $keyLog -ErrorAction SilentlyContinue) | Where-Object { $_ -in 'down','up' }
}

function Expect-Keys([int]$Count) {
    $null = Wait-For "$Count injected media key events" { @(Key-Events).Count -ge $Count }
    Start-Sleep -Milliseconds 250
    $events = @(Key-Events)
    if ($events.Count -ne $Count) { throw "Expected $Count key events, got $($events -join ',')" }
    for ($i = 0; $i -lt $Count; $i += 2) {
        if ($events[$i] -ne 'down' -or $events[$i + 1] -ne 'up') {
            throw "Media key events were not paired: $($events -join ',')"
        }
    }
}

$fixture = Join-Path $env:RUNNER_TEMP 'spoken-media-key-fixture'
$null = New-Item -ItemType Directory -Path $fixture -Force
$keyLog = Join-Path $fixture 'keys.txt'
$fixtureSource = Join-Path $PSScriptRoot '..\hosted-windows'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$observerExe = Join-Path $fixture 'MediaKeyObserver.exe'
$gameExe = Join-Path $fixture 'wow.exe'
$app = $null
$observer = $null
$game = $null
$player = $null
try {
    # These settings belong only to the disposable runner account.
    $pref = 'HKCU:\Software\SpokenMediaPause'
    Remove-ItemProperty $pref MediaKeyFallback -ErrorAction SilentlyContinue
    $window = Open-Companion
    $null = Wait-For 'the default checkbox' { Fallback-Control $window }
    if ((Fallback-Pattern $window).Current.ToggleState -ne 'Off') { throw 'Fallback was enabled by default' }
    (Fallback-Pattern $window).Toggle()
    $null = Wait-For 'the enabled setting being stored' {
        (Get-ItemProperty $pref MediaKeyFallback -ErrorAction SilentlyContinue).MediaKeyFallback -eq 1
    }
    Close-Companion
    $window = Open-Companion
    if ((Fallback-Pattern $window).Current.ToggleState -ne 'On') { throw 'Fallback was lost on restart' }
    Write-Host 'PASS: enabling the fallback persists across a real process restart.'

    & $csc /nologo /target:winexe "/out:$observerExe" /reference:System.Windows.Forms.dll (Join-Path $fixtureSource 'MediaKeyObserver.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Could not compile key observer' }
    $observer = Start-Process $observerExe -ArgumentList ('"{0}"' -f $keyLog) -PassThru
    $null = Wait-For 'the media key observer' { Test-Path -LiteralPath $keyLog }

    # A real Windows player lets us check whether the shell routes the key, too.
    foreach ($service in 'AudioEndpointBuilder', 'Audiosrv') {
        if ((Get-Service $service -ErrorAction SilentlyContinue).Status -ne 'Running') {
            try { Start-Service $service } catch { Write-Warning "Could not start $service" }
        }
    }
    $wave = Join-Path $fixture 'tone.wav'
    New-Tone $wave
    $playerState = Join-Path $fixture 'player-state.txt'
    $probeOut = Join-Path $fixture 'probe'
    & dotnet build (Join-Path $fixtureSource 'MediaProbe\MediaProbe.csproj') -c Release -o $probeOut --nologo
    if ($LASTEXITCODE -ne 0) { throw 'Could not build the native media player' }
    $player = Start-Process dotnet -ArgumentList ('"{0}" "{1}" "{2}"' -f (Join-Path $probeOut 'MediaProbe.dll'), $wave, $playerState) -PassThru
    $null = Wait-For 'the native test player' { Test-Path -LiteralPath $playerState }

    Invoke-Button $window 'Test Pause'
    Expect-Keys 2
    $shellPaused = (Get-Content $playerState -Raw) -eq 'paused'
    if ((Fallback-Control $window).Current.IsEnabled) { throw 'Mode can change while a key pair is pending' }
    Invoke-Button $window 'Test Pause'
    Expect-Keys 2
    Invoke-Button $window 'Test Play'
    Expect-Keys 4
    Write-Host 'PASS: Test Pause and Test Play each sent one complete Play/Pause press. Repeating Test Pause sent nothing.'
    if ($shellPaused -and (Get-Content $playerState -Raw) -eq 'playing') {
        Write-Host 'PASS: the Windows shell routed both toggles to the real native player.'
    }
    else {
        Write-Warning 'The runner received the injected keys. Playback routing to the native player was not verified.'
    }

    & $csc /nologo /target:winexe "/out:$gameExe" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll (Join-Path $fixtureSource 'HostedMarkerFixture.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Could not compile the WoW fixture' }
    $markerPath = Join-Path $fixture 'marker.txt'
    Set-Content -LiteralPath $markerPath -Value 'idle'
    $game = Start-Process $gameExe -ArgumentList ('"{0}"' -f $markerPath) -PassThru
    $null = Wait-For 'the live brass marker' { Find-Control $window 'Ready' }
    Set-Content -LiteralPath $markerPath -Value 'narrating'
    Expect-Keys 6
    Set-Content -LiteralPath $markerPath -Value 'missing'
    Start-Sleep -Milliseconds 2700
    Expect-Keys 6
    Set-Content -LiteralPath $markerPath -Value 'idle'
    Expect-Keys 8
    Set-Content -LiteralPath $markerPath -Value 'narrating'
    Expect-Keys 10
    Set-Content -LiteralPath $markerPath -Value 'close'
    Expect-Keys 12
    Write-Host 'PASS: automatic key pairs completed on live idle and game exit. Capture loss sent no extra key.'

    (Fallback-Pattern $window).Toggle()
    $null = Wait-For 'the disabled setting being stored' {
        (Get-ItemProperty $pref MediaKeyFallback -ErrorAction SilentlyContinue).MediaKeyFallback -eq 0
    }
    Close-Companion
    $window = Open-Companion
    if ((Fallback-Pattern $window).Current.ToggleState -ne 'Off') { throw 'Unchecking the fallback was lost after restart' }
    Write-Host 'PASS: disabling the fallback persists across a real process restart.'
} finally {
    try { Close-Companion } catch { if ($app -and -not $app.HasExited) { Stop-Process -Id $app.Id } }
    foreach ($fixtureProcess in @($game, $observer, $player)) {
        if ($fixtureProcess -and -not $fixtureProcess.HasExited) {
            Stop-Process -Id $fixtureProcess.Id -ErrorAction SilentlyContinue
        }
    }
}
