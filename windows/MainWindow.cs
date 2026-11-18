using System.Diagnostics;
using SpokenMediaPause.Core;

namespace SpokenMediaPause.Windows;

/// <summary>Shows capture status and exposes manual playback tests even before WoW opens.</summary>
internal sealed class MainWindow : Form
{
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private readonly WowWindowCapture capture = new();
    private readonly Narration narration = new();
    private readonly MediaPause media = new(new SmtcSessions());
    private readonly System.Windows.Forms.Timer poll = new() { Interval = 80 };
    private readonly Label status = new() { AutoSize = true, Text = "Waiting for WoW" };
    private readonly Label feedback = new() { AutoSize = true, MaximumSize = new(340, 0) };
    private readonly Button pause = new() { AutoSize = true, Text = "Test Pause" };
    private readonly Button play = new() { AutoSize = true, Text = "Test Play" };
    private bool closing, allowClose;

    public MainWindow()
    {
        Text = "Spoken Media Pause — Windows Beta";
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new(390, 205);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        var layout = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false,
            Padding = new(14), AutoScroll = true
        };
        layout.Controls.Add(status);
        layout.Controls.Add(new Label { AutoSize = true, MaximumSize = new(340, 0),
            Text = "Keep WoW’s top-left corner visible. Click the in-game marker to toggle auto-pause." });
        var buttons = new FlowLayoutPanel { AutoSize = true, Margin = Padding.Empty };
        buttons.Controls.AddRange([pause, play]);
        layout.Controls.Add(buttons);
        layout.Controls.Add(feedback);
        Controls.Add(layout);
        pause.Click += async (_, _) => await TestAsync(true);
        play.Click += async (_, _) => await TestAsync(false);
        poll.Tick += (_, _) => Poll();
        Shown += (_, _) => poll.Start();
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
        media.SetActive(narration.Active);
        status.Text = !capture.HasGame ? "Waiting for WoW"
            : !narration.Connected ? narration.Active ? "Waiting for Spoken’s marker (pause held)" : "Waiting for Spoken’s marker"
            : narration.Disabled ? "Auto-pause off"
            : !narration.Active ? "Ready"
            : media.OwnsAutomaticPause ? "Media paused" : "Spoken is narrating";
        UpdateButtons();
    }

    private void UpdateButtons() => pause.Enabled = play.Enabled = !closing && !narration.Active && !media.Busy;

    private async Task TestAsync(bool shouldPause)
    {
        var completion = media.TestAsync(shouldPause);
        feedback.Text = shouldPause ? "Sending Pause…" : "Sending Play…";
        UpdateButtons();
        string result = await completion;
        if (!closing)
        {
            feedback.Text = result;
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
        status.Text = "Releasing paused media…";
        UpdateButtons();
        try { await media.CloseAsync(); }
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
            capture.Dispose();
        }
        base.Dispose(disposing);
    }
}
