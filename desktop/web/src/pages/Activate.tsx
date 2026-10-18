import { useEffect, useState } from "react";
import { Alert, App as AntdApp, Button, Card, Descriptions, Form, Input, Spin, Typography } from "antd";
import { SafetyCertificateOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { activate, getState } from "../api/activation";
import type { ActivationState } from "../api/activation";

const { Title, Text, Paragraph } = Typography;

/**
 * 离线激活。
 *
 * ★本页**免登录**：未激活时全站被拦，如果激活还要先登录，用户就卡死了（docs/12）。
 * ★机器码必须能一键复制：它是卖家出码的唯一输入，现场手抄 32 位十六进制必然出错。
 */
export default function Activate() {
  const [state, setState] = useState<ActivationState | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState("");
  const [form] = Form.useForm<{ code: string }>();
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();

  useEffect(() => {
    getState()
      .then(setState)
      .catch(() => setState(null))
      .finally(() => setLoading(false));
  }, []);

  async function onSubmit(values: { code: string }) {
    setSubmitting(true);
    setErrorText("");
    try {
      const next = await activate(values.code);
      setState(next);
      message.success("激活成功，请登录使用");
      navigate("/login", { replace: true });
    } catch (err) {
      // 后端用 403/1013 表达「仍未激活」，这里把原因原样展示（不能当成功弹绿条）
      setErrorText(err instanceof ApiError ? err.message : "激活失败，请检查注册码");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="sd-center-wrap">
      <Card className="sd-center-card" bordered={false}>
        <Title level={4} style={{ marginBottom: 4 }}>
          <SafetyCertificateOutlined style={{ marginRight: 8, color: "#1677FF" }} />
          软件激活
        </Title>
        <Text type="secondary">本机尚未激活，输入卖家提供的注册码即可继续使用</Text>

        {loading ? (
          <div style={{ textAlign: "center", padding: "32px 0" }}>
            <Spin />
          </div>
        ) : (
          <>
            <Descriptions column={1} size="small" style={{ marginTop: 20 }}
                          items={[
                            {
                              key: "machine",
                              label: "本机机器码",
                              children: (
                                <Text code copyable={{ text: state?.machine_id || "" }}>
                                  {state?.display_machine_id || "读取失败"}
                                </Text>
                              ),
                            },
                            {
                              key: "status",
                              label: "授权状态",
                              children: state?.activated
                                ? `已激活（${state.expire_text || "永久有效"}）`
                                : (state?.message || "未激活"),
                            },
                          ]}
            />

            {state?.config_error ? (
              <Alert style={{ marginTop: 12 }} type="warning" showIcon
                     message="授权配置异常" description={state.config_error} />
            ) : null}

            <Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0, fontSize: 13 }}>
              把上面的机器码发给卖家，卖家会返回一段与**本机**绑定的注册码。
            </Paragraph>

            <Form form={form} layout="vertical" onFinish={onSubmit} style={{ marginTop: 16 }}>
              <Form.Item name="code" label="注册码"
                         rules={[{ required: true, message: "请粘贴注册码" }]}>
                <Input.TextArea rows={4} placeholder="把卖家返回的整段注册码粘贴到这里（含换行）"
                                spellCheck={false} />
              </Form.Item>

              {errorText ? <div className="sd-login-error" role="alert">{errorText}</div> : null}

              <Button type="primary" htmlType="submit" block size="large" loading={submitting}>
                提交激活
              </Button>
            </Form>
          </>
        )}
      </Card>
    </div>
  );
}
