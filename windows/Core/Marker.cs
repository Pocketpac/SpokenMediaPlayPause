namespace SpokenMediaPause.Core;

internal enum MarkerMode { Idle = 4, Narrating = 2, Disabled = 7 }
internal readonly record struct MarkerReading(MarkerMode Mode, bool Heartbeat);
// Eighth-pixel pitch keeps fractional scaling aligned across the longer bevel.
internal readonly record struct MarkerAnchor(int X, int Y, int Pitch, bool Brass)
{
    public int Cell => (Pitch + 4) / 8;
}

/// <summary>Reads an in-memory BGRA crop without copying rows or retaining frame data.</summary>
internal readonly ref struct MarkerPixels(ReadOnlySpan<byte> pixels, int width, int height,
    int stride, bool bottomUp)
{
    private readonly ReadOnlySpan<byte> data = pixels;
    public int Width => width;
    public int Height => height;

    /// <summary>Read a pixel from a top-down or bottom-up BGRA capture.</summary>
    private bool TryRgb(int x, int y, out int r, out int g, out int b)
    {
        if ((uint)x >= (uint)width || (uint)y >= (uint)height)
        {
            r = g = b = 0;
            return false;
        }
        int offset = (bottomUp ? height - 1 - y : y) * stride + x * 4;
        b = data[offset];
        g = data[offset + 1];
        r = data[offset + 2];
        return true;
    }

    /// <summary>Returns three legacy RGB bits or -1 for an ambiguous pixel.</summary>
    private int Color(int x, int y)
    {
        if (!TryRgb(x, y, out int r, out int g, out int b))
            return -1;
        if (b is > 80 and < 175 || g is > 80 and < 175 || r is > 80 and < 175)
            return -1;
        return (r >= 175 ? 4 : 0) | (g >= 175 ? 2 : 0) | (b >= 175 ? 1 : 0);
    }

    public const int ClassicKey = 5, BrassKey = 8;

    /// <summary>Identify either protocol's first-cell color without a second crop scan.</summary>
    public int Key(int x, int y)
    {
        if (!TryRgb(x, y, out int r, out int g, out int b))
            return -1;
        if (r >= 175 && g <= 80 && b >= 175)
            return ClassicKey;
        // Muted purple is uncommon in the dark terrain behind the badge.
        return r - g >= 11 && b - g >= 7 && BrassTone(r, g, b) == 2 ? BrassKey : -1;
    }

    /// <summary>Classify muted shadow colors by channel differences, rejecting blended edges.</summary>
    private static int BrassTone(int r, int g, int b)
    {
        if ((r + 2 * g + b) / 4 is < 23 or > 58)
            return -1;
        return (r - g, g - b) switch
        {
            (>= 8 and <= 26, >= 8 and <= 28) => 0,
            (>= -26 and <= -7, >= -14 and <= 5) => 1,
            (>= 11 and <= 32, >= -28 and <= -7) => 2,
            (>= -15 and <= 5, >= -37 and <= -18) => 3,
            (>= -22 and <= -4, >= 7 and <= 25) => 4,
            _ => -1
        };
    }

    private int Tone(int x, int y) => TryRgb(x, y, out int r, out int g, out int b) ? BrassTone(r, g, b) : -1;

    /// <summary>Validate the marker signature before returning mode and heartbeat.</summary>
    public MarkerReading? Read(MarkerAnchor anchor)
    {
        var (x, y, pitch, brass) = anchor;
        if (brass)
        {
            if (Key(x, y) != BrassKey)
                return null;
            // A fractional boundary pixel may change color when a neighboring
            // cell changes state. Try nearby pitches before rescanning.
            foreach (int adjustment in (ReadOnlySpan<int>)[0, 1, -1, 2, -2])
                if (ReadBrass(x, y, pitch + adjustment) is { } reading)
                    return reading;
            return null;
        }
        int cell = pitch / 8;
        if (Color(x, y) != 5 || Color(x + cell, y) != 3 ||
            Color(x, y + cell) != 6 || Color(x + cell, y + cell) != 1)
            return null;
        int state = Color(x + 2 * cell, y), clock = Color(x + 2 * cell, y + cell);
        return state is 4 or 2 or 7 && clock is 0 or 7
            ? new((MarkerMode)state, clock == 7) : null;
    }

    /// <summary>Read the remaining bevel cells after the first tone is confirmed.</summary>
    public MarkerReading? ReadBrass(int x, int y, int pitch)
    {
        // 2 0 2 1 mode (2-mode) clock 0, as drawn by the Lua addon.
        if (Tone(x + (pitch + 4) / 8, y) != 0
            || Tone(x + (2 * pitch + 4) / 8, y) != 2 || Tone(x + (3 * pitch + 4) / 8, y) != 1
            || Tone(x + (7 * pitch + 4) / 8, y) != 0)
            return null;
        int mode = Tone(x + (4 * pitch + 4) / 8, y), inverse = Tone(x + (5 * pitch + 4) / 8, y);
        int clock = Tone(x + (6 * pitch + 4) / 8, y);
        if (mode is < 0 or > 2 || inverse != 2 - mode || clock is not (3 or 4))
            return null;
        var state = mode switch { 0 => MarkerMode.Idle, 1 => MarkerMode.Narrating, _ => MarkerMode.Disabled };
        return new(state, clock == 4);
    }
}

/// <summary>Reads the known marker directly and limits full crop searches to twice per second.</summary>
internal sealed class MarkerDecoder
{
    public MarkerAnchor? Anchor { get; private set; }
    private long nextSearch;

    public void Reset()
    {
        Anchor = null;
        nextSearch = 0;
    }

    /// <summary>Estimate UI scale from the width of either marker's first cell.</summary>
    public MarkerReading? Sample(MarkerPixels pixels, long now)
    {
        if (Anchor is { } cached && pixels.Read(cached) is { } reading)
            return reading;
        if (now < nextSearch)
            return null;

        nextSearch = now + 550;
        Anchor = null;
        for (int y = 0; y < pixels.Height; y++)
        {
            for (int x = 0; x < pixels.Width; )
            {
                int key = pixels.Key(x, y);
                if (key == -1)
                {
                    x++;
                    continue;
                }
                int start = x++;
                while (pixels.Key(x, y) == key)
                    x++;
                int width = x - start;
                bool brass = key == MarkerPixels.BrassKey;
                int minimum = brass ? 1 : 3, maximum = brass ? 24 : 44;
                if (width < minimum || width > maximum)
                    continue;

                int middle = (start + x - 1) / 2;
                int firstPitch = Math.Max(brass ? 12 : 24, 8 * (width - 1));
                int lastPitch = 8 * (width + (brass ? 2 : 4));
                for (int pitch = firstPitch; pitch <= lastPitch; pitch += brass ? 1 : 8)
                for (int shift = 0; shift < 2; shift++)
                {
                    var anchor = new MarkerAnchor(middle + shift, y, pitch, brass);
                    if (anchor.X >= x)
                        continue;
                    var candidate = brass ? pixels.ReadBrass(anchor.X, y, pitch) : pixels.Read(anchor);
                    if (candidate is { } found)
                    {
                        Anchor = anchor;
                        return found;
                    }
                }
            }
        }
        return null;
    }

    public const int CaptureWidth = 200;
    public const int CaptureHeight = 160;
}

/// <summary>Applies live marker states and holds the last pause decision through capture gaps.</summary>
internal sealed class Narration
{
    private bool? heartbeat;
    private long? heartbeatAt, idleAt;
    public bool Connected { get; private set; }
    public bool Disabled { get; private set; }
    public bool Active { get; private set; }

    /// <summary>Clear the marker and release pause intent when the tracked game exits.</summary>
    public void Reset()
    {
        heartbeat = null;
        heartbeatAt = idleAt = null;
        Connected = Disabled = Active = false;
    }

    /// <summary>Honor live disable immediately and live idle after a 450 ms grace period.</summary>
    public void Update(MarkerReading? sample, long now)
    {
        if (sample is { } reading && heartbeat != reading.Heartbeat)
        {
            if (heartbeat.HasValue)
                heartbeatAt = now;
            heartbeat = reading.Heartbeat;
        }

        Connected = heartbeatAt is long tick && now - tick <= 2400;
        if (!Connected || sample is not { } state)
        {
            idleAt = null;
            return;
        }
        Disabled = state.Mode == MarkerMode.Disabled;
        switch (state.Mode)
        {
            case MarkerMode.Disabled:
                Active = false;
                idleAt = null;
                break;
            case MarkerMode.Narrating:
                Active = true;
                idleAt = null;
                break;
            case MarkerMode.Idle when Active:
                idleAt ??= now;
                Active = now - idleAt.Value < 450;
                break;
        }
    }
}
