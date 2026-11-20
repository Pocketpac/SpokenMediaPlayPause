using System.Diagnostics;
using SpokenMediaPause.Core;

namespace SpokenMediaPause.Windows;

/// <summary>Shows capture status and exposes manual playback tests even before WoW opens.</summary>
internal sealed class MainWindow : Form
{
    private const int StatusTextWidth = 324;
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private readonly WowWindowCapture capture = new();
    private readonly Narration narration = new();
    private readonly MediaPause media = new(new SmtcSessions());
    private readonly MediaKeyPlayback mediaKey = new(KeyboardMediaKey.SendToggle);
    private readonly System.Windows.Forms.Timer poll = new() { Interval = 80 };
    private readonly ReleaseUpdates updates = new();
    private readonly System.Windows.Forms.Timer dailyUpdate = new() { Interval = 86_400_000 };
    private readonly Label status = new() { AutoSize = true, MaximumSize = new(StatusTextWidth, 0), Text = "Waiting for WoW" };
    private readonly Label feedback = new() { AutoSize = true, MaximumSize = new(StatusTextWidth, 0), Visible = false };
    private readonly Label recovery = new() { AutoSize = true, MaximumSize = new(StatusTextWidth, 0), Visible = false };
    private readonly Button pause = new() { AutoSize = true, Text = "Test Pause" };
    private readonly Button play = new() { AutoSize = true, Text = "Test Play" };
    private readonly CheckBox fallback = new() { AutoSize = true, Text = "Use keyboard media key (fallback)" };
    private readonly Label fallbackHelp = new()
    {
        AutoSize = true, MaximumSize = new(StatusTextWidth, 0),
        Text = "Windows chooses which player receives Play/Pause. Start with media playing."
    };
    private readonly Button checkUpdate = new() { AutoSize = true, Text = "Check for updates" };
    private readonly LinkLabel releaseLink = new() { AutoSize = true, Text = "View release", Visible = false };
    private readonly Label updateFeedback = new() { AutoSize = true, MaximumSize = new(StatusTextWidth, 0), Visible = false };
    private string? newerTag;
    private bool closing, allowClose;

    public MainWindow()
    {
        Text = Application.ProductName;
        AutoScaleMode = AutoScaleMode.Dpi;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        // Leave room for the full companion title and fit the height to the visible controls.
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        var layout = new FlowLayoutPanel
        {
            AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            FlowDirection = FlowDirection.TopDown, WrapContents = false,
            MinimumSize = new(StatusTextWidth + 26, 0), Padding = new(10, 8, 10, 8)
        };
        layout.Controls.Add(status);
        layout.Controls.Add(new Label { AutoSize = true, MaximumSize = new(StatusTextWidth, 0),
            Text = "Keep WoW’s top-left corner visible. Click the in-game marker to toggle auto-pause." });
        fallback.Checked = PlaybackSettings.LoadFallback();
        fallbackHelp.Visible = fallback.Checked;
        layout.Controls.Add(fallback);
        layout.Controls.Add(fallbackHelp);
        // Keep the three actions on one row. Optional messages and the release link can grow below it.
        var buttons = new FlowLayoutPanel { AutoSize = true, WrapContents = false, Margin = Padding.Empty };
        buttons.Controls.AddRange([pause, play, checkUpdate]);
        layout.Controls.Add(buttons);
        layout.Controls.Add(feedback);
        layout.Controls.Add(recovery);
        layout.Controls.Add(updateFeedback);
        layout.Controls.Add(releaseLink);
        Controls.Add(layout);
        fallback.CheckedChanged += (_, _) => ChangePlaybackMode();
        pause.Click += async (_, _) => await TestAsync(true);
        play.Click += async (_, _) => await TestAsync(false);
        poll.Tick += (_, _) => Poll();
        checkUpdate.Click += async (_, _) => await CheckUpdatesAsync(true);
        releaseLink.LinkClicked += (_, _) => OpenRelease();
        dailyUpdate.Tick += async (_, _) => await CheckUpdatesAsync(false);
        Shown += async (_, _) =>
        {
            poll.Start();
            dailyUpdate.Start();
            await CheckUpdatesAsync(false);
        };
        FormClosing += OnClosing;
    }

    /// <summary>Updates the marker without waiting for media I/O and releases ownership when WoW closes.</summary>
    private void Poll()
    {
        long now = clock.ElapsedMilliseconds;
        bool hadGame = capture.HasGame;
        var reading = capture.Sample(now);
        if (hadGame && !capture.HasGame)
            narration.Reset();
        narration.Update(reading, now);
        if (fallback.Checked)
            mediaKey.SetActive(narration.Active, narration.CanPause);
        else
            media.SetActive(narration.Active, narration.CanPause);
        status.Text = !capture.HasGame ? "Waiting for WoW"
            : !narration.Connected ? CaptureStatus()
            : narration.Disabled ? "Auto-pause off"
            : !narration.Active ? "Ready"
            : fallback.Checked ? mediaKey.AutomaticTogglePending ? "Play/Pause key sent" : "Spoken is narrating"
            : media.OwnsAutomaticPause ? "Media paused" : "Spoken is narrating";
        string message = (fallback.Checked ? mediaKey.LastError : media.Recovery) ?? "";
        if (recovery.Text != message)
        {
            recovery.Text = message;
            recovery.Visible = message.Length != 0;
        }
        UpdateButtons();
    }

    /// <summary>Report capture gaps separately from whether a playback command was dispatched.</summary>
    private string CaptureStatus()
    {
        if (fallback.Checked && mediaKey.AutomaticTogglePending)
            return "Waiting for Spoken’s marker (resume key pending)";
        return !fallback.Checked && narration.Active
            ? "Waiting for Spoken’s marker (pause held)" : "Waiting for Spoken’s marker";
    }

    private void UpdateButtons()
    {
        pause.Enabled = play.Enabled = !closing && !narration.Active && !media.Busy;
        fallback.Enabled = pause.Enabled && !media.HasPendingPause && !mediaKey.HasPendingToggle;
    }

    /// <summary>Save only user changes and keep settings feedback separate from update checks.</summary>
    private void ChangePlaybackMode()
    {
        fallbackHelp.Visible = fallback.Checked;
        SetPlaybackFeedback(PlaybackSettings.SaveFallback(fallback.Checked) ? "" : "Couldn’t save the playback setting.");
    }

    /// <summary>Show playback feedback only while it has text, so it takes no space on startup or after a mode change.</summary>
    private void SetPlaybackFeedback(string message)
    {
        feedback.Text = message;
        feedback.Visible = message.Length != 0;
    }

    /// <summary>Display GitHub results independently of playback and prevent overlapping checks.</summary>
    private async Task CheckUpdatesAsync(bool manual)
    {
        if (closing || !checkUpdate.Enabled)
            return;
        checkUpdate.Enabled = false;
        if (manual)
        {
            updateFeedback.Text = "Checking GitHub…";
            updateFeedback.Visible = true;
        }
        var result = await updates.CheckAsync(manual);
        if (closing)
            return;
        checkUpdate.Enabled = true;
        newerTag = result.NewerTag;
        releaseLink.Visible = newerTag is not null;
        string message = newerTag is null ? "" : $"Update available ({newerTag.Replace("addon-", "")})";
        if (manual && result.Failed)
            message = message.Length == 0 ? "Could not check GitHub." : $"Could not check GitHub. {message}";
        else if (manual && newerTag is null)
            message = "No update available.";
        updateFeedback.Text = message;
        updateFeedback.Visible = message.Length != 0;
    }

    /// <summary>Open the validated release tag on the project's fixed GitHub URL.</summary>
    private void OpenRelease()
    {
        if (newerTag is null)
            return;
        try { Process.Start(new ProcessStartInfo(ReleaseUpdates.ReleasePage + newerTag) { UseShellExecute = true }); }
        catch (System.ComponentModel.Win32Exception)
        {
            updateFeedback.Text = "Could not open the browser.";
            updateFeedback.Visible = true;
        }
    }

    private async Task TestAsync(bool shouldPause)
    {
        if (fallback.Checked)
        {
            SetPlaybackFeedback(mediaKey.Test(shouldPause));
            UpdateButtons();
            return;
        }
        var completion = media.TestAsync(shouldPause);
        SetPlaybackFeedback(shouldPause ? "Sending Pause…" : "Sending Play…");
        UpdateButtons();
        string result = await completion;
        if (!closing)
        {
            SetPlaybackFeedback(result);
            UpdateButtons();
        }
    }

    /// <summary>Keeps the UI alive long enough to release pauses during normal shutdown.</summary>
    private async void OnClosing(object? sender, FormClosingEventArgs args)
    {
        if (allowClose)
            return;
        args.Cancel = true;
        if (closing)
            return;
        closing = true;
        poll.Stop();
        dailyUpdate.Stop();
        checkUpdate.Enabled = false;
        releaseLink.Enabled = false;
        status.Text = "Releasing paused media…";
        UpdateButtons();
        try
        {
            if (fallback.Checked)
                mediaKey.Close();
            else
                await media.CloseAsync();
        }
        finally
        {
            allowClose = true;
            Close();
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            poll.Dispose();
            dailyUpdate.Dispose();
            capture.Dispose();
        }
        base.Dispose(disposing);
    }
}
