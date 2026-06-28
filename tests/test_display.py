"""Tests for display detection and GUI/CLI dispatch — pure, no Qt."""


from netgrip.app import render_text
from netgrip.core.display import choose_gui, has_display
from netgrip.core.model import Address, Interface

# ---------------------------------------------------------------------------
# has_display
# ---------------------------------------------------------------------------

def test_has_display_x11(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert has_display() is True


def test_has_display_wayland(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert has_display() is True


def test_has_display_both(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    assert has_display() is True


def test_has_display_headless(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert has_display() is False


def test_has_display_offscreen_env_does_not_count(monkeypatch):
    """QT_QPA_PLATFORM=offscreen alone must not be treated as a display."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    assert has_display() is False


# ---------------------------------------------------------------------------
# choose_gui
# ---------------------------------------------------------------------------

def test_choose_gui_force_gui_overrides_headless(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert choose_gui(force_gui=True) is True


def test_choose_gui_force_cli_overrides_display(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert choose_gui(force_cli=True) is False


def test_choose_gui_autodetect_with_display(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert choose_gui() is True


def test_choose_gui_autodetect_headless(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert choose_gui() is False


def test_choose_gui_force_gui_wins_over_force_cli():
    # force_gui takes precedence (argparse makes them mutually exclusive, but
    # the function itself should be safe if called with both True somehow).
    assert choose_gui(force_gui=True, force_cli=True) is True


# ---------------------------------------------------------------------------
# render_text
# ---------------------------------------------------------------------------

def _make_iface(name="eth0", state="up", kind="physical", mac="aa:bb:cc:dd:ee:ff",
                addresses=None) -> Interface:
    iface = Interface(name=name, state=state, kind=kind, mac=mac)
    if addresses:
        iface.addresses = addresses
    return iface


def test_render_text_empty():
    assert render_text("host", []) == "(no interfaces found)"


def test_render_text_contains_label_and_iface():
    iface = _make_iface(
        addresses=[Address("10.0.0.1", 24, 4)],
    )
    out = render_text("myhost", [iface])
    assert "netgrip — myhost" in out
    assert "eth0" in out
    assert "10.0.0.1/24" in out


def test_render_text_marks_dynamic_address():
    iface = _make_iface(
        addresses=[Address("192.168.1.50", 24, 4, dynamic=True)],
    )
    out = render_text("host", [iface])
    assert "dynamic" in out


def test_render_text_down_interface():
    iface = _make_iface(state="down", addresses=[])
    out = render_text("host", [iface])
    assert "down" in out
    assert "(no addresses)" in out
