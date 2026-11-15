import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ACCOUNT_KIND_LABEL } from '../api'
import type { AccountKind, FundAllocation } from '../api'
import { useAuth, useBusinessDay } from '../auth'
import { Card, Empty, ErrorNote } from '../components/ui'
import { useLoad } from '../components/hooks'
import { addDays, dayLabel, peso, pesoExact } from '../time'

interface DayMoney {
  date: string
  money_in: string
  money_out: string
}

interface Dashboard {
  date_from: string
  date_to: string
  total_balance: string
  total_available: string
  total_allocated: string
  unverified: string
  accounts: {
    id: string
    name: string
    kind: AccountKind
    branch_name: string
    balance: string
    allocated: string
    available: string
  }[]
  sales: string
  other_income: string
  expenses: string
  net: string
  days: DayMoney[]
  expenses_by_category: { name: string; amount: string }[]
  overdue_count: number
  overdue_amount: string
  due_next_7_count: number
  due_next_7_amount: string
  allocations: FundAllocation[]
}

type Range = 'month' | 'last_month' | '30' | '90'

function rangeDates(range: Range, today: string): [string, string] {
  const first = `${today.slice(0, 8)}01`
  if (range === 'month') return [first, today]
  if (range === 'last_month') {
    const end = addDays(first, -1)
    return [`${end.slice(0, 8)}01`, end]
  }
  return [addDays(today, -(Number(range) - 1)), today]
}

function Tile({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: 'neg' }) {
  return (
    <div className="fund-card stat-tile">
      <span className="muted small">{label}</span>
      <span className={`fund-amount ${tone ?? ''}`}>{value}</span>
      {note && <span className="muted small">{note}</span>}
    </div>
  )
}

/** Money in vs out per day: two thin bars per day, one shared axis, hover for values. */
function DailyChart({ days }: { days: DayMoney[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(1, ...days.flatMap((d) => [Number(d.money_in), Number(d.money_out)]))
  const W = 760
  const H = 220
  const pad = { l: 56, r: 8, t: 8, b: 28 }
  const plotW = W - pad.l - pad.r
  const plotH = H - pad.t - pad.b
  const slot = plotW / days.length
  const bar = Math.max(2, Math.min(14, (slot - 6) / 2))
  const y = (v: number) => pad.t + plotH - (v / max) * plotH
  const ticks = [0, 0.5, 1].map((f) => f * max)
  const every = Math.ceil(days.length / 8)
  const h = hover !== null ? days[hover] : null

  return (
    <div className="chart-wrap">
      <div className="chart-legend" aria-hidden="true">
        <span>
          <i className="sw sw-in" /> Money in
        </span>
        <span>
          <i className="sw sw-out" /> Money out
        </span>
      </div>
      <div className="chart-scroll">
      <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Money in and out per day" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" className="axis">
              {peso(t)}
            </text>
          </g>
        ))}
        {days.map((d, i) => {
          const x = pad.l + i * slot + slot / 2
          const vin = Number(d.money_in)
          const vout = Number(d.money_out)
          return (
            <g key={d.date}>
              {hover === i && <rect x={pad.l + i * slot} y={pad.t} width={slot} height={plotH} className="hover-band" />}
              {vin > 0 && (
                <path d={barPath(x - bar - 1, y(vin), bar, y(0) - y(vin))} className="bar-in" />
              )}
              {vout > 0 && <path d={barPath(x + 1, y(vout), bar, y(0) - y(vout))} className="bar-out" />}
              {i % every === 0 && (
                <text x={x} y={H - 8} textAnchor="middle" className="axis">
                  {Number(d.date.slice(8))}
                </text>
              )}
              <rect
                x={pad.l + i * slot}
                y={pad.t}
                width={slot}
                height={plotH}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                tabIndex={0}
                aria-label={`${dayLabel(d.date)}: in ${pesoExact(d.money_in)}, out ${pesoExact(d.money_out)}`}
              />
            </g>
          )
        })}
      </svg>
      </div>
      {h && (
        <div className="chart-tip" role="status">
          <strong>{dayLabel(h.date)}</strong>
          <span>
            <i className="sw sw-in" /> In {pesoExact(h.money_in)}
          </span>
          <span>
            <i className="sw sw-out" /> Out {pesoExact(h.money_out)}
          </span>
        </div>
      )}
      <details>
        <summary className="muted small">Show as a table</summary>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Day</th>
                <th className="num">In</th>
                <th className="num">Out</th>
              </tr>
            </thead>
            <tbody>
              {days.map((d) => (
                <tr key={d.date}>
                  <td>{dayLabel(d.date)}</td>
                  <td className="num">{pesoExact(d.money_in)}</td>
                  <td className="num">{pesoExact(d.money_out)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  )
}

/** A bar with 4px rounded top corners, square at the baseline. */
function barPath(x: number, top: number, w: number, h: number): string {
  if (h <= 0) return ''
  const r = Math.min(4, w / 2, h)
  return `M${x},${top + h} V${top + r} Q${x},${top} ${x + r},${top} H${x + w - r} Q${x + w},${top} ${x + w},${top + r} V${top + h} Z`
}

function CategoryBars({ rows }: { rows: { name: string; amount: string }[] }) {
  const max = Math.max(1, ...rows.map((r) => Number(r.amount)))
  return (
    <ul className="cat-bars">
      {rows.map((r) => (
        <li key={r.name} title={`${r.name}: ${pesoExact(r.amount)}`}>
          <span className="cat-name">{r.name}</span>
          <span className="cat-track">
            <span className="cat-fill" style={{ width: `${(Number(r.amount) / max) * 100}%` }} />
          </span>
          <span className="num">{pesoExact(r.amount)}</span>
        </li>
      ))}
    </ul>
  )
}

export default function AccountingDashboard() {
  const { me } = useAuth()
  const business = useBusinessDay()
  const today = business.today()
  const [range, setRange] = useState<Range>('month')
  const [branchId, setBranchId] = useState('')
  const [from, to] = rangeDates(range, today)
  const { data, error } = useLoad(() => {
    const q = new URLSearchParams({ date_from: from, date_to: to })
    if (branchId) q.set('branch_id', branchId)
    return api<Dashboard>(`/fund-dashboard?${q}`)
  }, [from, to, branchId])
  const branches = me?.branches ?? []

  return (
    <div className="stack accounting-page">
      <div className="board-toolbar">
        <h1>Accounting dashboard</h1>
        <div className="toolbar">
          <div className="segmented-buttons" role="group" aria-label="Period">
            {(
              [
                ['month', 'This month'],
                ['last_month', 'Last month'],
                ['30', '30 days'],
                ['90', '90 days'],
              ] as [Range, string][]
            ).map(([v, label]) => (
              <button key={v} className={range === v ? 'primary' : ''} aria-pressed={range === v} onClick={() => setRange(v)}>
                {label}
              </button>
            ))}
          </div>
          {branches.length > 1 && (
            <select aria-label="Branch" value={branchId} onChange={(e) => setBranchId(e.target.value)}>
              <option value="">All branches</option>
              {branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>
      <ErrorNote message={error} />
      {data && (
        <>
          <div className="fund-grid">
            <Tile
              label="Money now"
              value={pesoExact(data.total_balance)}
              note={Number(data.unverified) > 0 ? `${pesoExact(data.unverified)} not verified yet` : 'All accounts'}
            />
            <Tile label="Free to spend" value={pesoExact(data.total_available)} note={`${pesoExact(data.total_allocated)} set aside`} />
            <Tile label="Sales" value={pesoExact(data.sales)} note={`${dayLabel(data.date_from)} – ${dayLabel(data.date_to)}`} />
            <Tile
              label="Expenses"
              value={pesoExact(data.expenses)}
              note={Number(data.other_income) > 0 ? `Other income ${pesoExact(data.other_income)}` : undefined}
            />
            <Tile label="Net" value={pesoExact(data.net)} tone={Number(data.net) < 0 ? 'neg' : undefined} note="Money in minus money out" />
            <Link to="/accounting" className="fund-card stat-tile tile-link">
              <span className="muted small">Bills due</span>
              <span className={`fund-amount ${data.overdue_count > 0 ? 'neg' : ''}`}>
                {data.overdue_count > 0 ? `${data.overdue_count} overdue` : 'None overdue'}
              </span>
              <span className="muted small">
                {data.overdue_count > 0 && `${pesoExact(data.overdue_amount)} · `}
                {data.due_next_7_count} due in the next 7 days ({pesoExact(data.due_next_7_amount)})
              </span>
            </Link>
          </div>

          <Card title="Money in and out per day">
            {data.days.every((d) => Number(d.money_in) === 0 && Number(d.money_out) === 0) ? (
              <Empty>No money in or out in this period.</Empty>
            ) : (
              <DailyChart days={data.days} />
            )}
            <p className="muted small">Transfers between your own accounts are not counted.</p>
          </Card>

          <div className="dash-two">
            <Card title="Where the money went">
              {data.expenses_by_category.length === 0 ? (
                <Empty>No expenses in this period.</Empty>
              ) : (
                <CategoryBars rows={data.expenses_by_category} />
              )}
            </Card>
            <Card title="Set aside">
              {data.allocations.length === 0 && <Empty>Nothing set aside.</Empty>}
              <ul className="dash-allocs">
                {data.allocations.map((a) => {
                  const pct = a.target_amount && Number(a.target_amount) > 0 ? Math.min(100, (Number(a.balance) / Number(a.target_amount)) * 100) : null
                  return (
                    <li key={a.id}>
                      <div className="dash-alloc-head">
                        <strong>{a.name}</strong>
                        <span className="num">{pesoExact(a.balance)}</span>
                      </div>
                      <span className="muted small">
                        {a.account_name}
                        {a.target_amount && ` · goal ${pesoExact(a.target_amount)}`}
                        {a.target_date && ` by ${dayLabel(a.target_date)}`}
                      </span>
                      {pct !== null && (
                        <div className="alloc-bar" role="img" aria-label={`${Math.round(pct)}% of the goal`}>
                          <span style={{ width: `${pct}%` }} />
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
              <Link to="/accounting/allocations" className="small">
                Manage allocations
              </Link>
            </Card>
          </div>

          <Card title="Accounts">
            <div className="table-scroll">
              <table className="table">
                <thead>
                  <tr>
                    <th>Account</th>
                    <th className="num">Balance</th>
                    <th className="num">Set aside</th>
                    <th className="num">Free</th>
                  </tr>
                </thead>
                <tbody>
                  {data.accounts.map((a) => (
                    <tr key={a.id}>
                      <td>
                        {a.name}
                        <span className="muted small">
                          {' '}
                          · {ACCOUNT_KIND_LABEL[a.kind]} · {a.branch_name}
                        </span>
                      </td>
                      <td className={`num ${Number(a.balance) < 0 ? 'error' : ''}`}>{pesoExact(a.balance)}</td>
                      <td className="num">{Number(a.allocated) > 0 ? pesoExact(a.allocated) : '–'}</td>
                      <td className="num">{pesoExact(a.available)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}
