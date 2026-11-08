import { Fragment, useState } from 'react'
import { api } from '../api'
import type { TherapistPay as Row, WeekPay } from '../api'
import { useAuth } from '../auth'
import { Card, Empty, ErrorNote } from '../components/ui'
import { useLoad } from '../components/hooks'
import { addDays, dayLabel, peso, todayIn } from '../time'

const DEFAULT_TZ = 'Asia/Manila'

/** One week's pay per therapist: commission, daily allowance and quota bonus. */
export default function TherapistPay() {
  const { branch, isAdmin } = useAuth()
  const [day, setDay] = useState(() => todayIn(DEFAULT_TZ))
  const [open, setOpen] = useState<string | null>(null)
  const { data, error } = useLoad(
    () => (branch ? api<WeekPay>(`/branches/${branch.id}/pay?week=${day}`) : Promise.resolve(null)),
    [branch?.id, day],
  )

  if (!branch) return <p className="note">You are not assigned to a branch yet.</p>
  const s = data?.settings
  const bySales = s?.quota_basis === 'sales'
  const progress = (r: Row) => (bySales ? peso(r.quota_progress) : `${Number(r.quota_progress)} services`)
  const thisWeek = data && todayIn(DEFAULT_TZ) >= data.week_start && todayIn(DEFAULT_TZ) <= data.week_end

  function exportCsv() {
    if (!data) return
    const head = ['Therapist', 'Services', 'Sales', 'Commission', 'Days clocked in', 'Allowance', 'Bonus', 'Total']
    const lines = data.therapists.map((r) =>
      [r.name, r.services, r.sales, r.commission, r.days_worked, r.allowance, r.bonus, r.total]
        .map((v) => `"${String(v).replace(/"/g, '""')}"`)
        .join(','),
    )
    const blob = new Blob([[head.join(','), ...lines].join('\n')], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `therapist-pay-${branch?.name ?? 'branch'}-${data.week_start}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <div className="stack">
      <h1>Therapist pay</h1>
      <div className="day-nav">
        <button onClick={() => setDay(addDays(data?.week_start ?? day, -7))} aria-label="Previous week">
          ‹
        </button>
        <button onClick={() => setDay(todayIn(DEFAULT_TZ))} disabled={Boolean(thisWeek)}>
          This week
        </button>
        <button onClick={() => setDay(addDays(data?.week_start ?? day, 7))} aria-label="Next week">
          ›
        </button>
        <input type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} aria-label="Any day in the week" />
        {data && (
          <strong>
            {dayLabel(data.week_start)} – {dayLabel(data.week_end)}
          </strong>
        )}
        {data && data.therapists.length > 0 && <button onClick={exportCsv}>Download CSV</button>}
      </div>
      <ErrorNote message={error} />
      {s && (
        <p className="muted">
          Commission per guest served (set per service length) · {peso(s.daily_allowance)} for each day clocked in ·{' '}
          {s.quota_tiers.length === 0 ? (
            <>
              no weekly quota bonus set up yet{isAdmin ? ' (set it in Setup › Pay rules)' : ''}.
            </>
          ) : (
            <>
              weekly bonus:{' '}
              {s.quota_tiers
                .map((t) => (bySales ? `${peso(t.threshold)} sales → ${peso(t.bonus)}` : `${Number(t.threshold)} services → ${peso(t.bonus)}`))
                .join(', ')}
              .
            </>
          )}{' '}
          Only checked-out services count.
        </p>
      )}
      {data && (
        <Card title={`${branch.name} · ${data.therapists.length} therapist${data.therapists.length === 1 ? '' : 's'}`}>
          {data.therapists.length === 0 ? (
            <Empty>No therapists at this branch.</Empty>
          ) : (
            <div className="table-scroll">
              <table className="table pay-table">
                <thead>
                  <tr>
                    <th>Therapist</th>
                    <th className="num">Services</th>
                    <th className="num">Sales</th>
                    <th className="num">Commission</th>
                    <th className="num">Days in</th>
                    <th className="num">Allowance</th>
                    <th className="num">Quota bonus</th>
                    <th className="num">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {data.therapists.map((r) => (
                    <Fragment key={r.therapist_id}>
                      <tr>
                        <td>
                          <button className="link" aria-expanded={open === r.therapist_id} onClick={() => setOpen(open === r.therapist_id ? null : r.therapist_id)}>
                            {open === r.therapist_id ? '▾' : '▸'} {r.name}
                          </button>
                        </td>
                        <td className="num">{r.services}</td>
                        <td className="num">{peso(r.sales)}</td>
                        <td className="num">{peso(r.commission)}</td>
                        <td className="num">{r.days_worked}</td>
                        <td className="num">{peso(r.allowance)}</td>
                        <td className="num" title={`Progress: ${progress(r)}`}>
                          {Number(r.bonus) > 0 ? peso(r.bonus) : <span className="muted">—</span>}
                        </td>
                        <td className="num">
                          <strong>{peso(r.total)}</strong>
                        </td>
                      </tr>
                      {open === r.therapist_id &&
                        r.days.map((d) => (
                          <tr key={d.date} className="pay-days">
                            <td>{dayLabel(d.date)}</td>
                            <td className="num">{d.services || ''}</td>
                            <td className="num">{d.services ? peso(d.sales) : ''}</td>
                            <td className="num">{d.services ? peso(d.commission) : ''}</td>
                            <td className="num">{d.clocked_in ? '✓' : ''}</td>
                            <td className="num">{d.clocked_in ? peso(s?.daily_allowance ?? 0) : ''}</td>
                            <td />
                            <td />
                          </tr>
                        ))}
                    </Fragment>
                  ))}
                  <tr className="pay-total">
                    <td>Total</td>
                    <td className="num">{data.therapists.reduce((n, r) => n + r.services, 0)}</td>
                    <td className="num">{peso(data.therapists.reduce((n, r) => n + Number(r.sales), 0))}</td>
                    <td className="num">{peso(data.therapists.reduce((n, r) => n + Number(r.commission), 0))}</td>
                    <td className="num">{data.therapists.reduce((n, r) => n + r.days_worked, 0)}</td>
                    <td className="num">{peso(data.therapists.reduce((n, r) => n + Number(r.allowance), 0))}</td>
                    <td className="num">{peso(data.therapists.reduce((n, r) => n + Number(r.bonus), 0))}</td>
                    <td className="num">{peso(data.total)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          )}
          {thisWeek && <p className="muted small">This week is still running: the totals grow as services are checked out.</p>}
        </Card>
      )}
    </div>
  )
}
