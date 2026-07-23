export function Header() {
  return (
    <header className="border-b border-gray-700 px-6 py-3 flex items-center justify-between bg-gray-800/50 backdrop-blur-sm">
      <div className="flex items-center space-x-3">
        <span className="text-2xl">🔮</span>
        <div>
          <h1 className="text-lg font-semibold text-white">Nexus AI</h1>
          <p className="text-xs text-gray-400">Enterprise Intelligence Agent</p>
        </div>
      </div>
      <div className="flex items-center space-x-3 text-xs text-gray-500">
        <span className="px-2 py-1 bg-gray-700 rounded">37 services</span>
        <span className="px-2 py-1 bg-gray-700 rounded">5 teams</span>
        <span className="px-2 py-1 bg-green-900/50 text-green-400 rounded">● Connected</span>
      </div>
    </header>
  )
}
