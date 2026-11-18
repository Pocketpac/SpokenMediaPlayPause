namespace SpokenMediaPause.Windows;

internal static class Program
{
    /// <summary>Starts one helper per Windows desktop session and enables physical-pixel coordinates.</summary>
    [STAThread]
    public static void Main()
    {
        using var singleton = new Mutex(true, @"Local\SpokenMediaPause.Windows", out bool first);
        if (!first)
        {
            MessageBox.Show("Spoken Media Pause is already running.", "Spoken Media Pause Beta");
            return;
        }
        try
        {
            Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainWindow());
        }
        finally { singleton.ReleaseMutex(); }
    }
}
