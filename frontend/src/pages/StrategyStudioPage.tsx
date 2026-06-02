import { useState } from 'react'
import { Container } from '@/components/common/Container'
import { backtestApi } from '@/lib/api'
import { BookOpen, Play } from 'lucide-react'

const STRATEGY_TYPES = [
  { id: 'RSI', name: 'RSI 摆动', params: ['period', 'oversold', 'overbought'] },
  { id: 'MACD', name: 'MACD 趋势', params: ['fast', 'slow', 'signal'] },
  { id: 'BOLLINGER', name: '布林带均值回归', params: ['period', 'std_dev'] },
  { id: 'SMA', name: '均线交叉', params: ['fast', 'slow'] },
  { id: 'VOTE', name: '多策略投票', params: ['threshold'] },
  { id: 'ATRSTOP', name: 'ATR 趋势止损', params: ['ema_period', 'atr_period', 'atr_multiplier'] },
  { id: 'DONCHIAN', name: '唐奇安通道', params: ['channel_period', 'breakout_period'] },
]

export function StrategyStudioPage() {
  const [selectedStrategy, setSelectedStrategy] = useState(STRATEGY_TYPES[0])
  const [params, setParams] = useState<Record<string, string>>({})
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [timeframe, setTimeframe] = useState('4h')
  const [backtestResult, setBacktestResult] = useState<any>(null)
  const [running, setRunning] = useState(false)

  const runBacktest = async () => {
    setRunning(true)
    try {
      const result = await backtestApi.run({
        symbol,
        timeframe,
        strategy: selectedStrategy.id,
        params,
      })
      setBacktestResult(result.data)
    } catch (e) {
      console.error('Backtest failed', e)
    } finally {
      setRunning(false)
    }
  }

  return (
    <Container className="py-8">
      <h1 className="text-2xl font-bold text-[#EAECEF] mb-6 flex items-center gap-2">
        <BookOpen className="w-6 h-6 text-[#F0B90B]" />
        Strategy Studio
      </h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Strategy Config */}
        <div className="binance-card p-6">
          <h2 className="text-lg font-bold text-[#EAECEF] mb-4">策略配置</h2>
          
          {/* Strategy Selector */}
          <div className="grid grid-cols-2 gap-2 mb-6">
            {STRATEGY_TYPES.map((s) => (
              <button
                key={s.id}
                onClick={() => { setSelectedStrategy(s); setParams({}) }}
                className={`p-3 rounded-lg text-left text-sm transition-all ${
                  selectedStrategy.id === s.id
                    ? 'bg-[#F0B90B]/10 border border-[#F0B90B]/30 text-[#F0B90B]'
                    : 'bg-[#1E2329] text-[#848E9C] hover:text-[#EAECEF]'
                }`}
              >
                <span className="font-semibold block">{s.name}</span>
              </button>
            ))}
          </div>

          {/* Parameters */}
          <div className="space-y-3 mb-6">
            <div>
              <label className="text-xs text-[#848E9C] block mb-1">交易对</label>
              <input
                type="text"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="input-dark"
              />
            </div>
            <div>
              <label className="text-xs text-[#848E9C] block mb-1">时间周期</label>
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
                className="input-dark"
              >
                {['1m', '5m', '15m', '1h', '4h', '1d'].map((tf) => (
                  <option key={tf} value={tf}>{tf}</option>
                ))}
              </select>
            </div>
            {selectedStrategy.params.map((p) => (
              <div key={p}>
                <label className="text-xs text-[#848E9C] block mb-1">{p}</label>
                <input
                  type="text"
                  value={params[p] || ''}
                  onChange={(e) => setParams({ ...params, [p]: e.target.value })}
                  placeholder={p}
                  className="input-dark"
                />
              </div>
            ))}
          </div>

          <button
            onClick={runBacktest}
            disabled={running}
            className="btn-primary w-full flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {running ? '回测中...' : <><Play className="w-4 h-4" /> 运行回测</>}
          </button>
        </div>

        {/* Right: Results */}
        <div className="lg:col-span-2 binance-card p-6">
          <h2 className="text-lg font-bold text-[#EAECEF] mb-4">回测结果</h2>
          {backtestResult ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                {[
                  { label: '收益率', value: `${backtestResult.totalReturn?.toFixed(2) || 0}%`, color: '#0ECB81' },
                  { label: '最大回撤', value: `${backtestResult.maxDrawdown?.toFixed(2) || 0}%`, color: '#F6465D' },
                  { label: '夏普比率', value: backtestResult.sharpe?.toFixed(2) || '0.00', color: '#00F0FF' },
                  { label: '胜率', value: `${((backtestResult.winRate || 0) * 100).toFixed(1)}%`, color: '#F0B90B' },
                ].map(({ label, value, color }) => (
                  <div key={label} className="bg-[#1E2329] rounded-lg p-4">
                    <p className="text-xs text-[#848E9C] mb-1">{label}</p>
                    <p className="text-xl font-bold" style={{ color }}>{value}</p>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center h-64 text-[#848E9C]">
              <p>选择策略并运行回测查看结果</p>
            </div>
          )}
        </div>
      </div>
    </Container>
  )
}