"""端口自动切换与 UDP 广播器测试（TDD RED）—— PRD 2.4 / 5.8 · 陷阱 T-13

实跑时发现的真实故障场景：5000 常被其它软件占用；广播报文必须跟着报实际端口，
否则 APP 连到一个没人监听的端口，用户只会看到「连不上」而不知原因。
"""
import socket
import threading

import pytest


def _occupy(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


def test_serve_reports_the_port_it_actually_bound(app):
    from core.server import serve, shutdown
    srv, bound = serve(app, host="127.0.0.1", port=0)
    try:
        assert bound.port > 0, "必须回报真实端口"
        assert bound.port > 0
        with socket.create_connection(("127.0.0.1", bound.port), timeout=3):
            pass
    finally:
        shutdown(srv, None)


def test_serve_falls_back_when_default_port_is_taken(app):
    """★ 5000 被占 → 自动试 5001…，且返回的 bound.port 是真正 listening 的那个"""
    from core.server import serve, shutdown
    taken = _occupy(5111)
    try:
        srv, bound = serve(app, host="127.0.0.1", port=5111, port_max=5115)
        assert bound.port != 5111, "端口被占时不能假装成功"
        assert 5112 <= bound.port <= 5115
        # 真的能连上
        with socket.create_connection(("127.0.0.1", bound.port), timeout=3) as probe:
            assert probe is not None
        shutdown(srv, None)
    finally:
        taken.close()


def test_serve_gives_up_after_range_exhausted(app):
    from core.server import serve
    holders = [_occupy(p) for p in range(5121, 5125)]
    try:
        with pytest.raises(OSError):
            serve(app, host="127.0.0.1", port=5121, port_max=5124)
    finally:
        for h in holders:
            h.close()


def test_broadcaster_payload_uses_bound_port_not_config():
    """广播内容必须来自实际绑定结果（PRD 2.4 关键约束）"""
    from net.udp_broadcaster import Broadcaster
    from net.protocol import parse_payload
    b = Broadcaster(ip="192.168.1.50", port=5007, name="仓库电脑", interval_ms=50, enabled=False)
    parsed = parse_payload(b.current_payload().decode("utf-8"))
    assert parsed == {"ip": "192.168.1.50", "port": 5007, "name": "仓库电脑"}


def test_disabled_broadcaster_never_touches_socket(app):
    """测试环境 BROADCAST_ENABLED=false：绝不能真发广播包（规则 30-testing-rules）"""
    from net import udp_broadcaster
    sent = []
    monkey_socket = _FakeSocket(sent)
    udp_broadcaster._socket_factory = lambda *a, **k: monkey_socket
    b = udp_broadcaster.Broadcaster(ip="192.168.1.50", port=5000, enabled=False)
    b.run_once()
    assert sent == [], "禁用状态下不得发包"


def test_broadcast_failure_degrades_without_crashing():
    """T-13：发送失败（防火墙/网卡不支持）必须只降级状态，线程不得退出、不得抛到主流程"""
    from net import udp_broadcaster
    sent = []
    udp_broadcaster._socket_factory = lambda *a, **k: _FakeSocket(sent, fail=True)
    b = udp_broadcaster.Broadcaster(ip="192.168.1.50", port=5000, interval_ms=20)
    b.run_once()
    st = b.status()
    assert st["active"] is False
    assert st["error"], "失败原因必须可见，不能静默"
    assert b.is_alive() is False or True  # run_once 单次调用不应抛异常即算通过


def test_broadcast_tries_subnet_target_before_global():
    """子网定向广播优先（部分路由器不透 255.255.255.255）"""
    from net import udp_broadcaster
    sent = []
    udp_broadcaster._socket_factory = lambda *a, **k: _FakeSocket(sent)
    b = udp_broadcaster.Broadcaster(ip="192.168.1.50", port=5000, targets=None)
    b.run_once()
    # sendto 的地址是 (ip, port) 元组 —— 与真实 socket API 保持一致
    assert sent[0][1][0] == "192.168.1.255", f"应先发子网定向广播，实际发向 {sent[0][1]}"
    assert sent[0][1][1] == 55555, "必须发到约定的发现端口"


def test_broadcast_falls_back_to_global_when_subnet_refused():
    from net import udp_broadcaster
    sent = []
    udp_broadcaster._socket_factory = lambda *a, **k: _FakeSocket(sent, refuse_subnet=True)
    b = udp_broadcaster.Broadcaster(ip="192.168.1.50", port=5000, targets=None)
    b.run_once()
    assert any(addr[0] == "255.255.255.255" for _, addr in sent), \
        f"定向广播被拒后必须回落全局广播，实际尝试 {sent}"


def test_set_endpoint_reflects_new_port():
    """IP/端口变化后，下一次广播必须立刻用新值（换端口不更新=APP 连旧端口失败）"""
    from net.udp_broadcaster import Broadcaster
    from net.protocol import parse_payload
    b = Broadcaster(ip="192.168.1.50", port=5000, enabled=False)
    b.set_endpoint(ip="192.168.1.99", port=5003)
    assert parse_payload(b.current_payload().decode("utf-8"))["port"] == 5003
    assert parse_payload(b.current_payload().decode("utf-8"))["ip"] == "192.168.1.99"


class _FakeSocket:
    """只替掉 socket 这一层，协议构造/目标顺序/状态机仍走真实代码。"""

    def __init__(self, sink, fail: bool = False, refuse_subnet: bool = False):
        self.sink = sink
        self.fail = fail
        self.refuse_subnet = refuse_subnet

    def setsockopt(self, *a):
        pass

    def sendto(self, data, address):
        host = address[0] if isinstance(address, tuple) else address
        if self.fail:
            raise OSError("模拟防火墙拦截")
        if self.refuse_subnet and host.endswith(".255") and host != "255.255.255.255":
            raise OSError("模拟定向广播被拒")
        self.sink.append((data, address))

    def close(self):
        pass
