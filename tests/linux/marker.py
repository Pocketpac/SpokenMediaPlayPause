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


def check_window_replacement():
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

    capture = helper.X11Capture()
    try:
        with subprocess.Popen(["sleep", "30"]) as game:
            try:
                first = open_window(game.pid)
                assert capture.find_window() and capture.sample(0) is not None
                first.destroy()
                connection.sync()
                assert capture.sample(1) is None
                assert capture.sample(1.1) is None
                replacement = open_window(game.pid)
                assert capture.sample(2.2) is not None and capture.window.id == replacement.id
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
    check_playback()
    check_window_replacement()
    print("Linux marker hold and X11 replacement checks passed.")
