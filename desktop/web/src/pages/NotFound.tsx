import { Button, Result } from "antd";
import { useNavigate } from "react-router-dom";

/** 前端路由未命中：给回首页的路，不要静默重定向（用户会以为「点了没反应」） */
export default function NotFound() {
  const navigate = useNavigate();
  return (
    <Result
      status="404"
      title="页面不存在"
      subTitle="地址可能输入有误，或者该功能尚未在当前版本开放。"
      extra={<Button type="primary" onClick={() => navigate("/", { replace: true })}>返回首页</Button>}
    />
  );
}
