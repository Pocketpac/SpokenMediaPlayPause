namespace SpokenMediaPause.Core;

internal enum MarkerMode { Idle = 4, Narrating = 2, Disabled = 7 }
internal readonly record struct MarkerReading(MarkerMode Mode, bool Heartbeat);
internal readonly record struct MarkerAnchor(int X, int Y, int Cell);

/// <summary>Reads an in-memory BGRA crop without copying rows or retaining frame data.</summary>
internal readonly ref struct MarkerPixels(ReadOnlySpan<byte> pixels, int width, int height,
    int stride, bool bottomUp)
{
    private readonly ReadOnlySpan<byte> data = pixels;
    public int Width => width;
    public int Height => height;

    /// <summary>Returns three RGB bits, or -1 for blended or out-of-bounds colors.</summary>
    public int Color(int x, int y)
    {
        if ((uint)x >= (uint)width || (uint)y >= (uint)height)
            return -1;
        int offset = (bottomUp ? height - 1 - y : y) * stride + x * 4;
        byte b = data[offset], g = data[offset + 1], r = data[offset + 2];
        if (b is > 80 and < 175 || g is > 80 and < 175 || r is > 80 and < 175)
            return -1;
        return (r >= 175 ? 4 : 0) | (g >= 175 ? 2 : 0) | (b >= 175 ? 1 : 0);
    }

    /// <summary>Checks the 0.3.0 addon's four fixed cells before reading state and heartbeat.</summary>
    public MarkerReading? Read(MarkerAnchor anchor)
    {
        var (x, y, cell) = anchor;
        if (Color(x, y) != 5 || Color(x + cell, y) != 3 ||
            Color(x, y + cell) != 6 || Color(x + cell, y + cell) != 1)
            return null;
        int state = Color(x + 2 * cell, y), clock = Color(x + 2 * cell, y + cell);
        return state is 4 or 2 or 7 && clock is 0 or 7
            ? new((MarkerMode)state, clock == 7) : null;
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

    /// <summary>Returns a marker sample, using the magenta cell's width to estimate UI scaling.</summary>
    public MarkerReading? Sample(MarkerPixels pixels, long now)
    {
        if (Anchor is { } cached && pixels.Read(cached) is { } reading)
            return reading;
        if (now < nextSearch)
            return null;

        nextSearch = now + 550;
        Anchor = null;
        for (int y = 1; y < pixels.Height; y += 2)
        {
            for (int x = 0; x < pixels.Width; )
            {
                int color = pixels.Color(x, y);
                if (color != 5)
                {
                    x++;
                    continue;
                }
                int start = x++;
                while (pixels.Color(x, y) == 5)
                    x++;
                int width = x - start;
                if (width is < 3 or > 44)
                    continue;

                int middle = (start + x - 1) / 2;
                for (int cell = Math.Max(3, width - 1); cell < width + 5; cell++)
                for (int shift = 0; shift < 2; shift++)
                {
                    var anchor = new MarkerAnchor(middle + shift, y, cell);
                    if (pixels.Read(anchor) is { } found)
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

/// <summary>Converts marker samples into pause intent and expires stale or obscured frames.</summary>
internal sealed class Narration
{
    private bool? heartbeat;
    private long? heartbeatAt, activeAt, idleAt;
    public bool Connected { get; private set; }
    public bool Disabled { get; private set; }
    public bool Active { get; private set; }

    public void Reset()
    {
        heartbeat = null;
        heartbeatAt = activeAt = idleAt = null;
        Connected = Disabled = Active = false;
    }

    /// <summary>Requires a changing heartbeat and applies the same grace periods as Linux.</summary>
    public void Update(MarkerReading? sample, long now)
    {
        if (sample is { } reading && heartbeat != reading.Heartbeat)
        {
            if (heartbeat.HasValue)
                heartbeatAt = now;
            heartbeat = reading.Heartbeat;
        }

        Connected = heartbeatAt is long tick && now - tick <= 2400;
        if (Connected && sample is { } state)
        {
            Disabled = state.Mode == MarkerMode.Disabled;
            switch (state.Mode)
            {
                case MarkerMode.Disabled: activeAt = idleAt = null; break;
                case MarkerMode.Narrating: activeAt = now; idleAt = null; break;
                case MarkerMode.Idle: idleAt ??= now; break;
            }
        }
        Active = Connected && activeAt is long lastActive && now - lastActive < 900
            && (idleAt is null || now - idleAt.Value < 450);
    }
}
