using Microsoft.Win32;
using System.Security;

namespace SpokenMediaPause.Windows;

/// <summary>Store the playback preference per Windows user across app restarts and upgrades.</summary>
internal static class PlaybackSettings
{
    private const string Key = @"HKEY_CURRENT_USER\Software\SpokenMediaPause";
    private const string MediaKeyFallback = "MediaKeyFallback";

    public static bool LoadFallback()
    {
        try { return Registry.GetValue(Key, MediaKeyFallback, 0) is 1; }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or SecurityException)
        {
            return false;
        }
    }

    public static bool SaveFallback(bool enabled)
    {
        try
        {
            Registry.SetValue(Key, MediaKeyFallback, enabled ? 1 : 0, RegistryValueKind.DWord);
            return true;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or SecurityException)
        {
            return false;
        }
    }
}
