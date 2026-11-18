"""Control independent MPRIS players on the user's session bus."""

from dataclasses import dataclass
import sys

from gi.repository import Gio, GLib

DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER = MPRIS_PREFIX + "Player"
PROPERTIES = "org.freedesktop.DBus.Properties"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
MAX_PARALLEL_PLAYERS = 8
CALL_TIMEOUT_MS = 1500
PLAYER_PROPERTIES = GLib.Variant("(s)", (MPRIS_PLAYER,))
EMPTY_REPLY = GLib.VariantType.new("()")
REPLY_TYPES = {method: GLib.VariantType.new(signature) for method, signature in (
    ("ListNames", "(as)"), ("GetNameOwner", "(s)"), ("GetAll", "(a{sv})")
)}


@dataclass
class Pause:
    track: tuple
    confirmed: bool = False


class MediaPlayers:
    def __init__(self, bus):
        self.bus = bus
        self.paused = {}
        self.test_paused = {}
        self.active = False
        self.started = False
        self.release_requested = False
        self.busy = False
        self.closed = False
        self.close_loop = None
        self.pause_reads = None
        self.subscription = bus.signal_subscribe(
            None, PROPERTIES, "PropertiesChanged", MPRIS_PATH, MPRIS_PLAYER,
            Gio.DBusSignalFlags.NONE, self.properties_changed,
        )

    @staticmethod
    def track(properties):
        """Use identity metadata, excluding position, duration, and artwork."""
        metadata = properties.get("Metadata", {})
        if not isinstance(metadata, dict):
            return None
        track_id = metadata.get("mpris:trackid")
        url = metadata.get("xesam:url")
        has_id = isinstance(track_id, str) and track_id.startswith("/") and track_id != NO_TRACK
        if not has_id and not (isinstance(url, str) and url):
            return None
        return (track_id, url, metadata.get("xesam:title"),
                metadata.get("xesam:artist"), metadata.get("xesam:album"))

    def properties_changed(self, _bus, owner, _path, _interface, _signal, parameters):
        if owner not in self.paused and owner not in self.test_paused:
            return
        _, changed, _ = parameters.unpack()
        status = changed.get("PlaybackStatus")
        for records in (self.paused, self.test_paused):
            record = records.get(owner)
            if record and (status in ("Playing", "Stopped") or
                           ("Metadata" in changed and self.track(changed) != record.track)):
                # A user or the player has taken back control of this item.
                records.pop(owner)

    @property
    def owns_pause(self):
        return any(record.confirmed for record in self.paused.values())

    def request(self, owner, interface, method, parameters, receive, cancellable=None):
        """Dispatch local D-Bus I/O without blocking GTK or frame polling."""
        def completed(connection, result):
            try:
                reply = connection.call_finish(result)
            except GLib.Error as error:
                receive(None, error)
            else:
                receive(reply.unpack(), None)

        path = DBUS_PATH if owner == DBUS else MPRIS_PATH
        self.bus.call(owner, path, interface, method, parameters, REPLY_TYPES.get(method, EMPTY_REPLY),
                      Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, cancellable, completed)

    def run_batch(self, items, visit, allowed, done):
        """Bound concurrent requests. Each slot follows one player to completion."""
        pending = iter(items)
        in_flight = successful = failed = 0
        exhausted = False

        def finish(applied=None):
            nonlocal in_flight, successful, failed
            in_flight -= 1
            successful += applied is True
            failed += applied is False
            pump()

        def pump():
            nonlocal in_flight, exhausted
            while not exhausted and allowed() and in_flight < MAX_PARALLEL_PLAYERS:
                item = next(pending, None)
                if item is None:
                    exhausted = True
                    break
                in_flight += 1
                visit(item, finish)
            if not in_flight:
                done(successful, failed)

        pump()

    def send(self, owner, method, done):
        def received(_reply, error):
            if error:
                print(f"MPRIS {method} failed for {owner}: {error.message}", file=sys.stderr)
            done(error is None)

        self.request(owner, MPRIS_PLAYER, method, None, received)

    def pause(self, records, allowed, done):
        """Discover active MPRIS services and pause each identifiable playing item."""
        self.busy = True
        self.pause_reads = reads = Gio.Cancellable()

        def listed(reply, error):
            if not allowed():
                done(0, 0)
                return
            if error:
                print(f"Could not list media players: {error.message}", file=sys.stderr)
                done(0, 1)
                return
            names, = reply
            for owner in self.test_paused.keys() - set(names):
                self.test_paused.pop(owner)
            seen = set()

            def visit(name, finish):
                def resolved(reply, error):
                    if error or not allowed():
                        finish()
                        return
                    owner, = reply
                    if owner in seen:
                        finish()
                        return
                    seen.add(owner)

                    def inspected(reply, error):
                        if error or not allowed():
                            finish()
                            return
                        properties, = reply
                        track = self.track(properties)
                        if (properties.get("PlaybackStatus") != "Playing" or track is None
                                or not all(properties.get(key, True)
                                           for key in ("CanControl", "CanPause", "CanPlay"))):
                            finish()
                            return
                        record = records[owner] = Pause(track)

                        def paused(success):
                            if records.get(owner) is record:
                                if success:
                                    record.confirmed = True
                                else:
                                    records.pop(owner)
                            finish(success)

                        self.send(owner, "Pause", paused)

                    self.request(owner, PROPERTIES, "GetAll", PLAYER_PROPERTIES, inspected, reads)

                self.request(DBUS, DBUS, "GetNameOwner", GLib.Variant("(s)", (name,)), resolved, reads)

            # playerctld forwards to another player, which has its own bus name.
            players = (name for name in names if name.startswith(MPRIS_PREFIX)
                       and name != MPRIS_PREFIX + "playerctld")
            self.run_batch(players, visit, allowed, done)

        self.request(DBUS, DBUS, "ListNames", None, listed, reads)

    def resume(self, records, done):
        """Play only the same item still paused on the original bus connection."""
        self.busy = True

        def visit(owner, finish):
            record = records.get(owner)

            def inspected(reply, error):
                current = records.pop(owner, None)
                if error or current is None or current is not record or not current.confirmed:
                    finish()
                    return
                properties, = reply
                if (properties.get("PlaybackStatus") == "Paused"
                        and properties.get("CanControl", True) and properties.get("CanPlay", True)
                        and self.track(properties) == current.track):
                    self.send(owner, "Play", finish)
                else:
                    finish()

            self.request(owner, PROPERTIES, "GetAll", PLAYER_PROPERTIES, inspected)

        self.run_batch(tuple(records), visit, lambda: True, done)

    def completion(self, method, report=None):
        def complete(successful, failed):
            self.busy = False
            self.pause_reads = None
            if successful:
                noun = "player" if successful == 1 else "players"
                message = f"Sent {method} to {successful} {noun}."
            elif method == "Pause":
                message = "No eligible playing media found."
            else:
                message = "No media paused by this test is still selected."
            if failed:
                noun = "request" if failed == 1 else "requests"
                failure = f"{failed} {method} {noun} failed."
                message = f"{message} {failure}" if successful else failure
            if report is not None:
                report(message)
            elif successful or failed:
                print(message, flush=True)
            self.advance()

        return complete

    def advance(self):
        if self.busy:
            return
        if self.release_requested:
            self.release_requested = self.started = False
            self.resume(self.paused, self.completion("Play"))
        elif self.active and not self.started:
            self.started = True
            self.pause(self.paused, lambda: self.active and not self.release_requested,
                       self.completion("Pause"))
        elif self.closed:
            if self.test_paused:
                self.resume(self.test_paused, self.completion("Play"))
            elif self.close_loop is not None:
                self.close_loop.quit()

    def set_active(self, active):
        changed = active != self.active
        self.active = active
        if not active and self.started:
            self.release_requested = True
        # A pending query cannot change playback. Cancel it so a stalled
        # player cannot delay release. Let any dispatched Pause complete.
        if (changed or self.closed) and self.pause_reads is not None:
            self.pause_reads.cancel()
        self.advance()

    def test_command(self, method, report):
        if self.active or self.busy or self.closed:
            report("Playback controls are busy.")
        elif method == "Pause":
            self.pause(self.test_paused, lambda: not (self.active or self.closed),
                       self.completion(method, report))
        else:
            self.resume(self.test_paused, self.completion(method, report))

    def close(self):
        """Finish in-flight commands and release recorded pauses before exiting."""
        self.closed = True
        self.set_active(False)
        if self.busy:
            self.close_loop = GLib.MainLoop()
            self.close_loop.run()
            self.close_loop = None
        self.bus.signal_unsubscribe(self.subscription)
