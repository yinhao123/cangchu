"""口令与设备令牌测试（TDD RED）—— PRD 5.1 / 10.1 · UT-17

覆盖三件安全底线：
  1. 口令不可逆哈希（pbkdf2:sha256:600000，纯 Python，PyInstaller 友好）
  2. 口令强度规则（≥8 且含字母与数字）—— 因为初始口令 admin123 是弱口令，必须有改密闸门
  3. 设备令牌只存哈希（库被翻走不等于凭据泄露）
"""
import pytest

from services.auth_service import (
    check_password_strength,
    hash_password,
    hash_token,
    verify_password,
)


def test_same_password_hashes_differ_each_time():
    """加盐：相同口令两次哈希必须不同，否则能从哈希判断谁和谁口令一样"""
    assert hash_password("Adm1n-Test") != hash_password("Adm1n-Test")


def test_hash_uses_pbkdf2_sha256_and_verifies():
    h = hash_password("Adm1n-Test")
    assert h.startswith("pbkdf2:sha256"), f"应为 pbkdf2:sha256，实际 {h[:24]}"
    assert verify_password("Adm1n-Test", h) is True
    assert verify_password("wrong", h) is False


def test_verify_tolerates_none_hash():
    assert verify_password("anything", None) is False


@pytest.mark.parametrize("bad", ["", "abc", "12345678", "abcdefgh", "Ab1", "Pass word"])
def test_weak_passwords_are_rejected(bad):
    """规则：长度 ≥8 且同时含字母与数字；纯数字/纯字母/过短/含空格分隔的短语都拒"""
    with pytest.raises(Exception) as ei:
        check_password_strength(bad)
    assert ei.value.code == 1002


@pytest.mark.parametrize("good", ["admin123", "Passw0rd", "仓库2026ab", "a1b2c3d4"])
def test_valid_passwords_pass_strength_check(good):
    check_password_strength(good)  # 不抛即通过


def test_initial_weak_credential_passes_but_is_flagged():
    """admin123 符合「≥8 且含字母数字」，但数据库里 must_change_password=1 会拦住它——
       这条测试锁住「初始口令可用但不安全」的设计意图，避免以后误改成拒绝 admin123。"""
    check_password_strength("admin123")


def test_cjk_only_digits_case_is_rejected_because_letter_means_ascii():
    """口径固定：「含字母」指 ASCII 字母。汉字口令虽安全但不满足规则，
       明确测出来，避免以后有人按不同理解改实现。"""
    with pytest.raises(Exception) as ei:
        check_password_strength("仓库仓库仓库20")
    assert ei.value.code == 1002
    assert any("字母" in e for e in ei.value.data["errors"]["new_password"])


def test_token_hash_is_sha256_hex_and_stable():
    import hashlib
    t = "3f9a" + "0" * 44
    assert hash_token(t) == hashlib.sha256(t.encode()).hexdigest()
    assert len(hash_token(t)) == 64
