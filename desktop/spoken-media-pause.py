#!/usr/bin/env python3
"""Pause Firefox during Spoken narration on a Wayland desktop.

X11 and XWayland games supply only a small rectangle from the WoW window.
For native Wayland the ScreenCast portal supplies the selected stream, with a
low frame-rate request at the PipeWire source. Both use the same marker decoder
and send Pause and Play directly to Firefox over D-Bus.
"""

import argparse
from contextlib import suppress
import os
import re
import signal
import sys
from time import monotonic
from uuid import uuid4

import gi

for namespace in ("Gst", "GstApp", "GstVideo"):
    gi.require_version(namespace, "1.0")
from gi.repository import Gio, GLib, Gst, GstVideo

try:
    from Xlib import X, Xatom, display, error as xerror
except ImportError:
    display = None


PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
DBUS = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER = MPRIS_PREFIX + "Player"
PROPERTIES = "org.freedesktop.DBus.Properties"
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


class Firefox:
    """Own at most one pause for the selected Firefox MPRIS session.

    Control uses the unique bus owner to prevent resuming a restarted browser.
    Track metadata guards against a tab or item changing during narration.
    A browser already paused at the start never belongs to this helper.
    """

    def __init__(self, bus):
        self.bus = bus
        self.paused = None

    def properties(self, owner):
        """Fetch the current media state and metadata together for one owner."""
        (properties,) = call(self.bus, owner, MPRIS_PATH, PROPERTIES, "GetAll",
                             GLib.Variant("(s)", (MPRIS_PLAYER,)))
        return properties

    @staticmethod
    def track(properties):
        """Use identity fields Firefox exposes, excluding art, position, and duration."""
        data = properties.get("Metadata", {})
        return tuple(data.get(key) for key in (
            "mpris:trackid", "xesam:url", "xesam:title", "xesam:artist", "xesam:album"
        ))

    def sessions(self):
        """Find Firefox sessions and use their unique owners for control."""
        (names,) = call(self.bus, DBUS, DBUS_PATH, DBUS, "ListNames")
        for name in sorted(names):
            if not re.fullmatch(re.escape(MPRIS_PREFIX) + r"firefox(?:\..+)?", name):
                continue
            try:
                (owner,) = call(self.bus, DBUS, DBUS_PATH, DBUS, "GetNameOwner",
                                GLib.Variant("(s)", (name,)))
                properties = self.properties(owner)
            except GLib.Error:
                continue  # A Firefox session can vanish between discovery and querying.
            yield owner, properties

    def test_command(self, method):
        """Send a manual test without creating or changing an automatic pause."""
        expected = "Playing" if method == "Pause" else "Paused"
        for owner, properties in self.sessions():
            if properties.get("PlaybackStatus") == expected and properties.get("Can" + method, True):
                call(self.bus, owner, MPRIS_PATH, MPRIS_PLAYER, method)
                return f"Sent {method} to Firefox."
        return f"No {expected.lower()} Firefox media found."

    def pause(self):
        """Pause a playing Firefox session once and remember what to resume."""
        for owner, properties in self.sessions():
            try:
                if properties.get("PlaybackStatus") == "Playing" and properties.get("CanPause", True):
                    self.paused = owner, self.track(properties)
                    call(self.bus, owner, MPRIS_PATH, MPRIS_PLAYER, "Pause")
                    print("Paused Firefox.", flush=True)
                    return
            except GLib.Error as error:
                # Keep the record if the command timed out. Firefox might have
                # received it before the reply was lost.
                print(f"Firefox pause request failed: {error.message}", file=sys.stderr)
                if self.paused:
                    return

    def resume(self):
        """Resume only the recorded Firefox owner if its same media is still paused."""
        if not self.paused:
            return
        owner, track = self.paused
        self.paused = None
        try:
            properties = self.properties(owner)
            if properties.get("PlaybackStatus") == "Paused" and self.track(properties) == track:
                call(self.bus, owner, MPRIS_PATH, MPRIS_PLAYER, "Play")
                print("Resumed Firefox.", flush=True)
        except GLib.Error as error:
            print(f"Could not resume Firefox: {error.message}", file=sys.stderr)


# Six cells shared with SpokenMediaPause.lua. RGB bit codes are ordered red,
# green, blue. Values near a color boundary are rejected to tolerate scaling.
MAGENTA, CYAN, YELLOW, BLUE, RED, GREEN, WHITE, BLACK = 5, 3, 6, 1, 4, 2, 7, 0
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

    def color(self, x, y):
        """Return a three-bit RGB color or -1 for mixed or out-of-bounds pixels."""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return -1
        # PyGObject's map.data copies the whole buffer. Extract just the
        # requested rows within our crop, normally two rows per fresh frame.
        row = self.rows.get(y)
        if row is None:
            row = self.extract(self.offset + y * self.stride, self.width * self.pixel_size)
            self.rows[y] = row
        index = x * self.pixel_size
        if index + self.pixel_size > len(row):
            return -1
        code = 0
        for channel in self.channels:
            value = row[index + channel]
            if 80 < value < 175:
                return -1
            code = (code << 1) | (value >= 175)
        return code

    def read(self, anchor):
        """Validate four fixed cells and return (state color, heartbeat) if present."""
        x, y, size = anchor
        if (self.color(x, y) != MAGENTA or self.color(x + size, y) != CYAN
                or self.color(x, y + size) != YELLOW or self.color(x + size, y + size) != BLUE):
            return None
        state = self.color(x + 2 * size, y)
        heartbeat = self.color(x + 2 * size, y + size)
        if state in (RED, GREEN, WHITE) and heartbeat in (WHITE, BLACK):
            return state, heartbeat == WHITE
        return None

    def locate(self):
        """Find the marker after startup or a resize by the width of its first cell."""
        for y in range(1, self.height, 2):
            x = 0
            while x < self.width:
                if self.color(x, y) != MAGENTA:
                    x += 1
                    continue
                left = x
                while self.color(x, y) == MAGENTA:
                    x += 1
                width = x - left
                if 3 <= width <= 44:
                    center = (left + x - 1) // 2
                    for size in range(max(3, width - 1), width + 5):
                        for shift in (0, 1):
                            anchor = center + shift, y, size
                            state = self.read(anchor)
                            if state is not None:
                                return anchor, state
        return None, None


class X11Capture:
    """Poll only the corner of a viewable WoW window on X11 or XWayland.

    Auto-discovery reads the window manager client list and WoW's class/title.
    Keep the drawable until the game closes. If the window is minimized or
    resized, refresh its geometry at most once per second.
    """

    def __init__(self):
        self.connection = display.Display()
        try:
            self.root = self.connection.screen().root
            self.clients_atom = self.connection.intern_atom("_NET_CLIENT_LIST")
            self.title_atom = self.connection.intern_atom("_NET_WM_NAME")
            self.utf8_atom = self.connection.intern_atom("UTF8_STRING")
            self.window = None
            self.next_search = 0
        except (xerror.XError, xerror.DisplayError):
            self.close()
            raise

    def close(self):
        """Close only this helper's X11 connection."""
        with suppress(xerror.DisplayError, xerror.ConnectionClosedError, OSError):
            self.connection.close()

    @staticmethod
    def is_wow(title, classes):
        """Match a WoW game client without selecting Battle.net or a browser."""
        names = {name.casefold() for name in classes}
        if names.intersection({"wow.exe", "wowb.exe", "wowt.exe", "wowclassic.exe", "wowclassicb.exe"}):
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
                attributes = window.get_attributes()
                if attributes.map_state != X.IsViewable:
                    continue
                geometry = window.get_geometry()
                self.configure(window, geometry, attributes.visual)
                if self.read_window() is not None:
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
            return self.read_window()
        except (xerror.BadDrawable, xerror.BadWindow):
            raise CaptureEnded() from None
        except xerror.BadMatch:
            # The game may still be running. Refresh geometry once per
            # second and keep the window ID so closing it still ends capture.
            self.next_search = now + 1
            try:
                attributes = self.window.get_attributes()
                if attributes.map_state == X.IsViewable:
                    self.configure(self.window, self.window.get_geometry(), attributes.visual)
            except (xerror.BadDrawable, xerror.BadWindow, xerror.ConnectionClosedError):
                raise CaptureEnded() from None
            except (xerror.XError, ValueError) as error:
                raise RuntimeError(f"Could not refresh WoW window geometry: {error}") from error
            return None
        except xerror.ConnectionClosedError:
            raise CaptureEnded() from None
        except xerror.XError as error:
            raise RuntimeError(f"X11 capture failed: {error}") from error

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

    Sample the newest frame only. Acquire a toggling heartbeat before acting.
    A 450 ms grace covers adjacent lines and stop fades. Wait up to 900 ms
    for another active sample when frames are missing, since the compositor
    may only emit a frame on damage. A frozen heartbeat expires after 2.4 s.
    An explicit disable clears the active window immediately, so a missing
    sample after the click cannot reapply the previous pause.
    """

    def __init__(self, player, show_status=None):
        self.player = player
        self.show_status = show_status
        self.anchor = None
        self.heartbeat = None
        self.last_heartbeat = float("-inf")
        self.last_active = float("-inf")
        self.first_idle = None
        self.search_after = 0
        self.holding = False
        self.connected = False
        self.disabled = False

    def update(self, frame, now):
        """Process one sample, or advance expiry without an available sample."""
        state = None
        if frame is not None:
            if self.anchor is not None:
                state = frame.read(self.anchor)
            if state is None and now >= self.search_after:
                self.anchor, state = frame.locate()
                self.search_after = now + 0.55

        if state is not None and state[1] != self.heartbeat:
            if self.heartbeat is not None:
                self.last_heartbeat = now
            self.heartbeat = state[1]

        live = now - self.last_heartbeat <= 2.4
        if live and state is not None:
            self.disabled = state[0] == WHITE
            if state[0] == WHITE:
                self.last_active = float("-inf")
                self.first_idle = None
            elif state[0] == GREEN:
                self.last_active = now
                self.first_idle = None
            elif self.first_idle is None:
                self.first_idle = now
        if live != self.connected:
            self.connected = live
            print("Connected to Spoken." if live else "Waiting for Spoken’s marker.", flush=True)

        idle_too_long = self.first_idle is not None and now - self.first_idle >= 0.45
        holding = live and now - self.last_active < 0.9 and not idle_too_long
        if holding != self.holding:
            self.holding = holding
            if holding:
                self.player.pause()
            else:
                self.player.resume()

        if self.show_status:
            if not live:
                status = "Waiting for WoW’s marker"
            elif self.disabled:
                status = "Auto-pause off"
            elif holding:
                status = "Firefox paused" if self.player.paused else "Spoken is narrating"
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
        self.share = Gtk.Button(label="Share WoW Window")
        self.share.set_visible(False)
        self.share.connect("clicked", self.share_clicked)
        content.append(self.share)
        self.window.set_child(content)
        self.on_close = None
        self.on_test = None
        self.on_share = None
        self.window.connect("close-request", self.close_request)
        self.show("Starting…")
        self.window.present()

    def show(self, status, holding=False):
        """Show capture state and reserve playback for automation while narrating."""
        if self.label.get_label() != status:
            self.label.set_label(status)
        enabled = self.on_test is not None and not holding
        for button in self.test_buttons:
            if button.get_sensitive() != enabled:
                button.set_sensitive(enabled)

    def run_test(self, _button, method):
        try:
            message = self.on_test(method)
        except GLib.Error as error:
            message = f"Firefox {method} failed: {error.message}"
        self.feedback.set_label(message)
        self.feedback.set_visible(True)

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
            status.window.destroy()


def run_capture(args, status):
    """Own one capture session and release media on normal or error exit."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    # Prevent two helpers from independently claiming Firefox's pause.
    (name_status,) = call(bus, DBUS, DBUS_PATH, DBUS, "RequestName",
                          GLib.Variant("(su)", ("io.github.SpokenMediaPause", 4)))
    if name_status != 1:
        raise RuntimeError("Spoken Media Pause is already running.")

    portal = ScreenCast(bus)
    player = Firefox(bus)
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
        try:
            now = monotonic()
            monitor.update(read_frame(now), now)
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
        player.resume()
        if x11 is not None:
            x11.close()
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
