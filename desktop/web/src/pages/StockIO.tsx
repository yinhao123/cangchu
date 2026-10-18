import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, App as AntdApp, Button, Card, Col, Divider, Empty, Input, InputNumber, Row, Segmented,
  Select, Space, Table, Tag, Typography,
} from "antd";
import type { InputRef, TableColumnsType } from "antd";
import { CheckCircleFilled, CloseCircleFilled, ThunderboltOutlined } from "@ant-design/icons";
import { useAsync } from "../hooks/useAsync";
import { ApiError, newClientMsgId } from "../api/client";
import { listPartners, listWarehouses } from "../api/master";
import { stockAdjust, stockIn, stockOut } from "../api/stock";
import type { Partner, StockWriteResult, Warehouse } from "../api/types";
import { TYPE_LABEL } from "../api/reports";
import { isAdmin } from "../store/session";
import { Num } from "../components/Values";

const { Text } = Typography;

type Mode = "IN" | "OUT" | "ADJUST";
const DRAFT_KEY = "scanable.stockio.draft";

interface SessionRow extends StockWriteResult {
  key: string;
}

interface Draft {
  mode: Mode;
  warehouseId?: number;
}

/**
 * 出入库扫码台 —— PRD 5.4 / 5.5。
 *
 * 现场约束（决定了这一页的每个细节）：
 * ★扫码枪就是键盘：条码框必须自动聚焦，回车即提交，成功后再自动聚焦回来 —— 连续扫描不打断。
 * ★仓库选择要记住（localStorage）：一个班次基本只在一个仓库，每次重选是纯浪费。
 * ★`client_msg_id` 一次提交只生成一次、成功才清空：网络抖动重试时不会变成两笔真实流水（T-09）。
 * ★数量方向由接口表达（IN/OUT），界面上**没有**正负号输入；盘点填的是目标值而不是差值。
 */
export default function StockIO() {
  const [mode, setMode] = useState<Mode>("IN");
  const [warehouseId, setWarehouseId] = useState<number | undefined>();
  const [partnerId, setPartnerId] = useState<number | undefined>();
  const [barcode, setBarcode] = useState("");
  const [quantity, setQuantity] = useState<number | null>(1);
  const [targetQty, setTargetQty] = useState<number | null>(null);
  const [remark, setRemark] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<StockWriteResult | null>(null);
  const [failure, setFailure] = useState<string>("");
  const [sessionRows, setSessionRows] = useState<SessionRow[]>([]);

  const barcodeRef = useRef<InputRef>(null);
  const pendingIdRef = useRef<string | null>(null);
  const { message } = AntdApp.useApp();
  const admin = isAdmin();

  const warehouses = useAsync(() => listWarehouses(), []);
  const partners = useAsync(
    () => listPartners(mode === "IN" ? "SUPPLIER" : mode === "OUT" ? "CUSTOMER" : undefined),
    [mode],
  );

  // 草稿暂存：仓库与模式是「一个班次内不变」的选择，刷新页面不该丢
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(DRAFT_KEY);
      if (raw) {
        const draft = JSON.parse(raw) as Draft;
        if (draft.warehouseId) {
          setWarehouseId(draft.warehouseId);
        }
        if (draft.mode && (draft.mode !== "ADJUST" || admin)) {
          setMode(draft.mode);
        }
      }
    } catch {
      /* 草稿坏了就当没有，不能因此拦着用户干活 */
    }
    // 只在挂载时恢复一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ mode, warehouseId } satisfies Draft));
    } catch {
      /* 忽略 */
    }
  }, [mode, warehouseId]);

  useEffect(() => {
    // 切模式时清掉上一次的结果，避免「出库卡」显示着入库的结存
    setResult(null);
    setFailure("");
    setPartnerId(undefined);
    barcodeRef.current?.focus();
  }, [mode]);

  const focusScanner = useCallback(() => {
    window.setTimeout(() => barcodeRef.current?.focus(), 0);
  }, []);

  const warehouseOptions = useMemo(
    () => (warehouses.data?.list ?? []).filter((w: Warehouse) => w.is_active === 1)
      .map((w: Warehouse) => ({ value: w.id, label: w.name })),
    [warehouses.data],
  );

  const partnerOptions = useMemo(() => {
    const items: Partner[] = partners.data?.list ?? [];
    return items.filter((p) => p.is_active === 1).map((p) => ({ value: p.id, label: p.name }));
  }, [partners.data]);

  async function submit() {
    if (!warehouseId) {
      message.warning("请先选择仓库");
      return;
    }
    if (mode === "ADJUST") {
      if (!barcode.trim()) {
        message.warning("请先扫描或输入商品条码");
        barcodeRef.current?.focus();
        return;
      }
      if (targetQty === null) {
        message.warning("请填写盘点后的实际数量");
        return;
      }
      if (!remark.trim()) {
        message.warning("盘点必须说明差异原因");
        return;
      }
    } else if (!barcode.trim()) {
      message.warning("请先扫描或输入商品条码");
      barcodeRef.current?.focus();
      return;
    }

    // 同一次「按下提交」只用一个 id：网络抖动重试时服务端才会认出是同一笔（T-09）
    pendingIdRef.current = pendingIdRef.current ?? newClientMsgId();
    const clientMsgId = pendingIdRef.current;
    setSubmitting(true);
    setFailure("");
    try {
      const common = { client_msg_id: clientMsgId, warehouse_id: warehouseId, barcode: barcode.trim() };
      const data = mode === "IN"
        ? await stockIn({ ...common, quantity: quantity ?? 1, partner_id: partnerId ?? null,
          remark: remark.trim() || undefined })
        : mode === "OUT"
          ? await stockOut({ ...common, quantity: quantity ?? 1, partner_id: partnerId ?? null,
            remark: remark.trim() || undefined })
          : await stockAdjust({ ...common, target_qty: targetQty ?? 0, remark: remark.trim() });

      pendingIdRef.current = null;
      setResult(data);
      setSessionRows((rows) => [{ ...data, key: `${data.log_id}-${Date.now()}` }, ...rows].slice(0, 20));
      // 连续扫描：只清条码与数量，仓库/往来单位/模式全部保留
      setBarcode("");
      setQuantity(1);
      setTargetQty(null);
      setRemark("");
      if (data.duplicated) {
        message.info("这笔已经提交过了，显示的是首次结果（没有重复扣减）");
      } else {
        message.success(`${TYPE_LABEL[data.type]}成功，当前库存 ${data.after_qty}`);
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setFailure(err.message);
        if (err.code === 1009) {
          const { current, requested, lack } = err.payload as
            { current?: number; requested?: number; lack?: number };
          setFailure(`库存不足：现有 ${current ?? "?"}，本次要出 ${requested ?? "?"}，缺 ${lack ?? "?"}`);
        } else if (err.code === 1007) {
          setFailure(`条码未登记：${barcode.trim()}。请先到「商品与条码」新增该商品`);
        }
      } else {
        setFailure("提交失败，请重试");
      }
      // 保留条码与数量：现场要能直接改一个数字再交一次
    } finally {
      setSubmitting(false);
      focusScanner();
    }
  }

  const sessionColumns: TableColumnsType<SessionRow> = [
    { title: "时间", dataIndex: "created_at", key: "created_at", width: 92,
      render: (value: string) => value.slice(11, 19) },
    { title: "类型", dataIndex: "type", key: "type", width: 84,
      render: (value: StockWriteResult["type"]) => (
        <Tag color={value === "IN" ? "green" : value === "OUT" ? "orange" : "blue"}>
          {TYPE_LABEL[value]}
        </Tag>
      ) },
    { title: "商品", dataIndex: "product_name", key: "product_name", ellipsis: true },
    { title: "数量", dataIndex: "quantity", key: "quantity", width: 72, align: "right",
      render: (value: number) => <Num value={value} /> },
    { title: "结存", dataIndex: "after_qty", key: "after_qty", width: 72, align: "right",
      render: (value: number) => <Num value={value} /> },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">出入库</h1>
        <Segmented
          value={mode}
          onChange={(value) => setMode(value as Mode)}
          options={[
            { value: "IN", label: "入库" },
            { value: "OUT", label: "出库" },
            ...(admin ? [{ value: "ADJUST", label: "盘点调整" }] : []),
          ]}
        />
      </div>

      <Row gutter={16}>
        <Col xs={24} xl={13}>
          <Card bordered={false} title="扫码录入">
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              <Space size={12} wrap>
                <span>
                  <Text type="secondary">仓库</Text>
                  <Select
                    style={{ width: 200, marginLeft: 8 }}
                    placeholder="选择仓库"
                    value={warehouseId}
                    options={warehouseOptions}
                    onChange={setWarehouseId}
                    status={warehouseId ? undefined : "warning"}
                  />
                </span>
                {mode !== "ADJUST" ? (
                  <span>
                    <Text type="secondary">{mode === "IN" ? "供应商" : "客户"}</Text>
                    <Select
                      style={{ width: 200, marginLeft: 8 }}
                      placeholder="可不选"
                      allowClear
                      value={partnerId}
                      options={partnerOptions}
                      onChange={setPartnerId}
                    />
                  </span>
                ) : null}
              </Space>

              <div className="sd-scan-box">
                <Text type="secondary">条码（扫码枪扫一下，或手工输入后回车）</Text>
                <Input
                  ref={barcodeRef}
                  size="large"
                  autoFocus
                  allowClear
                  value={barcode}
                  placeholder="扫码 / 输入条码，回车提交"
                  onChange={(event) => setBarcode(event.target.value)}
                  onPressEnter={submit}
                  suffix={<ThunderboltOutlined />}
                />
              </div>

              {mode === "ADJUST" ? (
                <div>
                  <Text type="secondary">盘点后的实际数量（服务端自己算差异）</Text>
                  <InputNumber
                    size="large"
                    style={{ width: "100%" }}
                    min={0}
                    max={1000000}
                    precision={0}
                    value={targetQty}
                    onChange={setTargetQty}
                    placeholder="清点后的实际库存"
                    onPressEnter={submit}
                  />
                </div>
              ) : (
                <div>
                  <Text type="secondary">数量</Text>
                  <InputNumber
                    size="large"
                    style={{ width: "100%" }}
                    min={1}
                    max={100000}
                    precision={0}
                    value={quantity}
                    onChange={setQuantity}
                    onPressEnter={submit}
                  />
                </div>
              )}

              <div>
                <Text type="secondary">
                  备注{mode === "ADJUST" ? "（盘点必填：差异原因）" : "（选填）"}
                </Text>
                <Input.TextArea
                  rows={2}
                  maxLength={200}
                  showCount
                  value={remark}
                  onChange={(event) => setRemark(event.target.value)}
                  placeholder={mode === "ADJUST" ? "例如：盘库发现破损 3 件" : "例如：到货入库"}
                />
              </div>

              <Button type="primary" size="large" block loading={submitting} onClick={submit}>
                提交{TYPE_LABEL[mode]}
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} xl={11}>
          <Card bordered={false} title="最近一笔结果">
            {failure ? (
              <Alert type="error" showIcon icon={<CloseCircleFilled />} message="本次提交未成功"
                     description={failure} />
            ) : result ? (
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <Space size={8}>
                  <CheckCircleFilled style={{ color: "#52c41a", fontSize: 20 }} />
                  <Text strong>{result.product_name}</Text>
                  <Tag color={result.type === "IN" ? "green" : result.type === "OUT" ? "orange" : "blue"}>
                    {TYPE_LABEL[result.type]}
                  </Tag>
                  {result.duplicated ? <Tag color="warning">重复提交（未重复扣减）</Tag> : null}
                </Space>
                <div className="sd-result-qty">{result.after_qty}</div>
                <Text type="secondary">
                  本次{result.quantity > 0 ? "增加" : "减少"} {Math.abs(result.quantity)}，
                  该仓库当前结存 {result.after_qty}
                </Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {result.created_at.replace("T", " ").slice(0, 19)} · 单号 #{result.log_id}
                </Text>
              </Space>
            ) : (
              <Empty description="扫描条码后，这里会显示最新结存" />
            )}
          </Card>

          <Card bordered={false} title="本次会话流水" style={{ marginTop: 16 }}
                extra={<Text type="secondary" style={{ fontSize: 12 }}>最多保留 20 条</Text>}>
            <Table<SessionRow>
              rowKey="key"
              size="small"
              columns={sessionColumns}
              dataSource={sessionRows}
              pagination={false}
              locale={{ emptyText: <Empty description="本次会话还没有记录" /> }}
              scroll={{ y: 260 }}
            />
            <Divider style={{ margin: "12px 0" }} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              本表只是本次页面的临时回显；正式流水请到「流水查询」查看（那里才有筛选与导出）。
            </Text>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
