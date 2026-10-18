/**
 * 批量导入 / 导出（P0-1）—— docs/14 第 3 章
 *
 * 交互刻意做成「先预检、再提交」两步：
 * 客户第一次搬档案时一定有脏行，直接提交等于让他们在几百行里找错。
 * 预检结果里行号用的是 CSV 物理行号（和 Excel 里看到的一致），照着改就行。
 */
import { useState } from "react";
import {
  Alert, App, Button, Divider, Space, Switch, Table, Tabs, Typography, Upload,
} from "antd";
import type { UploadFile } from "antd";
import {
  DownloadOutlined, InboxOutlined, UploadOutlined,
} from "@ant-design/icons";
import {
  commitImport, downloadTemplate, exportData, previewImport,
  type CommitResult, type ExportKind, type ImportKind, type ImportRowError,
  type PreviewResult,
} from "../api/imports";
import { ApiError } from "../api/client";
import { isAdmin } from "../store/session";

const { Text, Paragraph } = Typography;

const FIELD_LABELS: Record<string, string> = {
  name: "名称", barcode: "条码", category: "分类", unit: "单位", cost_price: "成本价",
  sale_price: "售价", min_stock: "预警库存", remark: "备注", type: "类型", contact: "联系人",
  phone: "电话", address: "地址", warehouse_code: "仓库", qty: "数量", file: "文件",
};

interface KindMeta {
  key: ImportKind;
  label: string;
  hint: string;
  columns: string;
}

const KINDS: KindMeta[] = [
  {
    key: "products",
    label: "商品档案",
    hint: "名称必填；条码可空，填了就要唯一（含已停用商品）。成本价/售价留空按 0。",
    columns: "名称, 条码, 分类, 单位, 成本价, 售价, 预警库存, 备注",
  },
  {
    key: "partners",
    label: "客户 / 供应商",
    hint: "类型只能是 CUSTOMER（客户）或 SUPPLIER（供应商）；同名同类型不能重复登记。",
    columns: "名称, 类型, 联系人, 电话, 地址, 备注",
  },
  {
    key: "opening-stock",
    label: "期初库存（仅管理员）",
    hint: "只能在还没有任何流水的账本上执行。已开过单的库请改用盘点单；重复导入不会重复加库存。",
    columns: "条码, 仓库（编码或名称）, 数量, 备注",
  },
];

const EXPORTS: { key: ExportKind; label: string }[] = [
  { key: "products", label: "导出商品档案" },
  { key: "partners", label: "导出客户 / 供应商" },
  { key: "stock-current", label: "导出当前库存" },
];

export default function ImportExport() {
  const { message } = App.useApp();
  const [kind, setKind] = useState<ImportKind>("products");
  const [file, setFile] = useState<File | null>(null);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [result, setResult] = useState<CommitResult | null>(null);
  const [serverErrors, setServerErrors] = useState<ImportRowError[]>([]);
  const [skipInvalid, setSkipInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const meta = KINDS.find((item) => item.key === kind) as KindMeta;
  const admin = isAdmin();

  function reset(next: ImportKind = kind) {
    setKind(next);
    setFile(null);
    setFileList([]);
    setPreview(null);
    setResult(null);
    setServerErrors([]);
  }

  async function handlePreview() {
    if (!file) return;
    setBusy(true);
    setResult(null);
    setServerErrors([]);
    try {
      const data = await previewImport(kind, file);
      setPreview(data);
      if (data.invalid > 0) {
        message.warning(`预检完成：${data.valid} 行可导入，${data.invalid} 行有问题`);
      } else {
        message.success(`预检通过：${data.valid} 行全部可导入`);
      }
    } catch (err) {
      reportError(err);
    } finally {
      setBusy(false);
    }
  }

  async function handleCommit() {
    if (!file) return;
    setBusy(true);
    try {
      const data = await commitImport(kind, file, skipInvalid);
      setResult(data);
      setPreview(null);
      message.success(
        data.duplicated
          ? "这份文件之前已经导入过，未重复加库存"
          : `导入完成：成功 ${data.ok_count} 行${data.duplicated_count ? `，已存在跳过 ${data.duplicated_count} 行` : ""}`,
      );
    } catch (err) {
      reportError(err);
    } finally {
      setBusy(false);
    }
  }

  function reportError(err: unknown) {
    if (err instanceof ApiError) {
      const rows = (err.payload.rows as ImportRowError[] | undefined) ?? [];
      setServerErrors(rows);
      message.error(err.message);
      return;
    }
    message.error("操作失败，请稍后重试");
  }

  function guard(task: Promise<void>) {
    task.catch((err) => {
      if (err instanceof ApiError) {
        message.error(err.message);
      } else {
        message.error("下载失败，请检查网络或权限");
      }
    });
  }

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">批量导入 / 导出</h1>
      </div>

      <Tabs
        activeKey={kind}
        onChange={(key) => reset(key as ImportKind)}
        items={KINDS.map((item) => ({ key: item.key, label: item.label }))}
      />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={`${meta.label}：CSV 表头`}
        description={
          <>
            <Paragraph style={{ marginBottom: 4 }}><Text code>{meta.columns}</Text></Paragraph>
            <Text type="secondary">{meta.hint} 用 Excel 编辑后「另存为 CSV」即可（UTF-8 或 GBK 都能识别）。</Text>
          </>
        }
      />

      <Space wrap style={{ marginBottom: 16 }}>
        <Button icon={<DownloadOutlined />} onClick={() => guard(downloadTemplate(kind))}>
          下载模板
        </Button>
        <Upload
          accept=".csv"
          maxCount={1}
          fileList={fileList}
          beforeUpload={(picked) => {
            setFile(picked);
            setFileList([{ uid: picked.name, name: picked.name, status: "done" }]);
            setPreview(null);
            setResult(null);
            setServerErrors([]);
            return false;  // 不自动上传：先预检，用户确认后才提交
          }}
          onRemove={() => {
            setFile(null);
            setFileList([]);
            setPreview(null);
          }}
        >
          <Button icon={<UploadOutlined />}>选择 CSV 文件</Button>
        </Upload>
        <Button type="primary" disabled={!file} loading={busy} onClick={handlePreview}>
          预检
        </Button>
        <Button
          type="primary"
          danger
          disabled={!file}
          loading={busy}
          onClick={handleCommit}
          title={kind === "opening-stock" && !admin ? "期初建账仅管理员可执行" : undefined}
        >
          确认导入
        </Button>
        <Space size={4}>
          <Switch size="small" checked={skipInvalid} onChange={setSkipInvalid} />
          <Text type="secondary">跳过不合法行（关闭时整批回滚）</Text>
        </Space>
      </Space>

      {preview && (
        <>
          <Space size="large" style={{ marginBottom: 12 }}>
            <Text>共 <Text strong>{preview.total}</Text> 行</Text>
            <Text type="success">可导入 {preview.valid}</Text>
            <Text type="danger">有问题 {preview.invalid}</Text>
            {preview.unknown_columns.length > 0 && (
              <Text type="warning">已忽略未知列：{preview.unknown_columns.join("、")}</Text>
            )}
          </Space>
          <Table<ImportRowError>
            size="small"
            rowKey={(row) => `${row.row}-${row.field}-${row.msg}`}
            dataSource={preview.errors}
            pagination={false}
            locale={{ emptyText: "没有发现错误行" }}
            columns={[
              { title: "行号", dataIndex: "row", width: 80 },
              { title: "字段", dataIndex: "field", width: 120,
                render: (value: string) => FIELD_LABELS[value] ?? value },
              { title: "内容", dataIndex: "value", width: 200, ellipsis: true },
              { title: "原因", dataIndex: "msg" },
            ]}
          />
        </>
      )}

      {serverErrors.length > 0 && !preview && (
        <Table<ImportRowError>
          size="small"
          rowKey={(row) => `${row.row}-${row.field}-${row.msg}`}
          dataSource={serverErrors}
          pagination={false}
          style={{ marginTop: 12 }}
          columns={[
            { title: "行号", dataIndex: "row", width: 80 },
            { title: "字段", dataIndex: "field", width: 120,
              render: (value: string) => FIELD_LABELS[value] ?? value },
            { title: "原因", dataIndex: "msg" },
          ]}
        />
      )}

      {result && (
        <Alert
          style={{ marginTop: 16 }}
          type={result.failed_count > 0 ? "warning" : "success"}
          showIcon
          message={`导入结果：成功 ${result.ok_count} 行，失败 ${result.failed_count} 行${result.duplicated_count ? `，已存在跳过 ${result.duplicated_count} 行` : ""}`}
          description={
            result.failed_rows.length > 0 && (
              <div>
                {result.failed_rows.map((row) => (
                  <div key={row.row}>第 {row.row} 行：{row.msg}</div>
                ))}
              </div>
            )
          }
        />
      )}

      <Divider style={{ marginTop: 32 }} />

      <Space wrap>
        <Text strong>导出（Excel 打开中文不乱码）：</Text>
        {EXPORTS.map((item) => (
          <Button key={item.key} icon={<InboxOutlined />}
                  onClick={() => guard(exportData(item.key))}>
            {item.label}
          </Button>
        ))}
      </Space>

      <Paragraph type="secondary" style={{ marginTop: 24 }}>
        期初库存导出的是「当前库存快照」，不能直接当模板导回：期初模板要的是
        <Text code>条码, 仓库, 数量</Text>，导出里带的是商品名与仓库名，请按模板整理后再导入。
      </Paragraph>
    </div>
  );
}
