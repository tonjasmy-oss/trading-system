import { ReactNode } from 'react'

interface ContainerProps {
  children: ReactNode
  className?: string
  as?: 'div' | 'main' | 'section' | 'header'
  fluid?: boolean
}

export function Container({
  children,
  className = '',
  as: Component = 'div',
  fluid = false,
}: ContainerProps) {
  return (
    <Component
      className={`${fluid ? 'w-full' : 'max-w-[1920px]'} mx-auto px-6 sm:px-8 lg:px-12 ${className}`}
    >
      {children}
    </Component>
  )
}