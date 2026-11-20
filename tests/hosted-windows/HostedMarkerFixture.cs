using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

/// <summary>Simulates WoW's window title, process name and live brass marker on the Windows desktop.</summary>
internal static class HostedMarkerFixture
{
    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();

    // The addon publishes 2 0 2 1 mode (2-mode) heartbeat 0 on the badge's lower edge.
    private static readonly Color[] Tones = {
        Color.FromArgb(54, 39, 23), Color.FromArgb(29, 43, 47), Color.FromArgb(52, 32, 48),
        Color.FromArgb(30, 35, 59), Color.FromArgb(32, 42, 29)
    };

    [STAThread]
    private static void Main(string[] args)
    {
        SetProcessDPIAware();
        using (var window = new Form {
            Text = "World of Warcraft Forever", TopMost = true,
            StartPosition = FormStartPosition.Manual, Location = new Point(40, 40),
            ClientSize = new Size(320, 220), BackColor = Color.FromArgb(65, 83, 42)
        })
        using (var timer = new Timer { Interval = 400 })
        {
            string state = "idle";
            int clock = 0;
            window.Paint += delegate(object sender, PaintEventArgs paint) {
                if (state == "missing")
                    return;
                int mode = state == "narrating" ? 1 : 0;
                int[] strip = { 0, 2, 0, 2, 1, mode, 2 - mode, 3 + clock, 0 };
                for (int slot = 0; slot < strip.Length; slot++)
                    using (var brush = new SolidBrush(Tones[strip[slot]]))
                        paint.Graphics.FillRectangle(brush, 13 + slot * 5, 44, 5, 6);
            };
            timer.Tick += delegate {
                try { state = File.ReadAllText(args[0]).Trim(); }
                catch (IOException) { return; } // The test may be replacing the control file.
                if (state == "close")
                {
                    window.Close();
                    return;
                }
                clock ^= 1;
                window.Invalidate();
            };
            timer.Start();
            Application.Run(window);
        }
    }
}
