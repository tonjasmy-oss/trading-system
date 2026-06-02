import { useEffect, useRef } from 'react'
import { createChart, LineData, Time, LineSeries } from 'lightweight-charts'
import type { EquityCurve } from '@/types'

interface EquityChartProps {
  data: EquityCurve[]
}

export function EquityChart({ data }: EquityChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const seriesRef = useRef<any>(null)

  useEffect(() => {
    if (!containerRef.current) return
    
    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: 'transparent' },
        textColor: '#848E9C',
      },
      grid: {
        vertLines: { color: '#1E2329' },
        horzLines: { color: '#1E2329' },
      },
      crosshair: {
        mode: 1,
        vertLine: { color: '#F0B90B', width: 1, style: 2 },
        horzLine: { color: '#F0B90B', width: 1, style: 2 },
      },
      timeScale: {
        borderColor: '#1E2329',
        timeVisible: true,
      },
      rightPriceScale: {
        borderColor: '#1E2329',
      },
    })
    
    const series = chart.addSeries(LineSeries, {
      color: '#F0B90B',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })
    
    chartRef.current = chart
    seriesRef.current = series
    
    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    }
    window.addEventListener('resize', handleResize)
    handleResize()
    
    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
    }
  }, [])

  useEffect(() => {
    if (!seriesRef.current || data.length === 0) return
    const lineData: LineData<Time>[] = data.map((d) => ({
      time: (d.time / 1000) as Time,
      value: d.equity,
    }))
    seriesRef.current.setData(lineData)
    chartRef.current?.timeScale().fitContent()
  }, [data])

  return (
    <div ref={containerRef} className="w-full" style={{ height: '300px' }} />
  )
}
