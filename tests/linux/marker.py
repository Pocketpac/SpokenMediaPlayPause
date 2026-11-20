"""Exercise capture gaps and game window replacement under Xvfb."""

from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

from Xlib import X, Xatom, display

desktop = Path(__file__).resolve().parents[2] / "desktop"
sys.path.insert(0, str(desktop))
helper = SimpleNamespace(**runpy.run_path(str(desktop / "spoken-media-pause.py")))


class Marker:
    """Return a fixed decoded reading or simulate an unreadable image."""

    def __init__(self, state):
        self.state = state

    def read(self, _anchor):
        return self.state

    def locate(self):
        return (0, 0, 9), self.state


def check_playback():
    """Only a live marker can change pause intent during a capture session."""
    commands = []
    monitor = helper.Narration(SimpleNamespace(set_active=commands.append))
    monitor.update(Marker((helper.GREEN, True)), 0)
    assert not commands
    monitor.update(Marker((helper.GREEN, False)), 0.4)
    monitor.update(Marker((helper.RED, False)), 0.8)
    monitor.update(None, 1.5)
    monitor.update(Marker((helper.RED, False)), 3)
    assert not monitor.connected and commands == [True]
    monitor.update(Marker((helper.RED, True)), 3.4)
    monitor.update(Marker(None), 3.8)
    monitor.update(Marker((helper.RED, False)), 3.9)
    assert commands == [True]
    monitor.update(Marker((helper.RED, True)), 4.4)
    monitor.update(None, 8)
    assert commands == [True, False]
    monitor.update(Marker((helper.GREEN, False)), 8.4)
    monitor.update(Marker((helper.WHITE, False)), 8.5)
    monitor.update(None, 12)
    assert commands == [True, False, True, False]


def fixture_checks(fixtures):
    """Check the same Lua-rendered crops as Windows, through the PipeWire frame reader."""
    modes = {"idle": helper.RED, "narrating": helper.GREEN, "off": helper.WHITE}
    helper.Gst.init(None)
    caps = helper.Gst.Caps.from_string("video/x-raw,format=BGRx,width=200,height=160,framerate=6/1")

    def load_frame(name):
        """Read a fixture through the same frame adapter used by the Wayland stream."""
        buffer = helper.Gst.Buffer.new_wrapped((fixtures / name).read_bytes())
        return helper.frame_view(helper.Gst.Sample.new(buffer, caps, None, None))

    count = 0
    for line in (fixtures / "expected.tsv").read_text().splitlines():
        name, mode, heartbeat = line.split("\t")
        frame = load_frame(name)
        anchor, actual = frame.locate()
        expected = None if mode == "-" else (modes[mode], heartbeat == "1")
        assert actual == expected, (name, actual, expected)
        if anchor is not None:
            assert frame.read(anchor) == expected, name
            if name.startswith("brass-"):
                # A state change can alter blended edge pixels at fractional scales.
                variant = name.split("-", 3)[3]
                flip = "dark" if heartbeat == "1" else "light"
                for next_mode, code in modes.items():
                    next_name = f"brass-{next_mode}-{flip}-{variant}"
                    assert load_frame(next_name).read(anchor) == (code, flip == "light"), (name, next_name)
        count += 1
    print(f"Decoded {count} Lua-rendered GStreamer crops.")


def check_window_replacement(fixtures=None):
    """Keep the X11 source across a new window and end it on process exit."""
    connection = display.Display()
    root = connection.screen().root
    client_list = connection.intern_atom("_NET_CLIENT_LIST")
    pid_atom = connection.intern_atom("_NET_WM_PID")

    def open_window(pid):
        """Advertise the process that owns the simulated WoW drawable."""
        window = root.create_window(0, 0, 640, 480, 0, X.CopyFromParent, X.InputOutput,
                                    X.CopyFromParent, background_pixel=0)
        window.set_wm_name("World of Warcraft Forever")
        window.change_property(pid_atom, Xatom.CARDINAL, 32, [pid])
        root.change_property(client_list, Xatom.WINDOW, 32, [window.id])
        window.map()
        connection.sync()
        return window

    def verify_capture(window, now):
        """Read an actual X11 upload of the Lua-rendered brass badge."""
        if fixtures is not None:
            data = (fixtures / "brass-idle-light-1.6-0.bgra").read_bytes()
            gc = window.create_gc()
            window.put_image(gc, 0, 0, 200, 160, X.ZPixmap, connection.screen().root_depth, 0, data)
            connection.sync()
        frame = capture.sample(now)
        assert frame is not None
        if fixtures is not None:
            assert frame.locate()[1] == (helper.RED, True)

    capture = helper.X11Capture()
    try:
        with subprocess.Popen(["sleep", "30"]) as game:
            try:
                first = open_window(game.pid)
                assert capture.find_window()
                verify_capture(first, 0)
                first.destroy()
                connection.sync()
                assert capture.sample(1) is None
                assert capture.sample(1.1) is None
                replacement = open_window(game.pid)
                verify_capture(replacement, 2.2)
                assert capture.window.id == replacement.id
            finally:
                game.terminate()
                game.wait()
        replacement.destroy()
        connection.sync()
        try:
            capture.sample(3.3)
        except helper.CaptureEnded:
            pass
        else:
            raise AssertionError("Closing WoW must end capture")
    finally:
        capture.close()
        connection.close()


if __name__ == "__main__":
    fixtures = Path(sys.argv[1]) if len(sys.argv) == 2 else None
    if fixtures is not None:
        fixture_checks(fixtures)
    check_playback()
    check_window_replacement(fixtures)
    print("Linux marker hold and X11 replacement checks passed.")
