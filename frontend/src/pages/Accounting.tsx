import { useState } from 'react'
import { api, ACCOUNT_KIND_LABEL } from '../api'
import type { FundAccount, FundCategory, FundEntryList } from '../api'
import { useAuth, useBusinessDay } from '../auth'
import { Card, Empty, ErrorNote } from '../components/ui'
import { useLoad } from '../components/hooks'
import { EntriesTable } from '../accounting/Entries'
import { RecordForm, TransferForm } from '../accounting/Forms'
import { addDays, pesoExact } from '../time'

type Show = 'all' | 'sales' | 'in' | 'out' | 'transfers'

const SHOWS: { value: Show; label: string }[] = [
  { value: 'all', label: 'Everything' },
  { value: 'sales', label: 'Sales' },
  { value: 'out', label: 'Money out' },
  { value: 'in', label: 'Other money in' },
  { value: 'transfers', label: 'Transfers' },
]

type Panel = null | 'out' | 'in' | 'transfer'

export default function Accounting() {
  const { branch, me } = useAuth()
  const business = useBusinessDay()
  const today = business.today()
  const tz = branch?.timezone ?? 'Asia/Manila'
  const [from, setFrom] = useState(addDays(today, -6))
  const [to, setTo] = useState(today)
  const [accountId, setAccountId] = useState('')
  const [branchId, setBranchId] = useState('')
  const [show, setShow] = useState<Show>('all')
  const [panel, setPanel] = useState<Panel>(null)

  const accounts = useLoad(() => api<FundAccount[]>('/accounts'), [])
  const categories = useLoad(() => api<FundCategory[]>('/fund-categories'), [])
  const list = useLoad(() => {
    const q = new URLSearchParams({ date_from: from, date_to: to, show })
    if (accountId) q.set('account_id', accountId)
    if (branchId) q.set('branch_id', branchId)
    return api<FundEntryList>(`/fund-entries?${q}`)
  }, [from, to, accountId, branchId, show])

  function changed() {
    void accounts.reload()
    void list.reload()
  }

  const active = (accounts.data ?? []).filter((a) => a.active)
  const total = active.reduce((s, a) => s + Number(a.balance), 0)
  const branches = me?.branches ?? []

  return (
    <div className="stack accounting-page">
      <div className="board-toolbar">
        <h1>Accounting</h1>
        <div className="toolbar">
          <button className={panel === 'out' ? 'primary' : ''} onClick={() => setPanel(panel === 'out' ? null : 'out')}>
            Record expense or bill
          </button>
          <button className={panel === 'in' ? 'primary' : ''} onClick={() => setPanel(panel === 'in' ? null : 'in')}>
            Record other income
          </button>
          <button
            className={panel === 'transfer' ? 'primary' : ''}
            onClick={() => setPanel(panel === 'transfer' ? null : 'transfer')}
          >
            Move money / refill petty cash
          </button>
        </div>
      </div>

      {panel && accounts.data && categories.data && (
        <Card title={panel === 'transfer' ? 'Move money between accounts' : panel === 'out' ? 'Money out' : 'Money in'}>
          {panel === 'transfer' ? (
            <TransferForm accounts={accounts.data} today={today} onDone={changed} onCancel={() => setPanel(null)} />
          ) : (
            <RecordForm
              key={panel}
              accounts={accounts.data}
              categories={categories.data}
              today={today}
              defaultDirection={panel}
              onDone={changed}
              onCancel={() => setPanel(null)}
            />
          )}
        </Card>
      )}

      <Card title={`Balances · ${pesoExact(total)}`}>
        <ErrorNote message={accounts.error} />
        <div className="fund-grid">
          {active.map((a) => (
            <button
              key={a.id}
              className={`fund-card ${accountId === a.id ? 'on' : ''}`}
              aria-pressed={accountId === a.id}
              onClick={() => setAccountId(accountId === a.id ? '' : a.id)}
            >
              <span className="muted small">
                {ACCOUNT_KIND_LABEL[a.kind]} · {a.branch_name ?? 'All branches'}
              </span>
              <strong>{a.name}</strong>
              <span className={`fund-amount ${Number(a.balance) < 0 ? 'neg' : ''}`}>{pesoExact(a.balance)}</span>
              {Number(a.unverified) > 0 && (
                <span className="muted small">{pesoExact(a.unverified)} not verified yet</span>
              )}
            </button>
          ))}
        </div>
      </Card>

      <Card
        title="Transactions"
        actions={
          <div className="toolbar">
            <div className="segmented-buttons" role="group" aria-label="Show">
              {SHOWS.map((s) => (
                <button key={s.value} className={show === s.value ? 'primary' : ''} aria-pressed={show === s.value} onClick={() => setShow(s.value)}>
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        }
      >
        <div className="toolbar ledger-filters">
          <label className="muted" htmlFor="fund-from">
            From
          </label>
          <input id="fund-from" type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
          <label className="muted" htmlFor="fund-to">
            to
          </label>
          <input id="fund-to" type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} />
          <select aria-label="Account" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            <option value="">Every account</option>
            {(accounts.data ?? []).map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
                {a.branch_name ? ` · ${a.branch_name}` : ''}
              </option>
            ))}
          </select>
          {branches.length > 1 && (
            <select aria-label="Branch" value={branchId} onChange={(e) => setBranchId(e.target.value)}>
              <option value="">Every branch</option>
              {branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          )}
        </div>
        <ErrorNote message={list.error} />
        {list.data && (
          <p className="ledger-sum">
            {list.data.opening_balance !== null && (
              <span>
                Start <strong>{pesoExact(list.data.opening_balance)}</strong>
              </span>
            )}
            <span>
              In <strong>{pesoExact(list.data.money_in)}</strong>
            </span>
            <span>
              Out <strong>{pesoExact(list.data.money_out)}</strong>
            </span>
            {list.data.closing_balance !== null && (
              <span>
                End <strong>{pesoExact(list.data.closing_balance)}</strong>
              </span>
            )}
          </p>
        )}
        {list.data && list.data.items.length === 0 && <Empty>Nothing in these days.</Empty>}
        {list.data && list.data.items.length > 0 && (
          <EntriesTable
            items={list.data.items}
            tz={tz}
            showAccount={!accountId}
            showBranch={branches.length > 1 && !branchId}
            onChanged={changed}
          />
        )}
      </Card>
    </div>
  )
}
