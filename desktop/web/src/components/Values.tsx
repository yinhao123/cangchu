import { Typography } from "antd";

/** 数量/金额的统一展示：右对齐 + 等宽数字（列表里「扫一眼比大小」全靠对齐） */
export function Num({ value, danger = false, suffix = "" }: {
  value: number | null | undefined;
  danger?: boolean;
  suffix?: string;
}) {
  if (value === null || value === undefined) {
    return <span className="sd-num sd-muted">—</span>;
  }
  return (
    <span className="sd-num">
      <Typography.Text type={danger ? "danger" : undefined}>{value}{suffix}</Typography.Text>
    </span>
  );
}

/** 金额：只在展示这一处保留两位小数（T-21：入库与展示两处做 round） */
export function Money({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) {
    return <span className="sd-num sd-muted">—</span>;
  }
  return <span className="sd-num">{value.toFixed(2)}</span>;
}
