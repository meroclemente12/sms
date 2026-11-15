import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import type { Frequency, FundAccount, FundCategory, FundSchedule, FundScheduleIn, ScheduleKind } from '../api'
import { useAuth } from '../auth'
import { Card, Empty, ErrorNote, Field } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import { dayLabel, pesoExact } from '../time'

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

function every(s: Pick<FundSchedule, 'frequency' | 'weekday' | 'day_of_month'>): string {
  if (s.frequency === 'weekly') return `Every ${WEEKDAYS[s.weekday]}`
  return s.day_of_month >= 29 ? `Monthly on the ${s.day_of_month}th (or last day)` : `Monthly on day ${s.day_of_month}`
}

function ScheduleForm({
  initial,
  accounts,
  categories,
  busy,
  onSave,
  onCancel,
}: {
  initial: FundScheduleIn
  accounts: FundAccount[]
  categories: FundCategory[]
  busy: boolean
  onSave: (d: FundScheduleIn) => void
  onCancel?: () => void
}) {
  const { me } = useAuth()
  const [d, setD] = useState<FundScheduleIn>(initial)
  const here = accounts.filter((a) => a.branch_id === d.branch_id && a.active)
  const dir = d.kind === 'income' ? 'in' : 'out'
  function submit(e: FormEvent) {
    e.preventDefault()
    onSave({ ...d, name: d.name.trim() })
  }
  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Name">
        <input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="e.g. Rent, Meralco" required />
      </Field>
      {(me?.branches.length ?? 0) > 1 && (
        <Field label="Branch">
          <select
            value={d.branch_id}
            disabled={!!onCancel}
            onChange={(e) => setD({ ...d, branch_id: e.target.value, account_id: '', to_account_id: null })}
          >
            {me?.branches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      <Field label="Type">
        <select
          value={d.kind}
          onChange={(e) => setD({ ...d, kind: e.target.value as ScheduleKind, category_id: null, to_account_id: null })}
        >
          <option value="expense">Bill / expense</option>
          <option value="transfer">Transfer (e.g. petty cash refill)</option>
          <option value="income">Income</option>
        </select>
      </Field>
      <Field label={d.kind === 'income' ? 'Into' : 'From'}>
        <select value={d.account_id} onChange={(e) => setD({ ...d, account_id: e.target.value })} required>
          <option value="">Pick…</option>
          {here.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </Field>
      {d.kind === 'transfer' ? (
        <Field label="To">
          <select value={d.to_account_id ?? ''} onChange={(e) => setD({ ...d, to_account_id: e.target.value || null })} required>
            <option value="">Pick…</option>
            {here
              .filter((a) => a.id !== d.account_id)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </Field>
      ) : (
        <Field label="Category">
          <select value={d.category_id ?? ''} onChange={(e) => setD({ ...d, category_id: e.target.value || null })} required>
            <option value="">Pick…</option>
            {categories
              .filter((c) => c.active && c.direction === dir)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
        </Field>
      )}
      <Field label="Usual amount (₱)" hint="You can change it each time">
        <input type="number" step="0.01" min="0.01" value={d.amount} onChange={(e) => setD({ ...d, amount: e.target.value })} required />
      </Field>
      <Field label="How often">
        <select value={d.frequency} onChange={(e) => setD({ ...d, frequency: e.target.value as Frequency })}>
          <option value="monthly">Monthly</option>
          <option value="weekly">Weekly</option>
        </select>
      </Field>
      {d.frequency === 'weekly' ? (
        <Field label="On">
          <select value={d.weekday} onChange={(e) => setD({ ...d, weekday: Number(e.target.value) })}>
            {WEEKDAYS.map((w, i) => (
              <option key={w} value={i}>
                {w}
              </option>
            ))}
          </select>
        </Field>
      ) : (
        <Field label="Day of month" hint="29–31: last day in short months">
          <input
            type="number"
            min={1}
            max={31}
            value={d.day_of_month}
            onChange={(e) => setD({ ...d, day_of_month: Number(e.target.value) })}
            required
          />
        </Field>
      )}
      <Field label="Starts">
        <input type="date" value={d.starts_on ?? ''} onChange={(e) => setD({ ...d, starts_on: e.target.value || null })} />
      </Field>
      <Field label="Ends (optional)">
        <input type="date" value={d.ends_on ?? ''} onChange={(e) => setD({ ...d, ends_on: e.target.value || null })} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          {onCancel ? 'Save' : 'Add'}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel}>
            Back
          </button>
        )}
      </div>
    </form>
  )
}

function toIn(s: FundSchedule): FundScheduleIn {
  return {
    branch_id: s.branch_id,
    name: s.name,
    kind: s.kind,
    account_id: s.account_id,
    to_account_id: s.to_account_id,
    category_id: s.category_id,
    amount: s.amount,
    frequency: s.frequency,
    weekday: s.weekday,
    day_of_month: s.day_of_month,
    starts_on: s.starts_on,
    ends_on: s.ends_on,
    description: s.description,
    active: s.active,
  }
}

function ScheduleRow({
  s,
  accounts,
  categories,
  onDone,
}: {
  s: FundSchedule
  accounts: FundAccount[]
  categories: FundCategory[]
  onDone: () => void
}) {
  const { isAdmin } = useAuth()
  const [editing, setEditing] = useState(false)
  const { busy, error, run } = useAction()
  async function save(d: FundScheduleIn) {
    if (await run(() => api(`/fund-schedules/${s.id}`, { method: 'PUT', json: d }))) {
      setEditing(false)
      onDone()
    }
  }
  if (editing)
    return (
      <tr>
        <td colSpan={5}>
          <ScheduleForm
            initial={toIn(s)}
            accounts={accounts}
            categories={categories}
            busy={busy}
            onSave={(d) => void save(d)}
            onCancel={() => setEditing(false)}
          />
          <ErrorNote message={error} />
        </td>
      </tr>
    )
  return (
    <tr>
      <td>
        <strong>{s.name}</strong>
        <div className="muted small">
          {s.kind === 'transfer' ? `${s.account_name} → ${s.to_account_name}` : `${s.category_name} · ${s.account_name}`} ·{' '}
          {s.branch_name}
        </div>
      </td>
      <td className="small">{every(s)}</td>
      <td className="num">{pesoExact(s.amount)}</td>
      <td className="small">
        {s.active ? (s.next_due ? `Next: ${dayLabel(s.next_due)}` : 'Ended') : <span className="pill">Paused</span>}
        <ErrorNote message={error} />
      </td>
      <td className="row-actions">
        {isAdmin && (
          <>
            <button className="ghost" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button className="ghost" disabled={busy} onClick={() => void save({ ...toIn(s), active: !s.active })}>
              {s.active ? 'Pause' : 'Resume'}
            </button>
          </>
        )}
      </td>
    </tr>
  )
}

export default function Recurring() {
  const { branch, isAdmin } = useAuth()
  const schedules = useLoad(() => api<FundSchedule[]>('/fund-schedules'), [])
  const accounts = useLoad(() => api<FundAccount[]>('/accounts'), [])
  const categories = useLoad(() => api<FundCategory[]>('/fund-categories'), [])
  const add = useAction()
  const [formKey, setFormKey] = useState(0)

  async function create(d: FundScheduleIn) {
    if (await add.run(() => api('/fund-schedules', { method: 'POST', json: d }))) {
      setFormKey((k) => k + 1)
      void schedules.reload()
    }
  }

  const blank: FundScheduleIn = {
    branch_id: branch?.id ?? '',
    name: '',
    kind: 'expense',
    account_id: '',
    to_account_id: null,
    category_id: null,
    amount: '',
    frequency: 'monthly',
    weekday: 0,
    day_of_month: 1,
    starts_on: null,
    ends_on: null,
    description: null,
    active: true,
  }

  return (
    <div className="stack">
      <h1>Recurring bills and refills</h1>
      <Card title="Every week or month">
        <p className="muted">
          Nothing is paid by itself: on each due date the entry shows as Due on the Accounting page, and an admin posts it
          with the real amount, or skips it.
        </p>
        <ErrorNote message={schedules.error} />
        {schedules.data && schedules.data.length === 0 && <Empty>No recurring entries yet.</Empty>}
        <div className="table-scroll">
          <table className="table">
            <tbody>
              {schedules.data?.map((s) => (
                <ScheduleRow
                  key={s.id}
                  s={s}
                  accounts={accounts.data ?? []}
                  categories={categories.data ?? []}
                  onDone={() => void schedules.reload()}
                />
              ))}
            </tbody>
          </table>
        </div>
        {isAdmin && accounts.data && categories.data && (
          <>
            <h3>Add one</h3>
            <ScheduleForm
              key={formKey}
              initial={blank}
              accounts={accounts.data}
              categories={categories.data}
              busy={add.busy}
              onSave={(d) => void create(d)}
            />
            <ErrorNote message={add.error} />
          </>
        )}
      </Card>
    </div>
  )
}
