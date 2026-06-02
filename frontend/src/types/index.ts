export interface Trader {
  id: string
  name: string
  symbol: string
  strategy: string
  exchange: string
  isRunning: boolean
  pnl: number
  pnlPct: number
  winRate: number
  totalTrades: number
  createdAt: string
}

export interface Position {
  symbol: string
  side: 'LONG' | 'SHORT'
  quantity: number
  entryPrice: number
  markPrice: number
  unrealizedPnl: number
  unrealizedPnlPct: number
  leverage: number
  liquidationPrice?: number
}

export interface OHLCV {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface StrategyConfig {
  id: string
  name: string
  type: string  // RSI | MACD | BOLLINGER | SMA | VOTE | ATRSTOP | FORMULA
  params: Record<string, any>
  isActive: boolean
}

export interface Exchange {
  id: string
  name: string
  enabled: boolean
  apiKeySet: boolean
  testnet: boolean
}

export interface AIModel {
  id: string
  provider: string  // deepseek | openai | minimax | qwen
  model: string
  enabled: boolean
  apiKeySet: boolean
}

export interface TradeRecord {
  id: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  price: number
  pnl: number
  timestamp: string
  strategy: string
}

export interface EquityCurve {
  time: number
  equity: number
  drawdown: number
}