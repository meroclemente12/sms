import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { Branch, DiscountCode, DiscountCodeIn, DiscountUse, Service } from '../../api'
import { useAuth } from '../../auth'
import { Card, Empty, ErrorNote, Field, StatusPill } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'
import { clockIn, dayLabel, dayOf, peso } from '../../time'

const BLANK: DiscountCodeIn = {
  code: '',
  description: null,
  kind: 'percent',
  value: '',
  starts_on: null,
  ends_on: null,
  max_uses: null,
  once_per_client: false,
  branch_ids: [],
  service_ids: [],
  active: true,
}

function discountLabel(c: Pick<DiscountCode, 'kind' | 'value'>): string {
  return c.kind === 'percent' ? `${Number(c.value)}% off` : `${peso(c.value)} off`
}

function toggle(list: string[], id: string, on: boolean): string[] {
  return on ? [...list, id] : list.filter((x) => x !== id)
}

function Picks({
  label,
  all,
  options,
  chosen,
  onChange,
}: {
  label: string
  all: string
  options: { id: string; name: string }[]
  chosen: string[]
  onChange: (ids: string[]) => void
}) {
  return (
    <fieldset className="picks">
      <legend>{label}</legend>
      <label className="check">
        <input type="checkbox" checked={chosen.length === 0} onChange={(e) => e.target.checked && onChange([])} /> {all}
      </label>
      {options.map((o) => (
        <label key={o.id} className="check">
          <input
            type="checkbox"
            checked={chosen.includes(o.id)}
            onChange={(e) => onChange(toggle(chosen, o.id, e.target.checked))}
          />{' '}
          {o.name}
        </label>
      ))}
    </fieldset>
  )
}

function CodeForm({
  initial,
  branches,
  services,
  mustPickBranch,
  onSave,
  onCancel,
  busy,
}: {
  initial: DiscountCodeIn
  branches: Branch[]
  services: Service[]
  mustPickBranch: boolean
  onSave: (d: DiscountCodeIn) => void
  onCancel?: () => void
  busy: boolean
}) {
  const [d, setD] = useState<DiscountCodeIn>(initial)
  function submit(e: FormEvent) {
    e.preventDefault()
    onSave({ ...d, code: d.code.trim().toUpperCase(), description: d.description?.trim() || null })
  }
  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Code" hint="What the guest says at the desk, e.g. BDAY20">
        <input
          value={d.code}
          onChange={(e) => setD({ ...d, code: e.target.value.toUpperCase() })}
          required
          minLength={2}
          maxLength={40}
          pattern="[A-Za-z0-9_\-]+"
          autoFocus={!!onCancel}
        />
      </Field>
      <Field label="Description (optional)">
        <input
          value={d.description ?? ''}
          onChange={(e) => setD({ ...d, description: e.target.value })}
          placeholder="Birthday month promo"
          maxLength={200}
        />
      </Field>
      <Field label="Discount">
        <select value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value as DiscountCodeIn['kind'] })}>
          <option value="percent">Percent off</option>
          <option value="fixed">Peso amount off</option>
        </select>
      </Field>
      <Field label={d.kind === 'percent' ? 'Percent (%)' : 'Amount (₱)'}>
        <input
          type="number"
          min="0.01"
          max={d.kind === 'percent' ? 100 : undefined}
          step="0.01"
          value={d.value}
          onChange={(e) => setD({ ...d, value: e.target.value })}
          required
        />
      </Field>
      <Field label="From (optional)">
        <input type="date" value={d.starts_on ?? ''} onChange={(e) => setD({ ...d, starts_on: e.target.value || null })} />
      </Field>
      <Field label="Until (optional)">
        <input type="date" value={d.ends_on ?? ''} onChange={(e) => setD({ ...d, ends_on: e.target.value || null })} />
      </Field>
      <Field label="Total uses (optional)" hint="Leave empty for no limit">
        <input
          type="number"
          min={1}
          value={d.max_uses ?? ''}
          onChange={(e) => setD({ ...d, max_uses: e.target.value ? Number(e.target.value) : null })}
        />
      </Field>
      <label className="check">
        <input type="checkbox" checked={d.once_per_client} onChange={(e) => setD({ ...d, once_per_client: e.target.checked })} />{' '}
        Once per client
      </label>
      {branches.length > 1 || mustPickBranch ? (
        <Picks
          label="Branches"
          all={mustPickBranch ? 'Every branch (admins only)' : 'Every branch'}
          options={branches}
          chosen={d.branch_ids}
          onChange={(branch_ids) => setD({ ...d, branch_ids })}
        />
      ) : null}
      <Picks
        label="Services"
        all="Every service"
        options={services.filter((s) => s.active)}
        chosen={d.service_ids}
        onChange={(service_ids) => setD({ ...d, service_ids })}
      />
      <div className="form-actions">
        <button className="primary" disabled={busy || (mustPickBranch && d.branch_ids.length === 0)}>
          {onCancel ? 'Save' : 'Add code'}
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

function Uses({ code }: { code: DiscountCode }) {
  const { data, error } = useLoad(() => api<DiscountUse[]>(`/discount-codes/${code.id}/uses`), [code.id])
  const tz = 'Asia/Manila'
  return (
    <div className="uses">
      <ErrorNote message={error} />
      {data && data.length === 0 && <p className="muted small">Not used yet.</p>}
      {data && data.length > 0 && (
        <table className="table compact">
          <thead>
            <tr>
              <th>When</th>
              <th>Client</th>
              <th>Branch</th>
              <th>Off</th>
              <th>By</th>
            </tr>
          </thead>
          <tbody>
            {data.map((u) => (
              <tr key={u.booking_id} className={u.counted ? '' : 'muted'}>
                <td>
                  {dayLabel(dayOf(u.redeemed_at, tz))} {clockIn(u.redeemed_at, tz)}
                </td>
                <td>{u.client_name}</td>
                <td>{u.branch_name}</td>
                <td>
                  {peso(u.amount)}
                  {!u.counted && <span className="small"> · {u.booking_status.replace('_', ' ')}, not counted</span>}
                </td>
                <td>{u.redeemed_by_name ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function limits(c: DiscountCode, branches: Branch[], services: Service[]): string {
  const parts: string[] = []
  if (c.starts_on || c.ends_on) {
    parts.push(
      c.starts_on && c.ends_on
        ? `${dayLabel(c.starts_on)} – ${dayLabel(c.ends_on)}`
        : c.starts_on
          ? `from ${dayLabel(c.starts_on)}`
          : `until ${dayLabel(c.ends_on!)}`,
    )
  }
  if (c.once_per_client) parts.push('once per client')
  if (c.branch_ids.length) parts.push(branches.filter((b) => c.branch_ids.includes(b.id)).map((b) => b.name).join(', '))
  if (c.service_ids.length) parts.push(services.filter((s) => c.service_ids.includes(s.id)).map((s) => s.name).join(', '))
  return parts.join(' · ')
}

function CodeRow({
  c,
  branches,
  services,
  canEdit,
  mustPickBranch,
  onDone,
}: {
  c: DiscountCode
  branches: Branch[]
  services: Service[]
  canEdit: boolean
  mustPickBranch: boolean
  onDone: () => void
}) {
  const [mode, setMode] = useState<'view' | 'edit' | 'uses'>('view')
  const { busy, error, run } = useAction()
  const draft: DiscountCodeIn = {
    code: c.code,
    description: c.description,
    kind: c.kind,
    value: String(Number(c.value)),
    starts_on: c.starts_on,
    ends_on: c.ends_on,
    max_uses: c.max_uses,
    once_per_client: c.once_per_client,
    branch_ids: c.branch_ids,
    service_ids: c.service_ids,
    active: c.active,
  }
  async function save(d: DiscountCodeIn) {
    if (await run(() => api(`/discount-codes/${c.id}`, { method: 'PUT', json: d }))) {
      setMode('view')
      onDone()
    }
  }
  if (mode === 'edit')
    return (
      <tr>
        <td colSpan={5}>
          <CodeForm
            initial={draft}
            branches={branches}
            services={services}
            mustPickBranch={mustPickBranch}
            busy={busy}
            onSave={(d) => void save(d)}
            onCancel={() => setMode('view')}
          />
          <ErrorNote message={error} />
        </td>
      </tr>
    )
  return (
    <>
      <tr>
        <td>
          <strong className="mono">{c.code}</strong>
          {c.description && <div className="muted small">{c.description}</div>}
        </td>
        <td>
          {discountLabel(c)}
          <div className="muted small">{limits(c, branches, services)}</div>
        </td>
        <td>
          {c.uses}
          {c.max_uses !== null && ` of ${c.max_uses}`} used
          {c.uses > 0 && <div className="muted small">{peso(c.discount_total)} given</div>}
        </td>
        <td>
          {canEdit ? (
            <label className="check">
              <input type="checkbox" checked={c.active} disabled={busy} onChange={(e) => void save({ ...draft, active: e.target.checked })} />{' '}
              On
            </label>
          ) : (
            <StatusPill active={c.active} />
          )}
          <ErrorNote message={error} />
        </td>
        <td className="row-actions">
          <button className="ghost" onClick={() => setMode(mode === 'uses' ? 'view' : 'uses')}>
            {mode === 'uses' ? 'Hide uses' : 'Uses'}
          </button>
          {canEdit && (
            <button className="ghost" onClick={() => setMode('edit')}>
              Edit
            </button>
          )}
          {canEdit && c.uses === 0 && (
            <button
              className="ghost danger"
              disabled={busy}
              onClick={() => void run(async () => {
                await api(`/discount-codes/${c.id}`, { method: 'DELETE' })
                onDone()
              })}
            >
              Delete
            </button>
          )}
        </td>
      </tr>
      {mode === 'uses' && (
        <tr>
          <td colSpan={5}>
            <Uses code={c} />
          </td>
        </tr>
      )}
    </>
  )
}

export default function DiscountCodesPage() {
  const { me, isAdmin } = useAuth()
  const [showOff, setShowOff] = useState(false)
  const codes = useLoad(() => api<DiscountCode[]>(`/discount-codes?include_inactive=${showOff}`), [showOff])
  const branches = useLoad(() => api<Branch[]>('/branches'), [])
  const services = useLoad(() => api<Service[]>('/services'), [])
  const add = useAction()
  const [formKey, setFormKey] = useState(0)
  const mine = new Set(me?.branches.map((b) => b.id))
  // A manager works only with codes limited to their own branches.
  const editable = (c: DiscountCode) => isAdmin || (c.branch_ids.length > 0 && c.branch_ids.every((b) => mine.has(b)))
  const branchOptions = (branches.data ?? []).filter((b) => isAdmin || mine.has(b.id))

  async function create(d: DiscountCodeIn) {
    if (await add.run(() => api('/discount-codes', { method: 'POST', json: d }))) {
      setFormKey((k) => k + 1)
      void codes.reload()
    }
  }

  return (
    <div className="stack">
      <h1>Discount codes</h1>
      <Card
        title="Codes"
        actions={
          <label className="check">
            <input type="checkbox" checked={showOff} onChange={(e) => setShowOff(e.target.checked)} /> Show switched-off codes
          </label>
        }
      >
        <p className="muted">
          The front desk types a code at check-out to take a percentage or a peso amount off. Uses on cancelled bookings
          don&apos;t count. A code that was used can be switched off but not deleted, so its history stays.
        </p>
        <ErrorNote message={codes.error} />
        {codes.data && codes.data.length === 0 && <Empty>No discount codes yet.</Empty>}
        <div className="table-scroll">
          <table className="table">
            <tbody>
              {codes.data?.map((c) => (
                <CodeRow
                  key={c.id}
                  c={c}
                  branches={branches.data ?? []}
                  services={services.data ?? []}
                  canEdit={editable(c)}
                  mustPickBranch={!isAdmin}
                  onDone={() => void codes.reload()}
                />
              ))}
            </tbody>
          </table>
        </div>
        <h3>Add a code</h3>
        <CodeForm
          key={`${formKey}-${branchOptions.length}`}
          initial={{ ...BLANK, branch_ids: isAdmin ? [] : branchOptions.length === 1 ? [branchOptions[0].id] : [] }}
          branches={branchOptions}
          services={services.data ?? []}
          mustPickBranch={!isAdmin}
          busy={add.busy}
          onSave={(d) => void create(d)}
        />
        <ErrorNote message={add.error} />
      </Card>
    </div>
  )
}
