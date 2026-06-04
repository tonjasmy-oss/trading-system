"""
backtest_validation.py — 回测验证增强
======================================

蒙特卡洛模拟、Bootstrap 置信区间、Walk-Forward 滚动验证、基准对比。

借鉴 Vibe-Trading 的回测验证理念。

用法:
  from backtest_validation import BacktestValidator
  bv = BacktestValidator(returns=[+2.5, -1.3, +3.0, ...])
  result = bv.run_all()
  print(bv.format_report(result))
"""

import math
import random
from typing import Dict, List, Tuple, Optional
from statistics import mean, stdev


class BacktestValidator:
    """回测验证器 — 多种统计验证方法"""

    def __init__(self, returns: List[float],
                 benchmark_returns: List[float] = None,
                 mc_samples: int = 1000,
                 wf_train_pct: float = 0.6,
                 wf_step: int = 90):
        """
        Args:
            returns: 逐笔收益率序列（百分比，如 +2.5 表示 +2.5%）
            benchmark_returns: 基准收益率序列（如 BTC 同期表现）
            mc_samples: 蒙特卡洛模拟次数
            wf_train_pct: Walk-Forward 训练集比例
            wf_step: Walk-Forward 步长（数据点数）
        """
        self.returns = [r for r in returns if r is not None]
        self.benchmark = benchmark_returns or []
        self.mc_samples = mc_samples
        self.wf_train_pct = wf_train_pct
        self.wf_step = wf_step

    def run_all(self) -> Dict:
        """运行所有验证方法"""
        if len(self.returns) < 10:
            return {"error": f"数据不足（{len(self.returns)}<10），无法运行验证"}

        return {
            "monte_carlo": self.monte_carlo(),
            "bootstrap_ci": self.bootstrap_ci(),
            "walk_forward": self.walk_forward(),
            "benchmark": self.benchmark_compare() if self.benchmark else None,
            "basic_stats": self._basic_stats(),
        }

    # ---------- 基础统计 ----------

    def _basic_stats(self) -> Dict:
        ret = self.returns
        n = len(ret)
        mu = mean(ret)
        sigma = stdev(ret) if n > 1 else 0

        # 年化（假设每笔交易平均 30 天）
        annual_return = (1 + mu / 100) ** (365 / 30) - 1 if mu != 0 else 0
        annual_vol = (sigma / 100) * math.sqrt(365 / 30) if sigma else 0
        sharpe = (annual_return - 0.02) / max(annual_vol, 0.001) if annual_vol else 0

        # 最大回撤
        cumulative = 100.0
        peak = 100.0
        max_dd = 0.0
        for r in ret:
            cumulative *= (1 + r / 100)
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / peak * 100
            max_dd = max(max_dd, dd)

        # 盈亏比
        wins = [r for r in ret if r > 0]
        losses = [abs(r) for r in ret if r <= 0]
        profit_factor = sum(wins) / max(sum(losses), 0.01)

        return {
            "count": n,
            "mean_return": round(mu, 2),
            "std_return": round(sigma, 2),
            "annual_return_pct": round(annual_return * 100, 1),
            "annual_vol_pct": round(annual_vol * 100, 1),
            "sharpe": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "profit_factor": round(profit_factor, 2),
            "win_rate": round(len(wins) / n, 2),
        }

    # ---------- 蒙特卡洛模拟 ----------

    def monte_carlo(self) -> Dict:
        """
        蒙特卡洛模拟：对收益率序列随机打乱重采样 mc_samples 次，
        给出最终收益的分布区间。
        """
        ret = self.returns
        n = len(ret)
        final_values = []

        for _ in range(self.mc_samples):
            shuffled = random.choices(ret, k=n)
            final = 100.0
            for r in shuffled:
                final *= (1 + r / 100)
            final_values.append(final)

        final_values.sort()
        ci_95_low = final_values[int(self.mc_samples * 0.025)]
        ci_95_high = final_values[int(self.mc_samples * 0.975)]
        median = final_values[int(self.mc_samples * 0.5)]

        # 实际最终值
        actual_final = 100.0
        for r in ret:
            actual_final *= (1 + r / 100)

        return {
            "samples": self.mc_samples,
            "ci_95_low": round(ci_95_low - 100, 1),  # % 收益
            "ci_95_high": round(ci_95_high - 100, 1),
            "median": round(median - 100, 1),
            "actual": round(actual_final - 100, 1),
            "actual_percentile": round(
                sum(1 for v in final_values if v <= actual_final) / self.mc_samples * 100, 1
            ),
            "interpretation": self._mc_interpretation(actual_final, ci_95_low, ci_95_high),
        }

    def _mc_interpretation(self, actual: float, low: float, high: float) -> str:
        if actual > high:
            return "✅ 实际收益显著优于随机（>97.5百分位），策略大概率有效"
        elif actual < low:
            return "❌ 实际收益显著劣于随机（<2.5百分位），策略可能靠运气亏钱"
        elif actual > 105:
            return "📊 实际收益在随机分布中上区间，有一定超额收益可能"
        else:
            return "⚪ 实际收益在随机分布正常范围内，无法排除随机性"

    # ---------- Bootstrap 置信区间 ----------

    def bootstrap_ci(self) -> Dict:
        """Bootstrap 方法估计夏普比率和总收益的 95% 置信区间"""
        ret = self.returns
        n = len(ret)

        sharpe_samples = []
        return_samples = []

        for _ in range(self.mc_samples):
            sample = random.choices(ret, k=n)
            mu = mean(sample)
            sigma = stdev(sample) if len(sample) > 1 else 0.001
            annual_r = (1 + mu / 100) ** (365 / 30) - 1
            annual_v = (sigma / 100) * math.sqrt(365 / 30)
            sharpe = (annual_r - 0.02) / max(annual_v, 0.001)
            sharpe_samples.append(sharpe)

            final = 100.0
            for r in sample:
                final *= (1 + r / 100)
            return_samples.append(final - 100)

        sharpe_samples.sort()
        return_samples.sort()

        idx_low = int(self.mc_samples * 0.025)
        idx_high = int(self.mc_samples * 0.975)

        return {
            "sharpe_ci": (
                round(sharpe_samples[idx_low], 2),
                round(sharpe_samples[idx_high], 2),
            ),
            "return_ci": (
                round(return_samples[idx_low], 1),
                round(return_samples[idx_high], 1),
            ),
            "sharpe_median": round(sharpe_samples[self.mc_samples // 2], 2),
        }

    # ---------- Walk-Forward 分析 ----------

    def walk_forward(self) -> Dict:
        """Walk-Forward 滚动验证：用历史数据训练，逐步外推测试"""
        ret = self.returns
        n = len(ret)
        train_size = max(5, int(n * self.wf_train_pct))
        step = max(3, min(self.wf_step, n - train_size))

        windows = []
        start = 0
        while start + train_size + 3 <= n:
            train = ret[start:start + train_size]
            test = ret[start + train_size:min(start + train_size + step, n)]

            if len(test) < 1:
                break

            train_final = 100.0
            for r in train:
                train_final *= (1 + r / 100)

            test_final = 100.0
            for r in test:
                test_final *= (1 + r / 100)

            windows.append({
                "window": len(windows) + 1,
                "train_start": start + 1,
                "train_end": start + train_size,
                "test_start": start + train_size + 1,
                "test_end": min(start + train_size + step, n),
                "train_return": round(train_final - 100, 1),
                "test_return": round(test_final - 100, 1),
                "is_profitable": test_final > 100,
            })
            start += step

        if not windows:
            return {"error": "数据不足，无法进行 Walk-Forward 分析"}

        profitable_windows = sum(1 for w in windows if w["is_profitable"])
        consistency = profitable_windows / len(windows)

        return {
            "total_windows": len(windows),
            "profitable_windows": profitable_windows,
            "consistency": round(consistency, 2),
            "windows": windows,
            "interpretation": (
                "✅ 策略稳定（各窗口均盈利）" if consistency >= 0.8
                else "📊 策略有一定稳定性" if consistency >= 0.5
                else "❌ 策略不稳定（多数窗口亏损），可能过拟合"
            ),
        }

    # ---------- 基准对比 ----------

    def benchmark_compare(self) -> Dict:
        """与基准对比：超额收益、信息比率、胜率差异"""
        ret = self.returns
        bench = self.benchmark[:len(ret)] if len(self.benchmark) >= len(ret) else self.benchmark

        if len(bench) < len(ret):
            # 对齐长度
            bench = bench + [0] * (len(ret) - len(bench))

        excess = [r - b for r, b in zip(ret, bench)]
        mean_excess = mean(excess) if excess else 0
        std_excess = stdev(excess) if len(excess) > 1 else 0.001

        # 信息比率
        ir = (mean_excess / 100) / max(std_excess / 100, 0.0001) * math.sqrt(365 / 30)

        # 策略 vs 基准最终收益
        strat_final = 100.0
        bench_final = 100.0
        for r, b in zip(ret, bench):
            strat_final *= (1 + r / 100)
            bench_final *= (1 + b / 100)

        # 超额胜率
        beat_count = sum(1 for r, b in zip(ret, bench) if r > b)
        beat_rate = beat_count / len(ret) if ret else 0

        return {
            "strategy_return": round(strat_final - 100, 1),
            "benchmark_return": round(bench_final - 100, 1),
            "excess_return": round(strat_final - bench_final, 1),
            "information_ratio": round(ir, 2),
            "beat_rate": round(beat_rate, 2),
            "mean_excess_per_trade": round(mean_excess, 2),
            "interpretation": (
                "✅ 显著跑赢基准" if ir > 1.0
                else "📊 略优于基准" if ir > 0.5
                else "⚪ 与基准相当" if abs(ir) < 0.3
                else "❌ 跑输基准"
            ),
        }

    # ---------- 报告 ----------

    def format_report(self, result: Dict) -> str:
        if "error" in result:
            return f"⚠️ {result['error']}"

        lines = ["=" * 60,
                 "  📊 回测验证报告",
                 "=" * 60, ""]

        # 基础统计
        bs = result.get("basic_stats", {})
        if bs:
            lines.append("## 基础统计")
            lines.append(f"  交易次数: {bs['count']}")
            lines.append(f"  平均收益: {bs['mean_return']:+.2f}%  标准差: {bs['std_return']:.2f}%")
            lines.append(f"  年化收益: {bs['annual_return_pct']:+.1f}%  年化波动: {bs['annual_vol_pct']:.1f}%")
            lines.append(f"  夏普比率: {bs['sharpe']:.2f}  最大回撤: {bs['max_drawdown_pct']:.1f}%")
            lines.append(f"  盈亏比: {bs['profit_factor']:.2f}  胜率: {bs['win_rate']:.0%}")
            lines.append("")

        # 蒙特卡洛
        mc = result.get("monte_carlo", {})
        if mc:
            lines.append("## 蒙特卡洛模拟")
            lines.append(f"  模拟次数: {mc['samples']}")
            lines.append(f"  95% CI: [{mc['ci_95_low']:+.1f}%, {mc['ci_95_high']:+.1f}%]")
            lines.append(f"  策略实际: {mc['actual']:+.1f}%  (百分位: {mc['actual_percentile']:.1f}%)")
            lines.append(f"  {mc['interpretation']}")
            lines.append("")

        # Bootstrap
        bc = result.get("bootstrap_ci", {})
        if bc:
            lines.append("## Bootstrap 置信区间")
            sr = bc["sharpe_ci"]
            rt = bc["return_ci"]
            lines.append(f"  夏普比率 95% CI: [{sr[0]:.2f}, {sr[1]:.2f}]")
            lines.append(f"  总收益 95% CI: [{rt[0]:+.1f}%, {rt[1]:+.1f}%]")
            lines.append("")

        # Walk-Forward
        wf = result.get("walk_forward", {})
        if wf and "error" not in wf:
            lines.append("## Walk-Forward 验证")
            lines.append(f"  窗口数: {wf['total_windows']}  (盈利: {wf['profitable_windows']})")
            lines.append(f"  一致性: {wf['consistency']:.0%}")
            lines.append(f"  {wf['interpretation']}")
            for w in wf.get("windows", [])[:5]:
                lines.append(f"    窗口{w['window']}: 训练[{w['train_start']}-{w['train_end']}] "
                             f"测试[{w['test_start']}-{w['test_end']}] "
                             f"训练{w['train_return']:+.1f}%→测试{w['test_return']:+.1f}%")
            lines.append("")

        # 基准对比
        bm = result.get("benchmark", {})
        if bm:
            lines.append("## 基准对比")
            lines.append(f"  策略收益: {bm['strategy_return']:+.1f}%")
            lines.append(f"  基准收益: {bm['benchmark_return']:+.1f}%")
            lines.append(f"  超额收益: {bm['excess_return']:+.1f}%")
            lines.append(f"  信息比率: {bm['information_ratio']:.2f}")
            lines.append(f"  逐笔胜率: {bm['beat_rate']:.0%}")
            lines.append(f"  {bm['interpretation']}")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)
