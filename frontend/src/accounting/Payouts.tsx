import { useState } from 'react'
import { api } from '../api'
import type { FundAccount, PayoutList } from '../api'
import { useAuth } from '../auth'
import { Card, ErrorNote } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import { dayLabel, pesoExact } from '../time'

/** Paying the week's pay out of an account (admin), or seeing who was paid (accountant). */
export function PayoutCard({ branchId, week, today }: { branchId: string; week: string; today: string }) {
  const { isAdmin } = useAuth()
  const list = useLoad(() => api<PayoutList>(`/branches/${branchId}/pay/payouts?week=${week}`), [branchId, week])
  const accounts = useLoad(
    () => (isAdmin ? api<FundAccount[]>(`/accounts?branch_id=${branchId}`) : Promise.resolve([])),
    [branchId, isAdmin],
  )
  const usable = (accounts.data ?? []).filter((a) => a.active && a.kind !== 'petty_cash')
  const [accountId, setAccountId] = useState('')
  const [day, setDay] = useState(today)
  const { busy, error, run } = useAction()
  const chosen = accountId || usable.find((a) => a.kind === 'cash')?.id || usable[0]?.id || ''

  async function payAll() {
    const json = { week, account_id: chosen, business_date: day }
    if (await run(() => api(`/branches/${branchId}/pay/payouts`, { method: 'POST', json }))) {
      void list.reload()
      void accounts.reload()
    }
  }

  const items = (list.data?.items ?? []).filter((i) => i.paid || Number(i.amount_due) > 0)
  const free = usable.find((a) => a.id === chosen)
  return (
    <Card title="Paying out">
      <ErrorNote message={list.error} />
      {items.length === 0 && <p className="muted">Nobody earned anything this week yet.</p>}
      {items.length > 0 && (
        <div className="table-scroll">
          <table className="table">
            <tbody>
              {items.map((i) => (
                <tr key={i.therapist_id}>
                  <td>{i.name}</td>
                  <td className="num">{pesoExact(i.paid ? (i.paid_amount ?? i.amount_due) : i.amount_due)}</td>
                  <td>
                    {i.paid ? (
                      <span className="pill pill-on">
                        Paid {i.paid_on ? dayLabel(i.paid_on) : ''} from {i.paid_from}
                      </span>
                    ) : (
                      <span className="pill">Not paid</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {isAdmin && list.data && Number(list.data.to_pay) > 0 && (
        <div className="inline-form">
          <label className="check">
            Pay from{' '}
            <select value={chosen} onChange={(e) => setAccountId(e.target.value)} aria-label="Pay from">
              {usable.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({pesoExact(a.available)} free)
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            on <input type="date" value={day} onChange={(e) => setDay(e.target.value)} aria-label="Paid on" />
          </label>
          <button className="primary" disabled={busy || !chosen} onClick={() => void payAll()}>
            Pay {pesoExact(list.data.to_pay)} to everyone not paid yet
          </button>
          {free && Number(free.available) < Number(list.data.to_pay) && (
            <span className="error small">{free.name} does not have enough free money.</span>
          )}
          <ErrorNote message={error} />
        </div>
      )}
      <p className="muted small">
        Each therapist gets one Salaries entry in Accounting. Void it there if it was a mistake; they then show as not paid.
      </p>
    </Card>
  )
}
