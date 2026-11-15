import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import type { FundAccount, FundCategory, FundDirection } from '../api'
import { ErrorNote, Field } from '../components/ui'
import { useAction } from '../components/hooks'

function accountLabel(a: FundAccount): string {
  return a.branch_name ? `${a.name} · ${a.branch_name}` : `${a.name} · all branches`
}

/** Log money in or out of one account (an expense, a bill, other income). */
export function RecordForm({
  accounts,
  categories,
  today,
  deskMode = false,
  defaultDirection = 'out',
  onDone,
  onCancel,
}: {
  accounts: FundAccount[]
  categories: FundCategory[]
  today: string
  /** Petty cash at the desk: money out, today, a note required. */
  deskMode?: boolean
  defaultDirection?: FundDirection
  onDone: () => void
  onCancel?: () => void
}) {
  const usable = accounts.filter((a) => a.active)
  const [direction, setDirection] = useState<FundDirection>(deskMode ? 'out' : defaultDirection)
  const [accountId, setAccountId] = useState(usable[0]?.id ?? '')
  const [amount, setAmount] = useState('')
  const [day, setDay] = useState(today)
  const [categoryId, setCategoryId] = useState('')
  const [description, setDescription] = useState('')
  const [reference, setReference] = useState('')
  const { busy, error, run } = useAction()
  const cats = categories.filter((c) => c.active && c.direction === direction)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = {
      account_id: accountId,
      direction,
      amount,
      business_date: deskMode ? null : day,
      category_id: categoryId || null,
      description: description.trim() || null,
      reference: reference.trim() || null,
    }
    if (await run(() => api('/fund-transactions', { method: 'POST', json }))) {
      setAmount('')
      setDescription('')
      setReference('')
      onDone()
    }
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      {!deskMode && (
        <Field label="Money">
          <select
            value={direction}
            onChange={(e) => {
              setDirection(e.target.value as FundDirection)
              setCategoryId('')
            }}
          >
            <option value="out">Out (expense, bill)</option>
            <option value="in">In (other income)</option>
          </select>
        </Field>
      )}
      {usable.length > 1 && (
        <Field label={direction === 'out' ? 'Paid from' : 'Received in'}>
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)} required>
            {usable.map((a) => (
              <option key={a.id} value={a.id}>
                {accountLabel(a)}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="For">
        <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)} required>
          <option value="">Pick one…</option>
          {cats.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Amount (₱)">
        <input
          type="number"
          inputMode="decimal"
          min="0.01"
          step="0.01"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          required
        />
      </Field>
      {!deskMode && (
        <Field label="Date">
          <input type="date" value={day} onChange={(e) => setDay(e.target.value)} required />
        </Field>
      )}
      <Field label="Note" hint={deskMode ? 'What was bought or who was paid' : undefined}>
        <input value={description} onChange={(e) => setDescription(e.target.value)} required={deskMode} maxLength={500} />
      </Field>
      <Field label="Receipt / ref no.">
        <input value={reference} onChange={(e) => setReference(e.target.value)} maxLength={100} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={busy || !accountId}>
          Save
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel}>
            Back
          </button>
        )}
      </div>
      <ErrorNote message={error} />
    </form>
  )
}

/** Move money between two accounts: petty cash refill, cash deposit to the bank. */
export function TransferForm({
  accounts,
  today,
  defaultTo,
  onDone,
  onCancel,
}: {
  accounts: FundAccount[]
  today: string
  defaultTo?: string
  onDone: () => void
  onCancel?: () => void
}) {
  const usable = accounts.filter((a) => a.active)
  const [to, setTo] = useState(defaultTo ?? usable.find((a) => a.kind === 'petty_cash')?.id ?? '')
  const [from, setFrom] = useState(usable.find((a) => a.id !== to)?.id ?? '')
  const [amount, setAmount] = useState('')
  const [day, setDay] = useState(today)
  const [description, setDescription] = useState('')
  const [reference, setReference] = useState('')
  const { busy, error, run } = useAction()

  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = {
      from_account_id: from,
      to_account_id: to,
      amount,
      business_date: day,
      description: description.trim() || null,
      reference: reference.trim() || null,
    }
    if (await run(() => api('/fund-transfers', { method: 'POST', json }))) {
      setAmount('')
      setDescription('')
      setReference('')
      onDone()
    }
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="From">
        <select value={from} onChange={(e) => setFrom(e.target.value)} required>
          <option value="">Pick…</option>
          {usable.map((a) => (
            <option key={a.id} value={a.id}>
              {accountLabel(a)}
            </option>
          ))}
        </select>
      </Field>
      <Field label="To">
        <select value={to} onChange={(e) => setTo(e.target.value)} required>
          <option value="">Pick…</option>
          {usable.map((a) => (
            <option key={a.id} value={a.id}>
              {accountLabel(a)}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Amount (₱)">
        <input
          type="number"
          inputMode="decimal"
          min="0.01"
          step="0.01"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          required
        />
      </Field>
      <Field label="Date">
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} required />
      </Field>
      <Field label="Note">
        <input
          value={description}
          placeholder="e.g. Petty cash refill"
          onChange={(e) => setDescription(e.target.value)}
          maxLength={500}
        />
      </Field>
      <Field label="Ref no.">
        <input value={reference} onChange={(e) => setReference(e.target.value)} maxLength={100} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={busy || !from || !to || from === to}>
          Move money
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel}>
            Back
          </button>
        )}
      </div>
      <ErrorNote message={error} />
    </form>
  )
}
