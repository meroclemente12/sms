import { useState } from 'react'
import { api } from '../api'
import type { PaymentList, PaymentMethod, PaymentRow } from '../api'
import { useAuth } from '../auth'
import { Card, Empty, ErrorNote } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import { PaymentBadge } from '../desk/Payments'
import { addDays, clockIn, dayLabel, dayOf, peso, todayIn } from '../time'

type Filter = 'pending' | 'flagged' | 'verified' | 'cashless' | 'cash'

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'pending', label: 'To check' },
  { value: 'flagged', label: 'Flagged' },
  { value: 'verified', label: 'Verified' },
  { value: 'cashless', label: 'All cashless' },
  { value: 'cash', label: 'Cash' },
]

// Until the list loads, assume Philippine time (the branch's own timezone comes with it).
const DEFAULT_TZ = 'Asia/Manila'

function Row({ p, tz, onDone }: { p: PaymentRow; tz: string; onDone: () => void }) {
  const [flagging, setFlagging] = useState(false)
  const [note, setNote] = useState('')
  const { busy, error, run } = useAction()

  async function act(path: string, json?: object) {
    if (await run(() => api(`/payments/${p.id}/${path}`, { method: 'POST', json: json ?? {} }))) {
      setFlagging(false)
      setNote('')
      onDone()
    }
  }

  return (
    <tr className={p.status === 'flagged' ? 'flagged' : ''}>
      <td className="nowrap">
        {dayLabel(dayOf(p.recorded_at, tz))}
        <br />
        <span className="muted small">{clockIn(p.recorded_at, tz)}</span>
      </td>
      <td>
        <strong>{p.client_name}</strong>
        <br />
        <span className="muted small">
          {p.service_name} · {dayLabel(dayOf(p.booking_starts_at, tz))} {clockIn(p.booking_starts_at, tz)}
        </span>
      </td>
      <td>{p.method_name}</td>
      <td className="mono">{p.reference ?? <span className="muted">—</span>}</td>
      <td className="num">
        <strong>{peso(p.amount)}</strong>
      </td>
      <td className="muted small">{p.recorded_by_name ?? '—'}</td>
      <td>
        <PaymentBadge status={p.status} />
        {p.checked_by_name && p.status !== 'pending' && <div className="muted small">by {p.checked_by_name}</div>}
        {p.check_note && (
          <div className={p.status === 'flagged' ? 'flag-note small' : 'muted small'}>
            {p.status === 'pending' ? `Corrected by the desk. Was flagged: ${p.check_note}` : p.check_note}
          </div>
        )}
      </td>
      <td className="row-actions">
        {p.status === 'pending' && !flagging && (
          <>
            <button className="primary" disabled={busy} onClick={() => void act('verify')}>
              Verify
            </button>{' '}
            <button className="danger" disabled={busy} onClick={() => setFlagging(true)}>
              Flag
            </button>
          </>
        )}
        {(p.status === 'verified' || p.status === 'flagged') && (
          <button className="ghost" disabled={busy} onClick={() => void act('reopen')}>
            Undo
          </button>
        )}
        {flagging && (
          <form
            className="flag-form"
            onSubmit={(e) => {
              e.preventDefault()
              void act('flag', { note: note.trim() })
            }}
          >
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="What is wrong? e.g. not in statement"
              aria-label="Why it is flagged"
              autoFocus
              required
            />
            <button className="danger solid" disabled={busy || !note.trim()}>
              Flag
            </button>
            <button type="button" className="ghost" onClick={() => setFlagging(false)}>
              Back
            </button>
          </form>
        )}
        <ErrorNote message={error} />
      </td>
    </tr>
  )
}

export default function Payments() {
  const { branch } = useAuth()
  const branchId = branch?.id ?? ''
  const [tz, setTz] = useState(DEFAULT_TZ)
  const today = todayIn(tz)
  const [from, setFrom] = useState(today)
  const [to, setTo] = useState(today)
  const [filter, setFilter] = useState<Filter>('pending')
  const [methodId, setMethodId] = useState('')

  const methods = useLoad(
    () => (branchId ? api<PaymentMethod[]>(`/branches/${branchId}/payment-methods`) : Promise.resolve([])),
    [branchId],
  )
  const { data, error, reload } = useLoad(() => {
    if (!branchId) return Promise.resolve(null)
    const q = new URLSearchParams({ date_from: from, date_to: to, status: filter })
    if (methodId) q.set('method_id', methodId)
    return api<PaymentList>(`/branches/${branchId}/payments?${q}`).then((list) => {
      setTz(list.timezone)
      return list
    })
  }, [branchId, from, to, filter, methodId])

  function range(days: number) {
    setTo(today)
    setFrom(addDays(today, -(days - 1)))
  }

  const total = (data?.totals ?? []).reduce((s, t) => s + Number(t.amount), 0)
  const listed = (data?.items ?? []).reduce((s, p) => s + Number(p.amount), 0)

  return (
    <div className="stack payments-page">
      <div className="board-toolbar">
        <h1>Payments · {branch?.name}</h1>
        <div className="day-nav">
          <button onClick={() => range(1)}>Today</button>
          <button
            onClick={() => {
              setFrom(addDays(today, -1))
              setTo(addDays(today, -1))
            }}
          >
            Yesterday
          </button>
          <button onClick={() => range(7)}>Last 7 days</button>
          <label className="muted" htmlFor="pay-from">
            From
          </label>
          <input id="pay-from" type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
          <label className="muted" htmlFor="pay-to">
            to
          </label>
          <input id="pay-to" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
        </div>
      </div>

      <div className="pay-summary">
        <button className={`pay-stat ${filter === 'pending' ? 'on' : ''}`} onClick={() => setFilter('pending')}>
          <span className="pay-stat-n">{data?.pending ?? '–'}</span>
          <span>to check</span>
        </button>
        <button className={`pay-stat warn ${filter === 'flagged' ? 'on' : ''}`} onClick={() => setFilter('flagged')}>
          <span className="pay-stat-n">{data?.flagged ?? '–'}</span>
          <span>flagged</span>
        </button>
        <button className={`pay-stat ${filter === 'verified' ? 'on' : ''}`} onClick={() => setFilter('verified')}>
          <span className="pay-stat-n">{data?.verified ?? '–'}</span>
          <span>verified</span>
        </button>
      </div>

      <Card title="Collected by method">
        {data && data.totals.length === 0 && <Empty>No payments logged in these days.</Empty>}
        {data && data.totals.length > 0 && (
          <div className="table-scroll">
            <table className="table totals">
              <thead>
                <tr>
                  <th>Method</th>
                  <th className="num">Payments</th>
                  <th className="num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {data.totals.map((t) => (
                  <tr key={t.method_id}>
                    <td>
                      {t.method_name}
                      {t.kind === 'cash' && <span className="muted small"> · cash</span>}
                    </td>
                    <td className="num">{t.count}</td>
                    <td className="num">{peso(t.amount)}</td>
                  </tr>
                ))}
                <tr className="total-row">
                  <td>Total</td>
                  <td className="num">{data.totals.reduce((s, t) => s + t.count, 0)}</td>
                  <td className="num">{peso(String(total))}</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card
        title="Payments"
        actions={
          <div className="toolbar">
            <div className="segmented-buttons" role="group" aria-label="Show">
              {FILTERS.map((f) => (
                <button key={f.value} className={filter === f.value ? 'primary' : ''} aria-pressed={filter === f.value} onClick={() => setFilter(f.value)}>
                  {f.label}
                </button>
              ))}
            </div>
            <select aria-label="Payment method" value={methodId} onChange={(e) => setMethodId(e.target.value)}>
              <option value="">Every method</option>
              {(methods.data ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </div>
        }
      >
        <ErrorNote message={error} />
        {data && data.items.length === 0 && (
          <Empty>{filter === 'pending' ? 'Nothing left to check for these days.' : 'No payments match.'}</Empty>
        )}
        {data && data.items.length > 0 && (
          <div className="table-scroll">
            <table className="table pay-table">
              <thead>
                <tr>
                  <th>Logged</th>
                  <th>Guest and booking</th>
                  <th>Method</th>
                  <th>Reference</th>
                  <th className="num">Amount</th>
                  <th>By</th>
                  <th>Status</th>
                  <th>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((p) => (
                  <Row key={p.id} p={p} tz={tz} onDone={() => void reload()} />
                ))}
              </tbody>
            </table>
            <p className="muted small">
              {data.items.length} payment{data.items.length === 1 ? '' : 's'} · {peso(String(listed))}
            </p>
          </div>
        )}
      </Card>
    </div>
  )
}
