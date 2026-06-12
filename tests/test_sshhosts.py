"""Parsing of ~/.ssh/config Host entries."""

from netgrip.core.sshhosts import ssh_config_hosts


def test_basic_hosts_and_wildcard_skipping(tmp_path):
    config = tmp_path / "config"
    config.write_text(
        "# comment\n"
        "Host web1 web2\n"
        "    HostName 10.0.0.1\n"
        "\n"
        "Host *.internal\n"
        "    User admin\n"
        "Host db?\n"
        "Host backup\n"
    )
    assert ssh_config_hosts(str(config)) == ["backup", "web1", "web2"]


def test_include_directive(tmp_path):
    extra = tmp_path / "extra.conf"
    extra.write_text("Host included-host\n")
    config = tmp_path / "config"
    config.write_text(f"Include {extra}\nHost main-host\n")
    assert ssh_config_hosts(str(config)) == ["included-host", "main-host"]


def test_missing_file():
    assert ssh_config_hosts("/nonexistent/path/config") == []


def test_dedup_and_sort(tmp_path):
    config = tmp_path / "config"
    config.write_text("Host Zeta\nHost alpha\nHost Zeta\n")
    assert ssh_config_hosts(str(config)) == ["alpha", "Zeta"]
