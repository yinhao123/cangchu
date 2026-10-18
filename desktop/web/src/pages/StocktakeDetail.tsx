import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  App as AntdApp, Alert, Button, Descriptions, Input, InputNumber, Popconfirm, Select, Space,
  Table, Tag, Typography,
} from "antd";
import type { InputRef, TableColumnsType } from "antd";
import { ArrowLeftOutlined, PrinterOutlined, SaveOutlined } from "@ant-design/icons";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { usePrint } from "../hooks/usePrint";
import type { PaperKey } from "../api/print";
import { ApiError } from "../api/client";
import {
  STOCKTAKE_STATUS_META, cancelStocktake, getStocktake, postStocktake,
  rejectStocktake, submitStocktake, updateStocktake,
  type StocktakeLine,
} from "../api/stocktake";
import { isAdmin } from "../store/session";

const { Text } = Typography;

/**
 * 盘点单详情（录入 / 提交 / 过账 / 打印）—— docs/14 第 4 章。
 *
 * ★录入用「整表本地编辑 + 一次保存」：一百行盘一次只发一个 PUT，不是一百次 HTTP。
 * ★回车跳下一行：仓管在纸质表上填完一行就往下走，键盘动线必须一样。
 * ★差异列显示的是**快照口径**（实盘 − 账面），过账按「实盘 − 实时当前」算，两者都展示、都说明。
 * ★打印走 `.sd-print-only` + `@media print`，屏幕上不显示，打印只出表。
 */
export default function StocktakeDetail() {
  const { id } = useParams();
  const stocktakeId = Number(id);
  const navigate = useNavigate();
  const admin = isAdmin();
  const { message } = AntdApp.useApp();

  const state = useAsync(() => getStocktake(stocktakeId), [stocktakeId]);
  const doc = state.data;
  const editable = doc ? ["DRAFT", "REJECTED"].includes(doc.status) : false;

  const [edits, setEdits] = useState<Record<number, number | null>>({});
  const [saving, setSaving] = useState(false);
  const [barcode, setBarcode] = useState("");
  const [appendQty, setAppendQty] = useState<number | null>(null);
  const [appending, setAppending] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  // 打印（P0-3）：空白表给现场填数，差异表给复核签字；纸张可临时切成针式
  const printer = usePrint();
  const [paper, setPaper] = useState<PaperKey | null>(null);
  const paperKey = paper ?? printer.defaultPaper;

  // 实盘数输入框：只用来「回车跳下一行」，按原生元素存即可（InputNumber 的 ref 就是 input）
  const inputs = useRef<Record<number, HTMLElement | null>>({});
  const barcodeRef = useRef<InputRef | null>(null);

  // 换了单据或重新加载后，本地未保存的编辑必须清空（否则会把 A 单的数写到 B 单）
  useEffect(() => {
    setEdits({});
  }, [doc?.id, doc?.updated_at]);

  const changed = useMemo(() => Object.keys(edits).length, [edits]);

  async function doSave() {
    if (!doc || changed === 0) {
      return;
    }
    setSaving(true);
    try {
      await updateStocktake(doc.id, {
        lines: Object.entries(edits).map(([detailId, realQty]) => ({
          detail_id: Number(detailId), real_qty: realQty,
        })),
      });
      message.success(`已保存 ${changed} 行实盘数`);
      setEdits({});
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    } finally {
      setSaving(false);
    }
  }

  async function doAppend() {
    const code = barcode.trim();
    if (!doc || !code) {
      return;
    }
    if (appendQty === null) {
      message.warning("实盘数不能为空（盘盈请填实际数量）");
      return;
    }
    setAppending(true);
    try {
      await updateStocktake(doc.id, {
        append: [{ barcode: code, real_qty: appendQty, remark: "盘点追加行" }],
      });
      message.success("已追加一行（账面数按 0 记，属盘盈）");
      setBarcode("");
      setAppendQty(null);
      state.reload();
      barcodeRef.current?.focus();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    } finally {
      setAppending(false);
    }
  }

  async function doSubmit() {
    if (!doc) {
      return;
    }
    try {
      await submitStocktake(doc.id);
      message.success("已提交，等待审批");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doPost() {
    if (!doc) {
      return;
    }
    try {
      const result = await postStocktake(doc.id);
      message.success(`已过账：${result.diff_count} 行差异写入流水，库存已对齐实盘数`);
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doReject() {
    if (!doc) {
      return;
    }
    if (!rejectReason.trim()) {
      message.warning("驳回必须填写原因");
      return;
    }
    try {
      await rejectStocktake(doc.id, rejectReason.trim());
      message.success("已驳回");
      setRejectReason("");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doCancel() {
    if (!doc) {
      return;
    }
    try {
      await cancelStocktake(doc.id);
      message.success("已作废");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  function focusNext(currentDetailId: number, lines: StocktakeLine[]) {
    const index = lines.findIndex((line) => line.id === currentDetailId);
    const next = lines[index + 1];
    if (next) {
      inputs.current[next.id]?.focus();
    }
  }

  const columns: TableColumnsType<StocktakeLine> = [
    { title: "条码", dataIndex: "barcode", key: "barcode", width: 160,
      render: (value: string | null) => value || <Text type="secondary">无条码</Text> },
    { title: "商品", dataIndex: "product_name_snapshot", key: "name", ellipsis: true },
    { title: "单位", dataIndex: "unit", key: "unit", width: 70 },
    { title: "账面数", dataIndex: "book_qty", key: "book", width: 90, align: "right" },
    { title: "当前库存", dataIndex: "current_qty", key: "current", width: 100, align: "right",
      render: (value: number, row) => (
        <Text type={value !== row.book_qty ? "warning" : undefined}>{value}</Text>
      ) },
    {
      title: "实盘数", key: "real", width: 140,
      render: (_v, row) => {
        const value = row.id in edits ? edits[row.id] : row.real_qty;
        if (!editable) {
          return value === null ? <Text type="secondary">未盘</Text> : <Text strong>{value}</Text>;
        }
        return (
          <InputNumber
            min={0}
            max={1000000}
            style={{ width: "100%" }}
            placeholder="未盘"
            value={value ?? null}
            ref={(node) => { inputs.current[row.id] = node; }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                focusNext(row.id, doc?.details ?? []);
              }
            }}
            onChange={(next) => setEdits((prev) => ({ ...prev, [row.id]: next ?? null }))}
          />
        );
      },
    },
    {
      title: "差异", key: "diff", width: 90, align: "right",
      render: (_v, row) => {
        const real = row.id in edits ? edits[row.id] : row.real_qty;
        if (real === null) {
          return <Text type="secondary">—</Text>;
        }
        const diff = Number(real) - row.book_qty;
        return diff === 0
          ? <Text type="secondary">0</Text>
          : <Text type={diff > 0 ? "success" : "danger"}>{diff > 0 ? `+${diff}` : diff}</Text>;
      },
    },
    { title: "备注", dataIndex: "remark", key: "remark", ellipsis: true },
  ];

  return (
    <div className="sd-page">
      <div className="sd-screen-only">
        <div className="sd-page-head">
          <h1 className="sd-page-title">
            <Button type="link" style={{ paddingLeft: 0 }} icon={<ArrowLeftOutlined />}
                    onClick={() => navigate("/wms/stocktakes")}>盘点单</Button>
            {doc ? doc.code : ""}
          </h1>
          <Space wrap>
            <Select style={{ width: 190 }} value={paperKey} onChange={(value) => setPaper(value)}
                    options={printer.papers.map((p) => ({ label: p.label, value: p.key }))} />
            <Button icon={<PrinterOutlined />} loading={printer.busy}
                    onClick={() => void printer.print("stocktakes", stocktakeId,
                      { mode: "blank", paper: paperKey })}>
              打印空白表
            </Button>
            <Button icon={<PrinterOutlined />} loading={printer.busy}
                    onClick={() => void printer.print("stocktakes", stocktakeId,
                      { mode: "diff", paper: paperKey })}>
              打印差异表
            </Button>
            {editable ? (
              <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={changed === 0}
                      onClick={() => void doSave()}>
                保存录入{changed > 0 ? `（${changed} 行）` : ""}
              </Button>
            ) : null}
            {editable ? (
              <Button onClick={() => void doSubmit()}>提交审批</Button>
            ) : null}
            {admin && doc?.status === "SUBMITTED" ? (
              <Popconfirm title="确认过账？库存将按实盘数调整，之后不可修改。" onConfirm={() => void doPost()}>
                <Button type="primary">过账</Button>
              </Popconfirm>
            ) : null}
            {editable ? (
              <Popconfirm title="作废后不能再提交，确定？" onConfirm={() => void doCancel()}>
                <Button danger>作废</Button>
              </Popconfirm>
            ) : null}
          </Space>
        </div>

        <AsyncBoundary state={state} emptyText="盘点单不存在">
          {(data) => (
            <>
              {data.period_changes && data.period_changes > 0 ? (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message={`建单后该仓库又发生了 ${data.period_changes} 笔出入库`}
                  description="过账按「实盘数 − 实时当前库存」计算，最终库存一定等于实盘数；账面数（快照）只用于差异统计。"
                />
              ) : null}

              <Descriptions size="small" column={4} style={{ marginBottom: 12 }}>
                <Descriptions.Item label="状态">
                  <Tag color={STOCKTAKE_STATUS_META[data.status].color}>
                    {STOCKTAKE_STATUS_META[data.status].label}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="仓库">{data.warehouse_name}</Descriptions.Item>
                <Descriptions.Item label="快照时刻">
                  {data.book_locked_at.slice(0, 19).replace("T", " ")}
                </Descriptions.Item>
                <Descriptions.Item label="已盘 / 明细">
                  {data.counted_count} / {data.line_count}
                </Descriptions.Item>
                <Descriptions.Item label="差异行数">{data.diff_count}</Descriptions.Item>
                <Descriptions.Item label="差异绝对值">{data.diff_abs_qty}</Descriptions.Item>
                <Descriptions.Item label="创建人">{data.operator_name}</Descriptions.Item>
                <Descriptions.Item label="过账时间">
                  {data.posted_at ? data.posted_at.slice(0, 19).replace("T", " ") : "—"}
                </Descriptions.Item>
                {data.remark ? (
                  <Descriptions.Item label="备注" span={4}>{data.remark}</Descriptions.Item>
                ) : null}
              </Descriptions>

              {editable ? (
                <Space wrap style={{ marginBottom: 8 }}>
                  <Text type="secondary">追加行（不在快照里的商品，按盘盈计账面 0）：</Text>
                  <Input
                    ref={barcodeRef}
                    style={{ width: 220 }}
                    placeholder="扫码或输入条码"
                    value={barcode}
                    onChange={(e) => setBarcode(e.target.value)}
                    onPressEnter={() => void doAppend()}
                  />
                  <InputNumber
                    min={0}
                    max={1000000}
                    placeholder="实盘数"
                    value={appendQty}
                    onChange={(value) => setAppendQty(value)}
                    onPressEnter={() => void doAppend()}
                  />
                  <Button loading={appending} onClick={() => void doAppend()}>追加</Button>
                </Space>
              ) : null}

              {admin && data.status === "SUBMITTED" ? (
                <Space wrap style={{ marginBottom: 8 }}>
                  <Input style={{ width: 260 }} placeholder="驳回原因（驳回时必填）"
                         value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} />
                  <Button onClick={() => void doReject()}>驳回</Button>
                </Space>
              ) : null}

              <Table<StocktakeLine>
                rowKey="id"
                size="small"
                columns={columns}
                dataSource={data.details ?? []}
                scroll={{ x: 1100, y: 520 }}
                pagination={false}
              />
            </>
          )}
        </AsyncBoundary>
      </div>

      {printer.element}
    </div>
  );
}
