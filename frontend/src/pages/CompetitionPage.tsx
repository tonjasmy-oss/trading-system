import { useState, useEffect } from 'react'
import { Container } from '@/components/common/Container'
import api from '@/lib/api'
import { toast } from 'sonner'
import { Trophy, TrendingUp, TrendingDown, Loader2, RefreshCw, Medal, BarChart3, Target } from 'lucide-react'

interface Ranking {
  rank: number
  strategy: string
  return_pct: number
  sharpe: number
  max_dd: number
  win_rate: number
  trades: number
  profit_factor: number
  error?: string
}

interface CompetitionResult {
  symbol: string
  timeframe: string
  direction: string
  period: string
  bars: number
  rankings: Ranking[]
  error?: string
}

const SYMBOLS = ['BTC/USDT', 'SOL/USDT', 'XAUT/USDT', 'BNB/USDT', 'SUI/USDT']
const TIMEFRAMES = ['4h', '2h', '1h', '1d']
const DIRECTIONS = [
  { value: 'both', label: '多空双向' },
  { value: 'long', label: '仅做多' },
  { value: 'short', label: '仅做空' },
]

function pnlColor(v: number): string {
  if (v > 0) return 'text-[#0ECB81]'
  if (v < 0) return 'text-[#F6465D]'
  return 'text-[#848E9C]'
}

function pfColor(v: number): string {
  if (v >= 1.5) return 'text-[#0ECB81]'
  if (v >= 1.0) return 'text-[#F0B90B]'
  return 'text-[#F6465D]'
}

function rankMedal(rank: number) {
  if (rank === 1) return <Medal className="w-5 h-5 text-yellow-400" />
  if (rank === 2) return <Medal className="w-5 h-5 text-gray-300" />
  if (rank === 3) return <Medal className="w-5 h-5 text-amber-600" />
  return <span className="text-[#848E9C] text-sm w-5 text-center">{rank}</span>
}

export function CompetitionPage() {
  const [symbol, setSymbol] = useState('BTC/USDT')
  const [timeframe, setTimeframe] = useState('4h')
  const [direction, setDirection] = useState('both')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<CompetitionResult | null>(null)

  const runCompetition = async () => {
    setLoading(true)
    setResult(null)
    try {
      const data: CompetitionResult = await api.get('/competition', {
        params: { symbol, timeframe, direction },
      })
      if (data.error) {
        toast.error(data.error)
      }
      setResult(data)
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '竞赛运行失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { runCompetition() }, [])

  return (
    <Container className="py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-[#EAECEF] flex items-center gap-3">
          <Trophy className="w-6 h-6 text-[#F0B90B]" />
          策略竞赛
        </h1>
        <button
          onClick={runCompetition}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                     bg-[#F0B90B]/10 text-[#F0B90B] border border-[#F0B90B]/20
                     hover:bg-[#F0B90B]/20 transition-all
                     disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          运行竞赛
        </button>
      </div>

      {/* Controls */}
      <div className="binance-card p-4 mb-6">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="text-xs text-[#848E9C] block mb-1.5">
              <BarChart3 className="w-3 h-3 inline mr-1" />
              交易对
            </label>
            <select
              value={symbol}
              onChange={e => setSymbol(e.target.value)}
              className="w-full bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-3 py-2.5
                         border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                         transition-colors appearance-none cursor-pointer"
            >
              {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-[#848E9C] block mb-1.5">K线周期</label>
            <select
              value={timeframe}
              onChange={e => setTimeframe(e.target.value)}
              className="w-full bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-3 py-2.5
                         border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                         transition-colors appearance-none cursor-pointer"
            >
              {TIMEFRAMES.map(tf => <option key={tf} value={tf}>{tf}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-[#848E9C] block mb-1.5">
              <Target className="w-3 h-3 inline mr-1" />
              交易方向
            </label>
            <select
              value={direction}
              onChange={e => setDirection(e.target.value)}
              className="w-full bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-3 py-2.5
                         border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                         transition-colors appearance-none cursor-pointer"
            >
              {DIRECTIONS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-8 h-8 text-[#F0B90B] animate-spin" />
          <span className="ml-3 text-[#848E9C]">策略博弈中...</span>
        </div>
      )}

      {/* Results */}
      {result && !loading && (
        <>
          {/* Info Bar */}
          <div className="binance-card px-6 py-3 mb-4 flex flex-wrap items-center gap-4 text-sm">
            <span className="text-[#EAECEF] font-mono font-semibold">{result.symbol}</span>
            <span className="text-[#848E9C]">{result.timeframe} · {DIRECTIONS.find(d => d.value === result.direction)?.label}</span>
            <span className="text-[#5E6673] text-xs">{result.period}</span>
            <span className="text-[#5E6673] text-xs ml-auto">{result.bars?.toLocaleString()} bars</span>
          </div>

          {/* Leaderboard */}
          <div className="binance-card overflow-hidden">
            <div className="px-6 py-4 border-b border-[#1E2329]">
              <h2 className="text-lg font-semibold text-[#EAECEF] flex items-center gap-2">
                <Trophy className="w-5 h-5 text-[#F0B90B]" />
                排行榜
              </h2>
            </div>

            <div className="grid grid-cols-12 gap-4 px-6 py-3 text-xs text-[#848E9C] uppercase tracking-wider
                            bg-[#0B0E11]/50 border-b border-[#1E2329]">
              <div className="col-span-1">#</div>
              <div className="col-span-2">策略</div>
              <div className="col-span-2 text-right">收益率</div>
              <div className="col-span-1 text-right">PF</div>
              <div className="col-span-1 text-right">胜率</div>
              <div className="col-span-1 text-right">交易</div>
              <div className="col-span-2 text-right">回撤</div>
              <div className="col-span-2 text-right">夏普</div>
            </div>

            <div className="divide-y divide-[#1E2329]">
              {result.rankings?.map((r, i) => (
                <div
                  key={r.strategy}
                  className={`grid grid-cols-12 gap-4 px-6 py-4 items-center text-sm
                              hover:bg-[#1E2329]/30 transition-colors
                              ${i === 0 ? 'bg-[#F0B90B]/5' : ''}`}
                >
                  <div className="col-span-1 flex items-center">
                    {rankMedal(r.rank)}
                  </div>
                  <div className={`col-span-2 font-semibold ${i === 0 ? 'text-[#F0B90B]' : 'text-[#EAECEF]'}`}>
                    {r.strategy}
                  </div>
                  <div className={`col-span-2 text-right font-mono font-bold ${pnlColor(r.return_pct)}`}>
                    {r.return_pct >= 0 ? '+' : ''}{r.return_pct}%
                  </div>
                  <div className={`col-span-1 text-right font-mono ${pfColor(r.profit_factor)}`}>
                    {r.profit_factor?.toFixed(2)}
                  </div>
                  <div className="col-span-1 text-right text-[#848E9C] font-mono">
                    {r.win_rate}%
                  </div>
                  <div className="col-span-1 text-right text-[#5E6673] font-mono">
                    {r.trades}
                  </div>
                  <div className="col-span-2 text-right text-[#F6465D] font-mono">
                    -{r.max_dd}%
                  </div>
                  <div className={`col-span-2 text-right font-mono ${r.sharpe >= 1 ? 'text-[#0ECB81]' : r.sharpe >= 0 ? 'text-[#848E9C]' : 'text-[#F6465D]'}`}>
                    {r.sharpe?.toFixed(2)}
                  </div>
                </div>
              ))}

              {(!result.rankings || result.rankings.length === 0) && (
                <div className="px-6 py-12 text-center text-[#848E9C]">
                  暂无竞赛数据
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {/* Quick Tips */}
      {!loading && !result && (
        <div className="binance-card p-12 text-center">
          <Trophy className="w-16 h-16 text-[#2B3139] mx-auto mb-4" />
          <p className="text-[#848E9C] text-sm">选择标的和周期，点击「运行竞赛」</p>
          <p className="text-[#5E6673] text-xs mt-2">
            所有策略将在相同历史数据上对决，按收益率排行
          </p>
        </div>
      )}
    </Container>
  )
}
