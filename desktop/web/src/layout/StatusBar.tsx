import { useEffect, useState } from "react";
import { Badge, Space, Tooltip, Typography } from "antd";
import { WifiOutlined } from "@ant-design/icons";
import { ping } from "../api/system";
import type { PingData } from "../api/system";
import { getTrace, subscribeTrace } from "../store/trace";

const { Text } = Typography;

/** 状态栏：广播状态 / 服务器地址 / 连接状态 / 追踪码 / 本地时钟（PRD 28.2） */
export default function StatusBar() {
  const [info, setInfo] = useState<PingData | null>(null);
  const [online, setOnline] = useState(true);
  const [clock, setClock] = useState(() => new Date());
  const [trace, setTraceState] = useState(getTrace());

  useEffect(() => subscribeTrace(() => setTraceState(getTrace())), []);

  useEffect(() => {
    let alive = true;
    const probe = async () => {
      try {
        // /api/ping 免鉴权：既能当存活探针，又不需要 Token（断线后也能显示「服务端在线吗」）
        const data = await ping();
        if (alive) {
          setInfo(data);
          setOnline(true);
        }
      } catch {
        if (alive) {
          setOnline(false);
        }
      }
    };
    probe();
    const timer = window.setInterval(probe, 30_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="sd-statusbar">
      <Space size={20} wrap>
        <span>
          <Badge status={online ? "success" : "error"} />
          {online ? "服务端在线" : "与服务端断开"}
        </span>
        <span>
          <WifiOutlined style={{ marginRight: 6 }} />
          {info ? (
            info.broadcast_active ? "手机自动发现已开启" : "手机自动发现已关闭（可用二维码配对）"
          ) : (
            "广播状态未知"
          )}
        </span>
        <span>
          服务地址：
          <Text copyable={info ? { text: `http://${info.ip}:${info.port}/` } : false} code>
            {info ? `${info.ip}:${info.port}` : "—"}
          </Text>
        </span>
      </Space>
      <Space size={20}>
        <Tooltip title="出问题时把这个码报给运维，可直接定位服务端日志">
          <span>追踪码 {trace}</span>
        </Tooltip>
        <span>{clock.toLocaleString("zh-CN", { hour12: false })}</span>
      </Space>
    </div>
  );
}
