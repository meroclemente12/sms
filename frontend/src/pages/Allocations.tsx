import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import type { AllocationHistoryLine, FundAccount, FundAllocation, FundCategory } from '../api'
import { useAuth, useBusinessDay } from '../auth'
import { Card, Empty, ErrorNote, Field } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import { dayLabel, pesoExact } from '../time'

const KIND_LABEL: Record<AllocationHistoryLine['kind'], string> = {
  set_aside: 'Set aside',
  released: 'Released',
  spent: 'Spent',
}

type Panel = null | 'add' | 'release' | 'spend' | 'history'

function AllocationCard({
  a,
  categories,
  today,
  onDone,
}: {
  a: FundAllocation
  categories: FundCategory[]
  today: string
  onDone: () => void
}) {
  const { isAdmin } = useAuth()
  const [panel, setPanel] = useState<Panel>(null)
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [reference, setReference] = useState('')
  const [day, setDay] = useState(today)
  const { busy, error, run } = useAction()
  const history = useLoad(
    () =>
      panel === 'history' ? api<AllocationHistoryLine[]>(`/fund-allocations/${a.id}/history`) : Promise.resolve(null),
    [panel, a.id],
  )
  const pct = a.target_amount && Number(a.target_amount) > 0 ? Math.min(100, (Number(a.balance) / Number(a.target_amount)) * 100) : null

  async function submit(e: FormEvent) {
    e.preventDefault()
    const ok =
      panel === 'spend'
        ? await run(() =>
            api(`/fund-allocations/${a.id}/spend`, {
              method: 'POST',
              json: {
                amount,
                category_id: categoryId,
                business_date: day,
                description: note.trim() || null,
                reference: reference.trim() || null,
              },
            }),
          )
        : await run(() =>
            api(`/fund-allocations/${a.id}/moves`, {
              method: 'POST',
              json: { amount: panel === 'release' ? `-${amount}` : amount, business_date: day, note: note.trim() || null },
            }),
          )
    if (ok) {
      setAmount('')
      setNote('')
      setReference('')
      setPanel(null)
      onDone()
    }
  }
  async function close() {
    if (await run(() => api(`/fund-allocations/${a.id}/close`, { method: 'POST' }))) onDone()
  }

  return (
    <section className="card alloc-card">
      <header className="card-head">
        <h2>{a.name}</h2>
        <span className="muted small">
          in {a.account_name} · {a.branch_name}
          {a.closed && ' · closed'}
        </span>
      </header>
      <p className="fund-amount">{pesoExact(a.balance)}</p>
      <p className="muted small">
        {a.target_amount && <>Goal {pesoExact(a.target_amount)} </>}
        {a.target_date && <>by {dayLabel(a.target_date)} </>}· set aside {pesoExact(a.put_in)} · spent {pesoExact(a.used)}
      </p>
      {pct !== null && (
        <div className="alloc-bar" role="img" aria-label={`${Math.round(pct)}% of the goal`}>
          <span style={{ width: `${pct}%` }} />
        </div>
      )}
      {a.notes && <p className="small">{a.notes}</p>}
      <div className="toolbar">
        {isAdmin && !a.closed && (
          <>
            <button className={panel === 'add' ? 'primary' : ''} onClick={() => setPanel(panel === 'add' ? null : 'add')}>
              Set aside more
            </button>
            <button className={panel === 'spend' ? 'primary' : ''} onClick={() => setPanel(panel === 'spend' ? null : 'spend')}>
              Pay from it
            </button>
            <button className={panel === 'release' ? 'primary' : ''} onClick={() => setPanel(panel === 'release' ? null : 'release')}>
              Release
            </button>
          </>
        )}
        <button className={panel === 'history' ? 'primary' : 'ghost'} onClick={() => setPanel(panel === 'history' ? null : 'history')}>
          History
        </button>
        {isAdmin && !a.closed && (
          <button className="ghost" disabled={busy} onClick={() => void close()}>
            Close
          </button>
        )}
      </div>
      {panel && panel !== 'history' && (
        <form className="inline-form" onSubmit={submit}>
          <Field label="Amount (₱)">
            <input type="number" step="0.01" min="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required autoFocus />
          </Field>
          {panel === 'spend' && (
            <Field label="For">
              <select value={categoryId} onChange={(e) => setCategoryId(e.target.value)} required>
                <option value="">Pick…</option>
                {categories
                  .filter((c) => c.active && c.direction === 'out')
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
              </select>
            </Field>
          )}
          <Field label="Date">
            <input type="date" value={day} onChange={(e) => setDay(e.target.value)} required />
          </Field>
          <Field label="Note">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={panel === 'add' ? 'e.g. From November sales' : ''} />
          </Field>
          {panel === 'spend' && (
            <Field label="Ref no.">
              <input value={reference} onChange={(e) => setReference(e.target.value)} />
            </Field>
          )}
          <div className="form-actions">
            <button className="primary" disabled={busy}>
              Save
            </button>
          </div>
        </form>
      )}
      <ErrorNote message={error} />
      {panel === 'history' && history.data && (
        <div className="table-scroll">
          <table className="table alloc-history">
            <tbody>
              {history.data.map((h, i) => (
                <tr key={i} className={h.voided ? 'voided' : ''}>
                  <td>{dayLabel(h.business_date)}</td>
                  <td>
                    {KIND_LABEL[h.kind]}
                    {h.voided && ' (voided)'}
                  </td>
                  <td className="num">{pesoExact(h.amount)}</td>
                  <td className="small">{h.note}</td>
                  <td className="small muted">{h.by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function NewAllocation({ accounts, onDone }: { accounts: FundAccount[]; onDone: () => void }) {
  const usable = accounts.filter((a) => a.active && a.kind !== 'petty_cash')
  const [accountId, setAccountId] = useState('')
  const [name, setName] = useState('')
  const [target, setTarget] = useState('')
  const [targetDate, setTargetDate] = useState('')
  const [initial, setInitial] = useState('')
  const { busy, error, run } = useAction()
  const chosen = usable.find((a) => a.id === accountId)
  async function submit(e: FormEvent) {
    e.preventDefault()
    const json = {
      account_id: accountId,
      name: name.trim(),
      target_amount: target || null,
      target_date: targetDate || null,
      initial_amount: initial || null,
    }
    if (await run(() => api('/fund-allocations', { method: 'POST', json }))) {
      setName('')
      setTarget('')
      setTargetDate('')
      setInitial('')
      onDone()
    }
  }
  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="For">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Christmas party" required />
      </Field>
      <Field label="Kept in" hint={chosen ? `${pesoExact(chosen.available)} free` : undefined}>
        <select value={accountId} onChange={(e) => setAccountId(e.target.value)} required>
          <option value="">Pick…</option>
          {usable.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} · {a.branch_name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Goal (₱, optional)">
        <input type="number" step="0.01" min="0" value={target} onChange={(e) => setTarget(e.target.value)} />
      </Field>
      <Field label="Needed by (optional)">
        <input type="date" value={targetDate} onChange={(e) => setTargetDate(e.target.value)} />
      </Field>
      <Field label="Set aside now (₱)">
        <input type="number" step="0.01" min="0" value={initial} onChange={(e) => setInitial(e.target.value)} />
      </Field>
      <div className="form-actions">
        <button className="primary" disabled={busy}>
          Add
        </button>
      </div>
      <ErrorNote message={error} />
    </form>
  )
}

export default function Allocations() {
  const { isAdmin } = useAuth()
  const business = useBusinessDay()
  const [showClosed, setShowClosed] = useState(false)
  const list = useLoad(() => api<FundAllocation[]>(`/fund-allocations?include_closed=${showClosed}`), [showClosed])
  const accounts = useLoad(() => api<FundAccount[]>('/accounts'), [])
  const categories = useLoad(() => api<FundCategory[]>('/fund-categories'), [])
  function changed() {
    void list.reload()
    void accounts.reload()
  }
  const total = (list.data ?? []).filter((a) => !a.closed).reduce((s, a) => s + Number(a.balance), 0)

  return (
    <div className="stack">
      <div className="board-toolbar">
        <h1>Allocations · {pesoExact(total)} set aside</h1>
        <label className="check">
          <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> Show closed
        </label>
      </div>
      <p className="muted">
        Money put aside for future costs. It stays in its account, but the account&apos;s &ldquo;free&rdquo; balance leaves it out,
        so it isn&apos;t spent by mistake.
      </p>
      {isAdmin && accounts.data && (
        <Card title="Set money aside for something new">
          <NewAllocation accounts={accounts.data} onDone={changed} />
        </Card>
      )}
      <ErrorNote message={list.error} />
      {list.data && list.data.length === 0 && <Empty>Nothing set aside yet.</Empty>}
      <div className="alloc-grid">
        {list.data?.map((a) => (
          <AllocationCard key={a.id} a={a} categories={categories.data ?? []} today={business.today()} onDone={changed} />
        ))}
      </div>
    </div>
  )
}
