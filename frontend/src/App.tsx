import { useState, useRef, useEffect } from 'react'
import { ChatMessage, ToolCall } from './types'
import { MessageBubble } from './components/MessageBubble'
import { ChatInput } from './components/ChatInput'
import { Header } from './components/Header'

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async (text: string) => {
    const userMessage: ChatMessage = { role: 'user', content: text }
    setMessages(prev => [...prev, userMessage])
    setIsLoading(true)

    try {
      // Build conversation history from prior messages for multi-turn context
      const history = messages.map(msg => ({
        role: msg.role,
        content: msg.content,
      }))

      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: text,
          conversation_history: history.length > 0 ? history : undefined,
          include_evidence: true,
        }),
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }

      const data = await response.json()

      const assistantMessage: ChatMessage = {
        role: 'assistant',
        content: data.answer,
        toolCalls: data.tool_calls || [],
        model: data.model,
        tokens: data.total_tokens,
        durationMs: data.duration_ms,
      }

      setMessages(prev => [...prev, assistantMessage])
    } catch (error) {
      const errorMessage: ChatMessage = {
        role: 'assistant',
        content: `Error: ${error instanceof Error ? error.message : 'Failed to get response'}`,
        isError: true,
      }
      setMessages(prev => [...prev, errorMessage])
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-screen bg-gray-900">
      <Header />
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-gray-500 space-y-4">
            <div className="text-5xl">🔮</div>
            <h2 className="text-xl font-medium text-gray-300">Ask the enterprise anything</h2>
            <div className="text-sm text-gray-500 max-w-md text-center space-y-1">
              <p>"Who owns the Order Service?"</p>
              <p>"What APIs does Servicing Account expose?"</p>
              <p>"How does Real-Time Rates work?"</p>
              <p>"What services does Platform Engineering own?"</p>
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}
        {isLoading && (
          <div className="flex items-center space-x-2 text-gray-400 pl-4">
            <div className="flex space-x-1">
              <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
              <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
              <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
            </div>
            <span className="text-sm">Thinking...</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <ChatInput onSend={sendMessage} disabled={isLoading} />
    </div>
  )
}

export default App
