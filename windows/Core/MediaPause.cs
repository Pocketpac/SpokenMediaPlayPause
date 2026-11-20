using System.Diagnostics;

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
/// Serializes playback operations on the UI context with up to eight independent sessions in flight.
/// </summary>
internal sealed class MediaPause(IPlaybackSessions broker)
{
    private sealed record OwnedPause(IPlaybackSession Session, MediaItem Item, IPauseWatch Watch);
    private sealed class CommandDeclined(string method) : Exception($"A media player didn’t respond to {method}.") { }
    private const int ParallelPlayers = 8;
    private static readonly TimeSpan MarkerLease = TimeSpan.FromMilliseconds(550);
    private static readonly int[] RetryDelays = [250, 700];
    private readonly Dictionary<nint, OwnedPause> automatic = [];
    private readonly Dictionary<nint, OwnedPause> manual = [];
    private Task work = Task.CompletedTask;
    private CancellationTokenSource? discovery;
    private TaskCompletionSource? markerReady;
    private long? markerChecked;
    private bool active, started, releaseRequested, closing;
    private int retrying;
    public bool Busy => !work.IsCompleted;
    public bool OwnsAutomaticPause => automatic.Values.Any(item => item.Watch.Current);
    // A mode change must leave an existing pause with the controller that owns it.
    public bool HasPendingPause => OwnsAutomaticPause || manual.Values.Any(item => item.Watch.Current);
    internal Task WhenIdle => work;
    public string? LastError { get; private set; }
    public string? Recovery => retrying > 0 ? "Media player is slow. Retrying…" : LastError;

    // A queued retry may resume before the capture timer after a UI stall.
    private bool CanPause => markerChecked is long checkedAt && Stopwatch.GetElapsedTime(checkedAt) <= MarkerLease;

    /// <summary>Update pause intent and freshness, leaving already dispatched commands to finish.</summary>
    public void SetActive(bool value, bool fresh = true)
    {
        if (closing)
            return;
        markerChecked = fresh ? Stopwatch.GetTimestamp() : null;
        if (fresh && markerReady is { } ready)
        {
            markerReady = null;
            ready.TrySetResult();
        }
        if (active == value)
        {
            if (!Busy && active && fresh && !started)
                work = ReconcileAsync();
            return;
        }
        active = value;
        if (!active && (started || automatic.Count != 0))
            releaseRequested = true;
        else if (active && releaseRequested)
            releaseRequested = started = false;
        discovery?.Cancel();
        if (!Busy)
            work = ReconcileAsync();
    }

    /// <summary>Manually pause eligible sessions, or release pauses from a previous Test Pause.</summary>
    public Task<string> TestAsync(bool pause)
    {
        if (closing || active || Busy)
            return Task.FromResult("Playback controls are busy.");
        var request = TestAndReconcileAsync(pause);
        work = request;
        return request;
    }

    /// <summary>Wait for in-flight commands and one final release of any owned pauses.</summary>
    public Task CloseAsync()
    {
        closing = true;
        active = false;
        releaseRequested |= started || automatic.Count != 0;
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
                : await ReleaseAsync(manual, () => !active);
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
            if (releaseRequested && !active)
            {
                releaseRequested = started = false;
                LastError = null;
                await ReleaseAsync(automatic, () => !active);
            }
            else if (closing)
            {
                await ReleaseAsync(manual, () => true);
                // Owners still present after all attempts cannot be released safely.
                foreach (var item in automatic.Values.Concat(manual.Values))
                    item.Watch.Dispose();
                automatic.Clear();
                manual.Clear();
                return;
            }
            else if (active && CanPause && !started)
            {
                LastError = null;
                started = true;
                await PauseAsync(automatic, needsMarker: true);
            }
            else
                return;
        }
    }

    /// <summary>Retry temporary failures, including WinRT timeouts, with bounded backoff.</summary>
    private async Task<T> RetryAsync<T>(Func<Task<T>> attempt, CancellationToken cancellation)
    {
        bool retry = false;
        try
        {
            for (int index = 0; ; index++)
            {
                cancellation.ThrowIfCancellationRequested();
                try { return await attempt(); }
                catch (Exception problem) when (index < RetryDelays.Length && !cancellation.IsCancellationRequested &&
                    problem is TimeoutException or OperationCanceledException or CommandDeclined or
                    System.Runtime.InteropServices.COMException)
                {
                    if (!retry)
                    {
                        retry = true;
                        retrying++;
                    }
                    await Task.Delay(RetryDelays[index], cancellation);
                }
            }
        }
        finally
        {
            if (retry)
                retrying--;
        }
    }

    /// <summary>Wait without polling until capture confirms live narration again.</summary>
    private async Task WaitForMarkerAsync(CancellationToken cancellation)
    {
        while (!CanPause)
        {
            markerReady ??= new(TaskCreationOptions.RunContinuationsAsynchronously);
            await markerReady.Task.WaitAsync(cancellation);
        }
    }

    /// <summary>Let a ready session take the next open slot without waiting on a whole group.</summary>
    private static Task RunSlotsAsync<T>(IReadOnlyList<T> items, Func<T, Task> visit)
    {
        int next = 0;
        async Task RunSlot()
        {
            // Continuations share the UI context and claim each item before yielding.
            while (next < items.Count)
                await visit(items[next++]);
        }
        return Task.WhenAll(Enumerable.Range(0, Math.Min(items.Count, ParallelPlayers)).Select(_ => RunSlot()));
    }

    /// <summary>Capture identities before Pause and retain acknowledgments for safe release.</summary>
    private async Task<int> PauseAsync(Dictionary<nint, OwnedPause> owned, bool needsMarker = false)
    {
        using var cancellation = new CancellationTokenSource();
        discovery = cancellation;
        int count = 0;
        try
        {
            var sessions = await RetryAsync(() => broker.ListAsync(cancellation.Token), cancellation.Token);
            var seen = new HashSet<nint>();
            var live = sessions.Select(session => session.Id).ToHashSet();
            foreach (var old in owned.Values.Where(item => !live.Contains(item.Session.Id) || !item.Watch.Current).ToArray())
            {
                old.Watch.Dispose();
                owned.Remove(old.Session.Id);
            }
            var candidates = sessions.Where(session => seen.Add(session.Id) && !owned.ContainsKey(session.Id)).ToArray();
            await RunSlotsAsync(candidates, async session =>
            {
                if (cancellation.IsCancellationRequested)
                    return;
                var item = await TryPauseAsync(session, needsMarker, cancellation.Token);
                if (item is not null)
                {
                    owned.Add(item.Session.Id, item);
                    count++;
                }
            });
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
        catch (Exception problem) { RecordError(problem); }
        finally { discovery = null; }
        return count;
    }

    private async Task<OwnedPause?> TryPauseAsync(IPlaybackSession session, bool needsMarker,
        CancellationToken cancellation)
    {
        IPauseWatch? watch = null;
        try
        {
            MediaItem? requested = null;
            var item = await RetryAsync<OwnedPause?>(async () =>
            {
                Snapshot current;
                do
                {
                    if (needsMarker)
                        await WaitForMarkerAsync(cancellation);
                    cancellation.ThrowIfCancellationRequested();
                    watch ??= session.Watch();
                    current = await session.ReadAsync(cancellation);
                    cancellation.ThrowIfCancellationRequested();
                    if (!watch.Current || !current.Item.Identifiable ||
                        (requested is { } previous && current.Item != previous))
                        return null;
                    // SMTC can report Play as unavailable while already playing.
                    // Release checks CanPlay once the same owned item is paused.
                    if (current.Status != Playback.Playing || !current.CanPause)
                    {
                        // A timed-out Pause may have run. A paused status alone
                        // cannot show that this helper owns it.
                        if (requested is not null && current.Status == Playback.Paused)
                            LastError = "Couldn’t confirm a media pause.";
                        return null;
                    }
                    if (!needsMarker || CanPause)
                        break;
                } while (true);

                requested ??= current.Item;
                if (!await session.PauseAsync())
                    throw new CommandDeclined("Pause");
                return new OwnedPause(session, current.Item, watch);
            }, cancellation);
            if (item is not null)
                watch = null; // Ownership now retains the media-change subscription.
            return item;
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { return null; }
        catch (Exception problem) { RecordError(problem); return null; }
        finally { watch?.Dispose(); }
    }

    /// <summary>Recheck ownership before each Play, retaining the record across transient failures.</summary>
    private async Task<int> ReleaseAsync(Dictionary<nint, OwnedPause> owned, Func<bool> allowed)
    {
        if (owned.Count == 0)
            return 0;
        var items = owned.Values.ToArray();
        try
        {
            var live = (await RetryAsync(() => broker.ListAsync(CancellationToken.None), CancellationToken.None))
                .Select(session => session.Id).ToHashSet();
            int count = 0;
            await RunSlotsAsync(items, async item =>
            {
                try
                {
                    bool? played = live.Contains(item.Session.Id)
                        ? await RetryAsync(() => TryReleaseAsync(item, allowed), CancellationToken.None) : false;
                    if (played is null)
                        return; // A new narration deferred the pending release.
                    owned.Remove(item.Session.Id);
                    item.Watch.Dispose();
                    if (played == true)
                        count++;
                }
                catch (Exception problem) { RecordError(problem); }
            });
            return count;
        }
        catch (Exception problem) { RecordError(problem); return 0; }
    }

    private static async Task<bool?> TryReleaseAsync(OwnedPause pause, Func<bool> allowed)
    {
        if (!allowed())
            return null;
        if (!pause.Watch.Current)
            return false;
        var current = await pause.Session.ReadAsync(CancellationToken.None);
        if (!allowed())
            return null;
        if (!pause.Watch.Current || current.Status != Playback.Paused || !current.CanPlay || current.Item != pause.Item)
            return false;
        if (!await pause.Session.PlayAsync())
            throw new CommandDeclined("Play");
        return true;
    }

    private void RecordError(Exception problem) => LastError = problem switch
    {
        OperationCanceledException or TimeoutException => "A Windows media session timed out.",
        CommandDeclined => problem.Message,
        _ => $"Windows media controls error: {problem.Message}"
    };
}
