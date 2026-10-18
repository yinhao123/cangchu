"""服务模式对外信息与开机自启 —— docs/12

重点是「不许把地址算错」：端口必须是实际绑定端口（被占会顺延），IP 必须是实际宣告网卡，
否则现场表现是「服务在跑、手机/浏览器都连不上」，排查成本极高（T-13/T-14 的同类错误）。
"""
import base64
import json
import sys

import pytest

from core import service_mode
from core.server import Bound

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _bound(port: int = 5007, ip: str = "10.0.0.5") -> Bound:
    return Bound(host="0.0.0.0", port=port, ip=ip)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把可写数据目录指到 tmp：publish 会真的落盘两个文件。"""
    target = tmp_path / "data"
    target.mkdir()
    monkeypatch.setenv("SCANABLE_DATA_DIR", str(target))
    return target


def test_access_info_uses_bound_port_and_ip():
    """端口/IP 必须取实际绑定值，不能读配置里的 HTTP_PORT。"""
    info = service_mode.access_info(_bound(port=5007, ip="10.0.0.5"), "ScanableSelf")
    assert info["url"] == "http://10.0.0.5:5007/"
    assert info["port"] == 5007
    assert info["urls"][0] == info["url"]
    assert all(url.endswith(":5007/") for url in info["urls"])


def test_qr_payload_carries_no_credential():
    """二维码会被拍照贴墙上，内容只允许是连接信息。"""
    info = service_mode.announce(_bound(), "扫码出入库")
    payload = json.loads(info["qr_payload"])
    assert payload["host"] == "10.0.0.5" and payload["port"] == 5007
    assert payload["name"] == "扫码出入库"
    assert set(payload) == {"protocol", "version", "host", "port", "name"}
    assert base64.b64decode(info["qr_png_base64"]).startswith(PNG_MAGIC)


def test_publish_writes_qr_and_note(data_dir):
    info = service_mode.publish(_bound(), "ScanableSelf")
    qr = data_dir / service_mode.ACCESS_QR_FILE_NAME
    note = data_dir / service_mode.ACCESS_NOTE_FILE_NAME
    assert qr.read_bytes().startswith(PNG_MAGIC)
    text = note.read_text(encoding="utf-8-sig")
    assert "http://10.0.0.5:5007/" in text
    assert service_mode.FIREWALL_PORTS in text
    assert info["qr_path"] == str(qr) and info["note_path"] == str(note)


def test_publish_survives_qr_failure(data_dir, monkeypatch):
    """画不出二维码（缺 Pillow / 字体异常）不能让服务起不来，只退化成「手工输地址」。"""
    def boom(_payload):
        raise RuntimeError("no pillow")

    monkeypatch.setattr("net.qrcode_gen.to_png_base64", boom)
    info = service_mode.publish(_bound(), "ScanableSelf")
    assert info["qr_png_base64"] == "" and info["qr_path"] == ""
    assert info["url"] == "http://10.0.0.5:5007/"          # 地址仍然可用
    assert (data_dir / service_mode.ACCESS_NOTE_FILE_NAME).exists()


def test_autostart_roundtrip(tmp_path, monkeypatch):
    appdata = tmp_path / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert service_mode.is_autostart_enabled() is False
    path = service_mode.enable_autostart()
    assert path == service_mode.autostart_file()
    body = path.read_text(encoding="utf-8-sig")
    assert sys.executable in body and "ScanableSelf" in body
    assert service_mode.is_autostart_enabled() is True

    assert service_mode.disable_autostart() is True
    assert service_mode.is_autostart_enabled() is False
    assert service_mode.disable_autostart() is False      # 幂等：原本没有不报错


def test_autostart_refuses_dev_mode(tmp_path, monkeypatch):
    """开发态注册自启 = 每次登录都拉起一个调试进程，必须拒绝。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delattr(sys, "frozen", raising=False)
    with pytest.raises(RuntimeError):
        service_mode.enable_autostart()


def test_firewall_commands_cover_http_range_and_udp():
    joined = " ".join(service_mode.firewall_commands())
    assert "localport=5000-5010" in joined and "protocol=UDP" in joined and "55555" in joined
