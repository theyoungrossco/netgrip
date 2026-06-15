"""On-disk persistence of UI state (drafts, positions, box names)."""

from netgrip.core import store


def test_data_dir_honours_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert store.data_dir() == tmp_path / "netgrip"


def test_load_missing_host_returns_blank(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    data = store.load_host("never-seen")
    assert data == {
        "positions": {}, "drafts": [], "draft_vlans": [],
        "aliases": {}, "manual_dns": [],
    }


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    payload = {
        "positions": {"ip:eth0:192.168.1.10/24": [10.0, 20.0]},
        "drafts": [{"family": 4, "cidr": "10.0.0.1/24", "pos": [5.0, 6.0]}],
        "draft_vlans": [
            {"vlan_id": 40, "name": "lab", "cidrs": ["10.0.40.1/24"], "pos": [7.0, 8.0]}
        ],
        "aliases": {"4:10.0.0.1/24": "gateway"},
        "manual_dns": ["1.1.1.1"],
    }
    store.save_host("local", payload)
    assert store.load_host("local") == payload


def test_corrupt_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    store.save_host("local", {"positions": {}, "drafts": [], "aliases": {}})
    # Clobber the file with junk; loading must not raise.
    (tmp_path / "netgrip" / "local.json").write_text("{not json")
    assert store.load_host("local") == {
        "positions": {}, "drafts": [], "draft_vlans": [],
        "aliases": {}, "manual_dns": [],
    }


def test_label_sanitized_into_one_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # A label with path-unfriendly characters still saves and loads.
    store.save_host("user@10.0.0.2:22", {"positions": {}, "drafts": [], "aliases": {"x": "y"}})
    assert store.load_host("user@10.0.0.2:22")["aliases"] == {"x": "y"}
    files = list((tmp_path / "netgrip").glob("*.json"))
    assert len(files) == 1
    assert "/" not in files[0].name
