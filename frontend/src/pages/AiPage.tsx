import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Button } from '../components/ui/button'

const MCP_CONFIG = `{
  "mcpServers": {
    "wko5coach": {
      "command": "python",
      "args": ["-m", "backend.mcp_server"],
      "cwd": "${typeof window !== 'undefined' ? '' : ''}/path/to/WKO5reverse"
    }
  }
}`

const SAMPLE_QUESTIONS = [
  "本週我的 TSS 是多少？跟上週比較如何？",
  "最近 30 天的 CTL 趨勢？",
  "我的 FTP 估算是多少？過去 3 個月有變化嗎？",
  "昨天的訓練 NP 和 TSS 是多少？",
  "我最近訓練狀態（TSB）如何？現在適合高強度嗎？",
  "過去 4 週週均訓練量（TSS）是多少？",
  "幫我分析 5 分鐘最大功率的歷史趨勢",
]

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => {
        navigator.clipboard.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      }}
      className="h-6 w-6 shrink-0"
    >
      {copied ? <Check className="h-3 w-3 text-[#22c55e]" /> : <Copy className="h-3 w-3" />}
    </Button>
  )
}

export function AiPage() {
  return (
    <div className="p-5 max-w-3xl mx-auto space-y-4">
      <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">AI Integration</h1>

      <Card>
        <CardHeader>
          <CardTitle>Connect Claude Desktop via MCP</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-[#7d8fa6]">
            透過 MCP Server，讓 Claude Desktop 直接存取你的訓練資料庫。
            Claude 可以查詢 PMC 數據、活動記錄、MMP 曲線等。
          </p>

          <div>
            <div className="text-xs text-[#3e4e63] mb-2">1. 確認 MCP server 依賴已安裝</div>
            <div className="bg-[#07090f] border border-[#1c2333] rounded-md px-3 py-2 font-mono text-xs text-[#e8edf5]">
              pip install mcp
            </div>
          </div>

          <div>
            <div className="text-xs text-[#3e4e63] mb-2">
              2. 將以下設定加入 <code className="text-[#a78bfa]">~/Library/Application Support/Claude/claude_desktop_config.json</code>
            </div>
            <div className="relative">
              <pre className="bg-[#07090f] border border-[#1c2333] rounded-md px-3 py-3 text-xs text-[#e8edf5] overflow-x-auto">
                {MCP_CONFIG}
              </pre>
              <div className="absolute top-2 right-2">
                <CopyButton text={MCP_CONFIG} />
              </div>
            </div>
          </div>

          <div>
            <div className="text-xs text-[#3e4e63] mb-1">3. 重啟 Claude Desktop</div>
            <p className="text-xs text-[#7d8fa6]">
              重啟後在 Claude Desktop 的 MCP 工具列中看到 <code className="text-[#a78bfa]">wko5coach</code> 即表示連接成功。
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sample Questions</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {SAMPLE_QUESTIONS.map((q, i) => (
              <div
                key={i}
                className="flex items-center justify-between gap-3 bg-[#07090f] border border-[#131824] rounded-md px-3 py-2 group"
              >
                <span className="text-sm text-[#7d8fa6]">{q}</span>
                <CopyButton text={q} />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
