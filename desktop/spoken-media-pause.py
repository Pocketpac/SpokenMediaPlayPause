#!/usr/bin/env python3
"""Pause desktop media during Spoken narration.

X11 and XWayland games supply only a small rectangle from the WoW window.
For native Wayland the ScreenCast portal supplies the selected stream, with a
low frame-rate request at the PipeWire source. Both use the same marker decoder
and send Pause and Play directly to MPRIS players over D-Bus.
"""

import argparse
from contextlib import suppress
import os
import select
import signal
import sys
from time import monotonic
from uuid import uuid4

import gi

for namespace in ("Gst", "GstApp", "GstVideo"):
    gi.require_version(namespace, "1.0")
from gi.repository import Gio, GLib, Gst, GstVideo

from media_players import DBUS, DBUS_PATH, PROPERTIES, MediaPlayers
from release_updates import DAY, RELEASES, UpdateChecker

try:
    from Xlib import X, Xatom, display, error as xerror
except ImportError:
    display = None


PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
NO_FLAGS = Gio.DBusCallFlags.NONE


def call(bus, name, path, interface, method, parameters=None, timeout_ms=1500):
    """Call a local D-Bus method with a bounded wait and unpack its return tuple."""
    reply = bus.call_sync(name, path, interface, method, parameters, None, NO_FLAGS, timeout_ms, None)
    return reply.unpack()


class Cancelled(Exception):
    """The user stopped the helper or dismissed the capture picker."""


class CaptureEnded(Exception):
    """The game window or the selected screen-sharing stream closed normally."""


class ScreenCast:
    """Own one portal session and at most one outstanding picker request."""

    def __init__(self, bus):
        self.bus = bus
        self.session = None
        self.request_loop = None
        self.cancelled = False

    def cancel(self):
        """Wake the picker wait on Ctrl+C or SIGTERM."""
        self.cancelled = True
        if self.request_loop is not None:
            self.request_loop.quit()

    def close(self):
        """Release the selected stream even if setup failed after CreateSession."""
        if self.session:
            with suppress(GLib.Error):
                call(self.bus, PORTAL, self.session, SESSION, "Close")
            self.session = None

    def request(self, method, options, parameter_type="(a{sv})", *args):
        """Subscribe before the call so immediate portal responses cannot be missed.

        The request token predicts the object path on current portals. Subscribing
        broadly during this short-lived operation also handles older portals
        returning a different path. Responses are accepted only for the returned
        request object.
        """
        if self.cancelled:
            raise Cancelled()
        token = "spoken_" + uuid4().hex
        options["handle_token"] = GLib.Variant("s", token)
        result = None
        path = None
        loop = self.request_loop = GLib.MainLoop()

        def receive(_bus, _sender, object_path, _interface, _signal, parameters):
            nonlocal result
            if object_path == path:
                result = parameters.unpack()
                loop.quit()

        subscription = self.bus.signal_subscribe(
            PORTAL, REQUEST, "Response", None, None, Gio.DBusSignalFlags.NONE, receive
        )
        def expire():
            loop.quit()
            return GLib.SOURCE_CONTINUE

        timer = GLib.timeout_add_seconds(120, expire)
        try:
            (path,) = call(
                self.bus, PORTAL, PORTAL_PATH, SCREENCAST, method,
                GLib.Variant(parameter_type, (*args, options)), timeout_ms=5000
            )
            loop.run()
            if result is None:
                with suppress(GLib.Error):
                    call(self.bus, PORTAL, path, REQUEST, "Close")
                if self.cancelled:
                    raise Cancelled()
                raise RuntimeError("The Wayland screen-sharing request timed out.")
            code, data = result
            if code == 1 or self.cancelled:
                # A signal arriving just after CreateSession can still leave a
                # session handle in the successful response.
                if method == "CreateSession" and code == 0:
                    self.session = data.get("session_handle")
                raise Cancelled()
            if code:
                raise RuntimeError(f"The screen-sharing portal rejected {method} (code {code}).")
            return data
        finally:
            GLib.source_remove(timer)
            self.bus.signal_unsubscribe(subscription)
            self.request_loop = None

    def open(self, allow_display=False):
        """Prompt for a source and return its PipeWire fd, node ID, and optional serial."""
        data = self.request("CreateSession", {
            "session_handle_token": GLib.Variant("s", "spoken_session_" + uuid4().hex),
        })
        self.session = data["session_handle"]
        source_type = 1 if allow_display else 2
        (available,) = call(
            self.bus, PORTAL, PORTAL_PATH, PROPERTIES, "Get",
            GLib.Variant("(ss)", (SCREENCAST, "AvailableSourceTypes"))
        )
        if not (available & source_type):
            if allow_display:
                raise RuntimeError("The screen-sharing portal does not support display capture.")
            raise RuntimeError("Window capture is unavailable. Run again with --display.")
        self.request("SelectSources", {
            "types": GLib.Variant("u", source_type),
            "multiple": GLib.Variant("b", False),
        }, "(oa{sv})", self.session)
        if allow_display:
            print("Choose a display and keep WoW at its upper-left corner.", flush=True)
        else:
            print("Choose your WoW window in the screen-sharing picker.", flush=True)
        data = self.request("Start", {}, "(osa{sv})", self.session, "")
        (node, properties), = data["streams"]
        reply, fd_list = self.bus.call_with_unix_fd_list_sync(
            PORTAL, PORTAL_PATH, SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session, {})), None, NO_FLAGS, 5000, None, None
        )
        (index,) = reply.unpack()
        return fd_list.get(index), node, properties.get("pipewire-serial")


# Six cells shared with SpokenMediaPause.lua. RGB bit codes are ordered red,
# green, blue. Values near a color boundary are rejected to tolerate scaling.
MAGENTA, CYAN, YELLOW, BLUE, RED, GREEN, WHITE, BLACK = 5, 3, 6, 1, 4, 2, 7, 0
# Fixed tones and an inverse mode identify the Lua addon's lower bevel strip.
BRASS_KEY = 8
BRASS_FIXED = ((1, 0), (2, 2), (3, 1), (7, 0))
BRASS_STATES = (RED, GREEN, WHITE)
CROP_WIDTH, CROP_HEIGHT = 200, 160
PORTAL_MAX_FPS = 6
POLL_MS = 80


class Frame:
    """Read packed RGB pixels from either capture backend, honoring offset and stride."""

    def __init__(self, extract, width, height, stride, offset, channels, pixel_size):
        self.extract = extract
        self.rows = {}
        self.width = min(width, CROP_WIDTH)
        self.height = min(height, CROP_HEIGHT)
        self.stride = stride
        self.offset = offset
        self.channels = channels
        self.pixel_size = pixel_size

    def row(self, y):
        """Extract a cropped row once, even if several candidates sample it."""
        # PyGObject's map.data copies the whole buffer. Extract just the
        # requested rows within our crop, normally two or three per frame.
        row = self.rows.get(y)
        if row is None:
            row = self.extract(self.offset + y * self.stride, self.width * self.pixel_size)
            self.rows[y] = row
        return row

    def rgb(self, x, y):
        """Read one bounded pixel for marker validation."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        row = self.row(y)
        index = x * self.pixel_size
        if index + self.pixel_size > len(row):
            return None
        red, green, blue = self.channels
        return row[index + red], row[index + green], row[index + blue]

    def color(self, x, y):
        """Return a legacy three-bit RGB color or -1 for an ambiguous pixel."""
        rgb = self.rgb(x, y)
        if rgb is None:
            return -1
        code = 0
        for value in rgb:
            if 80 < value < 175:
                return -1
            code = (code << 1) | (value >= 175)
        return code

    @staticmethod
    def brass_tone(red, green, blue):
        """Classify muted shadow colors by channel differences, rejecting blended edges."""
        if not 23 <= (red + 2 * green + blue) // 4 <= 58:
            return -1
        red_green, green_blue = red - green, green - blue
        if 8 <= red_green <= 26 and 8 <= green_blue <= 28:
            return 0
        if -26 <= red_green <= -7 and -14 <= green_blue <= 5:
            return 1
        if 11 <= red_green <= 32 and -28 <= green_blue <= -7:
            return 2
        if -15 <= red_green <= 5 and -37 <= green_blue <= -18:
            return 3
        if -22 <= red_green <= -4 and 7 <= green_blue <= 25:
            return 4
        return -1

    def key_in_row(self, row, x):
        """Recognize either first-cell color while scanning a cropped row."""
        index = x * self.pixel_size
        if index + self.pixel_size > len(row):
            return -1
        r, g, b = self.channels
        red, green, blue = row[index + r], row[index + g], row[index + b]
        if red >= 175 and green <= 80 and blue >= 175:
            return MAGENTA
        # This muted purple is uncommon in the dark terrain behind the badge.
        if red - green >= 11 and blue - green >= 7 and self.brass_tone(red, green, blue) == 2:
            return BRASS_KEY
        return -1

    def key(self, x, y):
        """Validate the first cell of an existing brass anchor."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return -1
        return self.key_in_row(self.row(y), x)

    def tone(self, x, y):
        """Read a bevel cell at the projected cell center."""
        rgb = self.rgb(x, y)
        return self.brass_tone(*rgb) if rgb is not None else -1

    def read_brass(self, x, y, pitch):
        """Validate all eight cells at the supplied eighth-pixel pitch."""
        for column, tone in BRASS_FIXED:
            if self.tone(x + (column * pitch + 4) // 8, y) != tone:
                return None
        state = self.tone(x + (4 * pitch + 4) // 8, y)
        inverse = self.tone(x + (5 * pitch + 4) // 8, y)
        clock = self.tone(x + (6 * pitch + 4) // 8, y)
        if 0 <= state <= 2 and inverse == 2 - state and clock in (3, 4):
            return BRASS_STATES[state], clock == 4
        return None

    def read(self, anchor):
        """Return the validated state and heartbeat from either marker style."""
        x, y, pitch, key = anchor
        if key == BRASS_KEY:
            if self.key(x, y) != BRASS_KEY:
                return None
            # At fractional UI scales a boundary pixel can change color when
            # its neighbor changes state. Try nearby pitch before rescanning.
            for adjustment in (0, 1, -1, 2, -2):
                state = self.read_brass(x, y, pitch + adjustment)
                if state is not None:
                    return state
            return None
        size = pitch // 8
        if (self.color(x, y) != MAGENTA or self.color(x + size, y) != CYAN
                or self.color(x, y + size) != YELLOW or self.color(x + size, y + size) != BLUE):
            return None
        state = self.color(x + 2 * size, y)
        heartbeat = self.color(x + 2 * size, y + size)
        if state in (RED, GREEN, WHITE) and heartbeat in (WHITE, BLACK):
            return state, heartbeat == WHITE
        return None

    def locate(self):
        """Find either marker after startup or resize using its first cell's width."""
        for y in range(self.height):
            row = self.row(y)
            x = 0
            while x < self.width:
                key = self.key_in_row(row, x)
                if key == -1:
                    x += 1
                    continue
                left = x
                while x < self.width and self.key_in_row(row, x) == key:
                    x += 1
                width = x - left
                if key == BRASS_KEY:
                    if width > 24:
                        continue
                    pitches = range(max(12, 8 * (width - 1)), 8 * (width + 2) + 1)
                else:
                    if not 3 <= width <= 44:
                        continue
                    pitches = range(8 * max(3, width - 1), 8 * (width + 5), 8)
                center = (left + x - 1) // 2
                for pitch in pitches:
                    for shift in (0, 1):
                        sample_x = center + shift
                        if sample_x >= x:
                            continue
                        anchor = sample_x, y, pitch, key
                        if key == BRASS_KEY:
                            state = self.read_brass(sample_x, y, pitch)
                        else:
                            state = self.read(anchor)
                        if state is not None:
                            return anchor, state
        return None, None


WOW_EXECUTABLES = frozenset(("wow.exe", "wowforever.exe", "wowb.exe", "wowt.exe",
                             "wowclassic.exe", "wowclassicb.exe", "wow-64.exe"))


class GameProcesses:
    """Use kernel process handles to distinguish game exit from missing pixels."""

    def __init__(self):
        self.fds = []
        self.poller = select.poll()

    def watch(self, pid):
        """Track one process identity across window replacement and PID reuse."""
        try:
            fd = os.pidfd_open(pid)
        except (AttributeError, OSError):
            return False
        self.fds.append(fd)
        self.poller.register(fd, select.POLLIN)
        return True

    def watch_display_games(self):
        """Identify game processes once, since a monitor stream has no window PID."""
        with suppress(OSError), os.scandir("/proc") as entries:
            for entry in entries:
                if entry.name.isdecimal():
                    # Processes can exit between enumeration and the comm read.
                    with suppress(OSError):
                        with open(f"{entry.path}/comm", encoding="utf-8", errors="replace") as name:
                            if name.read().strip().casefold() in WOW_EXECUTABLES:
                                self.watch(int(entry.name))

    def all_exited(self):
        """Return true only when every tracked process has exited."""
        return bool(self.fds) and len(self.poller.poll(0)) == len(self.fds)

    def close(self):
        """Release the process handles when capture ends."""
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()


class X11Capture:
    """Poll only the corner of a viewable WoW window on X11 or XWayland.

    Auto-discovery reads the window manager client list and WoW's class/title.
    Track the game process when its window advertises a PID so fullscreen
    changes can replace the drawable without ending capture.
    """

    def __init__(self):
        self.game = GameProcesses()
        self.pid = None
        self.connection = display.Display()
        try:
            self.root = self.connection.screen().root
            self.clients_atom = self.connection.intern_atom("_NET_CLIENT_LIST")
            self.title_atom = self.connection.intern_atom("_NET_WM_NAME")
            self.utf8_atom = self.connection.intern_atom("UTF8_STRING")
            self.pid_atom = self.connection.intern_atom("_NET_WM_PID")
            self.window = None
            self.next_search = 0
        except (xerror.XError, xerror.DisplayError):
            self.close()
            raise

    def close(self):
        """Close this helper's X11 connection and process watch."""
        self.game.close()
        with suppress(xerror.DisplayError, xerror.ConnectionClosedError, OSError):
            self.connection.close()

    @staticmethod
    def is_wow(title, classes):
        """Match a WoW game client without selecting Battle.net or a browser."""
        names = {name.casefold() for name in classes}
        if names.intersection(WOW_EXECUTABLES):
            return True
        title = title.strip().casefold()
        return (title in ("world of warcraft", "world of warcraft forever")
                or title.startswith(("world of warcraft:", "world of warcraft - ")))

    def find_window(self):
        """Return the first mapped game with a readable packed RGB visual."""
        clients = self.root.get_full_property(self.clients_atom, Xatom.WINDOW)
        if clients is None:
            return False
        for xid in clients.value:
            window = self.connection.create_resource_object("window", xid)
            try:
                title = window.get_full_property(self.title_atom, self.utf8_atom)
                title = title.value if title else window.get_wm_name() or ""
                if isinstance(title, bytes):
                    title = title.decode("utf-8", "replace")
                if not self.is_wow(title, window.get_wm_class() or ()):
                    continue
                owner = window.get_full_property(self.pid_atom, Xatom.CARDINAL)
                pid = int(owner.value[0]) if owner is not None and len(owner.value) else None
                if self.pid is not None and pid != self.pid:
                    continue
                attributes = window.get_attributes()
                if attributes.map_state != X.IsViewable:
                    continue
                geometry = window.get_geometry()
                self.configure(window, geometry, attributes.visual)
                if self.read_window() is not None:
                    if self.pid is None and pid and self.game.watch(pid):
                        self.pid = pid
                    return True
            except (xerror.XError, ValueError):
                pass
            self.window = None
        return False

    def configure(self, window, geometry, visual_id):
        """Resolve stride, byte order and RGB channel offsets from the X server."""
        screen = self.connection.screen()
        visual = next((item for depth in screen.allowed_depths if depth.depth == geometry.depth
                       for item in depth.visuals if item.visual_id == visual_id), None)
        info = self.connection.display.info
        fmt = next((item for item in info.pixmap_formats if item.depth == geometry.depth), None)
        if visual is None or fmt is None or fmt.bits_per_pixel not in (24, 32):
            raise ValueError("Unsupported X11 pixel layout.")
        pixel_size = fmt.bits_per_pixel // 8
        masks = (visual.red_mask, visual.green_mask, visual.blue_mask)
        if any(mask not in (0xff, 0xff00, 0xff0000, 0xff000000) for mask in masks):
            raise ValueError("Unsupported X11 color masks.")
        channels = tuple((mask.bit_length() - 1) // 8 for mask in masks)
        if info.image_byte_order == X.MSBFirst:
            channels = tuple(pixel_size - 1 - channel for channel in channels)
        self.width = min(geometry.width, CROP_WIDTH)
        self.height = min(geometry.height, CROP_HEIGHT)
        self.stride = ((self.width * fmt.bits_per_pixel + fmt.scanline_pad - 1)
                       // fmt.scanline_pad * fmt.scanline_pad // 8)
        self.channels, self.pixel_size = channels, pixel_size
        self.window = window

    def sample(self, now):
        """Return one corner image or None if WoW is temporarily unavailable."""
        if now < self.next_search:
            return None
        try:
            if self.window is None:
                if self.game.all_exited():
                    raise CaptureEnded()
                self.next_search = now + 1
                if not self.find_window():
                    return None
                self.next_search = 0
            return self.read_window()
        except (xerror.BadDrawable, xerror.BadWindow):
            return self.window_gone()
        except xerror.BadMatch:
            # The game may still be running. Refresh geometry once per
            # second and keep the window ID so closing it still ends capture.
            self.next_search = now + 1
            try:
                attributes = self.window.get_attributes()
                if attributes.map_state == X.IsViewable:
                    self.configure(self.window, self.window.get_geometry(), attributes.visual)
            except (xerror.BadDrawable, xerror.BadWindow):
                return self.window_gone()
            except xerror.ConnectionClosedError:
                raise CaptureEnded() from None
            except (xerror.XError, ValueError) as error:
                raise RuntimeError(f"Could not refresh WoW window geometry: {error}") from error
            return None
        except xerror.ConnectionClosedError:
            raise CaptureEnded() from None
        except xerror.XError as error:
            raise RuntimeError(f"X11 capture failed: {error}") from error

    def window_gone(self):
        """Keep looking for the same process after it replaces its X11 window."""
        if self.pid is None or self.game.all_exited():
            raise CaptureEnded() from None
        self.window = None

    def read_window(self):
        """Copy the requested X11 rectangle into a frame for the decoder."""
        image = self.window.get_image(0, 0, self.width, self.height, X.ZPixmap, 0xffffffff)
        if image is None:
            return None
        pixels = image.data
        return Frame(lambda offset, size: pixels[offset:offset + size],
                     self.width, self.height, self.stride, 0, self.channels, self.pixel_size)


def x11_capture():
    """Use the small rectangle if WoW is reachable through X11/XWayland."""
    if display is None or not os.environ.get("DISPLAY"):
        return None
    capture = None
    try:
        capture = X11Capture()
        if capture.find_window():
            return capture
    except (xerror.DisplayError, xerror.ConnectionClosedError, xerror.XError, OSError):
        pass
    if capture is not None:
        capture.close()
    return None


# Ask PipeWire for packed RGB in system memory. KDE and GNOME screen capture
# offer these formats. Sampling directly avoids converting entire frames.
FORMATS = ("BGRx", "BGRA", "RGBx", "RGBA", "xRGB", "ARGB", "xBGR", "ABGR", "RGB", "BGR")


def frame_view(sample):
    """Wrap a live Gst sample without copying full frames.

    Read metadata first because PipeWire can deliver padded rows. The Frame
    must be consumed immediately, while its sample is still held by the caller.
    """
    caps = sample.get_caps()
    info = GstVideo.VideoInfo.new_from_caps(caps)
    buffer = sample.get_buffer()
    meta = GstVideo.buffer_get_video_meta(buffer)
    layout = caps.get_structure(0).get_string("format")
    if info is None or layout not in FORMATS:
        raise RuntimeError(f"Unexpected screen capture format: {caps.to_string()}")
    return Frame(buffer.extract_dup, info.width, info.height,
                 meta.stride[0] if meta else info.stride[0],
                 meta.offset[0] if meta else info.offset[0],
                 tuple(layout.index(channel) for channel in "RGB"), len(layout))


class Narration:
    """Translate live marker transitions into one pause and one release.

    A toggling heartbeat confirms the marker is live. Missing or frozen
    frames preserve the last pause decision. A live idle marker releases
    after 450 ms to cover adjacent lines, and a live disable releases at once.
    """

    def __init__(self, player, show_status=None):
        self.player = player
        self.show_status = show_status
        self.anchor = None
        self.heartbeat = None
        self.last_heartbeat = float("-inf")
        self.last_readable = float("-inf")
        self.latest_mode = None
        self.awaiting_tick = True
        self.first_idle = None
        self.search_after = 0
        self.holding = False
        self.connected = False
        self.disabled = False

    def update(self, frame, now):
        """Process a live marker sample and preserve playback during capture gaps."""
        state = None
        if frame is not None:
            if self.anchor is not None:
                state = frame.read(self.anchor)
            if state is None and now >= self.search_after:
                self.anchor, state = frame.locate()
                self.search_after = now + 0.55

        if state is None:
            # A PipeWire poll can be empty between frames. A damaged image or
            # a longer frame gap requires a new heartbeat before taking action.
            if frame is not None or now - self.last_readable > 0.55:
                self.awaiting_tick = True
        else:
            if now - self.last_readable > 0.55:
                self.awaiting_tick = True
            self.last_readable = now
            if state[1] != self.heartbeat:
                if self.heartbeat is not None:
                    self.last_heartbeat = now
                    self.awaiting_tick = False
                self.heartbeat = state[1]

        expired = now - self.last_heartbeat > 2.4
        live = not (expired or self.awaiting_tick)
        holding = self.holding
        if live and state is not None:
            self.latest_mode = state[0]
            self.disabled = state[0] == WHITE
            if state[0] == WHITE:
                holding = False
                self.first_idle = None
            elif state[0] == GREEN:
                holding = True
                self.first_idle = None
            elif holding:
                if self.first_idle is None:
                    self.first_idle = now
                holding = now - self.first_idle < 0.45
        elif expired:
            # A short gap can keep the idle candidate. It still takes a
            # fresh heartbeat and an idle reading to release after a gap.
            self.first_idle = None
        if live != self.connected:
            self.connected = live
            print("Connected to Spoken." if live else "Waiting for Spoken’s marker.", flush=True)

        self.holding = holding
        self.player.set_active(holding, live and self.latest_mode == GREEN)

        if self.show_status:
            if not live:
                status = "Waiting for WoW’s marker (pause held)" if holding else "Waiting for WoW’s marker"
            elif self.disabled:
                status = "Auto-pause off"
            elif holding:
                status = "Media paused" if self.player.owns_pause else "Spoken is narrating"
            else:
                status = "Ready"
            self.show_status(status, self.holding)


def make_pipeline(fd, node, serial):
    """Request the cap at the PipeWire source, before frames are produced."""
    caps = ("video/x-raw,format=(string){" + ",".join(FORMATS) + "}"
            f",framerate=(fraction)[0/1,{PORTAL_MAX_FPS}/1]"
            f",max-framerate=(fraction)[1/1,{PORTAL_MAX_FPS}/1]")
    pipeline = Gst.parse_launch(
        f'pipewiresrc name=source ! capsfilter name=capture_format caps="{caps}" ! '
        "appsink name=frames max-buffers=1 drop=true sync=false enable-last-sample=false"
    )
    source = pipeline.get_by_name("source")
    # pipewiresrc duplicates this descriptor when it connects. The caller
    # retains ownership of the original until the pipeline has stopped.
    source.set_property("fd", fd)
    if source.find_property("on-disconnect") is not None:
        Gst.util_set_object_arg(source, "on-disconnect", "eos")
    if serial is None or source.find_property("target-object") is None:
        source.set_property("path", str(node))
    else:
        source.set_property("target-object", str(serial))
    return pipeline


def stream_disconnected(error, debug):
    """Recognize the disconnect errors used by PipeWire and its GStreamer source."""
    if not (error.matches(Gst.ResourceError.quark(), Gst.ResourceError.FAILED)
            or error.matches(Gst.ResourceError.quark(), Gst.ResourceError.NOT_FOUND)):
        return False
    description = f"{error.message} {debug or ''}".casefold()
    return any(reason in description for reason in (
        "all buffers have been removed",
        "link to remote node was destroyed",
        "stream error: disconnected",
        "stream error: broken pipe",
        "stream error: no node available",
        "stream error: no such device",
        "target not found",
    ))


class StatusWindow:
    """A status-only GTK window driven by the capture's existing GLib loop."""

    def __init__(self):
        try:
            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk
        except (ImportError, ValueError) as error:
            raise RuntimeError("The status window needs the gtk4 package.") from error
        if not Gtk.init_check():
            raise RuntimeError("Could not open the status window on this desktop.")

        self.window = Gtk.Window(title="Spoken Media Pause")
        self.window.set_default_size(310, -1)
        self.window.set_resizable(False)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for set_margin in (content.set_margin_top, content.set_margin_bottom,
                           content.set_margin_start, content.set_margin_end):
            set_margin(16)
        self.label = Gtk.Label(xalign=0, wrap=True)
        self.label.add_css_class("title-4")
        detail = Gtk.Label(label="Click the marker in WoW to toggle auto-pause.",
                           xalign=0, wrap=True)
        detail.add_css_class("dim-label")
        content.append(self.label)
        content.append(detail)
        controls = Gtk.Box(spacing=8)
        self.test_buttons = []
        for method in ("Pause", "Play"):
            button = Gtk.Button(label=f"Test {method}")
            button.set_sensitive(False)
            button.connect("clicked", self.run_test, method)
            self.test_buttons.append(button)
            controls.append(button)
        content.append(controls)
        self.feedback = Gtk.Label(xalign=0, wrap=True)
        self.feedback.set_visible(False)
        content.append(self.feedback)
        self.recovery = Gtk.Label(xalign=0, wrap=True)
        self.recovery.set_visible(False)
        content.append(self.recovery)
        self.share = Gtk.Button(label="Share WoW Window")
        self.share.set_visible(False)
        self.share.connect("clicked", self.share_clicked)
        content.append(self.share)
        updates = Gtk.Box(spacing=8)
        self.check_button = Gtk.Button(label="Check for updates")
        self.check_button.connect("clicked", lambda _button: self.check_updates(True))
        self.release_link = Gtk.LinkButton.new_with_label(RELEASES, "View release")
        self.release_link.set_visible(False)
        updates.append(self.check_button)
        updates.append(self.release_link)
        content.append(updates)
        self.update_message = Gtk.Label(xalign=0, wrap=True)
        self.update_message.set_visible(False)
        content.append(self.update_message)
        self.window.set_child(content)
        self.on_close = None
        self.on_test = None
        self.testing = False
        self.holding = False
        self.media_busy = False
        self.on_share = None
        self.updates = UpdateChecker()
        self.update_timer = GLib.timeout_add_seconds(DAY, self.check_updates, False)
        self.update_idle = GLib.idle_add(self.start_updates)
        self.disposed = False
        self.window.connect("close-request", self.close_request)
        self.show("Starting…")
        self.window.present()

    def start_updates(self):
        """Start the first automatic check once the GTK loop is ready."""
        self.update_idle = None
        self.check_updates(False)
        return GLib.SOURCE_REMOVE

    def check_updates(self, manual):
        """Report update results separately from capture and playback controls."""
        if self.updates.busy:
            return GLib.SOURCE_CONTINUE
        self.check_button.set_sensitive(False)
        if manual:
            self.update_message.set_label("Checking GitHub…")
            self.update_message.set_visible(True)

        def show_result(failed, tag):
            if self.disposed:
                return
            self.check_button.set_sensitive(True)
            self.release_link.set_visible(tag is not None)
            if tag:
                self.release_link.set_uri(RELEASES + tag)
            message = f"Update available ({tag.removeprefix('addon-')})" if tag else ""
            if manual:
                if failed:
                    message = "Could not check GitHub. " + message if message else "Could not check GitHub."
                elif not tag:
                    message = "No update available."
            self.update_message.set_label(message)
            self.update_message.set_visible(bool(message))

        self.updates.check(manual, show_result)
        return GLib.SOURCE_CONTINUE

    def dispose(self):
        """Stop scheduled checks and ignore an in-flight check after closing."""
        self.disposed = True
        GLib.source_remove(self.update_timer)
        if self.update_idle is not None:
            GLib.source_remove(self.update_idle)
        self.window.destroy()

    def show_recovery(self, message, busy):
        """Show media recovery alongside capture state, keeping test controls safe."""
        if self.recovery.get_label() != message:
            self.recovery.set_label(message)
            self.recovery.set_visible(bool(message))
        self.media_busy = busy
        self.update_test_buttons()

    def show(self, status, holding=False):
        """Show capture state and reserve playback for automation while narrating."""
        if self.label.get_label() != status:
            self.label.set_label(status)
        self.holding = holding
        self.update_test_buttons()

    def update_test_buttons(self):
        enabled = self.on_test is not None and not self.holding and not self.testing and not self.media_busy
        for button in self.test_buttons:
            if button.get_sensitive() != enabled:
                button.set_sensitive(enabled)

    def run_test(self, _button, method):
        self.testing = True
        self.update_test_buttons()
        self.feedback.set_label(f"Sending {method}…")
        self.feedback.set_visible(True)

        def show_result(message):
            self.testing = False
            self.feedback.set_label(message)
            self.update_test_buttons()

        self.on_test(method, show_result)

    def share_clicked(self, _button):
        if self.on_share is not None:
            self.on_share()

    def close_request(self, _window):
        """Stop the owning loop before destroying the window."""
        if self.on_close is not None:
            self.on_close()
        return True

    def show_error(self, error):
        """Keep a launch or capture failure readable until the window closes."""
        self.on_test = None
        self.show(str(error))
        loop = GLib.MainLoop()
        self.on_close = loop.quit
        loop.run()


def main():
    """Select terminal or desktop mode, reporting errors in the same surface."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    capture_mode = parser.add_mutually_exclusive_group()
    capture_mode.add_argument("--portal", action="store_true", help="use the Wayland window picker")
    capture_mode.add_argument("--display", action="store_true", help="use the Wayland display picker")
    parser.add_argument("--status-window", action="store_true", help="show a small GTK status window")
    args = parser.parse_args()

    status = StatusWindow() if args.status_window else None
    try:
        run_capture(args, status)
    except (GLib.Error, KeyError, RuntimeError, ValueError, OSError) as error:
        if status is None:
            raise
        print(f"Spoken Media Pause: {error}", file=sys.stderr)
        status.show_error(error)
    finally:
        if status is not None:
            status.dispose()


def run_capture(args, status):
    """Own one capture session and release media on normal or error exit."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    # Keep two helpers from claiming the same media.
    (name_status,) = call(bus, DBUS, DBUS_PATH, DBUS, "RequestName",
                          GLib.Variant("(su)", ("io.github.SpokenMediaPause", 4)))
    if name_status != 1:
        raise RuntimeError("Spoken Media Pause is already running.")

    portal = ScreenCast(bus)
    player = MediaPlayers(bus, status.show_recovery if status is not None else None)
    monitor = Narration(player, status.show if status is not None else None)
    loop = GLib.MainLoop()
    errors = []
    pipeline = None
    gst_bus = None
    fd = None
    closed_subscription = None
    x11 = None
    poll = None
    capture_started = False
    capture_closed = False
    error_exit_timer = None
    display_games = GameProcesses() if args.display else None
    display_games_checked = False

    def stop():
        portal.cancel()
        loop.quit()
        return GLib.SOURCE_CONTINUE

    if status is not None:
        status.on_close = stop
        status.on_test = player.test_command

    def capture_ended(*_args):
        """Treat a user-closed portal session or an ended source as normal exit."""
        nonlocal capture_closed
        capture_closed = True
        loop.quit()

    def finish_after_error():
        """Allow the portal Closed signal to arrive after a pipeline error."""
        loop.quit()
        return GLib.SOURCE_CONTINUE

    def on_pipeline_message(_bus, message):
        nonlocal error_exit_timer
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            if capture_started and stream_disconnected(error, debug):
                capture_ended()
            else:
                errors.append(error.message)
                if error_exit_timer is None:
                    error_exit_timer = GLib.timeout_add(350, finish_after_error)
        elif message.type == Gst.MessageType.EOS:
            capture_ended()

    def poll_frame(read_frame):
        """Advance the shared detector from the selected backend."""
        nonlocal display_games_checked
        try:
            now = monotonic()
            if display_games is not None and display_games.all_exited():
                raise CaptureEnded()
            monitor.update(read_frame(now), now)
            if display_games is not None and monitor.connected and not display_games_checked:
                display_games_checked = True
                display_games.watch_display_games()
        except CaptureEnded:
            capture_ended()
        except (GLib.Error, RuntimeError, OSError) as error:
            errors.append(str(error))
            loop.quit()
        return GLib.SOURCE_CONTINUE

    def portal_frame(_now):
        """Pull the newest buffered sample without blocking on PipeWire."""
        nonlocal capture_started
        sample = sink.emit("try-pull-sample", 0)
        if sample is None:
            return None
        capture_started = True
        return frame_view(sample)

    interrupt = GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    terminate = GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    try:
        if not (args.portal or args.display):
            x11 = x11_capture()
            if x11 is None and status is not None:
                def find_game():
                    nonlocal x11
                    x11 = x11_capture()
                    if x11 is not None:
                        loop.quit()
                    return GLib.SOURCE_CONTINUE

                status.on_share = loop.quit
                status.share.set_visible(True)
                status.show("Waiting for WoW")
                scan = GLib.timeout_add_seconds(1, find_game)
                try:
                    loop.run()
                finally:
                    GLib.source_remove(scan)
                    status.share.set_visible(False)
                    status.on_share = None
                if portal.cancelled:
                    raise Cancelled()
        if x11 is not None:
            print(f"Reading only the {x11.width}×{x11.height} WoW corner (X11/XWayland).", flush=True)
            read_frame = x11.sample
        else:
            if not (args.portal or args.display):
                print("No readable X11 WoW window found. Opening the Wayland picker.", flush=True)
                if display is None:
                    print("Install python-xlib to enable X11/XWayland capture.", flush=True)
            Gst.init([])
            if status is not None:
                status.show("Choose WoW in the screen-sharing picker")
            fd, node, serial = portal.open(args.display)
            closed_subscription = bus.signal_subscribe(
                PORTAL, SESSION, "Closed", portal.session, None, Gio.DBusSignalFlags.NONE,
                capture_ended
            )
            pipeline = make_pipeline(fd, node, serial)
            sink = pipeline.get_by_name("frames")
            gst_bus = pipeline.get_bus()
            gst_bus.add_signal_watch()
            gst_bus.connect("message", on_pipeline_message)
            if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Could not start PipeWire capture.")
            print(f"Wayland capture (requested maximum {PORTAL_MAX_FPS} FPS).", flush=True)
            read_frame = portal_frame
        print("Waiting for Spoken’s marker. Press Ctrl+C to stop.", flush=True)
        if status is not None:
            status.show("Waiting for WoW’s marker")
        poll = GLib.timeout_add(POLL_MS, poll_frame, read_frame)
        loop.run()
        if capture_closed:
            print("WoW capture ended.", flush=True)
        elif errors:
            raise RuntimeError(errors[0])
    except Cancelled:
        pass
    finally:
        for source in (poll, interrupt, terminate, error_exit_timer):
            if source is not None:
                GLib.source_remove(source)
        player.close()
        if x11 is not None:
            x11.close()
        if display_games is not None:
            display_games.close()
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        if gst_bus is not None:
            gst_bus.remove_signal_watch()
        if fd is not None:
            os.close(fd)
        if closed_subscription is not None:
            bus.signal_unsubscribe(closed_subscription)
        portal.close()


if __name__ == "__main__":
    try:
        main()
    except (GLib.Error, KeyError, RuntimeError, ValueError, OSError) as error:
        sys.exit(f"Spoken Media Pause: {error}")
