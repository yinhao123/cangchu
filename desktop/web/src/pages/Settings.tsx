import { useEffect, useMemo, useState } from "react";
import { App as AntdApp, Button, Card, Divider, Input, InputNumber, Select, Space, Switch,
  Typography } from "antd";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import { getSettings, saveNetworkSettings, saveSettings } from "../api/admin";
import type { SettingsItem, SettingsView } from "../api/admin";

const { Text } = Typography;

type Value = string | number | boolean;

/**
 * 系统设置 —— PRD 5.9 / 20 章。
 *
 * ★分组、中文标签、提示文案全部来自服务端（`settings_service.SETTING_META`）：
 *   前端另写一套标签，改一处漏一处就会出现「界面写着 A、实际改的是 B」。
 * ★`needs_restart` 必须在保存后**明确告知**，否则用户遇到「改了没反应」只能猜。
 * ★网络组（端口 / 自动发现）走单独通道并再输一次本人口令（PRD 3.5）：
 *   改错端口就是全屋手机静默失联，要重启服务才发现。
 */
export default function Settings() {
  const state = useAsync<SettingsView>(() => getSettings(), []);
  const [values, setValues] = useState<Record<string, Value>>({});
  const [dirty, setDirty] = useState<string[]>([]);
  const [password, setPassword] = useState("");
  const [savingGroup, setSavingGroup] = useState("");
  const { message } = AntdApp.useApp();

  useEffect(() => {
    if (!state.data) {
      return;
    }
    const next: Record<string, Value> = {};
    state.data.items.forEach((item) => {
      next[item.key] = item.value;
    });
    setValues(next);
    setDirty([]);
  }, [state.data]);

  const grouped = useMemo(() => {
    const items = state.data?.items ?? [];
    const order = state.data?.groups ?? [];
    return order
      .map((group) => ({ ...group, items: items.filter((item) => item.group === group.id) }))
      .filter((group) => group.items.length > 0);
  }, [state.data]);

  const labelOf = (key: string) =>
    (state.data?.items.find((item) => item.key === key)?.label ?? key);

  function update(key: string, value: Value) {
    setValues((prev) => ({ ...prev, [key]: value }));
    setDirty((prev) => (prev.includes(key) ? prev : [...prev, key]));
  }

  async function saveGroup(groupId: string, keys: string[]) {
    const payload: Record<string, Value> = {};
    keys.forEach((key) => {
      if (dirty.includes(key)) {
        payload[key] = values[key];
      }
    });
    if (Object.keys(payload).length === 0) {
      message.info("没有需要保存的改动");
      return;
    }
    setSavingGroup(groupId);
    try {
      const result = groupId === "network"
        ? await saveNetworkSettings(payload, password)
        : await saveSettings(payload);
      const restart = result.needs_restart.map(labelOf);
      if (restart.length > 0) {
        // 常驻提示：需要重启才算真正生效，一闪而过的成功提示会让人以为已经生效了
        message.warning({
          content: `已保存。以下设置需重启程序才生效：${restart.join("、")}`,
          duration: 0,
        });
      } else {
        message.success("设置已保存");
      }
      setPassword("");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = Object.entries(err.fieldErrors);
        message.error(fields.length > 0
          ? fields.map(([key, errors]) => `${labelOf(key)}：${errors.join("；")}`).join("｜")
          : err.message);
      }
    } finally {
      setSavingGroup("");
    }
  }

  function renderField(item: SettingsItem) {
    const value = values[item.key];
    if (item.value_type === "bool") {
      return <Switch checked={value === true} onChange={(checked) => update(item.key, checked)} />;
    }
    if (item.options && item.options.length > 0) {
      return (
        <Select style={{ width: 220 }} value={String(value)}
                onChange={(next) => update(item.key, next)}
                // 只有「40x30」这类标签尺寸才补 mm；print_paper 的 a4/dot 不能显示成 “a4 mm”
                options={item.options.map((option) => ({
                  value: option,
                  label: /^\d+x\d+$/.test(option) ? `${option} mm` : option,
                }))} />
      );
    }
    if (item.value_type === "int") {
      return (
        <InputNumber style={{ width: 220 }} value={Number(value)}
                     onChange={(next) => update(item.key, next ?? 0)} />
      );
    }
    return (
      <Input style={{ width: 260 }} value={String(value ?? "")}
             onChange={(event) => update(item.key, event.target.value)} />
    );
  }

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">系统设置</h1>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.items.length === 0}
                     emptyText="没有可配置项">
        {() => (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            {grouped.map((group) => (
              <Card key={group.id} bordered={false} title={group.title}>
                <Space direction="vertical" size={16} style={{ width: "100%" }}>
                  {group.items.map((item) => (
                    <div key={item.key}>
                      <Space size={8}>
                        <Text strong>{item.label}</Text>
                        {item.needs_restart ? <Text type="warning">（需重启生效）</Text> : null}
                        {dirty.includes(item.key)
                          ? <Text type="secondary">· 有改动未保存</Text> : null}
                      </Space>
                      <div style={{ marginTop: 6 }}>{renderField(item)}</div>
                      {item.hint ? (
                        <Text type="secondary" style={{ fontSize: 12 }}>{item.hint}</Text>
                      ) : null}
                    </div>
                  ))}

                  {group.id === "network" ? (
                    <>
                      <Divider style={{ margin: "4px 0" }} />
                      <div>
                        <Text strong>本人口令</Text>
                        <div style={{ marginTop: 6 }}>
                          <Input.Password
                            style={{ width: 260 }}
                            value={password}
                            placeholder="修改端口 / 自动发现前需再次确认"
                            onChange={(event) => setPassword(event.target.value)}
                          />
                        </div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          改端口后手机需用新端口重新配对；关闭自动发现手机就搜不到本机（仍可用二维码配对）。
                        </Text>
                      </div>
                    </>
                  ) : null}

                  <Button type="primary" loading={savingGroup === group.id}
                          disabled={group.id === "network" && password.length === 0}
                          onClick={() => saveGroup(group.id, group.items.map((item) => item.key))}>
                    保存{group.title}
                  </Button>
                </Space>
              </Card>
            ))}
          </Space>
        )}
      </AsyncBoundary>
    </div>
  );
}
