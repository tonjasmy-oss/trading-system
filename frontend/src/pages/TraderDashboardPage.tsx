import { useEffect, useState } from 'react'
import { Container } from '@/components/common/Container'
import { traderApi, marketApi } from '@/lib/api'
import type { Trader, Position, OHLCV, EquityCurve } from '@/types'
import { PositionsPanel } from '@/components/trader/PositionsPanel'
import { EquityChart } from '@/components/charts/EquityChart'
import { KLineChart } from '@/components/charts/KLineChart'
import { TradeStats } from '@/components/trader/TradeStats'

export function TraderDashboardPage() {
  const [traders, setTraders] = useState<Trader[]>([])
  const [positions, setPositions] = useState<Position[]>([])
  const [klines, setKlines] = useState<OHLCV[]>([])
  const [equityCurve, setEquityCurve] = useState<EquityCurve[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState('BTC/USDT')
  const [timeframe, setTimeframe] = useState('4h')
  const [loading, setLoading] = useState(true)
  const [lastUpdate, setLastUpdate] = useState(new Date())

  useEffect(() => {
    loadData()
    const interval = setInterval(loadData, 30000)
    return () => clearInterval(interval)
  }, [])

  const loadData = async () => {
    try {
      const [tradersRes, positionsRes] = await Promise.all([
        traderApi.status(),
        marketApi.positions(),
      ])
      setTraders(tradersRes.data?.traders || [])
      setPositions(positionsRes.data?.positions || [])
      
      // Load klines for selected symbol
      const klineRes = await marketApi.klines(selectedSymbol, timeframe, 200)
      setKlines(klineRes.data?.klines || [])
      
      setLastUpdate(new Date())
    } catch (e) {
      console.error('Failed to load dashboard data', e)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Container className="py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-[#EAECEF]">Trader Dashboard</h1>
          <p className="text-sm text-[#848E9C]">
            上次更新: {lastUpdate.toLocaleTimeString('zh-CN')}
          </p>
        </div>
        <div className="flex gap-2">
          {['1h', '4h', '1d'].map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-all ${
                timeframe === tf
                  ? 'bg-[#F0B90B] text-black'
                  : 'bg-[#1E2329] text-[#848E9C] hover:text-[#EAECEF]'
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {/* Stats Row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
        {[
          { label: '运行交易员', value: traders.filter(t => t.isRunning).length, color: '#0ECB81' },
          { label: '总持仓', value: positions.length, color: '#00F0FF' },
          { label: '总收益', value: `${traders.reduce((sum, t) => sum + (t.pnl || 0), 0).toFixed(2)} USDT`, color: '#F0B90B' },
          { label: '总交易次数', value: traders.reduce((sum, t) => sum + (t.totalTrades || 0), 0), color: '#F6465D' },
        ].map(({ label, value, color }) => (
          <div key={label} className="binance-card p-4">
            <p className="text-xs text-[#848E9C] mb-1">{label}</p>
            <p className="text-xl font-bold" style={{ color }}>{value}</p>
          </div>
        ))}
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: KLine Chart */}
        <div className="lg:col-span-2 binance-card p-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold text-[#EAECEF]">{selectedSymbol} K线</h2>
            <select
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
              className="bg-[#1E2329] text-[#EAECEF] border border-[#1E2329] rounded px-3 py-1.5 text-sm"
            >
              {['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'SUI/USDT', 'XAUT/USDT'].map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>
          <KLineChart data={klines} symbol={selectedSymbol} />
        </div>

        {/* Right: Positions */}
        <div className="binance-card p-4">
          <h2 className="text-lg font-bold text-[#EAECEF] mb-4">当前持仓</h2>
          <PositionsPanel positions={positions} />
        </div>
      </div>

      {/* Equity Curve */}
      <div className="binance-card p-4 mt-6">
        <h2 className="text-lg font-bold text-[#EAECEF] mb-4">权益曲线</h2>
        <EquityChart data={equityCurve} />
      </div>

      {/* Traders List */}
      <div className="binance-card p-4 mt-6">
        <TradeStats traders={traders} />
      </div>
    </Container>
  )
}