import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import type { FundDue } from '../api'
import { useAuth } from '../auth'
import { Card, ErrorNote } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import { dayLabel, pesoExact } from '../time'

function DueRow({ d, today, onDone }: { d: FundDue; today: string; onDone: () => void }) {
  const { isAdmin } = useAuth()
  const [open, setOpen] = useState(false)
  const [amount, setAmount] = useState(d.schedule.amount)
  const [day, setDay] = useState(today)
  const [reference, setReference] = useState('')
  const { busy, error, run } = useAction()
  const s = d.schedule

  async function post(e: FormEvent) {
    e.preventDefault()
    const json = { due_date: d.due_date, amount, business_date: day, reference: reference.trim() || null }
    if (await run(() => api(`/fund-schedules/${s.id}/post`, { method: 'POST', json }))) onDone()
  }
  async function skip() {
    if (await run(() => api(`/fund-schedules/${s.id}/skip`, { method: 'POST', json: { due_date: d.due_date } })))
      onDone()
  }
  const what =
    s.kind === 'transfer' ? `${s.account_name} → ${s.to_account_name}` : `${s.category_name} · ${s.account_name}`
  return (
    <tr className={d.overdue ? 'flagged' : ''}>
      <td>
        {dayLabel(d.due_date)}
        {d.overdue && <div className="error small">Overdue</div>}
      </td>
      <td>
        <strong>{s.name}</strong>
        <div className="muted small">
          {what} · {s.branch_name}
        </div>
      </td>
      <td className="num">{pesoExact(s.amount)}</td>
      <td className="row-actions">
        {isAdmin && !open && (
          <>
            <button className="primary" onClick={() => setOpen(true)}>
              Post
            </button>{' '}
            <button className="ghost" disabled={busy} onClick={() => void skip()}>
              Skip
            </button>
          </>
        )}
        {open && (
          <form className="inline-form compact" onSubmit={post}>
            <input
              type="number"
              step="0.01"
              min="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              aria-label="Actual amount"
              required
            />
            <input type="date" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Paid on" required />
            <input value={reference} onChange={(e) => setReference(e.target.value)} placeholder="Ref no." aria-label="Ref no." />
            <button className="primary" disabled={busy}>
              Save
            </button>
            <button type="button" className="ghost" onClick={() => setOpen(false)}>
              Back
            </button>
          </form>
        )}
        <ErrorNote message={error} />
      </td>
    </tr>
  )
}

/** Recurring bills and refills that are due (or overdue), waiting to be posted. */
export function DueCard({ today, onPosted }: { today: string; onPosted: () => void }) {
  const due = useLoad(() => api<FundDue[]>('/fund-due?days_ahead=7'), [])
  if (!due.data || due.data.length === 0) return <ErrorNote message={due.error} />
  return (
    <Card title={`Due · ${due.data.filter((d) => d.due_date <= today).length} now`}>
      <p className="muted">Recurring bills and refills for the next 7 days. Post them once paid, with the real amount.</p>
      <div className="table-scroll">
        <table className="table pay-table">
          <tbody>
            {due.data.map((d) => (
              <DueRow
                key={`${d.schedule.id}-${d.due_date}`}
                d={d}
                today={today}
                onDone={() => {
                  void due.reload()
                  onPosted()
                }}
              />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
