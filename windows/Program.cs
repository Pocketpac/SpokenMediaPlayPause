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
            MessageBox.Show($"{Application.ProductName} is already running.", Application.ProductName);
            return;
        }
        try
        {
            Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            // Load the packaged icon once and keep it alive until the window closes.
            using var iconStream = typeof(Program).Assembly.GetManifestResourceStream("SpokenMediaPause.AppIcon");
            using var icon = new Icon(iconStream!);
            using var window = new MainWindow { Icon = icon };
            Application.Run(window);
        }
        finally { singleton.ReleaseMutex(); }
    }
}
