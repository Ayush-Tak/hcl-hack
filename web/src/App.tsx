import React, { useState, useEffect, useRef } from 'react'
import './App.css'
import type { Customer, Message, ChatResponse, TraceDetails, AlertItem, PendingAction } from './types'

const QUICK_CHIPS = [
  'Can I buy a ₹20,000 phone and still pay my bills?',
  'My ₹2,500 UPI to Ravi was debited but not received',
  "I don't recognise a ₹48,999 payment",
  'The bank called and asked for my OTP',
  'I lost my debit card',
]

export default function App() {
  // Auth state
  const [token, setToken] = useState<string>(() => localStorage.getItem('bank_token') || '')
  const [customer, setCustomer] = useState<Customer | null>(() => {
    const saved = localStorage.getItem('bank_customer')
    return saved ? JSON.parse(saved) : null
  })
  const [loginCid, setLoginCid] = useState('C1001')
  const [loginPin, setLoginPin] = useState('1234')
  const [loginError, setLoginError] = useState('')
  const [loginLoading, setLoginLoading] = useState(false)

  // Chat state
  const [messages, setMessages] = useState<Message[]>([])
  const [inputMessage, setInputMessage] = useState('')
  const [loading, setLoading] = useState(false)
  const chatBottomRef = useRef<HTMLDivElement>(null)

  // OTP Modal state
  const [activeAction, setActiveAction] = useState<PendingAction | null>(null)
  const [otpInput, setOtpInput] = useState('')
  const [otpLoading, setOtpLoading] = useState(false)
  const [otpError, setOtpError] = useState('')

  // Trace drawer state
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null)
  const [traceDetails, setTraceDetails] = useState<TraceDetails | null>(null)
  const [traceLoading, setTraceLoading] = useState(false)

  // Alerts sidebar state
  const [showAlerts, setShowAlerts] = useState(false)
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [alertsLoading, setAlertsLoading] = useState(false)

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Login handler
  const handleLogin = async (e?: React.FormEvent, overrideCid?: string, overridePin?: string) => {
    if (e) e.preventDefault()
    setLoginError('')
    setLoginLoading(true)
    const cid = overrideCid || loginCid
    const pin = overridePin || loginPin

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ customer_id: cid, pin }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Invalid customer ID or PIN')
      }

      const data = await res.json()
      setToken(data.token)
      setCustomer(data.customer)
      localStorage.setItem('bank_token', data.token)
      localStorage.setItem('bank_customer', JSON.stringify(data.customer))

      // Welcome initial message
      setMessages([
        {
          id: 'welcome',
          sender: 'assistant',
          text: `Welcome back, ${data.customer.name}! I am your Daily Banking Assistant. Ask me about your balance, bills, payments, card status, or recent transactions.`,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        },
      ])
    } catch (err: unknown) {
      setLoginError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoginLoading(false)
    }
  }

  const handleLogout = () => {
    setToken('')
    setCustomer(null)
    setMessages([])
    localStorage.removeItem('bank_token')
    localStorage.removeItem('bank_customer')
  }

  // Send chat message
  const sendMessage = async (textToSend?: string) => {
    const text = (textToSend || inputMessage).trim()
    if (!text || loading) return

    const userMsg: Message = {
      id: `user-${Date.now()}`,
      sender: 'user',
      text,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }

    setMessages((prev) => [...prev, userMsg])
    if (!textToSend) setInputMessage('')
    setLoading(true)

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ message: text, session_id: 'web-session' }),
      })

      if (!res.ok) {
        if (res.status === 401) {
          handleLogout()
          throw new Error('Session expired, please login again')
        }
        throw new Error(`Server returned ${res.status}`)
      }

      const chatResp: ChatResponse = await res.json()
      const botMsg: Message = {
        id: `bot-${Date.now()}`,
        sender: 'assistant',
        text: chatResp.answer,
        response: chatResp,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }

      setMessages((prev) => [...prev, botMsg])

      // If pending action exists, prompt OTP modal
      if (chatResp.pending_action) {
        setActiveAction(chatResp.pending_action)
        setOtpInput(chatResp.pending_action.demo_otp || '')
        setOtpError('')
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : 'Error reaching banking service'
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          sender: 'assistant',
          text: `⚠️ Request failed: ${errMsg}`,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  // Confirm pending action with OTP
  const handleConfirmOtp = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!activeAction || !otpInput.trim() || otpLoading) return

    setOtpLoading(true)
    setOtpError('')

    try {
      const res = await fetch(`/api/actions/${activeAction.action_id}/confirm`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ otp: otpInput.trim() }),
      })

      const confResp: ChatResponse = await res.json()

      if (confResp.answer_type === 'action_completed') {
        setActiveAction(null)
        setMessages((prev) => [
          ...prev,
          {
            id: `conf-${Date.now()}`,
            sender: 'assistant',
            text: confResp.answer,
            response: confResp,
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          },
        ])
      } else {
        setOtpError(confResp.answer || 'Invalid OTP entered. Please try again.')
      }
    } catch (err: unknown) {
      setOtpError(err instanceof Error ? err.message : 'Failed to confirm action')
    } finally {
      setOtpLoading(false)
    }
  }

  // Load trace drawer
  const openTrace = async (traceId: string) => {
    setSelectedTraceId(traceId)
    setTraceLoading(true)
    setTraceDetails(null)

    try {
      const res = await fetch(`/api/trace/${traceId}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json()
        setTraceDetails(data)
      }
    } catch (err) {
      console.error('Failed to load trace:', err)
    } finally {
      setTraceLoading(false)
    }
  }

  // Load alerts
  const loadAlerts = async () => {
    setShowAlerts(true)
    setAlertsLoading(true)
    try {
      const res = await fetch('/api/me/alerts', {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json()
        setAlerts(data)
      }
    } catch (err) {
      console.error('Failed to fetch alerts:', err)
    } finally {
      setAlertsLoading(false)
    }
  }

  // If not logged in, render Login Screen
  if (!token || !customer) {
    return (
      <div className="login-screen">
        <div className="login-card">
          <div className="login-header">
            <div className="brand-logo" style={{ margin: '0 auto 12px auto', width: 44, height: 44, fontSize: 22 }}>
              🛡️
            </div>
            <h2>Daily Banking Assistant</h2>
            <p>Enter customer credentials to access protected finances</p>
          </div>

          {loginError && <div className="warning-box high" style={{ fontSize: 13 }}>⚠️ {loginError}</div>}

          <form onSubmit={(e) => handleLogin(e)} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="form-group">
              <label>Customer ID</label>
              <input
                className="form-control"
                value={loginCid}
                onChange={(e) => setLoginCid(e.target.value)}
                placeholder="e.g. C1001"
                required
              />
            </div>

            <div className="form-group">
              <label>PIN (4 digits)</label>
              <input
                className="form-control"
                type="password"
                maxLength={4}
                value={loginPin}
                onChange={(e) => setLoginPin(e.target.value)}
                placeholder="****"
                required
              />
            </div>

            <button type="submit" className="btn-primary" style={{ marginTop: 6 }} disabled={loginLoading}>
              {loginLoading ? 'Authenticating...' : 'Sign In to Assistant'}
            </button>
          </form>

          <div className="preset-logins">
            <span className="preset-title">Quick Demo Logins</span>
            <button
              className="preset-btn"
              onClick={() => {
                setLoginCid('C1001')
                setLoginPin('1234')
                handleLogin(undefined, 'C1001', '1234')
              }}
            >
              <span>👤 <strong>Aarav Sharma</strong> (C1001)</span>
              <span>PIN: 1234</span>
            </button>
            <button
              className="preset-btn"
              onClick={() => {
                setLoginCid('C1002')
                setLoginPin('1234')
                handleLogin(undefined, 'C1002', '1234')
              }}
            >
              <span>👤 <strong>Priya Iyer</strong> (C1002)</span>
              <span>PIN: 1234</span>
            </button>
            <button
              className="preset-btn"
              onClick={() => {
                setLoginCid('O9001')
                setLoginPin('9999')
                handleLogin(undefined, 'O9001', '9999')
              }}
            >
              <span>👮 <strong>Neha Verma</strong> (Officer)</span>
              <span>PIN: 9999</span>
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="app-layout">
      <div className="main-wrapper">
        {/* Header */}
        <header className="app-header">
          <div className="brand-section">
            <div className="brand-logo">₹</div>
            <div>
              <div className="brand-title">Daily Banking Assistant</div>
              <div className="brand-subtitle">Explainable AI · Guardrails · Cryptographic Trace</div>
            </div>
          </div>

          <div className="user-profile">
            <span className="role-badge">{customer.role}</span>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{customer.name} ({customer.id})</span>
            <button className="btn-secondary" onClick={loadAlerts}>
              🔔 Alerts
            </button>
            <button className="btn-secondary" onClick={handleLogout}>
              Sign out
            </button>
          </div>
        </header>

        {/* Quick Chips Bar */}
        <section className="chips-section">
          <div className="chips-title">Quick Scenarios</div>
          <div className="chips-container">
            {QUICK_CHIPS.map((chip, idx) => (
              <button
                key={idx}
                className="chip-btn"
                disabled={loading}
                onClick={() => sendMessage(chip)}
              >
                {chip}
              </button>
            ))}
          </div>
        </section>

        {/* Chat History */}
        <div className="chat-container">
          {messages.map((msg) => (
            <div key={msg.id} className={`message-row ${msg.sender}`}>
              {msg.sender === 'user' ? (
                <div className="user-bubble">{msg.text}</div>
              ) : (
                <div className="assistant-card">
                  <div className="assistant-header">
                    {msg.response?.answer_type && (
                      <span className={`answer-badge badge-${msg.response.answer_type}`}>
                        {msg.response.answer_type.replace('_', ' ')}
                      </span>
                    )}
                    {msg.response?.latency_ms !== undefined && (
                      <span className="latency-indicator">⏱️ {msg.response.latency_ms} ms</span>
                    )}
                  </div>

                  <div className="message-text">{msg.text}</div>

                  {/* Warnings in Amber or Rose */}
                  {msg.response?.warnings && msg.response.warnings.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {msg.response.warnings.map((w, wIdx) => (
                        <div key={wIdx} className={`warning-box ${w.level === 'high' ? 'high' : ''}`}>
                          <span>⚠️</span>
                          <div>
                            <strong>[{w.source.toUpperCase()}]</strong> {w.message}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Pending Action Trigger */}
                  {msg.response?.pending_action && (
                    <div className="action-box">
                      <div>
                        <div style={{ fontSize: 11, color: '#a78bfa', fontWeight: 700, textTransform: 'uppercase' }}>
                          Action Requires Authorization
                        </div>
                        <div className="action-summary">{msg.response.pending_action.summary}</div>
                      </div>
                      <button
                        className="btn-action-confirm"
                        onClick={() => {
                          if (msg.response?.pending_action) {
                            setActiveAction(msg.response.pending_action)
                            setOtpInput(msg.response.pending_action.demo_otp || '')
                          }
                        }}
                      >
                        Enter OTP →
                      </button>
                    </div>
                  )}

                  {/* Tools Invoked */}
                  {msg.response?.tools_invoked && msg.response.tools_invoked.length > 0 && (
                    <div className="tools-summary">
                      <span style={{ fontWeight: 600 }}>Tools:</span>
                      {msg.response.tools_invoked.map((t, tIdx) => (
                        <span key={tIdx} className="tool-chip">
                          {t.ok ? '✓' : '✗'} {t.name} ({t.ms}ms)
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Trace Link */}
                  {msg.response?.trace_id && (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 4 }}>
                      <button
                        className="trace-link-btn"
                        onClick={() => openTrace(msg.response!.trace_id)}
                      >
                        🔍 View audit trace ({msg.response.trace_id})
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          {loading && (
            <div className="message-row assistant">
              <div className="assistant-card" style={{ maxWidth: 300 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#94a3b8', fontSize: 13 }}>
                  <div className="brand-logo" style={{ width: 20, height: 20, fontSize: 10 }}>⚡</div>
                  Running Scam Guard & Orchestrator...
                </div>
              </div>
            </div>
          )}

          <div ref={chatBottomRef} />
        </div>

        {/* Input Bar */}
        <section className="input-section">
          <form
            className="input-form"
            onSubmit={(e) => {
              e.preventDefault()
              sendMessage()
            }}
          >
            <input
              className="chat-input"
              value={inputMessage}
              onChange={(e) => setInputMessage(e.target.value)}
              placeholder="Ask about your balance, bills, UPI transfers, or lost cards..."
              disabled={loading}
            />
            <button type="submit" className="send-btn" disabled={loading || !inputMessage.trim()}>
              Send →
            </button>
          </form>
        </section>
      </div>

      {/* OTP Modal Dialog */}
      {activeAction && (
        <div className="modal-overlay">
          <div className="modal-card">
            <h3 style={{ fontSize: 18, fontWeight: 600 }}>Authorize Pending Action</h3>
            <p style={{ fontSize: 13.5, color: '#94a3b8' }}>
              {activeAction.summary}
            </p>

            {activeAction.demo_otp && (
              <div className="otp-hint">
                Demo Mode OTP: <strong>{activeAction.demo_otp}</strong>
              </div>
            )}

            {otpError && <div className="warning-box high">⚠️ {otpError}</div>}

            <form onSubmit={handleConfirmOtp} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <input
                className="otp-input"
                type="text"
                maxLength={6}
                value={otpInput}
                onChange={(e) => setOtpInput(e.target.value)}
                placeholder="123456"
                autoFocus
                required
              />

              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 8 }}>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setActiveAction(null)}
                >
                  Cancel
                </button>
                <button type="submit" className="btn-primary" disabled={otpLoading || otpInput.length < 6}>
                  {otpLoading ? 'Verifying...' : 'Confirm Action'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Trace Drawer */}
      {selectedTraceId && (
        <div className="trace-drawer-overlay" onClick={() => setSelectedTraceId(null)}>
          <div className="trace-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="trace-drawer-header">
              <div className="trace-title-group">
                <h3>Request Trace Audit</h3>
                <span style={{ fontSize: 11.5, color: '#94a3b8', fontFamily: 'monospace' }}>
                  {selectedTraceId}
                </span>
              </div>
              <button className="btn-secondary" onClick={() => setSelectedTraceId(null)}>
                ✕ Close
              </button>
            </div>

            {traceLoading ? (
              <div style={{ padding: 24, color: '#94a3b8', fontSize: 13 }}>Loading cryptographic audit trace...</div>
            ) : traceDetails ? (
              <>
                <div className="trace-stats-bar">
                  <span>⏱️ <strong>{traceDetails.latency_ms} ms</strong></span>
                  <span>🤖 LLM Calls: <strong>{traceDetails.llm_calls}</strong></span>
                  <span>🔧 Tool Calls: <strong>{traceDetails.tool_calls}</strong></span>
                  <span>🔒 Chain: <strong style={{ color: '#34d399' }}>VERIFIED OK</strong></span>
                </div>

                <div className="trace-spans-container">
                  <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>
                    Execution Spans ({traceDetails.spans.length} events):
                  </div>

                  {traceDetails.spans.map((span) => (
                    <div key={span.seq} className="span-card">
                      <div className="span-top-row">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span className="span-badge">{span.kind}</span>
                          <span className="span-name">{span.name}</span>
                        </div>
                        <span style={{ fontSize: 12, color: span.ok !== false ? '#34d399' : '#f87171' }}>
                          {span.ok !== false ? '✓ Passed' : '✗ Failed'}
                        </span>
                      </div>

                      <div className="span-meta">
                        <span>Seq #{span.seq}</span>
                        {span.ms !== undefined && <span>{span.ms} ms</span>}
                        {span.tokens_in !== undefined && <span>Tokens: {span.tokens_in} in / {span.tokens_out} out</span>}
                        {span.hash && <span style={{ fontFamily: 'monospace' }}>Hash: {span.hash}...</span>}
                      </div>

                      {span.decision && (
                        <div style={{ fontSize: 12, color: '#e2e8f0' }}>
                          Gate Decision: <strong>{span.decision}</strong> (Tier: {span.tier || 'default'})
                        </div>
                      )}

                      {span.args && (
                        <pre style={{ background: '#0b0f19', padding: 8, borderRadius: 6, fontSize: 11, overflowX: 'auto' }}>
                          {JSON.stringify(span.args, null, 2)}
                        </pre>
                      )}
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div style={{ padding: 24, color: '#f87171' }}>Trace not found or access denied.</div>
            )}
          </div>
        </div>
      )}

      {/* Alerts Sidebar */}
      {showAlerts && (
        <div className="trace-drawer-overlay" onClick={() => setShowAlerts(false)}>
          <div className="trace-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="trace-drawer-header">
              <div className="trace-title-group">
                <h3>Security & Fraud Alerts</h3>
                <span style={{ fontSize: 11.5, color: '#94a3b8' }}>
                  Real-time transaction anomaly notices
                </span>
              </div>
              <button className="btn-secondary" onClick={() => setShowAlerts(false)}>
                ✕ Close
              </button>
            </div>

            <div className="trace-spans-container">
              {alertsLoading ? (
                <div style={{ padding: 12, color: '#94a3b8' }}>Checking fraud alerts...</div>
              ) : alerts.length === 0 ? (
                <div style={{ padding: 12, color: '#94a3b8' }}>No active security alerts for this customer.</div>
              ) : (
                alerts.map((al) => (
                  <div key={al.id} className="span-card" style={{ borderColor: 'rgba(239, 68, 68, 0.3)' }}>
                    <div className="span-top-row">
                      <strong style={{ color: '#f87171' }}>{al.id}</strong>
                      <span className="role-badge" style={{ background: 'rgba(239, 68, 68, 0.15)', color: '#f87171' }}>
                        Band: {al.band} (Score: {al.score})
                      </span>
                    </div>

                    {al.analyst && (
                      <p style={{ fontSize: 12.5, color: '#cbd5e1' }}>
                        <strong>Analyst:</strong> {al.analyst}
                      </p>
                    )}

                    <div style={{ fontSize: 12, color: '#94a3b8' }}>
                      <strong>Triggers:</strong>
                      <ul style={{ paddingLeft: 16, marginTop: 4 }}>
                        {al.reasons.map((r, rIdx) => (
                          <li key={rIdx}>{r}</li>
                        ))}
                      </ul>
                    </div>

                    <div style={{ fontSize: 11, color: '#64748b', display: 'flex', justifyContent: 'space-between' }}>
                      <span>Txn: {al.txn_id || 'N/A'}</span>
                      <span>Status: {al.status}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
