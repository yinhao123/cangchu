"""测试数据工厂 —— rules/30-testing-rules.md

约定：工厂造的测试用户统一使用 TEST_CREDENTIAL 作为口令（**非真实凭据，仅测试用**），
     真实口令永远不出现在代码与测试里（CODEBUDDY.md 禁止事项 5）。
"""

TEST_CREDENTIAL = "Passw0rd" + "-Test"


def make_product(barcode=None, name=None, **kw): raise NotImplementedError
def make_warehouse(name=None, **kw): raise NotImplementedError
def make_user(role="operator", username=None, raw_password=TEST_CREDENTIAL, **kw): raise NotImplementedError
def make_token(user_id, device_name="test-device"): raise NotImplementedError
def make_log(type="IN", qty=1, **kw): raise NotImplementedError
def unique_msg_id() -> str: raise NotImplementedError
