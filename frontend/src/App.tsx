import { useState, useEffect, useRef, useCallback } from 'react'

// ── Types ────────────────────────────────────

interface FuelLevels {
  diesel: number
  petrol: number
  octane: number
}

interface Station {
  id: string
  name: string
  region: string
  profile: string
  inventory: FuelLevels
  capacity: FuelLevels
  demand_multiplier: number
  fill_pct: { diesel: number; petrol: number; octane: number }
}

interface Depot {
  id: string
  name: string
  region: string
  inventory: FuelLevels
  capacity: FuelLevels
  dispatch_rate: number
  headroom: FuelLevels
}

interface Route {
  id: string
  from_depot: string
  to_station: string
  travel_ticks: number
  max_load: number
  status: string
  is_cross_region: boolean
}

interface RiskScore {
  station_id: string
  fuel_type: string
  score: number
  level: string
  hours_until_empty: number
  contributing_factors: string[]
}

interface Decision {
  id: string
  tick: number
  type: string
  depot_id: string
  station_id: string
  fuel_type: string
  amount: number
  route_id: string
  risk_score: number
  hours_until_empty: number
  trigger_reason: string
  explanation: string
  confidence: number
  created_at: string
}

interface CrisisEvent {
  id: string
  type: string
  target: string
  start_tick: number
  end_tick: number | null
  severity: string
  description: string
}

interface AppState {
  tick: number
  sim_time: string | null
  status: string
  speed: number
  service_level: number
  operating_mode: string
  depots: Record<string, Depot>
  stations: Record<string, Station>
  routes: Record<string, Route>
  risk_scores: Record<string, RiskScore>
  pending_decisions: Decision[]
  allocations: any[]
  active_events: CrisisEvent[]
}

// ── Helpers ──────────────────────────────────

function getRiskLevel(station_id: string, risk_scores: Record<string, RiskScore>): string {
  let worst = 'ok'
  const order = ['ok', 'watch', 'high', 'critical']
  for (const ft of ['diesel', 'petrol', 'octane']) {
    const key = `${station_id}:${ft}`
    const rs = risk_scores[key]
    if (rs && order.indexOf(rs.level) > order.indexOf(worst)) {
      worst = rs.level
    }
  }
  return worst
}

function gaugeClass(pct: number): string {
  if (pct >= 60) return 'high'
  if (pct >= 35) return 'medium'
  if (pct >= 15) return 'low'
  return 'critical'
}

function shortId(id: string): string {
  return id.replace('station-', '').replace('depot-', '').replace('route-', '')
}

const API = '/api'

// ── Components ───────────────────────────────

function FuelGauge({ label, pct }: { label: string; pct: number }) {
  return (
    <div className="fuel-gauge">
      <span className="fuel-label">{label}</span>
      <div className="gauge-track">
        <div
          className={`gauge-fill ${gaugeClass(pct)}`}
          style={{ width: `${Math.min(100, Math.max(1, pct))}%` }}
        />
      </div>
      <span className="gauge-pct" style={{ color: pct < 20 ? 'var(--accent-red)' : 'var(--text-secondary)' }}>
        {pct.toFixed(0)}%
      </span>
    </div>
  )
}

function StationCard({ station, risk }: { station: Station; risk: string }) {
  return (
    <div className="station-card fade-in" data-risk={risk}>
      <div className="station-header">
        <div>
          <div className="station-name">{shortId(station.id)}</div>
          <div className="station-region">{station.region} · {station.profile}</div>
        </div>
        <span className={`risk-badge ${risk}`}>{risk}</span>
      </div>
      <div className="fuel-gauges">
        <FuelGauge label="D" pct={station.fill_pct.diesel} />
        <FuelGauge label="P" pct={station.fill_pct.petrol} />
        <FuelGauge label="O" pct={station.fill_pct.octane} />
      </div>
      {station.demand_multiplier > 1.1 && (
        <div style={{ marginTop: 8, fontSize: 10, color: 'var(--accent-yellow)' }}>
          ⚡ Demand ×{station.demand_multiplier.toFixed(1)}
        </div>
      )}
    </div>
  )
}

function DepotCard({ depot }: { depot: Depot }) {
  const pct = (fuel: string) => {
    const inv = (depot.inventory as any)[fuel] || 0
    const cap = (depot.capacity as any)[fuel] || 1
    return (inv / cap) * 100
  }
  return (
    <div className="depot-card fade-in">
      <div className="depot-name">🛢️ {shortId(depot.id)} <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>({depot.region})</span></div>
      <div className="fuel-gauges">
        <FuelGauge label="D" pct={pct('diesel')} />
        <FuelGauge label="P" pct={pct('petrol')} />
        <FuelGauge label="O" pct={pct('octane')} />
      </div>
      <div style={{ marginTop: 6, fontSize: 10, color: 'var(--text-muted)' }}>
        Dispatch rate: {depot.dispatch_rate?.toLocaleString()}L/tick
      </div>
    </div>
  )
}

function DecisionCard({ decision, onApprove, onReject }: {
  decision: Decision
  onApprove: (id: string) => void
  onReject: (id: string) => void
}) {
  return (
    <div className="decision-item slide-in">
      <div className="decision-header">
        <span className="decision-title">
          ⬆ {decision.amount?.toLocaleString()}L {decision.fuel_type}
        </span>
        <span className="decision-status pending">Pending</span>
      </div>
      <div className="decision-detail">
        {shortId(decision.depot_id)} → {shortId(decision.station_id)}<br />
        Risk: {(decision.risk_score * 100).toFixed(0)}% · {decision.hours_until_empty?.toFixed(1)}h left<br />
        <span style={{ color: 'var(--text-muted)' }}>{decision.explanation}</span>
      </div>
      <div className="decision-actions">
        <button className="btn btn-approve" onClick={() => onApprove(decision.id)}>✓ Approve</button>
        <button className="btn btn-reject" onClick={() => onReject(decision.id)}>✕ Reject</button>
      </div>
    </div>
  )
}

function EventItem({ event }: { event: CrisisEvent }) {
  const severityClass = event.severity === 'critical' ? 'critical' : event.severity === 'high' ? 'warning' : 'info'
  return (
    <div className={`alert-item ${severityClass}`}>
      <strong>{event.type.replace('_', ' ')}</strong> — {event.target}<br />
      <span style={{ fontSize: 11 }}>
        Tick {event.start_tick}{event.end_tick ? ` → ${event.end_tick}` : ' (ongoing)'}
      </span>
    </div>
  )
}

// ── Main App ─────────────────────────────────

export default function App() {
  const [state, setState] = useState<AppState | null>(null)
  const [connected, setConnected] = useState(false)
  const [history, setHistory] = useState<any[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  // WebSocket connection
  useEffect(() => {
    function connect() {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${protocol}//${window.location.host}/ws`
      const ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        setConnected(true)
        console.log('WebSocket connected')
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'pong') return
          setState(data)
        } catch (e) {
          console.error('Failed to parse WS message', e)
        }
      }

      ws.onclose = () => {
        setConnected(false)
        console.log('WebSocket disconnected, reconnecting in 3s...')
        setTimeout(connect, 3000)
      }

      ws.onerror = () => ws.close()
      wsRef.current = ws
    }

    connect()

    // Also fetch initial state via REST
    fetch(`${API}/state`).then(r => r.json()).then(setState).catch(() => {})
    fetch(`${API}/decisions/history?limit=20`).then(r => r.json()).then(d => setHistory(d.decisions || [])).catch(() => {})

    return () => { wsRef.current?.close() }
  }, [])

  // Periodic history refresh
  useEffect(() => {
    const interval = setInterval(() => {
      fetch(`${API}/decisions/history?limit=20`).then(r => r.json()).then(d => setHistory(d.decisions || [])).catch(() => {})
    }, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleApprove = useCallback(async (id: string) => {
    await fetch(`${API}/decisions/${id}/approve`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ by: 'operator' })
    })
  }, [])

  const handleReject = useCallback(async (id: string) => {
    await fetch(`${API}/decisions/${id}/reject`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'Operator rejected', by: 'operator' })
    })
  }, [])

  const handleSimControl = useCallback(async (action: string) => {
    await fetch(`${API}/simulator/${action}`, { method: 'POST' })
  }, [])

  if (!state) {
    return (
      <div className="app" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ textAlign: 'center' }}>
          <div className="header-logo" style={{ fontSize: 32, marginBottom: 12 }}>🛢️ Jalani Control Tower</div>
          <div style={{ color: 'var(--text-muted)' }}>Connecting to backend...</div>
        </div>
      </div>
    )
  }

  const stations = Object.values(state.stations || {})
  const depots = Object.values(state.depots || {})
  const risks = state.risk_scores || {}
  const pending = state.pending_decisions || []
  const events = state.active_events || []

  return (
    <div className="app">
      {/* ── Header ─────────────────────────── */}
      <header className="header">
        <div className="header-left">
          <span className="header-logo">🛢️ Jalani Control Tower</span>
          <span className={`mode-badge ${state.operating_mode}`}>{state.operating_mode}</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary" onClick={() => handleSimControl('start')} style={{ padding: '4px 10px', fontSize: 11 }}>▶ Start</button>
            <button className="btn btn-reject" onClick={() => handleSimControl('pause')} style={{ padding: '4px 10px', fontSize: 11 }}>⏸ Pause</button>
          </div>
        </div>
        <div className="header-right">
          <div className="header-stat">
            <span className="header-stat-label">Tick</span>
            <span className="header-stat-value">{state.tick}</span>
          </div>
          <div className="header-stat">
            <span className="header-stat-label">Service Level</span>
            <span className="header-stat-value" style={{ color: state.service_level < 85 ? 'var(--accent-red)' : state.service_level < 95 ? 'var(--accent-yellow)' : 'var(--accent-green)' }}>
              {state.service_level?.toFixed(1)}%
            </span>
          </div>
          <div className="header-stat">
            <span className="header-stat-label">Status</span>
            <span className="header-stat-value" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className={`status-dot ${state.status === 'running' ? 'healthy' : 'degraded'}`} />
              {state.status}
            </span>
          </div>
        </div>
      </header>

      {/* ── Main Content ───────────────────── */}
      <main className="main-content">

        {/* Column 1: Stations + Depots */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, overflow: 'hidden' }}>
          <div className="card" style={{ flex: 2 }}>
            <div className="card-title">Fuel Stations</div>
            <div className="stations-grid">
              {stations.map(s => (
                <StationCard key={s.id} station={s} risk={getRiskLevel(s.id, risks)} />
              ))}
            </div>
          </div>
          <div className="card" style={{ flex: 1 }}>
            <div className="card-title">Fuel Depots</div>
            {depots.map(d => <DepotCard key={d.id} depot={d} />)}
          </div>
        </div>

        {/* Column 2: Routes + Events + History */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, overflow: 'hidden' }}>
          <div className="card" style={{ flex: 1 }}>
            <div className="card-title">Active Events ({events.length})</div>
            {events.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>No active events</div>
            ) : (
              events.map((e, i) => <EventItem key={e.id || i} event={e} />)
            )}
          </div>
          <div className="card" style={{ flex: 1 }}>
            <div className="card-title">Routes</div>
            {Object.values(state.routes || {}).map((r: Route) => (
              <div key={r.id} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 12,
              }}>
                <span>{shortId(r.from_depot)} → {shortId(r.to_station)}</span>
                <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ color: 'var(--text-muted)' }}>{r.travel_ticks}t · {r.max_load?.toLocaleString()}L</span>
                  <span className={`status-dot ${r.status === 'available' ? 'healthy' : 'unhealthy'}`} />
                </span>
              </div>
            ))}
          </div>
          <div className="card" style={{ flex: 1 }}>
            <div className="card-title">Decision History</div>
            {history.length === 0 ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)' }}>
                No history available
              </div>
            ) : (
              history.slice(0, 8).map((d: any, i: number) => (
                <div key={d.decision_id || i} style={{
                  padding: '6px 0', borderBottom: '1px solid var(--border)', fontSize: 11,
                  display: 'flex', justifyContent: 'space-between',
                }}>
                  <span>{d.amount_liters?.toLocaleString()}L {d.fuel_type} → {shortId(d.station_id || '')}</span>
                  <span className={`decision-status ${d.status}`}>{d.status}</span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Column 3: Decisions + Alerts */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, overflow: 'hidden' }}>
          <div className="card" style={{ flex: 1 }}>
            <div className="card-title">Pending Decisions ({pending.length})</div>
            {pending.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: 12, textAlign: 'center', padding: 20 }}>
                No decisions awaiting approval
              </div>
            ) : (
              pending.map(d => (
                <DecisionCard key={d.id} decision={d} onApprove={handleApprove} onReject={handleReject} />
              ))
            )}
          </div>
          <div className="card" style={{ flex: 1 }}>
            <div className="card-title">Risk Scores</div>
            {Object.values(risks).sort((a: any, b: any) => b.score - a.score).slice(0, 8).map((rs: any, i: number) => (
              <div key={i} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '5px 0', borderBottom: '1px solid var(--border)', fontSize: 11
              }}>
                <span>{shortId(rs.station_id)} · {rs.fuel_type}</span>
                <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ color: 'var(--text-muted)' }}>{rs.hours_until_empty?.toFixed(1)}h</span>
                  <span className={`risk-badge ${rs.level}`}>{(rs.score * 100).toFixed(0)}%</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      </main>

      {/* ── Footer ─────────────────────────── */}
      <footer className="footer">
        <div className="footer-stats">
          <div className="footer-stat">
            <span className={`connection-status ${connected ? 'connected' : 'disconnected'}`}>
              ● {connected ? 'Live' : 'Disconnected'}
            </span>
          </div>
          <div className="footer-stat">Tick: <strong>{state.tick}</strong></div>
          <div className="footer-stat">SL: <strong>{state.service_level?.toFixed(1)}%</strong></div>
          <div className="footer-stat">Mode: <strong>{state.operating_mode}</strong></div>
          <div className="footer-stat">Pending: <strong>{pending.length}</strong></div>
        </div>
        <div style={{ color: 'var(--text-muted)' }}>
          Jalani Control Tower · BUP CSE Fest 2026
        </div>
      </footer>
    </div>
  )
}
