using System.Collections.Concurrent;
using SpokenMediaPause.Core;

/// <summary>Runs decoder, timing, and media ownership regressions against portable fixtures.</summary>
static class Checks
{
    static void Main(string[] args)
    {
        if (args.Length == 1)
            CheckFixtures(args[0]);
        using var context = new UiContext();
        SynchronizationContext.SetSynchronizationContext(context);
        var completed = RunAsync();
        context.Drain(completed);
        completed.GetAwaiter().GetResult();
        Console.WriteLine("Marker and playback ownership checks passed.");
    }

    static async Task RunAsync()
    {
        CheckMarker();
        CheckMediaKeyPlayback();
        await CheckMultiplePlayersAsync();
        await CheckCancellationAsync();
        await CheckClosingAsync();
        await CheckRecoveryAsync();
        await CheckUpdatesAsync();
    }

    /// <summary>Check that fallback sends paired keys once and holds its state across capture gaps.</summary>
    static void CheckMediaKeyPlayback()
    {
        int sent = 0;
        bool fail = false;
        var playback = new MediaKeyPlayback(() =>
        {
            sent++;
            return !fail;
        });
        playback.Test(false);
        Check(sent == 0, "Test Play needs an earlier toggle to pair with.");
        playback.Test(true);
        playback.Test(true);
        playback.SetActive(true, true);
        playback.SetActive(false, false);
        Check(sent == 1 && playback.HasPendingToggle, "Automatic narration must leave a manual test pause alone.");
        playback.Test(false);
        Check(sent == 2 && !playback.HasPendingToggle, "Test Play must pair with Test Pause once.");

        playback.SetActive(true, false);
        Check(sent == 2, "Wait for live narration before sending a key.");
        playback.SetActive(true, true);
        playback.SetActive(true, false);
        playback.SetActive(true, true);
        Check(sent == 3 && playback.AutomaticTogglePending, "Capture gaps must not toggle again.");
        playback.SetActive(false, true);
        playback.SetActive(false, true);
        Check(sent == 4 && !playback.HasPendingToggle, "Send one resume key when narration ends.");

        fail = true;
        playback.SetActive(true, true);
        playback.SetActive(true, true);
        playback.SetActive(false, true);
        Check(sent == 5 && !playback.HasPendingToggle, "A blocked initial key must not trigger a blind resume.");
        fail = false;
        playback.SetActive(true, true);
        fail = true;
        playback.SetActive(false, true);
        playback.SetActive(false, true);
        Check(sent == 7 && playback.HasPendingToggle, "A blocked resume must not be retried on every poll.");
        fail = false;
        playback.Close();
        playback.Close();
        Check(sent == 8 && !playback.HasPendingToggle, "Close pairs an outstanding key once.");
    }

    static void Check(bool value, string message)
    {
        if (!value)
            throw new InvalidOperationException(message);
    }

    /// <summary>Read the same scaled addon crops as the Linux helper, including occlusions.</summary>
    static void CheckFixtures(string directory)
    {
        var modes = new Dictionary<string, MarkerMode>
        {
            ["idle"] = MarkerMode.Idle,
            ["narrating"] = MarkerMode.Narrating,
            ["off"] = MarkerMode.Disabled
        };
        int count = 0;
        foreach (var row in File.ReadLines(Path.Combine(directory, "expected.tsv")))
        {
            var fields = row.Split('\t');
            var bytes = File.ReadAllBytes(Path.Combine(directory, fields[0]));
            var pixels = new MarkerPixels(bytes, 200, 160, 800, false);
            MarkerReading? expected = modes.TryGetValue(fields[1], out var mode)
                ? new(mode, fields[2] == "1") : null;
            var decoder = new MarkerDecoder();
            var actual = decoder.Sample(pixels, 0);
            Check(actual == expected, $"Fixture {fields[0]} decoded as {actual} instead of {expected}.");
            Check(decoder.Sample(pixels, 80) == expected, $"Cached fixture {fields[0]} changed reading.");
            if (decoder.Anchor is { Brass: true } anchor)
            {
                // An anchor must survive changed colors on partially covered cell edges.
                string variant = fields[0].Split('-', 4)[3];
                bool heartbeat = fields[2] == "0";
                string clock = heartbeat ? "light" : "dark";
                foreach (var (nextName, nextMode) in modes)
                {
                    string name = $"brass-{nextName}-{clock}-{variant}";
                    var next = new MarkerPixels(File.ReadAllBytes(Path.Combine(directory, name)), 200, 160, 800, false);
                    Check(next.Read(anchor) == new MarkerReading(nextMode, heartbeat),
                        $"Cached fixture {fields[0]} failed to read {name}.");
                }
            }
            count++;
        }
        Console.WriteLine($"Decoded {count} Lua-rendered BGRA crops.");
    }

    static void CheckMarker()
    {
        const int width = 200, height = 160, size = 17;
        var pixels = new byte[width * height * 4];
        int[] colors = [5, 3, 2, 6, 1, 7];
        for (int cell = 0; cell < colors.Length; cell++)
        {
            int color = colors[cell], left = 12 + cell % 3 * size, top = 12 + cell / 3 * size;
            for (int y = top; y < top + size; y++)
            for (int x = left; x < left + size; x++)
            {
                int index = (y * width + x) * 4;
                pixels[index] = (color & 1) != 0 ? (byte)255 : (byte)0;
                pixels[index + 1] = (color & 2) != 0 ? (byte)255 : (byte)0;
                pixels[index + 2] = (color & 4) != 0 ? (byte)255 : (byte)0;
            }
        }

        var frame = new MarkerPixels(pixels, width, height, width * 4, false);
        var decoder = new MarkerDecoder();
        var active = new MarkerReading(MarkerMode.Narrating, true);
        Check(decoder.Sample(frame, 0) == active, "The scaled 0.3.0 marker must decode.");
        Check(decoder.Sample(frame, 80) == active, "The cached marker must decode.");
        Array.Clear(pixels);
        Check(decoder.Sample(frame, 160) is null, "An obscured marker must be rejected.");

        var narration = new Narration();
        narration.Update(active, 0);
        Check(!narration.Active, "A static marker must not claim playback.");
        active = active with { Heartbeat = false };
        narration.Update(active, 400);
        Check(narration.Active, "A live narration marker must claim playback.");
        var idle = active with { Mode = MarkerMode.Idle };
        narration.Update(idle, 500);
        narration.Update(idle, 949);
        Check(narration.Active, "An idle frame must honor the gap between lines.");
        narration.Update(idle, 950);
        Check(!narration.Active, "The idle grace must expire.");
        narration.Update(active, 1000);
        narration.Update(active with { Mode = MarkerMode.Disabled }, 1100);
        Check(narration.Disabled && !narration.Active, "Disabling must release immediately.");
        narration.Update(active with { Heartbeat = true }, 1200);
        narration.Update(null, 2100);
        Check(narration.Active, "Missing capture must hold the existing pause.");
        narration.Update(active with { Heartbeat = true }, 3601);
        Check(!narration.Connected && narration.Active, "A frozen heartbeat must expire without releasing media.");
        narration.Update(idle, 4000);
        narration.Update(null, 4600);
        Check(narration.Active, "One idle sample followed by another gap must not release.");
        narration.Update(idle, 4780);
        Check(!narration.Connected && narration.Active && !narration.CanPause,
            "A recovered frame with a stale clock must hold without starting another Pause.");
        narration.Update(idle with { Heartbeat = true }, 5000);
        Check(narration.Connected && !narration.Active, "A fresh idle frame can finish the held release.");
        narration.Update(idle with { Heartbeat = false }, 5450);
        Check(narration.Connected && !narration.Active, "A live idle marker must release after recovery.");
        narration.Update(null, 9000);
        Check(!narration.Active, "A gap while idle must not start a pause.");
        narration.Update(active with { Heartbeat = true }, 9100);
        narration.Reset();
        Check(!narration.Connected && !narration.Active, "Game exit must clear the held pause.");
    }

    static async Task CheckMultiplePlayersAsync()
    {
        var players = Enumerable.Range(1, 10).Select(id => new Player(id)).ToArray();
        var prePaused = new Player(11) { Status = Playback.Paused };
        var blank = new Player(12) { Title = "" };
        var broker = new Broker([.. players, players[0], prePaused, blank]);
        var media = new MediaPause(broker);
        Check((await media.TestAsync(false)).StartsWith("No media"), "Test Play cannot claim another pause.");
        Check((await media.TestAsync(true)).Contains("10 players"), "Both batches of players must pause.");
        Check(players.All(p => p.PauseCalls == 1) && prePaused.PauseCalls == 0 && blank.PauseCalls == 0,
            "Duplicate identities and ineligible sessions must be skipped.");
        Check((await media.TestAsync(false)).Contains("10 players"), "Manual release must cover only owned sessions.");

        media.SetActive(true);
        await media.WhenIdle;
        Check(media.OwnsAutomaticPause && players.All(p => p.Status == Playback.Paused),
            "Narration must pause every eligible playing session.");
        players[0].Title = "Another item";
        players[1].Invalidate();
        var replacement = new Player(13) { Status = Playback.Paused };
        broker.Sessions = [.. players.Where(p => p != players[2]), replacement, prePaused, blank];
        media.SetActive(false);
        await media.WhenIdle;
        Check(players.Take(3).All(p => p.PlayCalls == 1) && replacement.PlayCalls == 0,
            "A changed item, invalidated watch, or disconnected owner must not receive another Play.");
        Check(players.Skip(3).All(p => p.PlayCalls == 2) && prePaused.PlayCalls == 0,
            "Release must resume all other owned sessions and leave pre-paused media alone.");
        await media.CloseAsync();
    }

    static async Task CheckCancellationAsync()
    {
        var fast = new Player(1);
        var slow = new Player(2) { StallSnapshot = true };
        var media = new MediaPause(new Broker([fast, slow]));
        media.SetActive(true);
        await slow.Entered.Task.WaitAsync(TimeSpan.FromSeconds(2));
        Check(fast.Status == Playback.Paused, "A slow player must not delay a ready player.");
        media.SetActive(false);
        await media.WhenIdle.WaitAsync(TimeSpan.FromSeconds(2));
        Check(fast.Status == Playback.Playing && slow.PauseCalls == 0,
            "Release must cancel discovery and resume ownership without the stalled player.");
    }

    static async Task CheckClosingAsync()
    {
        var deferred = new Player(1) { PauseReply = new() };
        var media = new MediaPause(new Broker([deferred]));
        media.SetActive(true);
        await deferred.Issued.Task.WaitAsync(TimeSpan.FromSeconds(2));
        var closing = media.CloseAsync();
        Check(!closing.IsCompleted, "Shutdown must retain an in-flight Pause command.");
        deferred.PauseReply.SetResult(true);
        await closing.WaitAsync(TimeSpan.FromSeconds(2));
        Check(deferred.PlayCalls == 1 && deferred.Status == Playback.Playing,
            "Shutdown must release a pause acknowledged after closing began.");
    }

    /// <summary>Cover isolated retries, stale marker deferral, ownership retention, and user overrides.</summary>
    static async Task CheckRecoveryAsync()
    {
        var slow = new Player(1) { PauseFailures = 1, PlayRequiresPaused = true };
        var peers = Enumerable.Range(2, 9).Select(id => new Player(id)).ToArray();
        var media = new MediaPause(new Broker([slow, .. peers]));
        media.SetActive(true);
        await slow.Issued.Task.WaitAsync(TimeSpan.FromSeconds(2));
        media.SetActive(true, fresh: false);
        await peers[^1].Issued.Task.WaitAsync(TimeSpan.FromSeconds(2));
        await Task.Delay(350);
        Check(slow.PauseCalls == 1 && media.Busy, "A deferred retry must wait for a fresh marker.");
        media.SetActive(true);
        await media.WhenIdle.WaitAsync(TimeSpan.FromSeconds(3));
        Check(slow.PauseCalls == 2 && slow.Status == Playback.Paused,
            "Pause should retry with live narration even when Windows disables Play while playing.");
        slow.PlayFailures = 1;
        var changed = peers[0];
        changed.PlayFailures = 1;
        media.SetActive(false);
        await changed.PlayIssued.Task.WaitAsync(TimeSpan.FromSeconds(2));
        changed.Title = "New track";
        changed.Invalidate();
        await media.WhenIdle.WaitAsync(TimeSpan.FromSeconds(3));
        Check(slow.PlayCalls == 2 && slow.Status == Playback.Playing, "A timeout must preserve ownership for the Play retry.");
        Check(changed.PlayCalls == 1 && peers.Skip(1).All(player => player.PlayCalls == 1),
            "A changed item must be skipped while unrelated players resume.");
        await media.CloseAsync();

        var late = new Player(20) { PauseFailures = 1 };
        var cancelled = new MediaPause(new Broker([late]));
        cancelled.SetActive(true);
        await late.Issued.Task.WaitAsync(TimeSpan.FromSeconds(2));
        // Pause the event loop long enough for a queued retry to see an expired marker lease.
        Thread.Sleep(600);
        await Task.Delay(100);
        Check(late.PauseCalls == 1 && cancelled.Busy, "A stalled UI must expire the last capture verdict.");
        cancelled.SetActive(false);
        await cancelled.WhenIdle.WaitAsync(TimeSpan.FromSeconds(2));
        Check(late.PauseCalls == 1, "Ending narration must cancel a deferred Pause.");
        await cancelled.CloseAsync();

        var repeated = new Player(21) { PlayFailures = 3 };
        var releasing = new MediaPause(new Broker([repeated]));
        releasing.SetActive(true);
        await releasing.WhenIdle;
        releasing.SetActive(false);
        await releasing.WhenIdle.WaitAsync(TimeSpan.FromSeconds(3));
        Check(repeated.PlayCalls == 3 && repeated.Status == Playback.Paused && releasing.LastError is not null,
            "Play retries must be bounded and report exhaustion.");
        await releasing.CloseAsync();
        Check(repeated.PlayCalls == 4 && repeated.Status == Playback.Playing, "Closing can release a retained pause.");
    }

    /// <summary>Check numeric versions, daily caching, both Windows asset names, and offline results.</summary>
    static async Task CheckUpdatesAsync()
    {
        string directory = Path.Combine(Path.GetTempPath(), "spoken-update-" + Guid.NewGuid());
        string cache = Path.Combine(directory, "release-check.json");
        int requests = 0;
        var now = new DateTimeOffset(2026, 11, 20, 0, 0, 0, TimeSpan.Zero);
        string tag = "addon-v0.5.10", asset = "Spoken-Media-Pause-Companion-Windows-x64.exe";
        bool offline = false;
        Task<string> Fetch()
        {
            requests++;
            return offline ? Task.FromException<string>(new HttpRequestException("offline")) :
                Task.FromResult($$"""{"tag_name":"{{tag}}","draft":false,"prerelease":false,"assets":[{"name":"{{asset}}","state":"uploaded"}]}""");
        }

        try
        {
            var updater = new ReleaseUpdates(new Version("0.5.9"), cache, Fetch, () => now);
            Check(await updater.CheckAsync(false) == new UpdateResult(false, tag), "0.5.10 is newer than 0.5.9.");
            Check(await updater.CheckAsync(false) == new UpdateResult(false, tag) && requests == 1,
                "An automatic check should reuse the daily result.");
            tag = "v0.5.9";
            Check(await updater.CheckAsync(true) == new UpdateResult(false, null) && requests == 2,
                "Manual checks bypass the cache.");
            tag = "v0.5.10";
            asset = "Spoken-Media-Pause-Companion-Linux-x64.AppImage";
            now = now.AddDays(1);
            Check(await updater.CheckAsync(false) == new UpdateResult(false, null) && requests == 3,
                "A release without a Windows download should not advertise an update.");
            asset = "Spoken-Media-Pause-Windows-x64-Beta.exe";
            Check(await updater.CheckAsync(true) == new UpdateResult(false, tag),
                "An older Windows download name should still advertise an update.");
            offline = true;
            Check(await updater.CheckAsync(true) == new UpdateResult(true, tag),
                "An offline manual check reports failure and keeps the last known update.");
        }
        finally { Directory.Delete(directory, recursive: true); }
    }

    /// <summary>Emulates the serial callback context of the WinForms message loop.</summary>
    sealed class UiContext : SynchronizationContext, IDisposable
    {
        private readonly BlockingCollection<(SendOrPostCallback Callback, object? State)> queue = new();
        public override void Post(SendOrPostCallback callback, object? state) => queue.Add((callback, state));

        public void Drain(Task task)
        {
            while (!task.IsCompleted)
            {
                if (!queue.TryTake(out var callback, TimeSpan.FromSeconds(5)))
                    throw new TimeoutException("A playback check did not finish.");
                callback.Callback(callback.State);
            }
        }

        public void Dispose() => queue.Dispose();
    }

    sealed class Broker(IReadOnlyList<IPlaybackSession> sessions) : IPlaybackSessions
    {
        public IReadOnlyList<IPlaybackSession> Sessions { get; set; } = sessions;
        public Task<IReadOnlyList<IPlaybackSession>> ListAsync(CancellationToken cancellation) =>
            Task.FromResult(Sessions);
    }

    /// <summary>Records explicit Pause/Play commands and can simulate delayed replies and item changes.</summary>
    sealed class Player(nint id) : IPlaybackSession
    {
        public nint Id => id;
        public string Title { get; set; } = "Track";
        public Playback Status { get; set; } = Playback.Playing;
        public bool StallSnapshot { get; init; }
        public TaskCompletionSource Entered { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource Issued { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource PlayIssued { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource<bool>? PauseReply { get; init; }
        public int PauseFailures { get; set; }
        public int PlayFailures { get; set; }
        public bool PlayRequiresPaused { get; init; }
        public int PauseCalls { get; private set; }
        public int PlayCalls { get; private set; }
        private int revision;

        public void Invalidate() => revision++;
        public IPauseWatch Watch() => new WatchState(this, revision);

        public async Task<Snapshot> ReadAsync(CancellationToken cancellation)
        {
            if (StallSnapshot)
            {
                Entered.TrySetResult();
                await Task.Delay(Timeout.Infinite, cancellation);
            }
            // A live Windows media session may enable Play only in its paused state.
            return new(Status, true, !PlayRequiresPaused || Status == Playback.Paused,
                new("App", Title, "", "", "", 1));
        }

        public Task<bool> PauseAsync()
        {
            PauseCalls++;
            Issued.TrySetResult();
            if (PauseFailures > 0)
            {
                PauseFailures--;
                return Task.FromException<bool>(new TimeoutException());
            }
            Status = Playback.Paused;
            return PauseReply?.Task ?? Task.FromResult(true);
        }

        public Task<bool> PlayAsync()
        {
            PlayCalls++;
            PlayIssued.TrySetResult();
            if (PlayFailures > 0)
            {
                PlayFailures--;
                return Task.FromException<bool>(new TimeoutException());
            }
            Status = Playback.Playing;
            return Task.FromResult(true);
        }

        sealed class WatchState(Player player, int original) : IPauseWatch
        {
            private bool disposed;
            public bool Current => !disposed && player.revision == original;
            public void Dispose() => disposed = true;
        }
    }
}
