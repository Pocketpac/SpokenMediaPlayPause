using Windows.Media;
using Windows.Media.Control;
using Windows.Media.Core;
using Windows.Media.Playback;

/// <summary>List Windows media sessions or publish one controlled, real Windows media session.</summary>
internal static class Program
{
    private static async Task Main(string[] args)
    {
        if (args[0] == "list")
            await ListSessions();
        else
            await RunPlayer(args[0], args[1]);
    }

    private static async Task ListSessions()
    {
        var manager = await GlobalSystemMediaTransportControlsSessionManager.RequestAsync()
            .AsTask().WaitAsync(TimeSpan.FromSeconds(5));
        var sessions = manager.GetSessions();
        Console.WriteLine($"Windows media sessions visible to the test process: {sessions.Count}");
        foreach (var session in sessions)
        {
            var info = session.GetPlaybackInfo();
            var item = await session.TryGetMediaPropertiesAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(5));
            Console.WriteLine($"Session {session.SourceAppUserModelId} | '{item?.Title}' | {info.PlaybackStatus} | "
                + $"can pause {info.Controls.IsPauseEnabled}, can play {info.Controls.IsPlayEnabled}");
        }
    }

    private static async Task RunPlayer(string sample, string status)
    {
        using var player = new MediaPlayer();
        player.CommandManager.IsEnabled = false;
        var controls = player.SystemMediaTransportControls;
        controls.IsEnabled = controls.IsPauseEnabled = controls.IsPlayEnabled = true;
        controls.DisplayUpdater.Type = MediaPlaybackType.Music;
        controls.DisplayUpdater.MusicProperties.Title = "Spoken native test tone";
        controls.DisplayUpdater.MusicProperties.Artist = "Spoken fixtures";
        controls.DisplayUpdater.Update();
        controls.ButtonPressed += (_, command) =>
        {
            // The runner checks the state only after the real OS command arrives.
            try
            {
                switch (command.Button)
                {
                    case SystemMediaTransportControlsButton.Pause:
                        player.Pause();
                        controls.PlaybackStatus = MediaPlaybackStatus.Paused;
                        File.WriteAllText(status, "paused");
                        break;
                    case SystemMediaTransportControlsButton.Play:
                        player.Play();
                        controls.PlaybackStatus = MediaPlaybackStatus.Playing;
                        File.WriteAllText(status, "playing");
                        break;
                }
            }
            catch (Exception error)
            {
                Console.Error.WriteLine(error);
                File.WriteAllText(status, "error");
            }
        };
        using var source = MediaSource.CreateFromUri(new Uri(Path.GetFullPath(sample)));
        player.Source = source;
        player.IsLoopingEnabled = true;
        player.Play();
        controls.PlaybackStatus = MediaPlaybackStatus.Playing;
        File.WriteAllText(status, "playing");
        await Task.Delay(TimeSpan.FromMinutes(3));
    }
}
