import { Alert, Card, Col, Descriptions, Empty, Row, Space, Statistic, Table, Tag, Timeline,
  Typography } from "antd";
import type { TableColumnsType } from "antd";
import {
  AlertOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  ImportOutlined,
  ExportOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useAsync } from "../hooks/useAsync";
import { dashboard, TYPE_LABEL } from "../api/reports";
import type { DashboardData, DashboardLowStock } from "../api/reports";
import AsyncBoundary from "../components/AsyncBoundary";

const { Text } = Typography;

const LOW_STOCK_COLUMNS: TableColumnsType<DashboardLowStock> = [
  {
    title: "商品", dataIndex: "name", key: "name", ellipsis: true,
    render: (name: string, row) => (
      <Space direction="vertical" size={0}>
        <Text>{name}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>{row.barcode || "无条码"}</Text>
      </Space>
    ),
  },
  { title: "仓库", dataIndex: "warehouse_name", key: "warehouse_name", width: 120, ellipsis: true },
  {
    title: "现有 / 下限", key: "qty", width: 140, align: "right",
    render: (_v, row) => (
      <span className="sd-num">
        <Text type="danger" strong>{row.qty}</Text>
        <Text type="secondary"> / {row.min_stock}</Text>
      </span>
    ),
  },
];

export default function Dashboard() {
  const state = useAsync<DashboardData>(() => dashboard(), []);

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">仪表盘</h1>
        <Text type="secondary">
          {state.data ? `数据生成于 ${state.data.generated_at.replace("T", " ").slice(0, 19)}` : ""}
        </Text>
      </div>

      <AsyncBoundary state={state} emptyWhen={(d) => d === null}>
        {(data: DashboardData) => (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            {data.errors.length > 0 ? (
              <Alert type="warning" showIcon
                     message="部分统计项读取失败"
                     description={`页面其余部分正常。失败项：${data.errors.join("；")}`} />
            ) : null}

            {data.stock_anomaly_count ? (
              <Alert type="error" showIcon icon={<AlertOutlined />}
                     message={`发现 ${data.stock_anomaly_count} 条负库存异常`}
                     description="负库存意味着账实不符，请到「库存现状」筛出异常并用盘点调整修正。"
                     action={<Link to="/stock/current">去处理</Link>} />
            ) : null}

            <Row gutter={16}>
              <Col xs={24} sm={12} xl={6}>
                <Card bordered={false}>
                  <Statistic title="在用商品" value={data.product_count ?? 0} prefix={<DatabaseOutlined />}
                             loading={data.product_count === null} />
                </Card>
              </Col>
              <Col xs={24} sm={12} xl={6}>
                <Card bordered={false}>
                  <Statistic title="库存总量" value={data.total_qty ?? 0} suffix="件"
                             valueStyle={{ color: (data.total_qty ?? 0) < 0 ? "#ff4d4f" : undefined }}
                             loading={data.total_qty === null} />
                </Card>
              </Col>
              <Col xs={24} sm={12} xl={6}>
                <Card bordered={false}>
                  <Statistic title="今日入库" value={data.today?.in_qty ?? 0}
                             suffix={`件 / ${data.today?.in_count ?? 0} 单`}
                             prefix={<ImportOutlined />} valueStyle={{ color: "#52c41a" }} />
                </Card>
              </Col>
              <Col xs={24} sm={12} xl={6}>
                <Card bordered={false}>
                  <Statistic title="今日出库" value={data.today?.out_qty ?? 0}
                             suffix={`件 / ${data.today?.out_count ?? 0} 单`}
                             prefix={<ExportOutlined />} valueStyle={{ color: "#faad14" }} />
                </Card>
              </Col>
            </Row>

            <Row gutter={16}>
              <Col xs={24} xl={14}>
                <Card bordered={false} title={
                  <Space>
                    <WarningOutlined style={{ color: "#faad14" }} />
                    低库存预警
                    <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
                      共 {data.low_stock_total ?? 0} 项
                    </Text>
                  </Space>
                } extra={<Link to="/stock/current">查看全部</Link>}>
                  <Table<DashboardLowStock>
                    rowKey={(row) => `${row.id}-${row.warehouse_name}`}
                    size="small"
                    columns={LOW_STOCK_COLUMNS}
                    dataSource={data.low_stock_list ?? []}
                    pagination={false}
                    locale={{ emptyText: <Empty description="没有低于预警线的商品" /> }}
                  />
                </Card>
              </Col>

              <Col xs={24} xl={10}>
                <Card bordered={false} title={<Space><ClockCircleOutlined />最近流水</Space>}
                      extra={<Link to="/stock/logs">流水查询</Link>}>
                  {(data.recent_logs ?? []).length === 0 ? (
                    <Empty description="今天还没有出入库记录" />
                  ) : (
                    <Timeline
                      items={(data.recent_logs ?? []).map((log) => ({
                        key: log.id,
                        color: log.type === "IN" ? "green" : log.type === "OUT" ? "orange" : "blue",
                        children: (
                          <Space direction="vertical" size={0}>
                            <Space size={8}>
                              <Tag color={log.type === "IN" ? "green" : log.type === "OUT" ? "orange" : "blue"}>
                                {TYPE_LABEL[log.type]}
                              </Tag>
                              <Text strong>{log.product_name_snapshot}</Text>
                              <Text type="secondary">{log.quantity}</Text>
                            </Space>
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              {log.created_at.replace("T", " ").slice(0, 19)} · {log.username || "未知"} · {log.source}
                            </Text>
                          </Space>
                        ),
                      }))}
                    />
                  )}
                </Card>
              </Col>
            </Row>

            <Card bordered={false} title={<Space><CheckCircleOutlined />服务端信息</Space>}>
              <Descriptions column={{ xs: 1, md: 2, xl: 4 }} size="small"
                            items={[
                              { key: "name", label: "设备名称", children: data.server.name },
                              { key: "addr", label: "访问地址",
                                children: <Text code copyable={{ text: `http://${data.server.ip}:${data.server.port}/` }}>
                                  {data.server.ip}:{data.server.port}</Text> },
                              { key: "version", label: "程序版本", children: data.server.version },
                              { key: "broadcast", label: "手机自动发现",
                                children: data.server.broadcast_active
                                  ? <Tag color="green">已开启</Tag>
                                  : <Tag color="orange">已关闭（可用二维码配对）</Tag> },
                              { key: "backup", label: "上次自动备份",
                                children: data.last_backup_at
                                  ? data.last_backup_at.replace("T", " ").slice(0, 19)
                                  : "暂无" },
                              { key: "time", label: "服务器时间",
                                children: data.server.server_time.replace("T", " ").slice(0, 19) },
                            ]}
              />
            </Card>
          </Space>
        )}
      </AsyncBoundary>
    </div>
  );
}
