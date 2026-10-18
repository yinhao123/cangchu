import { Button, Empty, Result, Skeleton, Space, Typography } from "antd";
import type { ReactNode } from "react";
import type { AsyncState } from "../hooks/useAsync";

const { Text } = Typography;

interface Props<T> {
  state: AsyncState<T>;
  /** 判空的条件（默认 `data === null` 即视为空） */
  emptyWhen?: (data: T) => boolean;
  emptyText?: ReactNode;
  /** 加载态的行数：表格页给 6，卡片页给 3 */
  skeletonRows?: number;
  children: (data: T) => ReactNode;
}

/**
 * 列表页统一的「加载中 / 失败可重试 / 空状态」三态边界（PRD 28.2）。
 *
 * ★加载中用骨架屏而不是整页遮罩：遮罩会让用户以为「点不动了」，
 *   而列表页最常见的是「数据没来」而不是「界面卡死」。
 * ★失败必须能原地重试，且要显示追踪码 —— 否则现场只能刷新整页、重新输筛选条件。
 */
export default function AsyncBoundary<T>({
  state,
  emptyWhen,
  emptyText,
  skeletonRows = 6,
  children,
}: Props<T>) {
  if (state.loading) {
    return <Skeleton active paragraph={{ rows: skeletonRows }} />;
  }

  if (state.error) {
    return (
      <Result
        status="warning"
        title="加载失败"
        subTitle={
          <Space direction="vertical" size={0}>
            <Text>{state.error.message}</Text>
            {state.error.traceId && state.error.traceId !== "-" ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                追踪码 {state.error.traceId}（报给运维可定位服务端日志）
              </Text>
            ) : null}
          </Space>
        }
        extra={<Button type="primary" onClick={state.reload}>重试</Button>}
      />
    );
  }

  if (state.data === null || (emptyWhen ? emptyWhen(state.data) : false)) {
    return <Empty description={emptyText ?? "暂无数据"} />;
  }

  return <>{children(state.data)}</>;
}
