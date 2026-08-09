"""资产负债表 — 净资产的真相

issue #31: 净资产必须用代码确定性算，**不允许 LLM 拍数**。

净资产 = 现金 + 房产现值 - 剩余贷款

每季度驱动：
1. 工资入账（按储蓄率）
2. 扣月供（利息 + 本金）
3. 房子按通胀/增值率微涨

买房时一次性：扣首付 + 记贷款 + 记房产现值。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("life_sim.balance_sheet")


@dataclass
class BalanceSheet:
    """资产负债 — 净资产的真实算式

    所有金额单位：万元
    """
    cash: float = 0.0                    # 现金
    house_value: float = 0.0             # 房产现值
    mortgage_remaining: float = 0.0      # 剩余贷款本金
    monthly_payment: float = 0.0         # 当前月供
    mortgage_rate_annual: float = 0.0375  # 年利率（默认 3.75% — 2024 LPR）
    mortgage_years_remaining: int = 0    # 剩余还款年数
    has_house: bool = False              # 是否持有房产

    @property
    def net_worth(self) -> float:
        """净资产 = 现金 + 房产现值 - 剩余贷款"""
        return self.cash + self.house_value - self.mortgage_remaining

    @property
    def annual_mortgage_cost(self) -> float:
        return self.monthly_payment * 12

    def can_afford(self, amount: float) -> bool:
        return self.cash >= amount

    def buy_house(
        self,
        house_price: float,
        down_payment_ratio: float = 0.30,
        mortgage_rate_annual: float | None = None,
        mortgage_years: int = 30,
    ) -> dict:
        """买房。一次性扣首付 + 记贷款 + 记房产现值。

        Returns:
            包含 down_payment / loan / monthly_payment 的 dict，失败抛 ValueError
        """
        if self.has_house:
            raise ValueError(f"已经有房（现值 {self.house_value:.0f}w），不能再买")
        if house_price <= 0:
            raise ValueError(f"房价 {house_price} 必须 > 0")

        down_payment = house_price * down_payment_ratio
        loan = house_price - down_payment

        if not self.can_afford(down_payment):
            raise ValueError(
                f"现金不够首付: 需要 {down_payment:.1f}w, 现有 {self.cash:.1f}w"
            )

        rate = mortgage_rate_annual or self.mortgage_rate_annual
        months = mortgage_years * 12
        monthly_rate = rate / 12
        # 等额本息月供
        if monthly_rate > 0:
            monthly_payment = loan * monthly_rate * (1 + monthly_rate) ** months / (
                (1 + monthly_rate) ** months - 1
            )
        else:
            monthly_payment = loan / months

        self.cash -= down_payment
        self.house_value = house_price
        self.mortgage_remaining = loan
        self.monthly_payment = monthly_payment
        self.mortgage_rate_annual = rate
        self.mortgage_years_remaining = mortgage_years
        self.has_house = True

        logger.info(
            "🏠 买房: 房价 %.0fw, 首付 %.0fw, 贷款 %.0fw, 月供 %.2fw, %d 年",
            house_price, down_payment, loan, monthly_payment, mortgage_years,
        )
        return {
            "house_price": house_price,
            "down_payment": down_payment,
            "loan": loan,
            "monthly_payment": monthly_payment,
            "mortgage_years": mortgage_years,
        }

    def tick_quarter(
        self,
        income_yearly: float,
        savings_rate: float = 0.40,
        house_appreciation_annual: float = 0.02,
    ) -> dict:
        """每季度推进 — 工资入账 + 扣月供 + 房子增值

        Args:
            income_yearly: 当前年收入（万元）
            savings_rate: 储蓄率（0.4 = 40% 工资存下来）
            house_appreciation_annual: 房价年化增值率（0.02 = 2% / 年）
        """
        # 1. 工资按储蓄率入账
        quarterly_income = income_yearly / 4
        saved = quarterly_income * savings_rate
        self.cash += saved

        # 2. 扣月供（3 个月）
        interest_paid = 0.0
        principal_paid = 0.0
        if self.has_house and self.monthly_payment > 0 and self.mortgage_remaining > 0:
            quarterly_payment = self.monthly_payment * 3
            # 利息按剩余本金算（季度）
            quarterly_rate = self.mortgage_rate_annual / 4
            interest_paid = self.mortgage_remaining * quarterly_rate
            principal_paid = max(0, quarterly_payment - interest_paid)
            self.cash -= quarterly_payment
            self.mortgage_remaining = max(0, self.mortgage_remaining - principal_paid)
            # 剩余年限按还款时间反推
            if self.monthly_payment > 0:
                remaining_months = self.mortgage_remaining / self.monthly_payment
                self.mortgage_years_remaining = max(0, int(remaining_months / 12))

        # 3. 房子按年化增值率微涨
        if self.has_house and self.house_value > 0:
            quarterly_appreciation = house_appreciation_annual / 4
            self.house_value *= (1 + quarterly_appreciation)

        return {
            "saved": saved,
            "interest_paid": interest_paid,
            "principal_paid": principal_paid,
            "quarterly_mortgage": self.monthly_payment * 3 if self.has_house else 0,
            "house_value_after": self.house_value,
            "cash_after": self.cash,
            "net_worth_after": self.net_worth,
        }

    def as_dict(self) -> dict:
        return {
            "cash": round(self.cash, 2),
            "house_value": round(self.house_value, 2),
            "mortgage_remaining": round(self.mortgage_remaining, 2),
            "monthly_payment": round(self.monthly_payment, 4),
            "mortgage_years_remaining": self.mortgage_years_remaining,
            "has_house": self.has_house,
            "net_worth": round(self.net_worth, 2),
        }
