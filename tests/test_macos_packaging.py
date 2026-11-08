"""Desktop binding fails closed instead of opening an unrelated local service."""
import socket
import pytest
from app.macos_desktop import is_app_url, reserve_socket


def test_desktop_reserves_loopback_before_startup():
    sock = reserve_socket('127.0.0.1', 0)
    try:
        host, port = sock.getsockname()
        assert host == '127.0.0.1'
        with socket.create_connection((host, port), timeout=1):
            pass
        with pytest.raises(OSError):
            reserve_socket(host, port)
    finally:
        sock.close()
    replacement = reserve_socket(host, port)
    replacement.close()


def test_desktop_rebinds_while_previous_connections_time_wait():
    sock = reserve_socket('127.0.0.1', 0)
    port = sock.getsockname()[1]
    client = socket.create_connection(('127.0.0.1', port), timeout=1)
    server_side, _ = sock.accept()
    # Closing the server side first leaves its end in TIME_WAIT, as after Quit.
    server_side.close()
    client.close()
    sock.close()
    replacement = reserve_socket('127.0.0.1', port)
    replacement.close()


def test_build_refuses_non_macos(monkeypatch, tmp_path):
    from deploy import build_macos_app
    monkeypatch.setattr(build_macos_app.sys, 'platform', 'linux')
    with pytest.raises(RuntimeError, match='Intel macOS'):
        build_macos_app.build(tmp_path)
    assert not list(tmp_path.iterdir())


def test_only_local_server_pages_stay_in_app_window():
    origin = 'http://127.0.0.1:8000'
    assert is_app_url('http://127.0.0.1:8000/operator', origin)
    assert is_app_url('about:blank', origin)
    assert not is_app_url('https://github.com/BaconDisk22/WXCOMMAND-PRO/releases', origin)
    assert not is_app_url('http://127.0.0.1:9000/', origin)
    assert not is_app_url('https://127.0.0.1:8000/', origin)


def test_dmg_creation_retries_busy_failures(monkeypatch, tmp_path):
    import subprocess
    from deploy import build_macos_app as b
    calls = []

    def flaky(cmd, check):
        calls.append(cmd)
        if len(calls) < 3:
            raise subprocess.CalledProcessError(16, cmd)

    monkeypatch.setattr(b.subprocess, "run", flaky)
    monkeypatch.setattr(b.time, "sleep", lambda _: None)
    b.create_dmg("src", tmp_path / "a.dmg")
    assert len(calls) == 3


def test_dmg_creation_gives_up_after_all_attempts(monkeypatch, tmp_path):
    import subprocess
    import pytest
    from deploy import build_macos_app as b

    def busy(cmd, check):
        raise subprocess.CalledProcessError(16, cmd)

    monkeypatch.setattr(b.subprocess, "run", busy)
    monkeypatch.setattr(b.time, "sleep", lambda _: None)
    with pytest.raises(subprocess.CalledProcessError):
        b.create_dmg("src", tmp_path / "a.dmg", attempts=2)
