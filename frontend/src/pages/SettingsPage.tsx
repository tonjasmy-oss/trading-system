import { useState, useEffect, useCallback, useMemo } from 'react'
import { Container } from '@/components/common/Container'
import api from '@/lib/api'
import { toast } from 'sonner'
import {
  Settings, Plus, Trash2, Save, RotateCcw, RefreshCw,
  Loader2, AlertTriangle, Zap, BarChart3,
  Globe, Clock, TrendingUp, TrendingDown, Info,
} from 'lucide-react'

interface AgentConfig {
  symbol: string
  strategy: string
  exchange: string
  timeframe: string
}

interface AgentSymbolsResponse {
  agents: AgentConfig[]
  available_strategies: string[]
  available_exchanges: string[]
  available_timeframes: string[]
}

interface BacktestMetrics {
  total_return_pct: number
  win_rate_pct: number
  profit_factor: number
  sharpe_ratio: number
  max_drawdown_pct: number
  total_trades: number
}

type BacktestSummary = Record<string, Record<string, BacktestMetrics>>

const DEFAULT_AGENT: AgentConfig = {
  symbol: '',
  strategy: 'EVR',
  exchange: 'binance',
  timeframe: '4h',
}

/** 🔴🟢 颜色函数 */
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

/** 单行指标徽章 */
function MetricsBadge({ m }: { m: BacktestMetrics | null }) {
  if (!m) return <span className="text-[10px] text-[#474D57] italic">无数据</span>
  return (
    <span className="inline-flex items-center gap-2 text-[10px] font-mono">
      <span className={pfColor(m.profit_factor)} title="Profit Factor">
        PF {m.profit_factor.toFixed(2)}
      </span>
      <span className={pnlColor(m.total_return_pct)} title="Total Return">
        {m.total_return_pct >= 0 ? '+' : ''}{m.total_return_pct}%
      </span>
      <span className="text-[#848E9C]" title="Win Rate">
        WR {m.win_rate_pct}%
      </span>
      <span className="text-[#5E6673]" title="Trades">
        {m.total_trades}T
      </span>
    </span>
  )
}

/** 详细 tooltip 卡片 */
function MetricsTooltip({ m, strategy }: { m: BacktestMetrics | null; strategy: string }) {
  if (!m) return null
  return (
    <div className="absolute left-0 top-full mt-1 z-50 w-56 p-3 rounded-lg
                    bg-[#1E2329] border border-[#2B3139] shadow-2xl text-xs">
      <div className="text-[#EAECEF] font-semibold mb-2">{strategy} 回测</div>
      <div className="space-y-1 text-[#848E9C]">
        <div className="flex justify-between">
          <span>收益率</span>
          <span className={pnlColor(m.total_return_pct)}>{m.total_return_pct >= 0 ? '+' : ''}{m.total_return_pct}%</span>
        </div>
        <div className="flex justify-between">
          <span>Profit Factor</span>
          <span className={pfColor(m.profit_factor)}>{m.profit_factor.toFixed(2)}</span>
        </div>
        <div className="flex justify-between">
          <span>胜率</span>
          <span>{m.win_rate_pct}%</span>
        </div>
        <div className="flex justify-between">
          <span>最大回撤</span>
          <span className="text-[#F6465D]">-{m.max_drawdown_pct}%</span>
        </div>
        <div className="flex justify-between">
          <span>夏普比率</span>
          <span>{m.sharpe_ratio.toFixed(2)}</span>
        </div>
        <div className="flex justify-between">
          <span>交易次数</span>
          <span>{m.total_trades}</span>
        </div>
      </div>
    </div>
  )
}

export function SettingsPage() {
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [available, setAvailable] = useState<{
    strategies: string[]
    exchanges: string[]
    timeframes: string[]
  }>({ strategies: [], exchanges: [], timeframes: [] })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [error, setError] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [newAgent, setNewAgent] = useState<AgentConfig>({ ...DEFAULT_AGENT })
  const [backtestSummary, setBacktestSummary] = useState<BacktestSummary>({})
  const [btLoading, setBtLoading] = useState(false)
  const [hoveredStrategy, setHoveredStrategy] = useState<string | null>(null)

  // Fetch current config
  const fetchConfig = useCallback(async () => {
    try {
      setLoading(true)
      const data: AgentSymbolsResponse = await api.get('/agents/symbols')
      setAgents(data.agents || [])
      setAvailable({
        strategies: data.available_strategies || [],
        exchanges: data.available_exchanges || [],
        timeframes: data.available_timeframes || [],
      })
      setError('')
    } catch (e: any) {
      setError(e.response?.data?.detail || '获取配置失败')
    } finally {
      setLoading(false)
    }
  }, [])

  // Fetch backtest summary
  const fetchBacktest = useCallback(async () => {
    try {
      setBtLoading(true)
      const symbols = agents.map(a => a.symbol).join(',')
      const data: any = await api.get('/backtest/summary', { params: { symbols } })
      setBacktestSummary(data.summary || {})
    } catch {
      // silient
    } finally {
      setBtLoading(false)
    }
  }, [agents])

  useEffect(() => { fetchConfig() }, [fetchConfig])
  useEffect(() => { if (agents.length > 0) fetchBacktest() }, [agents.length])

  /** 获取某个 symbol × (strategy:timeframe) 的回测数据 */
  const getBacktest = (symbol: string, strategy: string, timeframe: string): BacktestMetrics | null => {
    const symData = backtestSummary[symbol]
    if (!symData) return null
    return symData[`${strategy}:${timeframe}`] || null
  }

  // Update a field on an existing agent
  const updateAgent = (index: number, field: keyof AgentConfig, value: string) => {
    setAgents(prev => {
      const next = [...prev]
      next[index] = { ...next[index], [field]: value }
      return next
    })
  }

  // Remove an agent
  const removeAgent = (index: number) => {
    setAgents(prev => prev.filter((_, i) => i !== index))
  }

  // Add new agent
  const addAgent = () => {
    if (!newAgent.symbol.trim()) {
      toast.error('请输入交易对符号')
      return
    }
    setAgents(prev => [...prev, { ...newAgent }])
    setNewAgent({ ...DEFAULT_AGENT })
    setShowAdd(false)
    toast.success(`已添加 ${newAgent.symbol}`)
  }

  // Save all agents to backend
  const saveConfig = async () => {
    if (agents.length === 0) {
      toast.error('至少需要一个监控标的')
      return
    }
    try {
      setSaving(true)
      const res: any = await api.put('/agents/symbols', { agents })
      toast.success(res.message || '配置已保存')
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  // Restart live daemon
  const restartDaemon = async () => {
    try {
      setRestarting(true)
      const res: any = await api.post('/agents/restart')
      toast.success(res.message || 'Live Daemon 已重启')
      setTimeout(fetchConfig, 3000)
    } catch (e: any) {
      toast.error(e.response?.data?.detail || '重启失败')
    } finally {
      setRestarting(false)
    }
  }

  const strategyDesc: Record<string, string> = {
    EVR: 'EMA-Vol-RSI 复合趋势',
    RSI: 'RSI 超买超卖',
    SMA: '双均线交叉',
    MACD: 'MACD 金叉死叉',
    BOLLINGER: '布林带突破',
    KDJ: 'KDJ 随机指标',
    ATRSTOP: 'ATR 动态止损',
    MULTIFACTOR: '多因子趋势',
    DONCHIAN: '海龟通道突破',
    COINGLASS: '情绪+清算集群',
    FUNDING_ARB: '资金费率套利',
    STAT_ARB: '统计套利',
    SUI_SUPERTREND: 'SUI Supertrend',
    BTC_SUPERTREND: 'BTC Supertrend',
    BTC_TRENDFLOW: 'BTC TrendFlow',
    BTC_TRENDFLOW_2H: 'BTC TrendFlow 2H',
    RSI_LAYERED: 'RSI 分层策略',
    EMA_CROSS_LAYERED: 'EMA交叉分层',
    VOTE: '多策略投票',
    AUTO: '自动选择',
    BNB_TRENDMR: 'BNB趋势+均值回归',
    BNB_VOLBREAK: 'BNB波动突破',
  }

  if (loading) {
    return (
      <Container className="py-8">
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-8 h-8 text-[#F0B90B] animate-spin" />
        </div>
      </Container>
    )
  }

  return (
    <Container className="py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-[#EAECEF] flex items-center gap-3">
          <Settings className="w-6 h-6 text-[#F0B90B]" />
          系统设置
        </h1>
        <div className="flex items-center gap-3">
          <button onClick={fetchBacktest} disabled={btLoading}
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm
                       bg-[#1E2329] text-[#848E9C] hover:text-[#EAECEF] transition-all"
            title="刷新回测数据">
            <RefreshCw className={`w-4 h-4 ${btLoading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={saveConfig}
            disabled={saving || agents.length === 0}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                       bg-[#1E2329] text-[#EAECEF] border border-[#2B3139]
                       hover:border-[#F0B90B]/30 hover:text-[#F0B90B]
                       disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            保存配置
          </button>
          <button
            onClick={restartDaemon}
            disabled={restarting}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                       bg-[#F0B90B]/10 text-[#F0B90B] border border-[#F0B90B]/20
                       hover:bg-[#F0B90B]/20 transition-all
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {restarting ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
            重启生效
          </button>
        </div>
      </div>

      {error && (
        <div className="binance-card p-4 mb-4 border border-red-500/30 bg-red-500/5 flex items-center gap-3">
          <AlertTriangle className="w-5 h-5 text-red-400 flex-shrink-0" />
          <span className="text-red-400 text-sm">{error}</span>
          <button onClick={fetchConfig} className="ml-auto text-[#F0B90B] text-sm hover:underline">
            重试
          </button>
        </div>
      )}

      {/* Agents Table */}
      <div className="binance-card overflow-hidden">
        <div className="px-6 py-4 border-b border-[#1E2329] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-[#F0B90B]" />
            <h2 className="text-lg font-semibold text-[#EAECEF]">监控标的配置</h2>
            <span className="text-xs text-[#848E9C] bg-[#1E2329] px-2 py-0.5 rounded-full">
              {agents.length} 个标的
            </span>
          </div>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm
                       bg-[#F0B90B] text-black font-medium
                       hover:bg-[#F0B90B]/90 transition-all"
          >
            <Plus className="w-4 h-4" />
            添加标的
          </button>
        </div>

        {/* Table Header */}
        <div className="grid grid-cols-12 gap-4 px-6 py-3 text-xs text-[#848E9C] uppercase tracking-wider
                        bg-[#0B0E11]/50 border-b border-[#1E2329]">
          <div className="col-span-2">交易对</div>
          <div className="col-span-3">策略</div>
          <div className="col-span-2">回测数据</div>
          <div className="col-span-2">数据源</div>
          <div className="col-span-1">K线</div>
          <div className="col-span-2 text-right">操作</div>
        </div>

        {/* Agent Rows */}
        <div className="divide-y divide-[#1E2329]">
          {agents.map((agent, i) => {
            const bt = getBacktest(agent.symbol, agent.strategy, agent.timeframe)
            return (
            <div
              key={i}
              className="grid grid-cols-12 gap-4 px-6 py-4 items-center hover:bg-[#1E2329]/30 transition-colors"
            >
              {/* Symbol */}
              <div className="col-span-2">
                <div className="flex items-center gap-2">
                  <span className="w-6 h-6 rounded bg-[#F0B90B]/10 text-[#F0B90B] text-xs
                                 flex items-center justify-center font-mono font-bold">
                    {i + 1}
                  </span>
                  <span className="text-[#EAECEF] font-mono font-medium text-sm">
                    {agent.symbol}
                  </span>
                </div>
              </div>

              {/* Strategy */}
              <div className="col-span-3 relative">
                <select
                  value={agent.strategy}
                  onChange={e => updateAgent(i, 'strategy', e.target.value)}
                  onMouseEnter={() => setHoveredStrategy(`${i}`)}
                  onMouseLeave={() => setHoveredStrategy(null)}
                  className="w-full bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-3 py-2
                             border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                             transition-colors appearance-none cursor-pointer"
                >
                  {available.strategies.map(s => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
                <p className="text-[10px] text-[#848E9C] mt-1">
                  {strategyDesc[agent.strategy] || ''}
                </p>
                {hoveredStrategy === `${i}` && (
                  <MetricsTooltip m={bt} strategy={agent.strategy} />
                )}
              </div>

              {/* Backtest Badge */}
              <div className="col-span-2">
                <MetricsBadge m={bt} />
              </div>

              {/* Exchange */}
              <div className="col-span-2">
                <div className="flex items-center gap-2">
                  <Globe className="w-3.5 h-3.5 text-[#848E9C]" />
                  <select
                    value={agent.exchange}
                    onChange={e => updateAgent(i, 'exchange', e.target.value)}
                    className="flex-1 bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-3 py-2
                               border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                               transition-colors appearance-none cursor-pointer"
                  >
                    {available.exchanges.map(ex => (
                      <option key={ex} value={ex}>{ex}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Timeframe */}
              <div className="col-span-1">
                <select
                  value={agent.timeframe}
                  onChange={e => updateAgent(i, 'timeframe', e.target.value)}
                  className="w-full bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-2 py-2
                             border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                             transition-colors appearance-none cursor-pointer text-center"
                >
                  {available.timeframes.map(tf => (
                    <option key={tf} value={tf}>{tf}</option>
                  ))}
                </select>
              </div>

              {/* Actions */}
              <div className="col-span-2 flex justify-end gap-2">
                <button
                  onClick={() => removeAgent(i)}
                  className="p-2 rounded-lg text-[#848E9C] hover:text-red-400 hover:bg-red-400/10
                             transition-all"
                  title="删除此标的"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          )})}

          {agents.length === 0 && (
            <div className="px-6 py-12 text-center">
              <BarChart3 className="w-12 h-12 text-[#2B3139] mx-auto mb-4" />
              <p className="text-[#848E9C] text-sm">暂无监控标的</p>
              <button
                onClick={() => setShowAdd(true)}
                className="mt-4 text-[#F0B90B] text-sm hover:underline"
              >
                添加第一个标的
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Add Agent Modal */}
      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="binance-card p-6 w-full max-w-md mx-4 animate-in fade-in zoom-in">
            <h3 className="text-lg font-semibold text-[#EAECEF] mb-4 flex items-center gap-2">
              <Plus className="w-5 h-5 text-[#F0B90B]" />
              添加监控标的
            </h3>

            <div className="space-y-4">
              {/* Symbol */}
              <div>
                <label className="text-xs text-[#848E9C] block mb-1.5">交易对</label>
                <input
                  type="text"
                  placeholder="BTC/USDT"
                  value={newAgent.symbol}
                  onChange={e => setNewAgent(p => ({ ...p, symbol: e.target.value.toUpperCase() }))}
                  className="w-full bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-3 py-2.5
                             border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                             placeholder:text-[#2B3139] transition-colors font-mono"
                  autoFocus
                  onKeyDown={e => e.key === 'Enter' && addAgent()}
                />
              </div>

              {/* Strategy */}
              <div>
                <label className="text-xs text-[#848E9C] block mb-1.5">策略</label>
                <select
                  value={newAgent.strategy}
                  onChange={e => setNewAgent(p => ({ ...p, strategy: e.target.value }))}
                  className="w-full bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-3 py-2.5
                             border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                             transition-colors appearance-none cursor-pointer"
                >
                  {available.strategies.map(s => (
                    <option key={s} value={s}>{s} — {strategyDesc[s] || ''}</option>
                  ))}
                </select>
              </div>

              {/* Exchange & Timeframe row */}
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-[#848E9C] block mb-1.5">
                    <Globe className="w-3 h-3 inline mr-1" />
                    数据源
                  </label>
                  <select
                    value={newAgent.exchange}
                    onChange={e => setNewAgent(p => ({ ...p, exchange: e.target.value }))}
                    className="w-full bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-3 py-2.5
                               border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                               transition-colors appearance-none cursor-pointer"
                  >
                    {available.exchanges.map(ex => (
                      <option key={ex} value={ex}>{ex}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs text-[#848E9C] block mb-1.5">
                    <Clock className="w-3 h-3 inline mr-1" />
                    K线周期
                  </label>
                  <select
                    value={newAgent.timeframe}
                    onChange={e => setNewAgent(p => ({ ...p, timeframe: e.target.value }))}
                    className="w-full bg-[#0B0E11] text-[#EAECEF] text-sm rounded-lg px-3 py-2.5
                               border border-[#2B3139] focus:border-[#F0B90B] focus:outline-none
                               transition-colors appearance-none cursor-pointer"
                  >
                    {available.timeframes.map(tf => (
                      <option key={tf} value={tf}>{tf}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => { setShowAdd(false); setNewAgent({ ...DEFAULT_AGENT }) }}
                className="px-4 py-2 rounded-lg text-sm text-[#848E9C] hover:text-[#EAECEF]
                           hover:bg-[#1E2329] transition-all"
              >
                取消
              </button>
              <button
                onClick={addAgent}
                disabled={!newAgent.symbol.trim()}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                           bg-[#F0B90B] text-black hover:bg-[#F0B90B]/90
                           disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                <Zap className="w-4 h-4" />
                确认添加
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Quick Actions */}
      <div className="mt-6 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <QuickActionCard
          icon={Save}
          title="保存并重启"
          desc="保存当前配置并重启 Live Daemon 使新策略生效"
          onClick={async () => {
            if (agents.length === 0) {
              toast.error('至少需要一个监控标的')
              return
            }
            try {
              setSaving(true)
              await api.put('/agents/symbols', { agents })
              toast.success('配置已保存，正在重启...')
              setSaving(false)
              setTimeout(() => restartDaemon(), 500)
            } catch {
              toast.error('保存失败')
              setSaving(false)
            }
          }}
          disabled={saving || restarting}
        />
        <QuickActionCard
          icon={RefreshCw}
          title="刷新回测"
          desc="重新扫描 backtest_results 目录获取最新回测指标"
          onClick={fetchBacktest}
        />
        <QuickActionCard
          icon={TrendingUp}
          title="策略绩效"
          desc={`悬停策略选择器查看该标的×策略的完整回测指标（PF/收益率/胜率/回撤/夏普）`}
          disabled={false}
          static
        />
        <QuickActionCard
          icon={AlertTriangle}
          title="注意事项"
          desc="修改配置后需「保存」→「重启生效」，Live Daemon 才会使用新标的轮询"
          disabled={false}
          static
        />
      </div>
    </Container>
  )
}

function QuickActionCard({
  icon: Icon, title, desc, onClick, disabled, static: isStatic,
}: {
  icon: any; title: string; desc: string; onClick?: () => void; disabled?: boolean; static?: boolean
}) {
  const Comp = isStatic ? 'div' : 'button'
  return (
    <Comp onClick={onClick} disabled={disabled}
      className={`binance-card p-4 text-left transition-all ${
        isStatic ? '' : 'hover:border-[#F0B90B]/30 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed'
      }`}>
      <div className="flex items-start gap-3">
        <div className={`w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 ${
          isStatic ? 'bg-[#1E2329]' : 'bg-[#F0B90B]/10'
        }`}>
          <Icon className={`w-5 h-5 ${isStatic ? 'text-[#848E9C]' : 'text-[#F0B90B]'}`} />
        </div>
        <div>
          <h3 className={`text-sm font-medium ${isStatic ? 'text-[#848E9C]' : 'text-[#EAECEF]'}`}>{title}</h3>
          <p className="text-xs text-[#848E9C] mt-1 leading-relaxed">{desc}</p>
        </div>
      </div>
    </Comp>
  )
}
