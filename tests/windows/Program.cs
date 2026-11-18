using System.Collections.Concurrent;
using SpokenMediaPause.Core;

/// <summary>Runs decoder, timing, and media ownership regressions against portable fixtures.</summary>
static class Checks
{
    static void Main()
    {
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
        await CheckMultiplePlayersAsync();
        await CheckCancellationAsync();
        await CheckClosingAsync();
    }

    static void Check(bool value, string message)
    {
        if (!value)
            throw new InvalidOperationException(message);
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
        Check(!narration.Active, "Missing capture must release after 900 ms.");
        narration.Update(active with { Heartbeat = true }, 3601);
        Check(!narration.Connected && !narration.Active, "A frozen heartbeat must expire.");
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
        public TaskCompletionSource<bool>? PauseReply { get; init; }
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
            return new(Status, true, true, new("App", Title, "", "", "", 1));
        }

        public Task<bool> PauseAsync()
        {
            PauseCalls++;
            Status = Playback.Paused;
            Issued.TrySetResult();
            return PauseReply?.Task ?? Task.FromResult(true);
        }

        public Task<bool> PlayAsync()
        {
            PlayCalls++;
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
