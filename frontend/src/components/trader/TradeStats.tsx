import { Badge } from '@/components/ui/Badge'
import type { Trader } from '@/types'

interface TradeStatsProps {
  traders: Trader[]
}

export function TradeStats({ traders }: TradeStatsProps) {
  if (traders.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-[#848E9C]">
        暂无交易员配置
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-[#1E2329] text-left text-[#848E9C]">
            <th className="pb-3 font-medium">名称</th>
            <th className="pb-3 font-medium">状态</th>
            <th className="pb-3 font-medium text-right">收益率</th>
            <th className="pb-3 font-medium text-right">胜率</th>
            <th className="pb-3 font-medium text-right">交易次数</th>
            <th className="pb-3 font-medium">策略</th>
            <th className="pb-3 font-medium">交易所</th>
          </tr>
        </thead>
        <tbody>
          {traders.map((t) => (
            <tr key={t.id} className="border-b border-[#1E2329]/50 hover:bg-[#1E2329]/30">
              <td className="py-3 font-medium text-[#EAECEF]">{t.name}</td>
              <td className="py-3">
                <Badge variant={t.isRunning ? 'success' : 'muted'}>
                  {t.isRunning ? '运行中' : '已停止'}
                </Badge>
              </td>
              <td className="py-3 text-right font-mono" style={{ color: (t.pnl || 0) >= 0 ? '#0ECB81' : '#F6465D' }}>
                {(t.pnl || 0) >= 0 ? '+' : ''}{t.pnl?.toFixed(2) || '0.00'} USDT
              </td>
              <td className="py-3 text-right text-[#EAECEF]">
                {((t.winRate || 0) * 100).toFixed(1)}%
              </td>
              <td className="py-3 text-right text-[#EAECEF]">{t.totalTrades || 0}</td>
              <td className="py-3 text-[#EAECEF]">{t.strategy}</td>
              <td className="py-3 text-[#EAECEF]">{t.exchange}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}