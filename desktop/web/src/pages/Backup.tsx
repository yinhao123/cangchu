import { useState } from "react";
import { Alert, App as AntdApp, Button, Card, Input, Modal, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { CloudDownloadOutlined, SafetyOutlined, WarningOutlined } from "@ant-design/icons";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import { createBackup, listBackups, recalcApply, recalcPreview, restoreBackup } from "../api/admin";
import type { BackupInfo } from "../api/admin";

const { Text } = Typography;

/** 备份产物体积很小（几十 KB~几 MB），用 KB/MB 两级就够，不需要通用字节格式化器 */
function humanSize(bytes: number): string {
  if (!bytes) {
    return "—";
  }
  return bytes > 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(2)} MB`
    : `${(bytes / 1024).toFixed(0)} KB`;
}

/**
 * 备份与恢复 —— PRD 5.8。
 *
 * ★恢复是**破坏性**操作：必须再输本人口令，且服务端会先自动保住现库（pre_restore）。
 *   界面要把这两点写清楚，否则用户会因为怕「恢复错了就完了」而不敢用。
 * ★备份一律走 SQLite 的 `backup()`（T-04），界面上**不提供**「上传覆盖 / 复制 db 文件」这类旁路。
 * ★自动备份连续失败 ≥3 次时红条告警（L2 降级），否则出了问题要等到需要恢复那天才发现。
 */
export default function Backup() {
  const [busy, setBusy] = useState("");
  const [restoring, setRestoring] = useState<BackupInfo | null>(null);
  const [password, setPassword] = useState("");
  const [recalc, setRecalc] = useState<{ count: number; list: Record<string, unknown>[] } | null>(null);
  const { message, modal } = AntdApp.useApp();

  const state = useAsync(() => listBackups(), []);

  async function doBackup() {
    setBusy("backup");
    try {
      const info = await createBackup();
      message.success(`已生成备份 ${info.filename}（${humanSize(info.size_bytes)}）`);
      state.reload();
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : "备份失败");
    } finally {
      setBusy("");
    }
  }

  async function doRestore() {
    if (!restoring) {
      return;
    }
    setBusy("restore");
    try {
      await restoreBackup(restoring.filename, password);
      message.success("已恢复。请刷新页面（服务端数据已回到该备份时点）");
      setRestoring(null);
      setPassword("");
      state.reload();
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : "恢复失败");
    } finally {
      setBusy("");
    }
  }

  async function doRecalcPreview() {
    setBusy("check");
    try {
      const result = await recalcPreview();
      setRecalc({ count: result.count, list: result.list });
      if (result.count === 0) {
        message.success("账面与流水一致，未发现差异");
      }
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : "自检失败");
    } finally {
      setBusy("");
    }
  }

  function confirmRecalc() {
    modal.confirm({
      title: "按流水重算库存？",
      content: "以流水为准覆盖 stock_current 的余额行，重算前会自动备份。"
        + "本操作只修正派生表，不修改任何流水。",
      okText: "确认重算",
      okButtonProps: { danger: true },
      onOk: async () => {
        const result = await recalcApply();
        message.success(`已重算，修正 ${result.changed} 条；重算前备份：${result.backup ?? "—"}`);
        setRecalc(null);
        state.reload();
      },
    });
  }

  const columns: TableColumnsType<BackupInfo> = [
    { title: "备份文件", dataIndex: "filename", key: "filename", ellipsis: true,
      render: (value: string) => <Text code>{value}</Text> },
    { title: "大小", dataIndex: "size_bytes", key: "size_bytes", width: 110, align: "right",
      render: (value: number) => humanSize(value) },
    { title: "生成时间", dataIndex: "created_at", key: "created_at", width: 180,
      render: (value: string) => value.replace("T", " ").slice(0, 19) },
    { title: "触发方式", dataIndex: "trigger", key: "trigger", width: 120,
      render: (value: string) => <Tag>{value}</Tag> },
    {
      title: "操作", key: "actions", width: 100, fixed: "right",
      render: (_v, row) => (
        <Button type="link" size="small" danger onClick={() => {
          setRestoring(row);
          setPassword("");
        }}>恢复</Button>
      ),
    },
  ];

  const status = state.data?.status;

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">备份与恢复</h1>
        <Space>
          <Button loading={busy === "check"} onClick={doRecalcPreview}>库存一致性自检</Button>
          <Button type="primary" icon={<CloudDownloadOutlined />} loading={busy === "backup"}
                  onClick={doBackup}>立即备份</Button>
        </Space>
      </div>

      {(status?.consecutive_failures ?? 0) >= 3 ? (
        <Alert style={{ marginBottom: 16 }} type="error" showIcon icon={<WarningOutlined />}
               message={`自动备份已连续失败 ${status?.consecutive_failures} 次`}
               description={String(status?.last_error ?? "请查看日志，确认数据目录可写、磁盘未满")} />
      ) : (
        <Alert style={{ marginBottom: 16 }} type="success" showIcon icon={<SafetyOutlined />}
               message={status?.last_ok_at
                 ? `上次自动备份：${String(status.last_ok_at).replace("T", " ").slice(0, 19)}`
                 : "尚未执行过自动备份（可在「系统设置 → 数据与备份」里开启）"}
               description="备份使用 SQLite 在线备份接口生成一致性快照；恢复前还会自动保住现库。" />
      )}

      {recalc && recalc.count > 0 ? (
        <Card bordered={false} style={{ marginBottom: 16 }}
              title={<Text type="danger">发现 {recalc.count} 条「账面 vs 流水」不一致</Text>}
              extra={<Button danger onClick={confirmRecalc}>按流水重算</Button>}>
          <Text type="secondary">
            重算以流水为准（流水不可篡改），只覆盖派生表 stock_current，并在重算前自动备份。
          </Text>
        </Card>
      ) : null}

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText="还没有备份文件">
        {(data) => (
          <Table<BackupInfo>
            rowKey="filename"
            columns={columns}
            dataSource={data.list}
            pagination={false}
            scroll={{ x: 860 }}
          />
        )}
      </AsyncBoundary>

      <Modal open={restoring !== null} title="恢复数据库" okText="确认恢复并覆盖现库"
             cancelText="取消" destroyOnClose
             okButtonProps={{ danger: true, loading: busy === "restore",
               disabled: password.length === 0 }}
             onOk={doRestore} onCancel={() => setRestoring(null)}>
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert type="warning" showIcon message="恢复会覆盖当前全部数据"
                 description={`将用「${restoring?.filename ?? ""}」替换现库。
                   恢复前服务端会自动先备份现库（pre_restore），但之后录入的数据不会自动追回。`} />
          <div>
            <Text strong>你自己的密码</Text>
            <Input.Password style={{ marginTop: 6 }} value={password}
                            onChange={(event) => setPassword(event.target.value)} />
          </div>
        </Space>
      </Modal>
    </div>
  );
}
