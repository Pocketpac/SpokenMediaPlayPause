"""Control independent MPRIS players on the user's session bus."""

from dataclasses import dataclass
import sys
from time import monotonic

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
COMMAND_TIMEOUT_MS = 3000
RETRY_DELAYS_MS = (250, 700)
MARKER_LEASE_SECONDS = 0.55
RETRY = object()
DEFER = object()
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
    def __init__(self, bus, show_recovery=None):
        self.bus = bus
        self.paused = {}
        self.test_paused = {}
        self.active = False
        self.started = False
        self.marker_valid_until = float("-inf")
        self.waiting_for_marker = []
        self.retrying = 0
        self.last_error = ""
        self.show_recovery = show_recovery
        self.release_requested = False
        self.busy = False
        self.closed = False
        self.close_loop = None
        self.close_manual_started = False
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

    @property
    def can_pause(self):
        """Expire a capture verdict even when a retry runs before the next poll after a stall."""
        return monotonic() <= self.marker_valid_until

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
        timeout = COMMAND_TIMEOUT_MS if method in ("Pause", "Play") else CALL_TIMEOUT_MS
        self.bus.call(owner, path, interface, method, parameters, REPLY_TYPES.get(method, EMPTY_REPLY),
                      Gio.DBusCallFlags.NO_AUTO_START, timeout, cancellable, completed)

    def report_recovery(self):
        """Keep playback delays visible independently of WoW capture status."""
        if self.show_recovery is not None:
            message = "Media player is slow. Retrying…" if self.retrying else self.last_error
            self.show_recovery(message, self.busy)

    @staticmethod
    def failure(error):
        """Retry temporary bus timeouts and retire owners that have disappeared."""
        if any(error.matches(Gio.dbus_error_quark(), code) for code in
               (Gio.DBusError.NAME_HAS_NO_OWNER, Gio.DBusError.SERVICE_UNKNOWN, Gio.DBusError.DISCONNECTED)):
            return None
        if (error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT)
                or any(error.matches(Gio.dbus_error_quark(), code) for code in
                       (Gio.DBusError.NO_REPLY, Gio.DBusError.TIMEOUT, Gio.DBusError.TIMED_OUT))):
            return RETRY
        return False

    def retry(self, operation, allowed, done, needs_marker=False):
        """Run at most three attempts. Only a fresh narration marker may send a new automatic Pause."""
        retrying = False

        def finish(result):
            if retrying:
                self.retrying -= 1
                self.report_recovery()
            done(result)

        def attempt(index):
            if not allowed():
                finish(None)
                return
            if needs_marker and not self.can_pause:
                self.waiting_for_marker.append(lambda: attempt(index))
                return

            def completed(result):
                nonlocal retrying
                if result is DEFER:
                    attempt(index)
                elif result is RETRY and index < len(RETRY_DELAYS_MS) and allowed():
                    if not retrying:
                        retrying = True
                        self.retrying += 1
                        self.report_recovery()

                    def again():
                        attempt(index + 1)
                        return GLib.SOURCE_REMOVE

                    GLib.timeout_add(RETRY_DELAYS_MS[index], again)
                else:
                    finish(False if result is RETRY else result)

            operation(completed)

        attempt(0)

    def run_batch(self, items, visit, allowed, done):
        """Bound concurrent requests. Each slot follows one player to completion."""
        pending = iter(items)
        in_flight = successful = failed = 0
        exhausted = False
        pumping = False

        def finish(applied=None):
            nonlocal in_flight, successful, failed
            in_flight -= 1
            successful += applied is True
            failed += applied is False
            pump()

        def pump():
            nonlocal in_flight, exhausted, pumping
            if pumping:
                return
            # A synchronous skip must free its slot without recursing or
            # completing twice while the outer loop is still running.
            pumping = True
            try:
                while not exhausted and allowed() and in_flight < MAX_PARALLEL_PLAYERS:
                    item = next(pending, None)
                    if item is None:
                        exhausted = True
                        break
                    in_flight += 1
                    visit(item, finish)
                if not in_flight:
                    done(successful, failed)
            finally:
                pumping = False

        pump()

    def send(self, owner, method, done):
        """Report command success or a classified failure to the per-player operation."""
        def received(_reply, error):
            if error:
                print(f"MPRIS {method} failed for {owner}: {error.message}", file=sys.stderr)
            done(True if error is None else self.failure(error))

        self.request(owner, MPRIS_PLAYER, method, None, received)

    def pause(self, records, allowed, done, needs_marker=False):
        """Discover active MPRIS services and pause each identifiable playing item."""
        self.busy = True
        self.last_error = ""
        self.report_recovery()
        self.pause_reads = reads = Gio.Cancellable()
        desired = allowed
        allowed = lambda: not reads.is_cancelled() and desired()

        def listed(names):
            if names is None or names is False:
                done(0, int(names is False))
                return
            for owner in self.test_paused.keys() - set(names):
                self.test_paused.pop(owner)
            seen = set()

            def visit(name, finish):
                def visit_owner(owner):
                    if not owner:
                        finish(owner)
                        return
                    if owner in seen or owner in records:
                        finish()
                        return
                    seen.add(owner)
                    record = None

                    def inspect(complete):
                        def inspected(reply, error):
                            nonlocal record
                            if not allowed() or (record is not None and records.get(owner) is not record):
                                complete(None)
                                return
                            if error:
                                complete(self.failure(error))
                                return
                            properties, = reply
                            track = self.track(properties)
                            if track is None or (record is not None and record.track != track):
                                complete(None)
                                return
                            if properties.get("PlaybackStatus") != "Playing":
                                # A timed-out Pause may have run. Without an acknowledgment,
                                # this helper cannot safely claim that pause for a later Play.
                                complete(False if record is not None and
                                         properties.get("PlaybackStatus") == "Paused" else None)
                                return
                            if not all(properties.get(key, True) for key in ("CanControl", "CanPause", "CanPlay")):
                                complete(None)
                                return
                            if needs_marker and not self.can_pause:
                                complete(DEFER)
                                return
                            if record is None:
                                record = records[owner] = Pause(track)
                            self.send(owner, "Pause", complete)

                        self.request(owner, PROPERTIES, "GetAll", PLAYER_PROPERTIES, inspected, reads)

                    def paused(result):
                        if record is not None and records.get(owner) is record:
                            if result is True:
                                record.confirmed = True
                            else:
                                records.pop(owner)
                        finish(result)

                    self.retry(inspect, allowed, paused, needs_marker)

                def resolve(complete):
                    def resolved(reply, error):
                        complete(None if not allowed() else self.failure(error) if error else reply[0])

                    self.request(DBUS, DBUS, "GetNameOwner", GLib.Variant("(s)", (name,)),
                                 resolved, reads)

                self.retry(resolve, allowed, visit_owner, needs_marker)

            # playerctld forwards to another player, which has its own bus name.
            players = (name for name in names if name.startswith(MPRIS_PREFIX)
                       and name != MPRIS_PREFIX + "playerctld")
            self.run_batch(players, visit, allowed, done)

        def list_names(complete):
            def reply(result, error):
                complete(None if not allowed() else self.failure(error) if error else result[0])

            self.request(DBUS, DBUS, "ListNames", None, reply, reads)

        self.retry(list_names, allowed, listed, needs_marker)

    def resume(self, records, allowed, done):
        """Play only the same item still paused on the original bus connection."""
        self.busy = True
        self.last_error = ""
        self.report_recovery()

        def visit(owner, finish):
            record = records.get(owner)

            def forget():
                if records.get(owner) is record:
                    records.pop(owner, None)

            def inspect(complete):
                if record is None or records.get(owner) is not record:
                    complete(None)
                    return

                def inspected(reply, error):
                    if not allowed() or records.get(owner) is not record:
                        complete(None)
                        return
                    if error:
                        result = self.failure(error)
                        if result is None:
                            forget()
                        complete(result)
                        return
                    properties, = reply
                    if (not record.confirmed or properties.get("PlaybackStatus") != "Paused"
                            or not properties.get("CanControl", True) or not properties.get("CanPlay", True)
                            or self.track(properties) != record.track):
                        forget()
                        complete(None)
                        return

                    def played(result):
                        if result is True or result is None:
                            forget()
                        complete(result)

                    self.send(owner, "Play", played)

                self.request(owner, PROPERTIES, "GetAll", PLAYER_PROPERTIES, inspected)

            self.retry(inspect, allowed, finish)

        self.run_batch(tuple(records), visit, allowed, done)

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
                self.last_error = "A media player didn’t respond to " + method + "."
                noun = "request" if failed == 1 else "requests"
                failure = f"{failed} {method} {noun} failed."
                message = f"{message} {failure}" if successful else failure
            if report is not None:
                report(message)
            elif successful or failed:
                print(message, flush=True)
            self.report_recovery()
            self.advance()

        return complete

    def advance(self):
        if self.busy:
            return
        if self.release_requested and not self.active:
            self.release_requested = self.started = False
            self.resume(self.paused, lambda: not self.active, self.completion("Play"))
        elif self.active and self.can_pause and not self.started:
            self.started = True
            self.pause(self.paused, lambda: self.active and not self.release_requested,
                       self.completion("Pause"), needs_marker=True)
        elif self.closed:
            if self.test_paused and not self.close_manual_started:
                self.close_manual_started = True
                self.resume(self.test_paused, lambda: True, self.completion("Play"))
            elif self.close_loop is not None:
                self.close_loop.quit()

    def set_active(self, active, can_pause=True):
        """Hold owned media through missing capture and wake deferred Pause attempts on a live marker."""
        changed = active != self.active
        self.active = active
        self.marker_valid_until = monotonic() + MARKER_LEASE_SECONDS if can_pause else float("-inf")
        if changed or self.closed:
            if not active and (self.started or self.paused):
                self.release_requested = True
            elif active and self.release_requested:
                self.release_requested = self.started = False
        # A pending query cannot change playback. Cancel it so a stalled
        # player cannot delay release. Let any dispatched Pause complete.
        if (changed or self.closed) and self.pause_reads is not None:
            self.pause_reads.cancel()
        if self.waiting_for_marker and (self.can_pause or not self.active):
            waiting, self.waiting_for_marker = self.waiting_for_marker, []
            for wake in waiting:
                wake()
        self.advance()

    def test_command(self, method, report):
        if self.active or self.busy or self.closed:
            report("Playback controls are busy.")
        elif method == "Pause":
            self.pause(self.test_paused, lambda: not (self.active or self.closed),
                       self.completion(method, report))
        else:
            self.resume(self.test_paused, lambda: not self.active, self.completion(method, report))

    def close(self):
        """Finish in-flight commands and release recorded pauses before exiting."""
        self.closed = True
        self.set_active(False)
        if self.busy:
            self.close_loop = GLib.MainLoop()
            self.close_loop.run()
            self.close_loop = None
        self.bus.signal_unsubscribe(self.subscription)
