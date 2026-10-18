---
description: 安卓端契约（生成 /api/* 或 UDP 代码时用于校验兼容性，本期不实现安卓）
alwaysApply: false
enabled: true
---

# 安卓端契约（本期仅约束电脑端实现的兼容责任）

**规则**：本期不写安卓代码。但电脑端每个 `/api/*` 与 UDP 报文都必须满足下列契约，否则 V2 期安卓接入会返工。

1. **响应字段永不省略**：可空字段返回 `null`。Retrofit data class 依赖键存在。
2. **`X-Auth-Token`** 是唯一 APP 认证头；`/api/login` 返回 `token` + `expires_at`；Token 失效必须返回 `401 {code:1003}`，APP 据此清本地 Token 回配对页。
3. **`client_msg_id` 必填**：APP 在扫码瞬间生成 UUID 存 Room，网络超时后**用同一个 UUID 重发**（超时 ≠ 失败）。服务端命中 UNIQUE 返回首次结果，这是不重复入账的唯一保障。
4. **`/api/ping` 必须返回实际端口**（可能非 5000），APP 不得写死端口；且 `ping` 无 DB 查询、必须极快。
5. **UDP 报文**：`INVENTORY_SERVER v1.0 IP:.. PORT:.. NAME:..`，空格分隔 `KEY:VALUE`，`NAME` 放最后（可含空格），未知 KEY 必须忽略。报文 ≤512 字节、UTF-8。
6. **二维码内容**是紧凑 JSON `{protocol,version,host,port,name}`，不含任何凭据。
7. **批量同步**单次 ≤200 条，逐条返回结果数组（顺序与请求一致），单条失败不影响其余。业务类失败（1007/1008/1002/1009）APP 会停止自动重试，所以**不要**把它们返回成 5xx。
8. **`server_time`** 必须在 `ping`/`dashboard` 返回，「今日」口径以服务器时钟为准（APP 手机时间可能不准）。
9. `is_low_stock`、`total_qty` 等派生值由服务端算好返回，APP 不重复实现，避免口径分叉。
10. 字段名一律 `snake_case`（与库一致，避免安卓侧命名策略转换）。
