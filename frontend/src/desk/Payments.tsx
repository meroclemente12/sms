import { useState } from 'react'
import { api, PAYMENT_STATUS_LABEL } from '../api'
import type { Booking, Payment, PaymentLineIn, PaymentMethod, PaymentStatus } from '../api'
import { ErrorNote, Field } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import { clockIn, dayOf, dayLabel, peso } from '../time'

export function PaymentBadge({ status }: { status: PaymentStatus }) {
  return <span className={`badge pay-${status}`}>{PAYMENT_STATUS_LABEL[status]}</span>
}

// `auto`: the amount is still the rest of the bill, filled in for the desk.
type Line = { method_id: string; amount: string; reference: string; auto?: boolean }

function useMethods(branchId: string) {
  return useLoad(() => api<PaymentMethod[]>(`/branches/${branchId}/payment-methods`), [branchId])
}

function LineFields({
  line,
  methods,
  onChange,
  onRemove,
}: {
  line: Line
  methods: PaymentMethod[]
  onChange: (l: Line) => void
  onRemove?: () => void
}) {
  const method = methods.find((m) => m.id === line.method_id)
  return (
    <div className="pay-line">
      <Field label="Paid by">
        <select value={line.method_id} onChange={(e) => onChange({ ...line, method_id: e.target.value })}>
          {methods.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Amount (₱)">
        <input type="number" min="1" step="0.01" value={line.amount} onChange={(e) => onChange({ ...line, amount: e.target.value })} />
      </Field>
      {method?.kind === 'cashless' && (
        <Field label={method.needs_reference ? 'Reference no.' : 'Reference no. (optional)'}>
          <input
            value={line.reference}
            onChange={(e) => onChange({ ...line, reference: e.target.value })}
            placeholder={method.name === 'GCash' ? '13-digit GCash ref' : 'From the receipt or app'}
            maxLength={100}
          />
        </Field>
      )}
      {onRemove && (
        <button type="button" className="ghost" onClick={onRemove} aria-label="Remove this payment">
          Remove
        </button>
      )}
    </div>
  )
}

function missingRef(line: Line, methods: PaymentMethod[]): boolean {
  const m = methods.find((x) => x.id === line.method_id)
  return !!m && m.kind === 'cashless' && m.needs_reference && !line.reference.trim()
}

function toJson(lines: Line[]): PaymentLineIn[] {
  return lines.map((l) => ({ method_id: l.method_id, amount: l.amount, reference: l.reference.trim() || null }))
}

/** Check-out: one line per way the guest paid (cash plus GCash, for example). */
export function Checkout({
  booking,
  busy,
  onCancel,
  onDone,
}: {
  booking: Booking
  busy: boolean
  onCancel: () => void
  onDone: (payments: PaymentLineIn[]) => void
}) {
  const { data, error } = useMethods(booking.branch_id)
  const methods = (data ?? []).filter((m) => m.active)
  const [lines, setLines] = useState<Line[] | null>(null)
  const current: Line[] = lines ?? (methods.length ? [{ method_id: methods[0].id, amount: String(Number(booking.total)), reference: '' }] : [])
  const paid = current.reduce((sum, l) => sum + (Number(l.amount) || 0), 0)
  const due = Number(booking.total)
  const invalid = current.length === 0 || current.some((l) => !(Number(l.amount) > 0) || missingRef(l, methods))

  function set(i: number, l: Line) {
    const amountChanged = l.amount !== current[i].amount
    let next = current.map((x, n) => (n === i ? (amountChanged ? { ...l, auto: false } : l) : x))
    // Keep the split adding up: an untouched later line takes whatever is left.
    const last = next.length - 1
    if (amountChanged && last !== i && next[last].auto) {
      const others = next.reduce((sum, x, n) => (n === last ? sum : sum + (Number(x.amount) || 0)), 0)
      const rest = Math.max(due - others, 0)
      next = next.map((x, n) => (n === last ? { ...x, amount: rest ? String(rest) : '' } : x))
    }
    setLines(next)
  }

  return (
    <div className="inline-form column">
      <h3>Check out</h3>
      <ErrorNote message={error} />
      {data && methods.length === 0 && <p className="note">This branch has no payment methods. A manager can add them in Setup.</p>}
      {current.map((l, i) => (
        <LineFields
          key={i}
          line={l}
          methods={methods}
          onChange={(x) => set(i, x)}
          onRemove={current.length > 1 ? () => setLines(current.filter((_, n) => n !== i)) : undefined}
        />
      ))}
      {methods.length > 0 && (
        <button
          type="button"
          className="ghost add-line"
          onClick={() => {
            const rest = Math.max(due - paid, 0)
            const cashless = methods.find((m) => m.kind === 'cashless') ?? methods[0]
            setLines([...current, { method_id: cashless.id, amount: rest ? String(rest) : '', reference: '', auto: true }])
          }}
        >
          + Split the bill (add another payment)
        </button>
      )}
      <p className="muted small">
        Total {peso(String(due))} · paid {peso(String(paid))}
        {paid !== due && <strong className="warn"> · {paid < due ? `${peso(String(due - paid))} short` : `${peso(String(paid - due))} over`}</strong>}
      </p>
      <div className="form-actions">
        <button className="primary" disabled={busy || invalid} onClick={() => onDone(toJson(current))}>
          Complete
        </button>
        <button onClick={onCancel}>Back</button>
      </div>
    </div>
  )
}

/** Add a payment after check-out, or fix one the accountant flagged. */
function PaymentForm({
  booking,
  payment,
  onBack,
  onSaved,
}: {
  booking: Booking
  payment?: Payment
  onBack: () => void
  onSaved: () => void
}) {
  const { data } = useMethods(booking.branch_id)
  const methods = (data ?? []).filter((m) => m.active || m.id === payment?.method_id)
  const [line, setLine] = useState<Line | null>(
    payment ? { method_id: payment.method_id, amount: String(Number(payment.amount)), reference: payment.reference ?? '' } : null,
  )
  const current = line ?? (methods.length ? { method_id: methods[0].id, amount: '', reference: '' } : null)
  const { busy, error, run } = useAction()
  if (!current) return null
  const invalid = !(Number(current.amount) > 0) || missingRef(current, methods)
  return (
    <div className="inline-form column">
      <h3>{payment ? 'Correct this payment' : 'Add a payment'}</h3>
      {payment?.check_note && <p className="flag-note">Accountant: {payment.check_note}</p>}
      <LineFields line={current} methods={methods} onChange={setLine} />
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy || invalid}
          onClick={() =>
            void run(async () => {
              const json = toJson([current])[0]
              if (payment) await api(`/payments/${payment.id}`, { method: 'PUT', json })
              else await api(`/bookings/${booking.id}/payments`, { method: 'POST', json })
              onSaved()
            })
          }
        >
          {payment ? 'Save and send for checking' : 'Add payment'}
        </button>
        <button onClick={onBack}>Back</button>
      </div>
      <ErrorNote message={error} />
    </div>
  )
}

/** The payments on a booking, as the front desk sees them in the booking panel. */
export function BookingPayments({ booking, tz, onChanged }: { booking: Booking; tz: string; onChanged: () => void }) {
  const [editing, setEditing] = useState<Payment | 'new' | null>(null)
  const canAdd = booking.status === 'completed'
  if (booking.payments.length === 0 && !canAdd) return null
  return (
    <section className="booking-payments">
      <h3>Payments</h3>
      {booking.payments.length === 0 && <p className="muted small">No payment logged.</p>}
      <ul className="pay-list">
        {booking.payments.map((p) => (
          <li key={p.id} className={p.status === 'flagged' ? 'flagged' : ''}>
            <div className="pay-main">
              <strong>{peso(p.amount)}</strong> {p.method_name}
              {p.reference && <span className="muted"> · ref {p.reference}</span>}
              <PaymentBadge status={p.status} />
            </div>
            <div className="muted small">
              Logged {dayLabel(dayOf(p.recorded_at, tz))} {clockIn(p.recorded_at, tz)}
              {p.recorded_by_name && ` by ${p.recorded_by_name}`}
              {p.checked_by_name && p.status !== 'pending' && ` · checked by ${p.checked_by_name}`}
            </div>
            {p.status === 'flagged' && p.check_note && <p className="flag-note">Accountant: {p.check_note}</p>}
            {(p.status === 'flagged' || p.status === 'pending' || p.status === 'not_needed') && editing === null && (
              <button className={p.status === 'flagged' ? 'primary small-btn' : 'ghost small-btn'} onClick={() => setEditing(p)}>
                {p.status === 'flagged' ? 'Fix it' : 'Correct'}
              </button>
            )}
          </li>
        ))}
      </ul>
      {canAdd && editing === null && (
        <button className="ghost" onClick={() => setEditing('new')}>
          + Add a payment
        </button>
      )}
      {editing !== null && (
        <PaymentForm
          booking={booking}
          payment={editing === 'new' ? undefined : editing}
          onBack={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            onChanged()
          }}
        />
      )}
    </section>
  )
}
