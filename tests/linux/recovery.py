"""Check bounded playback retries on a private D-Bus and update checks on a GTK-style loop."""

from io import BytesIO
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch

from gi.repository import Gio, GLib

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "desktop"))
import media_players as media
import release_updates as updates


class Player:
    """An MPRIS session with independent identity and controllable timeout replies."""

    def __init__(self, name, status="Playing"):
        self.status = status
        self.title = "Original"
        self.calls = []
        self.failures = {}
        self.apply_on_timeout = False
        self.connection = Gio.DBusConnection.new_for_address_sync(
            os.environ["DBUS_SESSION_BUS_ADDRESS"],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None,
        )
        self.name = media.MPRIS_PREFIX + name
        self.node = Gio.DBusNodeInfo.new_for_xml(f"""
        <node><interface name="{media.MPRIS_PLAYER}"><method name="Pause"/><method name="Play"/></interface>
        <interface name="{media.PROPERTIES}"><method name="GetAll"><arg type="s" direction="in"/>
        <arg type="a{{sv}}" direction="out"/></method></interface></node>""")
        for interface in self.node.interfaces:
            self.connection.register_object(media.MPRIS_PATH, interface, self.method)
        response = self.connection.call_sync(media.DBUS, media.DBUS_PATH, media.DBUS, "RequestName",
                                            GLib.Variant("(su)", (self.name, 4)), None,
                                            Gio.DBusCallFlags.NONE, 1000, None)
        assert response.unpack() == (1,)

    def method(self, _bus, _sender, _path, _interface, name, _parameters, reply):
        """Fail once or more without blocking other players on the same loop."""
        if name == "GetAll":
            values = {
                "PlaybackStatus": GLib.Variant("s", self.status),
                "Metadata": GLib.Variant("a{sv}", {"mpris:trackid": GLib.Variant("o", "/track"),
                                                     "xesam:title": GLib.Variant("s", self.title)}),
            }
            if not self.failures.get(name, 0):
                reply.return_value(GLib.Variant("(a{sv})", (values,)))
                return
        else:
            self.calls.append(name)
            if not self.failures.get(name, 0) or self.apply_on_timeout:
                self.status = "Paused" if name == "Pause" else "Playing"
            if not self.failures.get(name, 0):
                reply.return_value(None)
                return
        self.failures[name] -= 1
        reply.return_dbus_error("org.freedesktop.DBus.Error.NoReply", "simulated timeout")


def wait_for(test, seconds=3):
    """Drain asynchronous I/O and timers until the predicate holds."""
    context = GLib.MainContext.default()
    deadline = monotonic() + seconds
    while not test():
        if monotonic() >= deadline:
            raise AssertionError("Timed out waiting for test state")
        if not context.iteration(False):
            sleep(0.005)


def check_playback():
    """Exercise fresh-marker gating, isolated progress, and reliable ownership."""
    slow = Player("spoken_test_slow")
    fast = Player("spoken_test_fast")
    changed = Player("spoken_test_changed")
    prepaused = Player("spoken_test_prepaused", "Paused")
    ambiguous = Player("spoken_test_ambiguous", "Paused")
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    events = []
    players = media.MediaPlayers(bus, lambda message, _busy: events.append(message))

    slow.failures["Pause"] = 1
    players.set_active(True)
    wait_for(lambda: slow.calls == ["Pause"] and players.retrying and fast.status == changed.status == "Paused")
    players.set_active(True, False)
    wait_for(lambda: players.waiting_for_marker and fast.status == changed.status == "Paused")
    assert slow.calls == ["Pause"] and fast.calls == ["Pause"]
    players.set_active(True, True)
    wait_for(lambda: not players.busy)
    assert slow.calls == ["Pause", "Pause"] and slow.status == "Paused"
    assert any("Retrying" in message for message in events)

    slow.failures["GetAll"] = 1
    slow.failures["Play"] = 1
    changed.failures["Play"] = 1
    players.set_active(False)
    wait_for(lambda: changed.calls == ["Pause", "Play"] and fast.status == "Playing")
    changed.title = "Changed item"
    wait_for(lambda: not players.busy)
    assert slow.calls == ["Pause", "Pause", "Play", "Play"] and slow.status == "Playing"
    assert changed.calls == ["Pause", "Play"] and not prepaused.calls

    slow.failures["Pause"] = 1
    players.set_active(True)
    wait_for(lambda: slow.calls[-1] == "Pause" and players.retrying)
    # Simulate the whole application stalling beyond the last marker's lease.
    sleep(media.MARKER_LEASE_SECONDS + 0.05)
    wait_for(lambda: players.waiting_for_marker)
    players.set_active(False)
    wait_for(lambda: not players.busy)
    assert slow.calls.count("Pause") == 3 and slow.status == "Playing"

    ambiguous.status = "Playing"
    ambiguous.failures["Pause"] = 1
    ambiguous.apply_on_timeout = True
    slow.failures["Play"] = 3
    players.set_active(True)
    wait_for(lambda: not players.busy)
    players.set_active(False)
    wait_for(lambda: not players.busy)
    assert slow.status == "Paused" and players.owns_pause and "didn’t respond" in players.last_error
    assert ambiguous.calls == ["Pause"] and ambiguous.status == "Paused"
    players.close()
    assert slow.status == "Playing" and slow.calls.count("Play") == 6
    assert ambiguous.calls == ["Pause"] and not prepaused.calls


def check_updates():
    """Check release filenames, version ordering, daily caching, and offline results."""
    tag, asset = "addon-v0.5.10", "Spoken-Media-Pause-Companion-Linux-x64.AppImage"
    now = 1_800_000_000
    requests = 0
    offline = False

    def fetch(_request, timeout):
        nonlocal requests
        requests += 1
        assert timeout <= 5
        if offline:
            raise OSError("offline")
        return BytesIO(json.dumps({"tag_name": tag, "draft": False, "prerelease": False,
                                   "assets": [{"name": asset, "state": "uploaded"}]}).encode())

    with TemporaryDirectory() as directory, patch.object(updates, "urlopen", fetch):
        cache = Path(directory) / "release-check.json"
        checker = updates.UpdateChecker("0.5.9", cache, now=lambda: now)

        def result(manual):
            readings = []
            checker.check(manual, lambda *args: readings.append(args))
            wait_for(lambda: readings)
            return readings[0]

        assert result(False) == (False, tag)
        assert result(False) == (False, tag) and requests == 1
        tag = "v0.5.9"
        assert result(True) == (False, None) and requests == 2
        tag, asset = "v0.5.10", "Spoken-Media-Pause-Companion-Windows-x64.exe"
        now += updates.DAY
        assert result(False) == (False, None) and requests == 3
        asset = "Spoken-Media-Pause-All-Players-Ubuntu-22.04-x86_64.AppImage"
        assert result(True) == (False, tag)
        offline = True
        # An offline check reports failure and keeps the last known update.
        assert result(True) == (True, tag)


if __name__ == "__main__":
    check_playback()
    check_updates()
    print("Linux media recovery and update checks passed.")
