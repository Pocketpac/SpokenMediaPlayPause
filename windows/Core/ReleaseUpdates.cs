using System.Reflection;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace SpokenMediaPause.Core;

internal readonly record struct UpdateResult(bool Failed, string? NewerTag);

/// <summary>Checks public GitHub release metadata and caches one result per day across launches.</summary>
internal sealed class ReleaseUpdates
{
    internal const string ReleasePage = "https://github.com/Pocketpac/SpokenMediaPlayPause/releases/tag/";
    private const string Api = "https://api.github.com/repos/Pocketpac/SpokenMediaPlayPause/releases/latest";
    private const string Executable = "Spoken-Media-Pause-Companion-Windows-x64.exe";
    // Recognize releases uploaded under the filename used before the companion rename.
    private const string LegacyExecutable = "Spoken-Media-Pause-Windows-x64-Beta.exe";
    private static readonly Regex TagFormat = new(@"\A(?:addon-)?v([0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9})\z");
    private static readonly HttpClient Client = new()
    {
        Timeout = TimeSpan.FromSeconds(5),
        MaxResponseContentBufferSize = 1_000_000
    };
    private readonly Func<Task<string>> fetch;
    private readonly Func<DateTimeOffset> now;
    private readonly string cache;
    private readonly Version installed;
    private sealed record Saved(DateTimeOffset Checked, string? Tag);

    static ReleaseUpdates()
    {
        Client.DefaultRequestHeaders.UserAgent.ParseAdd("Spoken-Media-Pause");
        Client.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
    }

    public ReleaseUpdates(Version? installed = null, string? cache = null,
        Func<Task<string>>? fetch = null, Func<DateTimeOffset>? now = null)
    {
        this.installed = installed ?? ReadBuildVersion();
        this.cache = cache ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "SpokenMediaPause", "release-check.json");
        this.fetch = fetch ?? (() => Client.GetStringAsync(Api));
        this.now = now ?? (() => DateTimeOffset.UtcNow);
    }

    private static Version ReadBuildVersion()
    {
        using var resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("SpokenMediaPause.ReleaseVersion")!;
        using var reader = new StreamReader(resource);
        return Version.Parse(reader.ReadToEnd().Trim());
    }

    /// <summary>Identify release tags that carry a complete, uploaded Windows download.</summary>
    private static string? MatchingTag(string json)
    {
        using var document = JsonDocument.Parse(json);
        var release = document.RootElement;
        string? tag = release.GetProperty("tag_name").GetString();
        if (ParseTag(tag) is null || release.GetProperty("draft").GetBoolean() ||
            release.GetProperty("prerelease").GetBoolean())
            throw new JsonException("Invalid full release tag");
        return release.GetProperty("assets").EnumerateArray().Any(asset =>
            (asset.GetProperty("name").GetString() is Executable or LegacyExecutable) &&
            asset.GetProperty("state").GetString() == "uploaded") ? tag : null;
    }

    private static Version? ParseTag(string? tag)
    {
        if (tag is null || tag.Length > 36)
            return null;
        var match = TagFormat.Match(tag);
        return match.Success ? Version.Parse(match.Groups[1].Value) : null;
    }

    /// <summary>Keep both network and cache I/O off the capture and playback event loop.</summary>
    public Task<UpdateResult> CheckAsync(bool manual) => Task.Run(async () =>
    {
        Saved? saved = null;
        try
        {
            if (new FileInfo(cache) is { Exists: true, Length: <= 4096 })
                saved = JsonSerializer.Deserialize<Saved>(File.ReadAllText(cache));
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or JsonException) { }

        var checkedAt = now();
        string? tag = saved?.Tag;
        bool failed = false;
        if (manual || saved is null || checkedAt < saved.Checked || checkedAt - saved.Checked >= TimeSpan.FromDays(1))
        {
            try
            {
                tag = MatchingTag(await fetch());
            }
            catch (Exception error) when (error is HttpRequestException or OperationCanceledException
                or JsonException or InvalidOperationException or KeyNotFoundException)
            {
                failed = true;
            }
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(cache)!);
                File.WriteAllText(cache, JsonSerializer.Serialize(new Saved(checkedAt, tag)));
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
        return new UpdateResult(failed, ParseTag(tag) is { } available && available > installed ? tag : null);
    });
}
