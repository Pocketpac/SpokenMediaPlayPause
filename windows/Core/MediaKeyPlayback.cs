namespace SpokenMediaPause.Core;

/// <summary>Pairs keyboard Play/Pause toggles without claiming to know the selected player.</summary>
internal sealed class MediaKeyPlayback(Func<bool> sendToggle)
{
    private bool active, attempted, automaticToggle, manualToggle;
    public bool AutomaticTogglePending => automaticToggle;
    public bool HasPendingToggle => automaticToggle || manualToggle;
    public string? LastError { get; private set; }

    /// <summary>Send one toggle on live narration and pair it on release. A capture gap keeps the state.</summary>
    public void SetActive(bool value, bool fresh)
    {
        if (!value)
        {
            if (active && automaticToggle && Send())
                automaticToggle = false;
            attempted = false;
        }
        else if (fresh && !attempted)
        {
            // A toggle cannot be retried like a dedicated Pause command.
            attempted = true;
            if (!HasPendingToggle)
                automaticToggle = Send();
        }
        active = value;
    }

    /// <summary>Send paired manual toggles. Test Play can also release a pending automatic toggle.</summary>
    public string Test(bool pause)
    {
        if (active)
            return "Spoken is narrating.";
        if (pause == HasPendingToggle)
            return pause ? "A media key is already paired with Test Play." : "There’s no media key to pair with Test Play.";
        if (!Send())
            return LastError!;
        automaticToggle = false;
        manualToggle = pause;
        return "Sent the Play/Pause media key.";
    }

    /// <summary>Pair an outstanding toggle once when the companion closes normally.</summary>
    public void Close()
    {
        if (HasPendingToggle && Send())
            automaticToggle = manualToggle = false;
    }

    private bool Send()
    {
        bool sent = sendToggle();
        LastError = sent ? null : "Windows couldn’t send the Play/Pause media key.";
        return sent;
    }
}
