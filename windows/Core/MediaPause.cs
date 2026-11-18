namespace SpokenMediaPause.Core;

internal enum Playback { Other, Playing, Paused }
internal readonly record struct MediaItem(string App, string Title, string Subtitle, string Artist,
    string Album, int Track)
{
    // SMTC has metadata but no universal track ID. Empty metadata cannot establish ownership.
    public bool Identifiable => !string.IsNullOrWhiteSpace(Title) || !string.IsNullOrWhiteSpace(Subtitle);
}
internal readonly record struct Snapshot(Playback Status, bool CanPause, bool CanPlay, MediaItem Item);

/// <summary>A watch becomes invalid when the player reports a media change or takes back playback.</summary>
internal interface IPauseWatch : IDisposable
{
    bool Current { get; }
}

/// <summary>A single OS playback session with a stable identity while the object is retained.</summary>
internal interface IPlaybackSession
{
    nint Id { get; }
    Task<Snapshot> ReadAsync(CancellationToken cancellation);
    IPauseWatch Watch();
    Task<bool> PauseAsync();
    Task<bool> PlayAsync();
}

internal interface IPlaybackSessions
{
    Task<IReadOnlyList<IPlaybackSession>> ListAsync(CancellationToken cancellation);
}

/// <summary>
/// Serializes manual tests and automatic control on the desktop UI thread.
/// Schedules at most one operation, with up to eight independent players in flight.
/// </summary>
internal sealed class MediaPause(IPlaybackSessions broker)
{
    private sealed record OwnedPause(IPlaybackSession Session, MediaItem Item, IPauseWatch Watch);
    private const int ParallelPlayers = 8;
    private readonly Dictionary<nint, OwnedPause> automatic = [];
    private readonly Dictionary<nint, OwnedPause> manual = [];
    private Task work = Task.CompletedTask;
    private CancellationTokenSource? discovery;
    private bool active, started, releaseRequested, closing;
    public bool Busy => !work.IsCompleted;
    public bool OwnsAutomaticPause => automatic.Count != 0;
    internal Task WhenIdle => work;
    public string? LastError { get; private set; }

    /// <summary>Requests a new narration state and cancels stale discovery without cancelling a Pause already sent.</summary>
    public void SetActive(bool value)
    {
        if (closing || active == value)
            return;
        active = value;
        if (!active && started)
            releaseRequested = true;
        discovery?.Cancel();
        if (!Busy)
            work = ReconcileAsync();
    }

    /// <summary>Manually pauses eligible sessions, or releases only pauses owned by an earlier Test Pause.</summary>
    public Task<string> TestAsync(bool pause)
    {
        if (closing || active || Busy)
            return Task.FromResult("Playback controls are busy.");
        var request = TestAndReconcileAsync(pause);
        work = request;
        return request;
    }

    /// <summary>Stops discovery and waits for in-flight commands and owned releases before the UI exits.</summary>
    public Task CloseAsync()
    {
        closing = true;
        active = false;
        releaseRequested |= started;
        discovery?.Cancel();
        if (!Busy)
            work = ReconcileAsync();
        return work;
    }

    private async Task<string> TestAndReconcileAsync(bool pause)
    {
        await Task.Yield();
        string result;
        try
        {
            LastError = null;
            int count = pause
                ? (closing || active ? 0 : await PauseAsync(manual))
                : await ReleaseAsync(manual);
            result = count > 0
                ? $"Sent {(pause ? "Pause" : "Play")} to {count} {(count == 1 ? "player" : "players")}."
                : LastError ?? (pause ? "No eligible playing media found."
                    : "No media paused by Test Pause is still selected.");
        }
        finally
        {
            await ReconcileAsync();
        }
        return result;
    }

    private async Task ReconcileAsync()
    {
        await Task.Yield();
        while (true)
        {
            if (releaseRequested)
            {
                releaseRequested = started = false;
                await ReleaseAsync(automatic);
            }
            else if (closing)
            {
                await ReleaseAsync(manual);
                return;
            }
            else if (active && !started)
            {
                LastError = null;
                started = true;
                await PauseAsync(automatic);
            }
            else
                return;
        }
    }

    /// <summary>Captures identities before issuing Pause and retains successful requests for safe release.</summary>
    private async Task<int> PauseAsync(Dictionary<nint, OwnedPause> owned)
    {
        using var cancellation = new CancellationTokenSource();
        discovery = cancellation;
        int count = 0;
        try
        {
            var sessions = await broker.ListAsync(cancellation.Token);
            var seen = new HashSet<nint>();
            var live = sessions.Select(session => session.Id).ToHashSet();
            foreach (var old in manual.Values.Where(item => !live.Contains(item.Session.Id)).ToArray())
            {
                old.Watch.Dispose();
                manual.Remove(old.Session.Id);
            }
            foreach (var group in sessions.Where(session => seen.Add(session.Id)).Chunk(ParallelPlayers))
            {
                if (cancellation.IsCancellationRequested)
                    break;
                var results = await Task.WhenAll(group.Select(session => TryPauseAsync(session, cancellation.Token)));
                foreach (var item in results)
                {
                    if (item is null)
                        continue;
                    if (owned.Remove(item.Session.Id, out var old))
                        old.Watch.Dispose();
                    owned.Add(item.Session.Id, item);
                    count++;
                }
            }
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception problem) { RecordError(problem); }
        finally { discovery = null; }
        return count;
    }

    private async Task<OwnedPause?> TryPauseAsync(IPlaybackSession session, CancellationToken cancellation)
    {
        IPauseWatch? watch = null;
        try
        {
            if (cancellation.IsCancellationRequested)
                return null;
            watch = session.Watch();
            var current = await session.ReadAsync(cancellation);
            if (cancellation.IsCancellationRequested || !watch.Current || current.Status != Playback.Playing ||
                !current.CanPause || !current.CanPlay || !current.Item.Identifiable)
                return null;
            if (!await session.PauseAsync())
                return null;
            var item = new OwnedPause(session, current.Item, watch);
            watch = null; // The returned record now owns the media-change subscription.
            return item;
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { return null; }
        catch (Exception problem) { RecordError(problem); return null; }
        finally { watch?.Dispose(); }
    }

    /// <summary>Rechecks the same live session and item before Play, then drops all released ownership.</summary>
    private async Task<int> ReleaseAsync(Dictionary<nint, OwnedPause> owned)
    {
        if (owned.Count == 0)
            return 0;
        var items = owned.Values.ToArray();
        owned.Clear();
        try
        {
            var live = (await broker.ListAsync(CancellationToken.None)).Select(session => session.Id).ToHashSet();
            int count = 0;
            foreach (var group in items.Where(item => live.Contains(item.Session.Id)).Chunk(ParallelPlayers))
            {
                var results = await Task.WhenAll(group.Select(TryReleaseAsync));
                count += results.Count(value => value);
            }
            return count;
        }
        catch (Exception problem) { RecordError(problem); return 0; }
        finally
        {
            foreach (var item in items)
                item.Watch.Dispose();
        }
    }

    private async Task<bool> TryReleaseAsync(OwnedPause pause)
    {
        try
        {
            if (!pause.Watch.Current)
                return false;
            var current = await pause.Session.ReadAsync(CancellationToken.None);
            return pause.Watch.Current && current.Status == Playback.Paused && current.CanPlay
                && current.Item == pause.Item && await pause.Session.PlayAsync();
        }
        catch (Exception problem) { RecordError(problem); return false; }
    }

    private void RecordError(Exception problem) =>
        LastError = problem is OperationCanceledException ? "A Windows media session timed out."
            : $"Windows media controls error: {problem.Message}";
}
