"""业务异常与错误码 —— PRD 10.3 / docs/03 1.3

BizError(code, msg, data=None, http_status=None) 由 services/models 抛出，api 层 errorhandler 转包络。
禁止：except: pass、裸 except Exception 后返回 200、在 BizError 里塞堆栈到 msg（堆栈只进日志）。

错误码表（不得私自新增，须先改 docs/03-API接口规范.md）：
  1001 请求格式错误      1002 字段校验失败     1003 未登录/Token 失效
  1004 无权限/需改密/停用 1005 登录失败过多     1006 资源不存在
  1007 条码未登记        1008 仓库无效或停用    1009 库存不足
  1010 唯一约束冲突/已冲销 1011 乐观锁冲突      1012 同步通道繁忙
  1013 软件未激活（离线授权，见 docs/11）      1999 服务内部错误
  1014 单据状态不允许该操作（docs/13 单据层）  1015 单据明细非法
  1016 超出单据可收/可发数量                   1017 仓库层级非法（两级树）
  1018 WMS 单据功能已关闭（设置 wms_bills_enabled=false）
  1019 导入文件非法（编码/表头缺列/空数据/单元格格式）  1020 导入超限（>5000 行或 >5MB）
  1021 期初库存已存在（账本非空，不允许重复建账）
"""

# ★Win7 服务端跑 Python 3.8：本模块注解含 PEP 585/604（dict[int,int] / int | None），
#   3.8 会在导入期真的求值注解 → "TypeError: 'type' object is not subscriptable"。
#   本模块被 app.py/core.response 早期导入，缺这行等于整个程序起不来（docs/12）。
from __future__ import annotations


HTTP_BY_CODE: dict[int, int] = {
    1001: 400, 1002: 422, 1003: 401, 1004: 403, 1005: 429, 1006: 404,
    1007: 404, 1008: 422, 1009: 422, 1010: 409, 1011: 409, 1012: 503, 1013: 403,
    1014: 409, 1015: 422, 1016: 409, 1017: 409, 1018: 409,
    1019: 422, 1020: 422, 1021: 409,
    1999: 500,
}


class BizError(Exception):
    def __init__(self, code: int, msg: str, data=None, http_status: int | None = None):
        self.code = code
        self.msg = msg
        self.data = data
        # 未显式指定则按映射表推断，保证 code!=0 永远配非 2xx（PRD 10.3）
        self.http_status = http_status if http_status is not None else HTTP_BY_CODE.get(code, 500)
        super().__init__(msg)


