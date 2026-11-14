import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { PaySettings, QuotaBasis } from '../../api'
import { useAuth } from '../../auth'
import { Card, ErrorNote, Field } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'
import { peso } from '../../time'

/** A target as typed: the boxes hold text until saved. */
interface TierForm {
  min_days: string
  threshold: string
  bonus: string
}

const WEEK_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

/** Daily allowance and the weekly sales quota bonus. Commission is set per service option. */
export default function PaySettingsPage() {
  const { isAdmin } = useAuth()
  const { data, error, reload } = useLoad(() => api<PaySettings>('/pay-settings'), [])
  const [allowance, setAllowance] = useState('')
  const [basis, setBasis] = useState<QuotaBasis>('sales')
  const [tiers, setTiers] = useState<TierForm[]>([])
  const [weekStart, setWeekStart] = useState(0)
  const [saved, setSaved] = useState(false)
  const save = useAction()

  useEffect(() => {
    if (!data) return
    setAllowance(String(Number(data.daily_allowance)))
    setBasis(data.quota_basis)
    setTiers(
      data.quota_tiers.map((t) => ({
        min_days: String(t.min_days ?? 0),
        threshold: String(Number(t.threshold)),
        bonus: String(Number(t.bonus)),
      })),
    )
    setWeekStart(data.week_starts_on)
  }, [data])

  function setTier(i: number, patch: Partial<TierForm>) {
    setTiers((ts) => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)))
    setSaved(false)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const ok = await save.run(() =>
      api('/pay-settings', {
        method: 'PUT',
        json: {
          daily_allowance: allowance || '0',
          quota_basis: basis,
          quota_tiers: tiers
            .filter((t) => t.threshold !== '' && t.bonus !== '')
            .map((t) => ({ min_days: Number(t.min_days) || 0, threshold: t.threshold, bonus: t.bonus })),
          week_starts_on: weekStart,
        },
      }),
    )
    if (ok) {
      setSaved(true)
      void reload()
    }
  }

  const unit = basis === 'sales' ? 'Sales of at least (₱)' : 'Services done, at least'
  return (
    <div className="stack">
      <h1>Pay rules</h1>
      {!isAdmin && <p className="note">You can view these rules. An admin can change them.</p>}
      <ErrorNote message={error} />
      {data && (
        <form className="stack" onSubmit={submit}>
          <Card title="Commission">
            <p className="muted">
              Commission is set on each service option (by length) on the Services and prices page. A therapist earns it
              for every guest they serve once the booking is checked out.
            </p>
          </Card>

          <Card title="Daily allowance">
            <Field label="Allowance per day clocked in (₱)" hint="Paid once for each day the desk clocked the therapist in">
              <input
                type="number"
                min={0}
                step="0.01"
                value={allowance}
                disabled={!isAdmin}
                onChange={(e) => {
                  setAllowance(e.target.value)
                  setSaved(false)
                }}
                required
              />
            </Field>
          </Card>

          <Card title="Weekly sales quota bonus">
            <p className="muted">
              Each week a therapist earns the bonus of a target when they worked at least its number of days and reached its sales. If they reach several targets they get the biggest bonus, not the sum.
            </p>
            <Field label="Count sales as">
              <select
                value={basis}
                disabled={!isAdmin}
                onChange={(e) => {
                  setBasis(e.target.value as QuotaBasis)
                  setSaved(false)
                }}
              >
                <option value="sales">Pesos of services done (menu price, before discounts)</option>
                <option value="services">Number of services done</option>
              </select>
            </Field>
            <Field label="Week starts on">
              <select
                value={weekStart}
                disabled={!isAdmin}
                onChange={(e) => {
                  setWeekStart(Number(e.target.value))
                  setSaved(false)
                }}
              >
                {WEEK_DAYS.map((d, i) => (
                  <option key={d} value={i}>
                    {d}
                  </option>
                ))}
              </select>
            </Field>
            {tiers.length === 0 && <p className="note">No targets yet, so no bonus is paid. Add the first target below.</p>}
            {tiers.map((t, i) => (
              <div className="tier-row" key={i}>
                <Field label="Days worked, at least">
                  <input
                    type="number"
                    min={0}
                    max={7}
                    step="1"
                    value={t.min_days}
                    disabled={!isAdmin}
                    onChange={(e) => setTier(i, { min_days: e.target.value })}
                  />
                </Field>
                <Field label={unit}>
                  <input
                    type="number"
                    min={0}
                    step={basis === 'sales' ? '0.01' : '1'}
                    value={t.threshold}
                    disabled={!isAdmin}
                    onChange={(e) => setTier(i, { threshold: e.target.value })}
                    required
                  />
                </Field>
                <Field label="Bonus (₱)">
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    value={t.bonus}
                    disabled={!isAdmin}
                    onChange={(e) => setTier(i, { bonus: e.target.value })}
                    required
                  />
                </Field>
                {isAdmin && (
                  <button
                    type="button"
                    onClick={() => {
                      setTiers((ts) => ts.filter((_, j) => j !== i))
                      setSaved(false)
                    }}
                  >
                    Remove
                  </button>
                )}
              </div>
            ))}
            {isAdmin && (
              <button
                type="button"
                onClick={() => {
                  setTiers((ts) => [...ts, { min_days: '6', threshold: '', bonus: '' }])
                  setSaved(false)
                }}
              >
                Add target
              </button>
            )}
            {tiers.length > 0 && (
              <p className="muted small">
                {[...tiers]
                  .filter((t) => t.threshold && t.bonus)
                  .sort((a, b) => Number(a.min_days) - Number(b.min_days) || Number(a.threshold) - Number(b.threshold))
                  .map(
                    (t) =>
                      `${Number(t.min_days) > 0 ? `${t.min_days}+ days and ` : ''}${
                        basis === 'sales' ? `${peso(t.threshold)} in sales` : `${t.threshold} services`
                      } → ${peso(t.bonus)}`,
                  )
                  .join(' · ')}
              </p>
            )}
          </Card>

          {isAdmin && (
            <div className="form-actions">
              <button className="primary" disabled={save.busy}>
                Save pay settings
              </button>
              {saved && <span className="muted">Saved.</span>}
            </div>
          )}
          <ErrorNote message={save.error} />
        </form>
      )}
    </div>
  )
}
