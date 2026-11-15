import { useState } from 'react'
import type { FormEvent } from 'react'
import { api, ACCOUNT_KIND_LABEL } from '../../api'
import type { AccountKind, FundAccount, FundAccountIn, FundCategory, FundDirection } from '../../api'
import { useAuth } from '../../auth'
import { Card, Empty, ErrorNote, Field } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'
import { dayLabel, pesoExact } from '../../time'

const BRANCH_ONLY: AccountKind[] = ['cash', 'petty_cash']

function blank(branchId: string): FundAccountIn {
  return {
    name: '',
    kind: 'bank',
    branch_id: branchId,
    account_number: null,
    opening_balance: '0',
    opening_date: null,
    accepts_payments: true,
    needs_reference: true,
    sort_order: 0,
    active: true,
  }
}

function AccountForm({
  initial,
  onSave,
  onCancel,
  busy,
}: {
  initial: FundAccountIn
  onSave: (d: FundAccountIn) => void
  onCancel?: () => void
  busy: boolean
}) {
  const { me } = useAuth()
  const [d, setD] = useState<FundAccountIn>(initial)
  const branchOnly = BRANCH_ONLY.includes(d.kind)
  const editing = !!onCancel
  function submit(e: FormEvent) {
    e.preventDefault()
    onSave({ ...d, name: d.name.trim() })
  }
  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Name" hint="e.g. BPI Savings, GCash 0917…, Petty cash">
        <input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} required autoFocus={!!onCancel} />
      </Field>
      <Field label="Type">
        <select
          value={d.kind}
          onChange={(e) => {
            const kind = e.target.value as AccountKind
            setD({
              ...d,
              kind,
              accepts_payments: kind === 'petty_cash' ? false : d.accepts_payments,
            })
          }}
        >
          {(Object.keys(ACCOUNT_KIND_LABEL) as AccountKind[]).map((k) => (
            <option key={k} value={k}>
              {ACCOUNT_KIND_LABEL[k]}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Branch">
        <select value={d.branch_id} disabled={editing} onChange={(e) => setD({ ...d, branch_id: e.target.value })} required>
          {me?.branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
      </Field>
      {!branchOnly && (
        <Field label="Account number">
          <input value={d.account_number ?? ''} onChange={(e) => setD({ ...d, account_number: e.target.value || null })} />
        </Field>
      )}
      <Field label="Opening balance (₱)" hint="What it held at the start of the opening date">
        <input
          type="number"
          step="0.01"
          value={d.opening_balance}
          onChange={(e) => setD({ ...d, opening_balance: e.target.value })}
          required
        />
      </Field>
      <Field label="Opening date">
        <input type="date" value={d.opening_date ?? ''} onChange={(e) => setD({ ...d, opening_date: e.target.value || null })} />
      </Field>
      {d.kind !== 'petty_cash' && (
        <label className="check">
          <input
            type="checkbox"
            checked={d.accepts_payments}
            onChange={(e) => setD({ ...d, accepts_payments: e.target.checked })}
          />{' '}
          Guests can pay with this at check-out
        </label>
      )}
      {d.accepts_payments && !branchOnly && (
        <label className="check">
          <input
            type="checkbox"
            checked={d.needs_reference}
            onChange={(e) => setD({ ...d, needs_reference: e.target.checked })}
          />{' '}
          Ask for the reference number
        </label>
      )}
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          {onCancel ? 'Save' : 'Add account'}
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

function toIn(a: FundAccount): FundAccountIn {
  return {
    name: a.name,
    kind: a.kind,
    branch_id: a.branch_id,
    account_number: a.account_number,
    opening_balance: a.opening_balance,
    opening_date: a.opening_date,
    accepts_payments: a.accepts_payments,
    needs_reference: a.needs_reference,
    sort_order: a.sort_order,
    active: a.active,
  }
}

function AccountRow({ a, onDone }: { a: FundAccount; onDone: () => void }) {
  const [editing, setEditing] = useState(false)
  const { busy, error, run } = useAction()
  async function save(d: FundAccountIn) {
    if (await run(() => api(`/accounts/${a.id}`, { method: 'PUT', json: d }))) {
      setEditing(false)
      onDone()
    }
  }
  if (editing)
    return (
      <tr>
        <td colSpan={5}>
          <AccountForm initial={toIn(a)} busy={busy} onSave={(d) => void save(d)} onCancel={() => setEditing(false)} />
          <ErrorNote message={error} />
        </td>
      </tr>
    )
  return (
    <tr>
      <td>
        <strong>{a.name}</strong>
        <div className="muted small">
          {ACCOUNT_KIND_LABEL[a.kind]} · {a.branch_name}
          {a.account_number && <> · {a.account_number}</>}
        </div>
      </td>
      <td className="small">
        {a.accepts_payments ? 'Guests can pay with it' : 'Not for guest payments'}
        {a.accepts_payments && a.needs_reference && <span className="muted"> · reference required</span>}
      </td>
      <td className="small">
        Opened {dayLabel(a.opening_date)} with {pesoExact(a.opening_balance)}
        <div className="muted">Now {pesoExact(a.balance)}</div>
      </td>
      <td>
        <label className="check">
          <input type="checkbox" checked={a.active} disabled={busy} onChange={(e) => void save({ ...toIn(a), active: e.target.checked })} /> In use
        </label>
        <ErrorNote message={error} />
      </td>
      <td className="row-actions">
        <button className="ghost" onClick={() => setEditing(true)}>
          Edit
        </button>
      </td>
    </tr>
  )
}

function CategoryRow({ c, onDone }: { c: FundCategory; onDone: () => void }) {
  const { busy, error, run } = useAction()
  async function save(patch: Partial<FundCategory>) {
    if (await run(() => api(`/fund-categories/${c.id}`, { method: 'PUT', json: { ...c, ...patch } }))) onDone()
  }
  return (
    <tr>
      <td>{c.name}</td>
      <td className="small">{c.direction === 'out' ? 'Money out' : 'Money in'}</td>
      <td>
        {c.direction === 'out' && (
          <label className="check">
            <input type="checkbox" checked={c.desk_can_use} disabled={busy} onChange={(e) => void save({ desk_can_use: e.target.checked })} />{' '}
            Front desk can use for petty cash
          </label>
        )}
      </td>
      <td>
        <label className="check">
          <input type="checkbox" checked={c.active} disabled={busy} onChange={(e) => void save({ active: e.target.checked })} /> In use
        </label>
        <ErrorNote message={error} />
      </td>
    </tr>
  )
}

function NewCategory({ count, onDone }: { count: number; onDone: () => void }) {
  const [name, setName] = useState('')
  const [direction, setDirection] = useState<FundDirection>('out')
  const [desk, setDesk] = useState(false)
  const { busy, error, run } = useAction()
  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = { name: name.trim(), direction, desk_can_use: desk, sort_order: count + 1, active: true }
    if (await run(() => api('/fund-categories', { method: 'POST', json }))) {
      setName('')
      onDone()
    }
  }
  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="New category">
        <input value={name} onChange={(e) => setName(e.target.value)} required placeholder="e.g. Bed maintenance" />
      </Field>
      <Field label="Money">
        <select value={direction} onChange={(e) => setDirection(e.target.value as FundDirection)}>
          <option value="out">Out</option>
          <option value="in">In</option>
        </select>
      </Field>
      {direction === 'out' && (
        <label className="check">
          <input type="checkbox" checked={desk} onChange={(e) => setDesk(e.target.checked)} /> Front desk can use
        </label>
      )}
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          Add
        </button>
      </div>
      <ErrorNote message={error} />
    </form>
  )
}

export default function AccountsPage() {
  const { branch } = useAuth()
  const accounts = useLoad(() => api<FundAccount[]>('/accounts'), [])
  const categories = useLoad(() => api<FundCategory[]>('/fund-categories'), [])
  const add = useAction()
  const [formKey, setFormKey] = useState(0)

  async function create(d: FundAccountIn) {
    const json = { ...d, sort_order: (accounts.data?.length ?? 0) + 1 }
    if (await add.run(() => api('/accounts', { method: 'POST', json }))) {
      setFormKey((k) => k + 1)
      void accounts.reload()
    }
  }

  return (
    <div className="stack">
      <h1>Accounts and payment methods</h1>
      <Card title="Where money is kept">
        <p className="muted">
          Every cash drawer, bank account, e-wallet and petty cash box. Tick &ldquo;Guests can pay with this&rdquo; to offer it
          at check-out: guest payments then go straight into that account. Each branch has its own accounts; a bank account used
          by two branches is added once for each.
        </p>
        <ErrorNote message={accounts.error} />
        {accounts.data && accounts.data.length === 0 && <Empty>No accounts yet.</Empty>}
        <div className="table-scroll">
          <table className="table">
            <tbody>
              {accounts.data?.map((a) => (
                <AccountRow key={a.id} a={a} onDone={() => void accounts.reload()} />
              ))}
            </tbody>
          </table>
        </div>
        <h3>Add an account</h3>
        <AccountForm key={formKey} initial={blank(branch?.id ?? '')} busy={add.busy} onSave={(d) => void create(d)} />
        <ErrorNote message={add.error} />
      </Card>

      <Card title="Categories">
        <p className="muted">What money is spent on or comes in for. The front desk only sees the ones ticked for them.</p>
        <ErrorNote message={categories.error} />
        <div className="table-scroll">
          <table className="table">
            <tbody>
              {categories.data?.map((c) => (
                <CategoryRow key={c.id} c={c} onDone={() => void categories.reload()} />
              ))}
            </tbody>
          </table>
        </div>
        <NewCategory count={categories.data?.length ?? 0} onDone={() => void categories.reload()} />
      </Card>
    </div>
  )
}
