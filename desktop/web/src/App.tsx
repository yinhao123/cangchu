import { useEffect, useMemo } from "react";
import { App as AntdApp, ConfigProvider, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter } from "react-router-dom";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import AppRoutes from "./router";
import { ApiError, setErrorNotifier } from "./api/client";
import { SPA_BASE } from "./config";
import { UiProvider, useUi } from "./store/ui";

dayjs.locale("zh-cn");

export default function App() {
  return (
    <UiProvider>
      <ThemedApp />
    </UiProvider>
  );
}

function ThemedApp() {
  const { dark } = useUi();

  const themeConfig = useMemo(() => ({
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: "#1677FF",
      borderRadius: 6,
      fontSize: 14,
      fontFamily: '"PingFang SC", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif',
    },
    components: {
      Layout: { headerHeight: 56, headerPadding: "0 16px" },
      Table: { headerBg: dark ? "#1f1f1f" : "#fafafa" },
    },
  }), [dark]);

  return (
    <ConfigProvider locale={zhCN} theme={themeConfig}>
      <AntdApp>
        <NotifierBridge />
        <BrowserRouter basename={SPA_BASE || "/"}>
          <AppRoutes />
        </BrowserRouter>
      </AntdApp>
    </ConfigProvider>
  );
}

/**
 * 字段名 → 中文：后端 `data.errors` 的键是接口字段名（`quantity`/`warehouse_id`…），
 * 直接摊给用户等于没说。这里只做展示用映射，不改变任何接口契约。
 */
const FIELD_LABELS: Record<string, string> = {
  quantity: "数量",
  qty: "数量",
  target_qty: "盘点目标数量",
  warehouse_id: "仓库",
  to_warehouse_id: "调入仓库",
  barcode: "条码",
  product_id: "商品",
  partner_id: "往来单位",
  name: "名称",
  remark: "备注",
  reason: "原因",
  reject_reason: "驳回原因",
  price: "单价",
  unit_price: "单价",
  type: "类型",
  op_type: "单据类型",
  action: "操作",
  client_msg_id: "提交标识",
  lines: "明细",
  username: "用户名",
  role: "角色",
  password: "口令",
  new_password: "新口令",
  expect_return_date: "预计归还日期",
};

/** 把 antd 的 message 接到 api/client —— 业务代码里就不用每次自己弹提示 */
function NotifierBridge() {
  const { message } = AntdApp.useApp();

  useEffect(() => {
    setErrorNotifier((error: ApiError) => {
      // 错误常驻且带追踪码：仓库现场报问题时，追踪码是唯一能对上服务端日志的线索（docs/07）
      const suffix = error.traceId && error.traceId !== "-" ? `（追踪码 ${error.traceId}）` : "";
      // 只弹「参数校验失败」等于什么都没说（现场无从下手）：把字段级原因一并展示
      const fields = Object.entries(error.fieldErrors);
      const detail =
        fields.length > 0
          ? `${error.message}：${fields
              .map(([key, errors]) => `${FIELD_LABELS[key] ?? key} ${errors.join("；")}`)
              .join("；")}`
          : error.message;
      message.open({ type: "error", content: `${detail}${suffix}`, duration: 0 });
    });
  }, [message]);

  return null;
}
