using System.Runtime.InteropServices;
using SpokenMediaPause.Core;
using Windows.Foundation;
using Windows.Media.Control;
using WinRT;
using Session = Windows.Media.Control.GlobalSystemMediaTransportControlsSession;
using Manager = Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager;
using Status = Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus;

namespace SpokenMediaPause.Windows;

/// <summary>Adapts the Windows system media controls to the platform-independent pause logic.</summary>
internal sealed class SmtcSessions : IPlaybackSessions
{
    private static readonly Guid IUnknown = new("00000000-0000-0000-C000-000000000046");
    private Manager? manager;

    /// <summary>Returns the current sessions using canonical COM identity rather than application names.</summary>
    public async Task<IReadOnlyList<IPlaybackSession>> ListAsync(CancellationToken cancellation)
    {
        manager ??= await TimedAsync(Manager.RequestAsync, cancellation);
        cancellation.ThrowIfCancellationRequested();
        IReadOnlyList<Session> current;
        try { current = manager.GetSessions(); }
        catch (COMException)
        {
            // Reacquire the manager on retry if its service has restarted.
            manager = null;
            throw;
        }
        var sessions = new List<IPlaybackSession>(current.Count);
        foreach (var session in current)
        {
            nint address = ((IWinRTObject)session).NativeObject.ThisPtr;
            Guid id = IUnknown;
            if (Marshal.QueryInterface(address, in id, out var identity) < 0)
                continue;
            // The retained session keeps the COM object alive until this record is released.
            sessions.Add(new SmtcSession(identity, session));
            Marshal.Release(identity);
        }
        return sessions;
    }

    /// <summary>Bounds hung requests and allows read-only discovery to be cancelled on a state change.</summary>
    private static async Task<T> TimedAsync<T>(Func<IAsyncOperation<T>> call,
        CancellationToken cancellation = default, int milliseconds = 1500)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        timeout.CancelAfter(TimeSpan.FromMilliseconds(milliseconds));
        timeout.Token.ThrowIfCancellationRequested();
        return await call().AsTask(timeout.Token).WaitAsync(timeout.Token);
    }

    private sealed class SmtcSession(nint id, Session session) : IPlaybackSession
    {
        public nint Id => id;

        public async Task<Snapshot> ReadAsync(CancellationToken cancellation)
        {
            if (session.GetPlaybackInfo().PlaybackStatus is not (Status.Playing or Status.Paused))
                return new(Playback.Other, false, false, default);
            var metadata = await TimedAsync(session.TryGetMediaPropertiesAsync, cancellation);

            // Playback can change while a browser or player supplies its metadata.
            var info = session.GetPlaybackInfo();
            var status = info.PlaybackStatus switch
            {
                Status.Playing => Playback.Playing,
                Status.Paused => Playback.Paused,
                _ => Playback.Other
            };
            MediaItem item = metadata is null ? default : new(session.SourceAppUserModelId, metadata.Title,
                metadata.Subtitle, metadata.Artist, metadata.AlbumTitle, metadata.TrackNumber);
            return new(status, info.Controls.IsPauseEnabled, info.Controls.IsPlayEnabled, item);
        }

        public IPauseWatch Watch() => new SessionWatch(session);
        // An acknowledged command can be owned even when the player responds slowly.
        public Task<bool> PauseAsync() => TimedAsync(session.TryPauseAsync, milliseconds: 3000);
        public Task<bool> PlayAsync() => TimedAsync(session.TryPlayAsync, milliseconds: 3000);
    }

    /// <summary>Invalidates ownership if the item changes or the player leaves a pause on its own.</summary>
    private sealed class SessionWatch : IPauseWatch
    {
        private readonly Session session;
        private int invalidated, sawPaused;
        public bool Current => Volatile.Read(ref invalidated) == 0;

        public SessionWatch(Session value)
        {
            session = value;
            session.MediaPropertiesChanged += MediaChanged;
            try { session.PlaybackInfoChanged += PlaybackChanged; }
            catch
            {
                session.MediaPropertiesChanged -= MediaChanged;
                throw;
            }
        }

        private void MediaChanged(Session sender, MediaPropertiesChangedEventArgs args) =>
            Interlocked.Exchange(ref invalidated, 1);

        private void PlaybackChanged(Session sender, PlaybackInfoChangedEventArgs args)
        {
            // WinRT may dispatch these events off the UI thread. The watch only publishes flags.
            try
            {
                var state = sender.GetPlaybackInfo().PlaybackStatus;
                if (state == Status.Paused)
                    Interlocked.Exchange(ref sawPaused, 1);
                else if (state == Status.Stopped || (state == Status.Playing && Volatile.Read(ref sawPaused) == 1))
                    Interlocked.Exchange(ref invalidated, 1);
            }
            catch (COMException)
            {
                Interlocked.Exchange(ref invalidated, 1);
            }
        }

        public void Dispose()
        {
            // A disappeared server may refuse event removal. Either way its watch is retired.
            Interlocked.Exchange(ref invalidated, 1);
            try { session.MediaPropertiesChanged -= MediaChanged; }
            catch (COMException) { }
            try { session.PlaybackInfoChanged -= PlaybackChanged; }
            catch (COMException) { }
        }
    }
}
