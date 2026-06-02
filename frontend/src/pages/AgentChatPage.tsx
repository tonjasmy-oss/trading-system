import { useState, useRef, useEffect } from 'react'
import { Container } from '@/components/common/Container'
import { agentApi } from '@/lib/api'
import { Bot, Send, Trash2 } from 'lucide-react'

interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
}

export function AgentChatPage() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async () => {
    if (!input.trim() || loading) return
    const userMsg = input.trim()
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: userMsg, timestamp: new Date() }])
    setLoading(true)
    try {
      const res = await agentApi.chat(userMsg, 'zh') as any
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: res.reply || String(res), timestamp: new Date() },
      ])
      if (res.session_id) setSessionId(res.session_id)
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: '抱歉，请求失败: ' + String(e), timestamp: new Date() },
      ])
    } finally {
      setLoading(false)
    }
  }

  const clearChat = async () => {
    setMessages([])
    await agentApi.clear().catch(() => {})
  }

  return (
    <Container className="py-8">
      <h1 className="text-2xl font-bold text-[#EAECEF] mb-6 flex items-center gap-2">
        <Bot className="w-6 h-6 text-[#F0B90B]" />
        AI Agent
      </h1>

      {/* Chat Container */}
      <div className="binance-card flex flex-col" style={{ height: 'calc(100vh - 200px)', minHeight: '500px' }}>
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-6 space-y-4">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-[#848E9C]">
              <Bot className="w-16 h-16 mb-4 text-[#F0B90B]/30" />
              <p className="text-center max-w-md">
                你可以问我：创建交易员、查看持仓、诊断交易问题、配置交易所API
              </p>
            </div>
          )}
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex items-start gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
            >
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                  msg.role === 'user' ? 'bg-[#F0B90B]' : 'bg-[#1E2329]'
                }`}
              >
                {msg.role === 'user' ? (
                  <span className="text-black text-sm font-bold">U</span>
                ) : (
                  <Bot className="w-4 h-4 text-[#F0B90B]" />
                )}
              </div>
              <div
                className={`max-w-[70%] rounded-xl px-4 py-3 text-sm ${
                  msg.role === 'user'
                    ? 'bg-[#F0B90B]/10 border border-[#F0B90B]/20 text-[#EAECEF]'
                    : 'bg-[#1E2329] text-[#EAECEF]'
                }`}
              >
                <pre className="whitespace-pre-wrap font-sans">{msg.content}</pre>
                <p className="text-xs text-[#848E9C] mt-1">
                  {msg.timestamp.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                </p>
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-full bg-[#1E2329] flex items-center justify-center">
                <Bot className="w-4 h-4 text-[#F0B90B]" />
              </div>
              <div className="bg-[#1E2329] rounded-xl px-4 py-3">
                <div className="flex gap-1">
                  {[0, 1, 2].map((i) => (
                    <div
                      key={i}
                      className="w-2 h-2 rounded-full bg-[#F0B90B] animate-pulse"
                      style={{ animationDelay: `${i * 0.2}s` }}
                    />
                  ))}
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="border-t border-[#1E2329] p-4 flex gap-3">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                sendMessage()
              }
            }}
            placeholder="输入你的问题，按 Enter 发送..."
            className="input-dark flex-1 resize-none"
            rows={1}
            style={{ maxHeight: '120px' }}
          />
          <button
            onClick={clearChat}
            className="btn-secondary px-3"
            title="清空对话"
          >
            <Trash2 className="w-4 h-4" />
          </button>
          <button
            onClick={sendMessage}
            disabled={!input.trim() || loading}
            className="btn-primary px-4 flex items-center gap-2 disabled:opacity-50"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </Container>
  )
}