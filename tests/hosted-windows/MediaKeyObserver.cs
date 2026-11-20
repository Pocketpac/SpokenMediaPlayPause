using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

/// <summary>Observe only injected Play/Pause events in the disposable runner desktop.</summary>
internal static class MediaKeyObserver
{
    private delegate IntPtr Hook(int code, IntPtr message, IntPtr data);
    private static readonly Hook Observe = OnInput;
    private static string logPath;

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr SetWindowsHookEx(int id, Hook hook, IntPtr module, uint thread);
    [DllImport("user32.dll")]
    private static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr message, IntPtr data);
    [DllImport("user32.dll")]
    private static extern bool UnhookWindowsHookEx(IntPtr hook);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr GetModuleHandle(string name);

    [STAThread]
    private static void Main(string[] args)
    {
        logPath = args[0];
        IntPtr hook = SetWindowsHookEx(13, Observe, GetModuleHandle(null), 0); // WH_KEYBOARD_LL
        if (hook == IntPtr.Zero)
            throw new Win32Exception();
        try
        {
            File.WriteAllText(logPath, "ready\n");
            Application.Run();
        }
        finally { UnhookWindowsHookEx(hook); }
    }

    private static IntPtr OnInput(int code, IntPtr message, IntPtr data)
    {
        if (code >= 0 && Marshal.ReadInt32(data) == 0xB3)
        {
            int flags = Marshal.ReadInt32(data, 8);
            if ((flags & 0x10) != 0) // LLKHF_INJECTED
            {
                try { File.AppendAllText(logPath, (flags & 0x80) == 0 ? "down\n" : "up\n"); }
                catch (IOException) { } // Let the test detect any missed event.
            }
        }
        return CallNextHookEx(IntPtr.Zero, code, message, data);
    }
}
