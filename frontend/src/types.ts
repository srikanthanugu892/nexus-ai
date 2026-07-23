export interface ToolCall {
  tool: string
  input: Record<string, unknown>
  output: Record<string, unknown>
  duration_ms: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  toolCalls?: ToolCall[]
  model?: string
  tokens?: number
  durationMs?: number
  isError?: boolean
}
