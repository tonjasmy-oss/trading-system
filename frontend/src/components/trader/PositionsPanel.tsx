import { Badge } from '@/components/ui/Badge'
import type { Position } from '@/types'

interface PositionsPanelProps {
  positions: Position[]
}

export function PositionsPanel({ positions }: PositionsPanelProps) {
  if (positions.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-[#848E9C] text-sm">
        当前无持仓
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {positions.map((pos, i) => (
        <div key={i} className="bg-[#1E2329] rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="font-bold text-[#EAECEF]">{pos.symbol}</span>
              <Badge variant={pos.side === 'LONG' ? 'success' : 'danger'}>
                {pos.side}
              </Badge>
            </div>
            <span className="text-sm" style={{ color: pos.unrealizedPnl >= 0 ? '#0ECB81' : '#F6465D' }}>
              {pos.unrealizedPnl >= 0 ? '+' : ''}{pos.unrealizedPnl.toFixed(2)} USDT
              ({pos.unrealizedPnlPct >= 0 ? '+' : ''}{pos.unrealizedPnlPct.toFixed(2)}%)
            </span>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs text-[#848E9C]">
            <div>数量: <span className="text-[#EAECEF]">{pos.quantity}</span></div>
            <div>开仓价: <span className="text-[#EAECEF]">{pos.entryPrice}</span></div>
            <div>标记价: <span className="text-[#EAECEF]">{pos.markPrice}</span></div>
          </div>
        </div>
      ))}
    </div>
  )
}