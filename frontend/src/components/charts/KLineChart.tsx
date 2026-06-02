import { useEffect, useRef } from 'react'
import { createChart, IChartApi, CandlestickData, Time, CandlestickSeries } from 'lightweight-charts'
import type { OHLCV } from '@/types'

interface KLineChartProps {
  data: OHLCV[]
  symbol: string
}

export function KLineChart({ data, symbol }: KLineChartProps) {
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
    
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#0ECB81',
      downColor: '#F6465D',
      borderUpColor: '#0ECB81',
      borderDownColor: '#F6465D',
      wickUpColor: '#0ECB81',
      wickDownColor: '#F6465D',
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
    const candleData: CandlestickData<Time>[] = data.map((d) => ({
      time: (d.time / 1000) as Time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }))
    seriesRef.current.setData(candleData)
    chartRef.current?.timeScale().fitContent()
  }, [data])

  return (
    <div ref={containerRef} className="w-full" style={{ height: '400px' }} />
  )
}
