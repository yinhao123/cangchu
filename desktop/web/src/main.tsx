import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/global.css";
import "./styles/print.css";   // 单据打印版式（P0-3）：只含 @media print 与打印文档样式

const container = document.getElementById("root");
if (!container) {
  throw new Error("缺少 #root 挂载点：index.html 被改坏了，重新构建前端");
}

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
