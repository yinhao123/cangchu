import { Alert, Button, Card, Col, Descriptions, Row, Space, Spin, Typography } from "antd";
import { CheckCircleOutlined, MobileOutlined, ReloadOutlined } from "@ant-design/icons";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { qrcode, systemInfo } from "../api/admin";

const { Text, Paragraph } = Typography;

/**
 * 手机配对 —— PRD 3.2（UDP 自动发现 + 扫码配对两条路）。
 *
 * ★二维码里必须是**实际绑定成功的 IP 与端口**（服务端用 BOUND_IP/BOUND_PORT 生成）：
 *   端口自动换到 5001 之后还印着 5000，手机扫了必然连不上（T-13/T-14 的同类坑）。
 * ★自动发现被关掉时必须给出「改用扫码配对」的引导，而不是只显示一句「已关闭」。
 */
export default function Connect() {
  const state = useAsync(() => qrcode(), []);
  const info = useAsync(() => systemInfo(), []);

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">手机配对</h1>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => {
            state.reload();
            info.reload();
          }}>刷新二维码</Button>
        </Space>
      </div>

      <Row gutter={16}>
        <Col xs={24} lg={10}>
          <Card bordered={false} title={<Space><MobileOutlined />扫码配对</Space>}>
            <AsyncBoundary state={state} emptyWhen={() => false} skeletonRows={5}>
              {(data) => (
                <Space direction="vertical" size={12} style={{ width: "100%", textAlign: "center" }}>
                  {/* 二维码由服务端生成（含实际 IP:端口），前端不做任何内容拼接 */}
                  <img src={`data:image/png;base64,${data.image_png_base64}`}
                       alt={`配对二维码 ${data.text}`}
                       style={{ width: 240, height: 240, border: "1px solid var(--sd-border)",
                         borderRadius: 8 }} />
                  <Text strong style={{ fontSize: 16 }}>{data.text}</Text>
                  <Text type="secondary">二维码 {data.expires_at.replace("T", " ").slice(11, 19)} 前有效</Text>
                </Space>
              )}
            </AsyncBoundary>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <AsyncBoundary state={info} emptyWhen={() => false} skeletonRows={5}>
            {(data) => (
              <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <Card bordered={false} title="连接信息">
                  <Descriptions column={1} size="small"
                                items={[
                                  { key: "url", label: "访问地址",
                                    children: (
                                      <Text code copyable={{ text: data.url }}>{data.url}</Text>
                                    ) },
                                  { key: "port", label: "服务端口",
                                    children: `${data.port}（端口被占用时程序会自动换到 5001~5010，请以本页显示为准）` },
                                  { key: "name", label: "设备名称", children: data.name },
                                  { key: "discover", label: "手机自动发现",
                                    children: data.broadcast_active
                                      ? <Text type="success">已开启</Text>
                                      : <Text type="warning">已关闭，请用上方二维码配对</Text> },
                                ]}
                  />
                </Card>

                {data.broadcast_active ? (
                  <Alert type="success" showIcon icon={<CheckCircleOutlined />}
                         message="手机连同一个路由器（同一局域网）后，打开 APP 即可自动搜到本机" />
                ) : (
                  <Alert type="warning" showIcon message="局域网自动发现已关闭"
                         description="手机无法自动列出本机，请用上方二维码扫码配对；如需开启请到「系统设置 → 网络与安全」（需再次输入本人口令）。" />
                )}

                <Card bordered={false} title="网络与防火墙">
                  <Paragraph type="secondary" style={{ marginBottom: 8 }}>
                    需要在 {data.ip} 这台机器上放行这些端口；只在首次设置或换网络时需要做一次。
                  </Paragraph>
                  <Descriptions column={1} size="small"
                                items={[
                                  { key: "ports", label: "放行端口", children: data.firewall_ports },
                                ]} />
                  <Space direction="vertical" size={4} style={{ width: "100%", marginTop: 8 }}>
                    {data.firewall_commands.map((command) => (
                      <Text key={command} code copyable={{ text: command }}
                            style={{ display: "block", whiteSpace: "normal" }}>
                        {command}
                      </Text>
                    ))}
                  </Space>
                </Card>

                <Card bordered={false} title="并发与性能提示">
                  <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                    {data.concurrency_hint}
                  </Paragraph>
                </Card>

                {data.candidates.length > 1 ? (
                  <Card bordered={false} title="本机其它可用地址">
                    <Text type="secondary">
                      这台机器有多个网卡（或装了虚拟网卡）。若手机连不上当前地址，
                      可尝试下面的地址，并在「系统设置」里把广播地址固定为它：
                    </Text>
                    <div style={{ marginTop: 8 }}>
                      <Space wrap>
                        {data.candidates.map((item) => (
                          <Text key={item.ip} code>{item.ip}</Text>
                        ))}
                      </Space>
                    </div>
                  </Card>
                ) : null}
              </Space>
            )}
          </AsyncBoundary>
        </Col>
      </Row>

      {state.loading ? <Spin style={{ display: "block", marginTop: 24 }} /> : null}
    </div>
  );
}
