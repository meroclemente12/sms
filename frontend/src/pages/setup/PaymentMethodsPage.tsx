import { useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../../api'
import type { PaymentKind, PaymentMethod } from '../../api'
import { useAuth } from '../../auth'
import { Card, Empty, ErrorNote, Field } from '../../components/ui'
import { useAction, useLoad } from '../../components/hooks'

type Draft = { name: string; kind: PaymentKind; needs_reference: boolean; active: boolean; sort_order: number }

function MethodForm({
  initial,
  onSave,
  onCancel,
  busy,
}: {
  initial: Draft
  onSave: (d: Draft) => void
  onCancel?: () => void
  busy: boolean
}) {
  const [d, setD] = useState<Draft>(initial)
  function submit(e: FormEvent) {
    e.preventDefault()
    onSave({ ...d, name: d.name.trim() })
  }
  return (
    <form className="inline-form" onSubmit={submit}>
      <Field label="Name" hint="As the desk and accountant know it, e.g. GCash or BDO transfer">
        <input value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} required autoFocus={!!onCancel} />
      </Field>
      <Field label="Type">
        <select value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value as PaymentKind })}>
          <option value="cashless">Cashless (checked by the accountant)</option>
          <option value="cash">Cash</option>
        </select>
      </Field>
      {d.kind === 'cashless' && (
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

function MethodRow({ m, canEdit, onDone }: { m: PaymentMethod; canEdit: boolean; onDone: () => void }) {
  const [editing, setEditing] = useState(false)
  const { busy, error, run } = useAction()
  async function save(d: Draft) {
    if (await run(() => api(`/payment-methods/${m.id}`, { method: 'PUT', json: d }))) {
      setEditing(false)
      onDone()
    }
  }
  const draft: Draft = { name: m.name, kind: m.kind, needs_reference: m.needs_reference, active: m.active, sort_order: m.sort_order }
  if (editing)
    return (
      <tr>
        <td colSpan={4}>
          <MethodForm initial={draft} busy={busy} onSave={(d) => void save(d)} onCancel={() => setEditing(false)} />
          <ErrorNote message={error} />
        </td>
      </tr>
    )
  return (
    <tr>
      <td>
        <strong>{m.name}</strong>
      </td>
      <td>
        {m.kind === 'cash' ? 'Cash' : 'Cashless'}
        {m.kind === 'cashless' && <span className="muted small">{m.needs_reference ? ' · reference number required' : ' · no reference'}</span>}
      </td>
      <td>
        {canEdit ? (
          <label className="check">
            <input type="checkbox" checked={m.active} disabled={busy} onChange={(e) => void save({ ...draft, active: e.target.checked })} /> In use
          </label>
        ) : (
          <span className={m.active ? 'pill pill-on' : 'pill pill-off'}>{m.active ? 'In use' : 'Not in use'}</span>
        )}
        <ErrorNote message={editing ? null : error} />
      </td>
      <td className="row-actions">
        {canEdit && (
          <button className="ghost" onClick={() => setEditing(true)}>
            Edit
          </button>
        )}
      </td>
    </tr>
  )
}

export default function PaymentMethodsPage() {
  const { branch, canManage } = useAuth()
  const branchId = branch?.id ?? ''
  const { data, error, reload } = useLoad(
    () => (branchId ? api<PaymentMethod[]>(`/branches/${branchId}/payment-methods`) : Promise.resolve([])),
    [branchId],
  )
  const add = useAction()
  const [formKey, setFormKey] = useState(0)

  async function create(d: Draft) {
    const json = { ...d, sort_order: (data?.length ?? 0) + 1 }
    if (await add.run(() => api(`/branches/${branchId}/payment-methods`, { method: 'POST', json }))) {
      setFormKey((k) => k + 1)
      void reload()
    }
  }

  return (
    <div className="stack">
      <h1>Payment methods</h1>
      <Card title={`How guests pay at ${branch?.name ?? 'this branch'}`}>
        <p className="muted">
          The front desk picks one of these at check-out. Cashless payments (GCash, bank transfers, cards) go to the
          accountant&apos;s Payments screen to be checked against the statements. Each branch has its own list.
        </p>
        <ErrorNote message={error} />
        {data && data.length === 0 && <Empty>No payment methods yet.</Empty>}
        <div className="table-scroll">
          <table className="table">
            <tbody>
              {data?.map((m) => (
                <MethodRow key={m.id} m={m} canEdit={canManage} onDone={() => void reload()} />
              ))}
            </tbody>
          </table>
        </div>
        {canManage && branchId && (
          <>
            <h3>Add a method</h3>
            <MethodForm
              key={formKey}
              initial={{ name: '', kind: 'cashless', needs_reference: true, active: true, sort_order: 0 }}
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
