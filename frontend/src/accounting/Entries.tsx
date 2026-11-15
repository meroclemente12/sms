import { useState } from 'react'
import type { FormEvent } from 'react'
import { api, PAYMENT_STATUS_LABEL } from '../api'
import type { FundEntry } from '../api'
import { ErrorNote } from '../components/ui'
import { useAction } from '../components/hooks'
import { clockIn, dayLabel, pesoExact } from '../time'

function what(e: FundEntry): string {
  if (e.kind === 'payment') return `Sale · ${e.client_name ?? 'Guest'}`
  if (e.kind === 'transfer')
    return e.direction === 'out' ? `Transfer to ${e.other_account_name ?? '?'}` : `Transfer from ${e.other_account_name ?? '?'}`
  return e.category_name ?? 'Other'
}

function VoidButton({ e, onDone }: { e: FundEntry; onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const { busy, error, run } = useAction()
  async function submit(ev: FormEvent) {
    ev.preventDefault()
    if (await run(() => api(`/fund-transactions/${e.id}/void`, { method: 'POST', json: { reason: reason.trim() } }))) {
      setOpen(false)
      onDone()
    }
  }
  if (!open)
    return (
      <button className="ghost" onClick={() => setOpen(true)}>
        Void
      </button>
    )
  return (
    <form className="inline-form compact" onSubmit={submit}>
      <input
        aria-label="Why void it"
        placeholder="Why? e.g. wrong amount"
        value={reason}
        onChange={(x) => setReason(x.target.value)}
        required
        autoFocus
      />
      <button className="danger" disabled={busy || !reason.trim()}>
        Void
      </button>
      <button type="button" className="ghost" onClick={() => setOpen(false)}>
        Back
      </button>
      <ErrorNote message={error} />
    </form>
  )
}

/** Money in and out, newest first. */
export function EntriesTable({
  items,
  tz,
  showAccount,
  showBranch,
  onChanged,
}: {
  items: FundEntry[]
  tz: string
  showAccount: boolean
  showBranch: boolean
  onChanged: () => void
}) {
  return (
    <div className="table-scroll">
      <table className="table ledger-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>What</th>
            {showAccount && <th>Account</th>}
            {showBranch && <th>Branch</th>}
            <th className="num">In</th>
            <th className="num">Out</th>
            <th>By</th>
            <th>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((e) => (
            <tr key={`${e.kind}-${e.id}`} className={e.voided ? 'voided' : ''}>
              <td>
                {dayLabel(e.business_date)}
                <span className="muted small"> {clockIn(e.at, tz)}</span>
              </td>
              <td>
                <strong>{what(e)}</strong>
                {e.description && <div className="small">{e.description}</div>}
                <div className="muted small">
                  {e.reference && <>Ref {e.reference} </>}
                  {e.payment_status && e.payment_status !== 'not_needed' && (
                    <span className={`pill ${e.payment_status === 'verified' ? 'pill-on' : ''}`}>
                      {PAYMENT_STATUS_LABEL[e.payment_status]}
                    </span>
                  )}
                  {e.voided && <span className="pill pill-void">Voided: {e.void_reason}</span>}
                </div>
              </td>
              {showAccount && <td>{e.account_name}</td>}
              {showBranch && <td>{e.branch_name ?? 'All branches'}</td>}
              <td className="num">{e.direction === 'in' ? pesoExact(e.amount) : ''}</td>
              <td className="num">{e.direction === 'out' ? pesoExact(e.amount) : ''}</td>
              <td className="small">{e.recorded_by_name ?? ''}</td>
              <td className="row-actions">{e.can_void && <VoidButton e={e} onDone={onChanged} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
