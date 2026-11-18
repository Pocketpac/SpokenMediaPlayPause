using System.ComponentModel;
using System.Diagnostics;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
using SpokenMediaPause.Core;

namespace SpokenMediaPause.Windows;

/// <summary>
/// Finds a visible WoW client and samples only its on-screen upper-left corner.
/// Frames stay in a single reusable bitmap and are never stored.
/// </summary>
internal sealed class WowWindowCapture : IDisposable
{
    private static readonly HashSet<string> GameProcesses = new(StringComparer.OrdinalIgnoreCase)
    {
        "Wow", "WowForever", "WowB", "WowT", "WowClassic", "WowClassicB"
    };

    private readonly Bitmap crop = new(MarkerDecoder.CaptureWidth, MarkerDecoder.CaptureHeight,
        PixelFormat.Format32bppArgb);
    private readonly Graphics graphics;
    private readonly MarkerDecoder decoder = new();
    private long searchAfter;
    private uint processId;
    private nint window;
    private SafeProcessHandle? game;
    public bool HasGame => window != 0 || game is not null;

    public WowWindowCapture() => graphics = Graphics.FromImage(crop);

    /// <summary>Returns a sample only when the tracked client still owns the visible marker.</summary>
    public unsafe MarkerReading? Sample(long now)
    {
        if (window != 0 && (!Native.IsWindow(window) ||
            Native.GetWindowThreadProcessId(window, out uint owner) == 0 || owner != processId))
        {
            window = 0;
            decoder.Reset();
            searchAfter = now;
            if (game is null)
                return null;
        }
        if (window == 0 && now >= searchAfter)
        {
            searchAfter = now + 1000;
            // A signaled process has exited. A window can disappear and return
            // with a new handle while that same game process is still running.
            if (game is not null && Native.WaitForSingleObject(game, 0) == 0)
            {
                game.Dispose();
                game = null;
                return null;
            }
            FindWindow();
        }
        if (window == 0 || !Native.IsWindowVisible(window) || Native.IsIconic(window) ||
            !Native.GetClientRect(window, out var client))
            return null;

        int width = Math.Min(client.Right, MarkerDecoder.CaptureWidth);
        int height = Math.Min(client.Bottom, MarkerDecoder.CaptureHeight);
        var origin = new Native.Point();
        if (width < 12 || height < 12 || !Native.ClientToScreen(window, ref origin)
            || !SystemInformation.VirtualScreen.Contains(new Rectangle(origin.X, origin.Y, width, height)))
            return null;

        try
        {
            graphics.CopyFromScreen(origin.X, origin.Y, 0, 0, new Size(width, height), CopyPixelOperation.SourceCopy);
        }
        catch (Win32Exception)
        {
            return null; // The display or window can change between validation and capture.
        }

        var locked = crop.LockBits(new Rectangle(0, 0, width, height), ImageLockMode.ReadOnly,
            PixelFormat.Format32bppArgb);
        try
        {
            bool upsideDown = locked.Stride < 0;
            int stride = Math.Abs(locked.Stride);
            nint start = locked.Scan0 + (upsideDown ? locked.Stride * (height - 1) : 0);
            var pixels = new MarkerPixels(new ReadOnlySpan<byte>((void*)start, stride * (height - 1) + width * 4),
                width, height, stride, upsideDown);
            var sample = decoder.Sample(pixels, now);
            if (sample is null || decoder.Anchor is not { } anchor)
                return null;

            var point = new Native.Point { X = origin.X + anchor.X + anchor.Cell,
                Y = origin.Y + anchor.Y };
            return Native.GetAncestor(Native.WindowFromPoint(point), 2) == window ? sample : null; // GA_ROOT
        }
        finally { crop.UnlockBits(locked); }
    }

    /// <summary>Find an initial WoW window or a replacement owned by the tracked game.</summary>
    private void FindWindow()
    {
        var title = new StringBuilder(256);
        Native.EnumWindows((candidate, _) =>
        {
            if (!Native.IsWindowVisible(candidate) || Native.IsIconic(candidate) ||
                Native.GetWindowText(candidate, title, title.Capacity) == 0)
                return true;
            var name = title.ToString().Trim();
            if (!name.Equals("World of Warcraft", StringComparison.OrdinalIgnoreCase) &&
                !name.Equals("World of Warcraft Forever", StringComparison.OrdinalIgnoreCase) &&
                !name.StartsWith("World of Warcraft:", StringComparison.OrdinalIgnoreCase) &&
                !name.StartsWith("World of Warcraft - ", StringComparison.OrdinalIgnoreCase))
                return true;
            if (Native.GetWindowThreadProcessId(candidate, out uint owner) == 0)
                return true;
            if (game is not null)
            {
                if (owner != processId)
                    return true;
                window = candidate;
                return false;
            }
            try
            {
                using var process = Process.GetProcessById((int)owner);
                if (!GameProcesses.Contains(process.ProcessName))
                    return true;
            }
            catch (Exception problem) when (problem is ArgumentException or InvalidOperationException or Win32Exception)
            {
                return true; // The process may exit during enumeration.
            }
            var handle = Native.OpenProcess(0x00100000, false, owner); // SYNCHRONIZE
            if (handle.IsInvalid)
                handle.Dispose();
            else
                game = handle;
            window = candidate;
            processId = owner;
            return false;
        }, 0);
    }

    public void Dispose()
    {
        game?.Dispose();
        graphics.Dispose();
        crop.Dispose();
    }

    /// <summary>Win32 window and coordinate APIs used with per-monitor DPI-aware coordinates.</summary>
    private static class Native
    {
        [StructLayout(LayoutKind.Sequential)]
        public struct Point { public int X, Y; }
        [StructLayout(LayoutKind.Sequential)]
        public struct Rect { public int Left, Top, Right, Bottom; }

        public delegate bool WindowCallback(nint window, nint data);
        [DllImport("user32.dll")] public static extern bool EnumWindows(WindowCallback callback, nint data);
        [DllImport("user32.dll")] public static extern bool IsWindow(nint window);
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(nint window);
        [DllImport("user32.dll")] public static extern bool IsIconic(nint window);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern int GetWindowText(nint window, StringBuilder title, int capacity);
        [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(nint window, out uint process);
        [DllImport("user32.dll")] public static extern bool GetClientRect(nint window, out Rect rectangle);
        [DllImport("user32.dll")] public static extern bool ClientToScreen(nint window, ref Point point);
        [DllImport("user32.dll")] public static extern nint WindowFromPoint(Point point);
        [DllImport("user32.dll")] public static extern nint GetAncestor(nint window, uint mode);
        [DllImport("kernel32.dll")] public static extern SafeProcessHandle OpenProcess(uint access, bool inherit, uint process);
        [DllImport("kernel32.dll")] public static extern uint WaitForSingleObject(SafeProcessHandle process, uint milliseconds);
    }
}
