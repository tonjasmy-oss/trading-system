import { clsx } from 'clsx'

interface BadgeProps {
  variant?: 'gold' | 'success' | 'danger' | 'muted'
  children: React.ReactNode
  className?: string
}

export function Badge({ variant = 'muted', children, className }: BadgeProps) {
  const variants = {
    gold: 'bg-[#F0B90B]/10 text-[#F0B90B] border border-[#F0B90B]/20',
    success: 'bg-[#0ECB81]/10 text-[#0ECB81] border border-[#0ECB81]/20',
    danger: 'bg-[#F6465D]/10 text-[#F6465D] border border-[#F6465D]/20',
    muted: 'bg-[#1E2329] text-[#848E9C] border border-[#1E2329]',
  }
  
  return (
    <span className={clsx('px-2 py-0.5 rounded text-xs font-bold', variants[variant], className)}>
      {children}
    </span>
  )
}