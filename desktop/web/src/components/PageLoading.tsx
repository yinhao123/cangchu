import { Spin } from "antd";

/** 路由级懒加载的占位：整页居中，不做整屏遮罩（遮罩会让人以为点不动） */
export default function PageLoading() {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center",
      minHeight: "100vh", background: "var(--sd-bg)" }}>
      <Spin size="large" tip="加载中…">
        <div style={{ width: 120, height: 60 }} />
      </Spin>
    </div>
  );
}
