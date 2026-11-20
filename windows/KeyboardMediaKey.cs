using System.Runtime.InteropServices;

namespace SpokenMediaPause.Windows;

/// <summary>Insert the same Play/Pause key press and release as a multimedia keyboard.</summary>
internal static class KeyboardMediaKey
{
    private const ushort PlayPause = 0xB3;
    private const uint Keyboard = 1, Extended = 1, KeyUp = 2;

    public static unsafe bool SendToggle()
    {
        Input* events = stackalloc Input[2];
        events[0] = Key(Extended);
        events[1] = Key(Extended | KeyUp);
        uint sent = SendInput(2, events, sizeof(Input));
        // A partial send may already have toggled playback. Send only key-up.
        if (sent == 1)
            SendInput(1, events + 1, sizeof(Input));
        return sent != 0;
    }

    private static Input Key(uint flags) => new()
    {
        Type = Keyboard,
        Data = new InputData { Key = new KeyInput { VirtualKey = PlayPause, Flags = flags } }
    };

    [DllImport("user32.dll")]
    private static extern unsafe uint SendInput(uint count, Input* inputs, int size);

    [StructLayout(LayoutKind.Sequential)]
    private struct Input
    {
        public uint Type;
        public InputData Data;
    }

    // Win32 INPUT reserves room for the largest member even for a keyboard event.
    [StructLayout(LayoutKind.Explicit)]
    private struct InputData
    {
        [FieldOffset(0)] public KeyInput Key;
        [FieldOffset(0)] private MouseInput mouse;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KeyInput
    {
        public ushort VirtualKey, ScanCode;
        public uint Flags, Time;
        public nuint ExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MouseInput
    {
        public int X, Y;
        public uint Data, Flags, Time;
        public nuint ExtraInfo;
    }
}
