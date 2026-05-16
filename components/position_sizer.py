"""
position_sizer.py — 自适应仓位管理
===================================

替代固定 100% 资金下单，根据胜率/盈亏比/波动率动态计算仓位。

方法：
  1. Kelly 公式：f = win_rate - (1-win_rate)/(avg_win/avg_loss)
     取半 Kelly (f/2) 以降低回撤
  2. ATR 波动率调整：高波动缩小仓位，低波动放大仓位
  3. 熔断限制：连续亏损后自动缩减仓位

使用方式：
  sizer = PositionSizer(equity=10000, win_rate=0.6, avg_win_pct=4.0, avg_loss_pct=2.0)
  ratio = sizer.calculate(atr=150, price=3000)
  # ratio = 0.4 (40% 资金)
"""

import math

class PositionSizer:
    """自适应仓位管理器"""

    def __init__(self, equity: float = 10000.0,
                 win_rate: float = 0.5, avg_win_pct: float = 4.0,
                 avg_loss_pct: float = 2.0):
        self.equity = equity
        self.win_rate = min(win_rate, 0.95)
        self.avg_win_pct = avg_win_pct
        self.avg_loss_pct = avg_loss_pct
        self._base_ratio = self._kelly_half()
        self._consecutive_losses = 0

    def _kelly_half(self) -> float:
        """半 Kelly 仓位比例"""
        if self.avg_loss_pct <= 0 or self.avg_win_pct <= 0:
            return 0.25  # 默认 25%
        win_loss_ratio = self.avg_win_pct / self.avg_loss_pct
        kelly = self.win_rate - (1 - self.win_rate) / win_loss_ratio
        kelly_half = max(0.05, min(0.5, kelly / 2))  # 半 Kelly，5%~50%
        return round(kelly_half, 3)

    def calculate(self, atr: float = None, price: float = None,
                  volatility_regime: str = "medium") -> float:
        """
        计算当前仓位比例

        Args:
            atr: ATR 值
            price: 当前价格
            volatility_regime: MarketRegime 波动率 ("high"/"medium"/"low")

        Returns:
            仓位比例 (0.01 ~ 1.0)
        """
        # 基础半 Kelly
        ratio = self._base_ratio

        # ATR 波动率调整
        if atr and price and price > 0:
            vol_ratio = atr / price
            if vol_ratio > 0.05:      # 高波动 → 减仓
                ratio *= 0.5
            elif vol_ratio > 0.03:    # 中高波动
                ratio *= 0.7
            elif vol_ratio < 0.01:    # 低波动 → 加仓
                ratio *= min(1.5, ratio * 1.5)

        # 市场状态调整
        vol_mult = {"high": 0.4, "medium": 0.7, "low": 1.2}
        ratio *= vol_mult.get(volatility_regime, 0.7)

        # 连续亏损惩罚
        if self._consecutive_losses >= 5:
            ratio *= 0.1  # 急刹车
        elif self._consecutive_losses >= 3:
            ratio *= 0.4

        # 上下限
        return max(0.01, min(1.0, ratio))

    def record_result(self, pnl_pct: float):
        """记录交易结果，更新统计"""
        if pnl_pct > 0:
            self._consecutive_losses = max(0, self._consecutive_losses - 1)
            # 动态更新胜率估计（指数平滑）
            self.win_rate = self.win_rate * 0.9 + 0.1
        else:
            self._consecutive_losses += 1
            self.win_rate = self.win_rate * 0.9 + 0
        # 更新半 Kelly
        self._base_ratio = self._kelly_half()

    def get_current_ratio(self) -> float:
        return self._base_ratio
