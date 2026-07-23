import { useState } from 'react'
import { ChatMessage } from '../types'
import { EvidencePanel } from './EvidencePanel'

interface MessageBubbleProps {
  message: ChatMessage
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-3xl ${isUser ? 'ml-12' : 'mr-12'}`}>
        {/* Message bubble */}
        <div
          className={`rounded-lg px-4 py-3 ${
            isUser
              ? 'bg-indigo-600 text-white'
              : message.isError
              ? 'bg-red-900/50 border border-red-700 text-red-200'
              : 'bg-gray-800 border border-gray-700 text-gray-100'
          }`}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="answer-content whitespace-pre-wrap text-sm leading-relaxed">
              {message.content}
            </div>
          )}
        </div>

        {/* Metadata + Evidence (assistant only) */}
        {!isUser && !message.isError && (
          <div className="mt-1 flex items-center space-x-3 text-xs text-gray-500 pl-1">
            {message.model && <span className="text-gray-600">{message.model.replace('bedrock-claude-', '')}</span>}
            {message.durationMs && <span>{(message.durationMs / 1000).toFixed(1)}s</span>}
            {message.tokens && <span>{message.tokens.toLocaleString()} tokens</span>}
            {message.toolCalls && message.toolCalls.length > 0 && (
              <EvidencePanel toolCalls={message.toolCalls} />
            )}
          </div>
        )}
      </div>
    </div>
  )
}
