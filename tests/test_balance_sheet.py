"""issue #31: 资产负债表测试

覆盖：
- buy_house 一次性扣首付 + 记贷款 + 记房产现值
- 现金不够首付时抛 ValueError
- tick_quarter 工资入账 (按储蓄率)
- tick_quarter 月供扣减 (利息 + 本金)
- 房子按年化增值率微涨
- net_worth 派生公式 = 现金 + 房子 - 贷款
- 已买房不能再买
- 零首付 / 全款买房
"""
import math
import pytest

from core.balance_sheet import BalanceSheet


# ============ buy_house ============

def test_buy_house_deducts_down_payment_and_records_loan():
    """买房: 现金扣首付, 记贷款 + 房产"""
    sheet = BalanceSheet(cash=200.0)
    info = sheet.buy_house(house_price=300.0, down_payment_ratio=0.30, mortgage_years=30)

    assert info["house_price"] == 300.0
    assert info["down_payment"] == 90.0
    assert info["loan"] == 210.0
    # 等额本息 30 年 3.75%: 月供约 0.97w
    assert 0.9 < info["monthly_payment"] < 1.1
    # 状态
    assert sheet.cash == pytest.approx(110.0)  # 200 - 90
    assert sheet.house_value == 300.0
    assert sheet.mortgage_remaining == pytest.approx(210.0)
    assert sheet.has_house is True
    assert sheet.mortgage_years_remaining == 30


def test_buy_house_insufficient_cash_raises():
    """现金不够首付 → ValueError"""
    sheet = BalanceSheet(cash=10.0)  # 只有 10w
    with pytest.raises(ValueError, match="现金不够首付"):
        sheet.buy_house(house_price=300.0, down_payment_ratio=0.30)


def test_buy_house_already_owns_raises():
    """已经买房 → 不能再买"""
    sheet = BalanceSheet(cash=500.0)
    sheet.buy_house(house_price=300.0, down_payment_ratio=0.30)
    with pytest.raises(ValueError, match="已经有房"):
        sheet.buy_house(house_price=200.0, down_payment_ratio=0.30)


def test_buy_house_zero_price_raises():
    """房价 <= 0 → ValueError"""
    sheet = BalanceSheet(cash=500.0)
    with pytest.raises(ValueError, match="房价.*必须"):
        sheet.buy_house(house_price=0)


def test_buy_house_full_cash_no_mortgage():
    """全款买房 (down_payment_ratio=1.0) → 无贷款"""
    sheet = BalanceSheet(cash=500.0)
    info = sheet.buy_house(house_price=300.0, down_payment_ratio=1.0)
    assert info["loan"] == 0.0
    assert info["monthly_payment"] == 0.0
    assert sheet.cash == 200.0
    assert sheet.mortgage_remaining == 0.0
    assert sheet.has_house is True


# ============ net_worth 派生 ============

def test_net_worth_equals_cash_plus_house_minus_mortgage():
    """净资产 = 现金 + 房产 - 贷款"""
    sheet = BalanceSheet(cash=50.0, house_value=300.0, mortgage_remaining=210.0)
    assert sheet.net_worth == pytest.approx(140.0)


def test_net_worth_no_house_equals_cash():
    """没买房时, 净资产 = 现金"""
    sheet = BalanceSheet(cash=8.0)
    assert sheet.net_worth == 8.0


# ============ tick_quarter (无房) ============

def test_tick_quarter_no_house_accumulates_savings():
    """无房时, 每季度按储蓄率累加"""
    sheet = BalanceSheet(cash=10.0)
    # 收入 40w/年, 储蓄率 0.4, 季度入账 = 40 * 0.4 / 4 = 4w
    info = sheet.tick_quarter(income_yearly=40.0, savings_rate=0.4)
    assert info["saved"] == pytest.approx(4.0)
    assert sheet.cash == pytest.approx(14.0)
    assert info["quarterly_mortgage"] == 0
    assert sheet.net_worth == pytest.approx(14.0)


def test_tick_quarter_default_savings_rate():
    """默认储蓄率 0.4"""
    sheet = BalanceSheet(cash=0.0)
    sheet.tick_quarter(income_yearly=100.0)
    # 100 * 0.4 / 4 = 10w
    assert sheet.cash == pytest.approx(10.0)


# ============ tick_quarter (有房 + 月供) ============

def test_tick_quarter_with_mortgage_deducts_payment():
    """有房贷时, 每季度扣月供"""
    sheet = BalanceSheet(cash=100.0, house_value=300.0, mortgage_remaining=210.0)
    sheet.buy_house(house_price=300.0, down_payment_ratio=0.30)  # 月供 ~0.97
    # 重置一下, 让 cash=100 起步
    sheet.cash = 100.0
    initial_mortgage = sheet.mortgage_remaining

    info = sheet.tick_quarter(income_yearly=40.0, savings_rate=0.4)
    quarterly_save = 4.0
    quarterly_mortgage = info["quarterly_mortgage"]  # 月供 × 3
    # 现金 = 100 + 4 - 季月供
    assert sheet.cash == pytest.approx(100 + quarterly_save - quarterly_mortgage)
    # 贷款本金 = 初始 - (季月供 - 季利息)
    assert sheet.mortgage_remaining < initial_mortgage
    # 利息 < 月供 (否则永远还不完)
    assert info["interest_paid"] < quarterly_mortgage
    # 本金 = 季月供 - 季利息
    assert info["principal_paid"] == pytest.approx(quarterly_mortgage - info["interest_paid"])


def test_tick_quarter_mortgage_principal_calculation():
    """季利息 = 剩余贷款 × 年利率 / 4"""
    sheet = BalanceSheet(
        cash=100.0, house_value=300.0, mortgage_remaining=100.0,
        mortgage_rate_annual=0.04, monthly_payment=1.0, mortgage_years_remaining=10, has_house=True,
    )
    info = sheet.tick_quarter(income_yearly=0, savings_rate=0.0)
    # 季利息 = 100 * 0.04 / 4 = 1.0
    assert info["interest_paid"] == pytest.approx(1.0)
    # 季月供 = 1.0 * 3 = 3.0
    # 本金 = 3.0 - 1.0 = 2.0
    assert info["principal_paid"] == pytest.approx(2.0)
    # 贷款剩 100 - 2 = 98
    assert sheet.mortgage_remaining == pytest.approx(98.0)


def test_tick_quarter_house_appreciation():
    """房子按年化增值率微涨"""
    sheet = BalanceSheet(cash=100.0, house_value=300.0, mortgage_remaining=200.0, has_house=True)
    sheet.tick_quarter(income_yearly=0, savings_rate=0.0, house_appreciation_annual=0.04)
    # 季度增值 = 0.04 / 4 = 0.01
    # 300 * 1.01 = 303
    assert sheet.house_value == pytest.approx(303.0)


def test_tick_quarter_mortgage_years_decreases():
    """剩余还款年数随还款下降"""
    sheet = BalanceSheet(
        cash=100.0, house_value=300.0, mortgage_remaining=120.0,
        monthly_payment=1.0, mortgage_years_remaining=10, has_house=True,
    )
    # 季月供 = 3.0, 利息 0.04/4*120 = 1.2, 本金 1.8
    # 剩 120 - 1.8 = 118.2
    # 剩余月数 = 118.2 / 1.0 = 118.2 月 = 9.85 年 → int(9.85) = 9
    sheet.tick_quarter(income_yearly=0, savings_rate=0.0, house_appreciation_annual=0)
    assert sheet.mortgage_years_remaining == 9


# ============ 综合场景: 30 岁 P7 净资产 ============

def test_realistic_30yo_p7_scenario():
    """30 岁 P7 一线老破小, 8 年工龄, 净资产应该 ~150w 量级"""
    # 起点: 18 岁, 父母给 30w (中产, 提前攒)
    sheet = BalanceSheet(cash=30.0)
    # 18 → 22 (4 年 = 16 季度): 大学 + 刚工作
    for _ in range(16):
        sheet.tick_quarter(income_yearly=20.0, savings_rate=0.5)
    # 22 岁 ≈ 30 + 16*2.5 = 70w
    assert 60 < sheet.cash < 80
    # 22 → 26 (4 年 = 16 季度): 大厂新人
    for _ in range(16):
        sheet.tick_quarter(income_yearly=40.0, savings_rate=0.5)
    # 26 岁 ≈ 70 + 16*5 = 150w
    assert 130 < sheet.cash < 170
    # 26 岁买一线老破小 350w, 首付 30% = 105w
    info = sheet.buy_house(house_price=350.0, down_payment_ratio=0.30)
    assert info["down_payment"] == 105.0
    assert info["loan"] == 245.0
    # 扣完首付: cash ≈ 45w
    assert 40 < sheet.cash < 50
    # 26 → 30 (4 年 = 16 季度): P7 收入高, 但要还月供
    for _ in range(16):
        sheet.tick_quarter(income_yearly=60.0, savings_rate=0.5)
    # 净资产 = 现金 + 房子(增值) - 剩贷款
    nw = sheet.net_worth
    # 期望 ~150-300w (房子 ~380w, 现金 ~50w, 剩贷款 ~220w)
    assert 100 < nw < 400, f"净资产 {nw:.0f}w 偏离 30岁 P7 期望范围"
    assert sheet.has_house is True
    assert sheet.mortgage_remaining > 0  # 还有贷款
    assert sheet.mortgage_remaining < 245  # 还了一部分


# ============ as_dict ============

def test_as_dict_structure():
    sheet = BalanceSheet(cash=10.0, house_value=100.0, mortgage_remaining=50.0, has_house=True)
    d = sheet.as_dict()
    assert d["cash"] == 10.0
    assert d["house_value"] == 100.0
    assert d["mortgage_remaining"] == 50.0
    assert d["has_house"] is True
    assert d["net_worth"] == 60.0


# ============ Driver 集成 ============

def test_driver_uses_balance_sheet_for_net_worth():
    """Driver 通过 balance_sheet 派生 net_worth"""
    from core.state import init_state_from_config
    cfg = {
        "simulation": {
            "start_age": 18, "end_age": 30,
            "initial_person": {
                "gaokao_score": 600, "family_background": "middle",
                "gender": "male", "city_tier": "tier1",
            },
        },
    }
    state = init_state_from_config(cfg, seed=42)
    # 初始: 现金 = 8w (middle), 净资产 = 8w
    assert state.balance_sheet.cash == 8.0
    assert state.metrics.net_worth == 8.0
    assert state.balance_sheet.net_worth == 8.0


def test_driver_state_includes_balance_sheet():
    """LifeState 默认带 balance_sheet"""
    from core.state import LifeState
    state = LifeState()
    assert hasattr(state, "balance_sheet")
    assert isinstance(state.balance_sheet, BalanceSheet)
