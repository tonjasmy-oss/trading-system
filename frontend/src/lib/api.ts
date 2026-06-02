import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截 - 添加时间戳防缓存
api.interceptors.request.use((config) => {
  config.params = { ...config.params, _t: Date.now() }
  return config
})

// 响应拦截
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const message = error.response?.data?.message || error.message
    console.error('API Error:', message)
    return Promise.reject(error)
  }
)

export default api

// ─── Trader APIs ───────────────────────────────────────────────
export const traderApi = {
  list: () => api.get('/trader/list'),
  status: () => api.get('/trader/status'),
  start: (id: string) => api.post(`/trader/start/${id}`),
  stop: (id: string) => api.post(`/trader/stop/${id}`),
  config: () => api.get('/trader/config'),
  saveConfig: (data: any) => api.post('/trader/config/save', data),
}

// ─── Market APIs ───────────────────────────────────────────────
export const marketApi = {
  klines: (symbol: string, timeframe: string, limit?: number) =>
    api.get('/market/klines', { params: { symbol, timeframe, limit } }),
  ticker: (symbol: string) => api.get('/market/ticker', { params: { symbol } }),
  balance: () => api.get('/market/balance'),
  positions: () => api.get('/market/positions'),
}

// ─── Backtest APIs ────────────────────────────────────────────
export const backtestApi = {
  run: (params: any) => api.post('/backtest/run', params),
  results: () => api.get('/backtest/results'),
  chart: (strategy: string, params: any) => api.get(`/backtest/chart/${strategy}`, { params }),
}

// ─── Agent APIs ────────────────────────────────────────────────
export const agentApi = {
  chat: (message: string, lang?: string) =>
    api.post('/agent/chat', { message, lang: lang || 'zh' }),
  chatStream: (message: string, lang?: string) =>
    api.post('/agent/chat/stream', { message, lang: lang || 'zh' }, { responseType: 'stream' }),
  clear: () => api.post('/agent/clear'),
  status: () => api.get('/agent/status'),
}