import { Container } from '@/components/common/Container'
import { Settings as SettingsIcon } from 'lucide-react'

export function SettingsPage() {
  return (
    <Container className="py-8">
      <h1 className="text-2xl font-bold text-[#EAECEF] mb-6 flex items-center gap-2">
        <SettingsIcon className="w-6 h-6 text-[#F0B90B]" />
        系统设置
      </h1>
      <div className="binance-card p-6">
        <p className="text-[#848E9C]">交易所配置 / AI模型配置 / 通知设置 / 安全设置</p>
      </div>
    </Container>
  )
}