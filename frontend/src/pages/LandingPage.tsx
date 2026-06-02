import { Link } from 'react-router-dom'
import { Container } from '@/components/common/Container'
import { BarChart3, Bot, BookOpen, Zap } from 'lucide-react'

const features = [
  {
    icon: BarChart3,
    title: 'Dashboard',
    description: '实时监控持仓、权益曲线、交易统计',
    path: '/dashboard',
    color: '#F0B90B',
  },
  {
    icon: BookOpen,
    title: 'Strategy Studio',
    description: '构建、测试、优化量化交易策略',
    path: '/strategy',
    color: '#00F0FF',
  },
  {
    icon: Bot,
    title: 'AI Agent',
    description: '智能助手帮你管理交易和诊断问题',
    path: '/agent',
    color: '#0ECB81',
  },
  {
    icon: Zap,
    title: '快速启动',
    description: '三省六部架构，信号→风控→执行分离',
    path: '/dashboard',
    color: '#F6465D',
  },
]

export function LandingPage() {
  return (
    <Container className="py-16 md:py-24">
      {/* Hero */}
      <div className="text-center mb-16">
        <h1
          className="text-5xl md:text-6xl font-bold mb-4"
          style={{
            background: 'linear-gradient(135deg, #F0B90B 0%, #FFD700 50%, #F0B90B 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}
        >
          VergeX Trading System
        </h1>
        <p className="text-xl text-[#848E9C] max-w-2xl mx-auto mb-8">
          三省六部制量化交易系统 · 中书省信号生成 · 门下省风控审核 · 尚书省执行调度
        </p>
        <Link to="/dashboard" className="btn-primary inline-flex items-center gap-2 text-lg px-8 py-3">
          进入 Dashboard
          <BarChart3 className="w-5 h-5" />
        </Link>
      </div>
      
      {/* Feature Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        {features.map(({ icon: Icon, title, description, path, color }) => (
          <Link
            key={path}
            to={path}
            className="binance-card p-6 hover:border-[#F0B90B]/30 transition-all group"
          >
            <div
              className="w-12 h-12 rounded-lg flex items-center justify-center mb-4"
              style={{ background: `${color}20`, border: `1px solid ${color}40` }}
            >
              <Icon className="w-6 h-6" style={{ color }} />
            </div>
            <h3 className="text-lg font-bold text-[#EAECEF] mb-2">{title}</h3>
            <p className="text-sm text-[#848E9C]">{description}</p>
          </Link>
        ))}
      </div>
    </Container>
  )
}