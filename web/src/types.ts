export interface Customer {
  id: string
  name: string
  role: string
}

export interface Citation {
  doc: string
  section: string
  version: string
  effective_date: string
}

export interface PendingAction {
  action_id: string
  type: string
  summary: string
  requires_otp: boolean
  expires_at: string
  demo_otp?: string
}

export interface WarningItem {
  source: string
  level: 'info' | 'high'
  message: string
}

export interface ToolInvocation {
  name: string
  ok: boolean
  ms: number
}

export interface ChatResponse {
  trace_id: string
  answer: string
  answer_type: string
  data: Record<string, unknown> | null
  citations: Citation[]
  pending_action: PendingAction | null
  warnings: WarningItem[]
  tools_invoked: ToolInvocation[]
  trace_url: string
  latency_ms: number
}

export interface Message {
  id: string
  sender: 'user' | 'assistant'
  text: string
  response?: ChatResponse
  timestamp: string
}

export interface TraceSpan {
  seq: number
  ts: string
  kind: string
  name: string
  ms?: number
  tokens_in?: number
  tokens_out?: number
  ok?: boolean
  hash?: string
  decision?: string
  tier?: string
  error?: string
  args?: Record<string, unknown>
  result?: Record<string, unknown>
  model?: string
}

export interface TraceDetails {
  trace_id: string
  customer_id: string
  ts: string
  message: string
  answer: string
  answer_type: string
  mode: string
  latency_ms: number
  llm_calls: number
  tool_calls: number
  tokens_in: number
  tokens_out: number
  spans: TraceSpan[]
  chain_ok: boolean
}

export interface AlertItem {
  id: string
  customer_id: string
  txn_id?: string
  score: number
  band: string
  reasons: string[]
  analyst?: string
  status: string
  created_at: string
  trace_id?: string
}
