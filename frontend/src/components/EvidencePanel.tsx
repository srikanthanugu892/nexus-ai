import { useState } from 'react'
import { ToolCall } from '../types'

interface EvidencePanelProps {
  toolCalls: ToolCall[]
}

export function EvidencePanel({ toolCalls }: EvidencePanelProps) {
  const [isOpen, setIsOpen] = useState(false)

  if (toolCalls.length === 0) return null

  return (
    <div className="inline-block">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="text-indigo-400 hover:text-indigo-300 transition-colors flex items-center space-x-1"
      >
        <span>{isOpen ? '▼' : '▶'}</span>
        <span>{toolCalls.length} tool call{toolCalls.length > 1 ? 's' : ''}</span>
      </button>

      {isOpen && (
        <div className="mt-2 ml-0 space-y-2">
          {toolCalls.map((tc, i) => (
            <div key={i} className="bg-gray-900 border border-gray-700 rounded-md p-3 text-xs">
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-indigo-300 font-medium">{tc.tool}</span>
                <span className="text-gray-500">{tc.duration_ms}ms</span>
              </div>
              <div className="mt-1 text-gray-400">
                <span className="text-gray-500">Input: </span>
                <code className="text-gray-300">{JSON.stringify(tc.input)}</code>
              </div>
              <div className="mt-1 text-gray-400">
                <span className="text-gray-500">Output: </span>
                <pre className="mt-1 text-gray-300 whitespace-pre-wrap max-h-40 overflow-y-auto bg-gray-950 p-2 rounded">
                  {JSON.stringify(tc.output, null, 2)}
                </pre>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
