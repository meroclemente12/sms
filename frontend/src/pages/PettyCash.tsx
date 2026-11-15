import { useState } from 'react'
import { api } from '../api'
import type { FundAccount, FundCategory, FundEntryList } from '../api'
import { useAuth, useBusinessDay } from '../auth'
import { Card, Empty, ErrorNote } from '../components/ui'
import { useLoad } from '../components/hooks'
import { EntriesTable } from '../accounting/Entries'
import { RecordForm } from '../accounting/Forms'
import { addDays, pesoExact } from '../time'

/** The front desk's petty cash: its balance, and expenses paid out of it today. */
export default function PettyCash() {
  const { branch } = useAuth()
  const business = useBusinessDay()
  const today = business.today()
  const branchId = branch?.id ?? ''
  const [days, setDays] = useState(1)

  const accounts = useLoad(
    () => (branchId ? api<FundAccount[]>(`/accounts?branch_id=${branchId}`) : Promise.resolve([])),
    [branchId],
  )
  const categories = useLoad(() => api<FundCategory[]>('/fund-categories'), [])
  const petty = (accounts.data ?? []).filter((a) => a.kind === 'petty_cash' && a.branch_id === branchId && a.active)
  const pettyId = petty[0]?.id ?? ''
  const list = useLoad(() => {
    if (!pettyId) return Promise.resolve(null)
    const q = new URLSearchParams({ account_id: pettyId, date_from: addDays(today, -(days - 1)), date_to: today })
    return api<FundEntryList>(`/fund-entries?${q}`)
  }, [pettyId, days, today])

  function changed() {
    void accounts.reload()
    void list.reload()
  }

  const deskCats = (categories.data ?? []).filter((c) => c.desk_can_use && c.direction === 'out')

  return (
    <div className="stack">
      <h1>Petty cash · {branch?.name}</h1>
      <ErrorNote message={accounts.error} />
      {accounts.data && petty.length === 0 && <Empty>This branch has no petty cash account yet. Ask an admin.</Empty>}
      {petty[0] && (
        <>
          <div className="fund-grid">
            <div className="fund-card">
              <span className="muted small">In the box now</span>
              <span className={`fund-amount ${Number(petty[0].balance) < 0 ? 'neg' : ''}`}>{pesoExact(petty[0].balance)}</span>
            </div>
          </div>
          <Card title="Pay something from petty cash">
            <p className="muted">For small expenses like laundry or supplies. Keep the receipt and write its number.</p>
            {categories.data && (
              <RecordForm accounts={petty} categories={deskCats} today={today} deskMode onDone={changed} />
            )}
          </Card>
          <Card
            title={days === 1 ? 'Today' : `Last ${days} days`}
            actions={
              <div className="segmented-buttons" role="group" aria-label="Days">
                {[1, 7, 30].map((d) => (
                  <button key={d} className={days === d ? 'primary' : ''} aria-pressed={days === d} onClick={() => setDays(d)}>
                    {d === 1 ? 'Today' : `${d} days`}
                  </button>
                ))}
              </div>
            }
          >
            <ErrorNote message={list.error} />
            {list.data && list.data.items.length === 0 && <Empty>Nothing paid out or added yet.</Empty>}
            {list.data && list.data.items.length > 0 && (
              <EntriesTable
                items={list.data.items}
                tz={branch?.timezone ?? 'Asia/Manila'}
                showAccount={false}
                showBranch={false}
                onChanged={changed}
              />
            )}
          </Card>
        </>
      )}
    </div>
  )
}
