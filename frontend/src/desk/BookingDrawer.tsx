import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import type { Booking, BookingEvent, BookingStatus, Channel, Price, Slot } from '../api'
import { Drawer, ErrorNote, Field, StatusBadge } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import { clockIn, dayOf, longDayLabel, peso, timeIn } from '../time'
import SlotPicker from './SlotPicker'
import { BookingPayments, Checkout } from './Payments'
import type { Names } from './SlotPicker'

// Next steps the desk can take from each status (the API enforces the same rules).
const NEXT: Record<BookingStatus, { to: BookingStatus; label: string; primary?: boolean }[]> = {
  booked: [
    { to: 'confirmed', label: 'Confirm' },
    { to: 'checked_in', label: 'Check in', primary: true },
  ],
  confirmed: [{ to: 'checked_in', label: 'Check in', primary: true }],
  checked_in: [{ to: 'in_service', label: 'Start service', primary: true }],
  in_service: [],
  completed: [],
  cancelled: [],
  no_show: [],
}

const MOVABLE: BookingStatus[] = ['booked', 'confirmed', 'checked_in']

export default function BookingDrawer({
  bookingId,
  tz,
  names,
  onClose,
  onChanged,
}: {
  bookingId: string
  tz: string
  names: Names
  onClose: () => void
  onChanged: () => void
}) {
  const { data: b, error, setData, reload } = useLoad(() => api<Booking>(`/bookings/${bookingId}`), [bookingId])
  const [mode, setMode] = useState<'view' | 'move' | 'edit' | 'cancel' | 'checkout'>('view')
  const action = useAction()

  function updated(next: Booking) {
    setData(next)
    setMode('view')
    onChanged()
  }

  async function setStatus(to: BookingStatus, body?: object) {
    await action.run(async () =>
      updated(await api<Booking>(`/bookings/${bookingId}/status/${to}`, { method: 'POST', json: body ?? {} })),
    )
  }

  return (
    <Drawer title={b ? b.client.name : 'Booking'} onClose={onClose}>
      <ErrorNote message={error} />
      {b && (
        <div className="booking-detail">
          <div className="detail-head">
            <StatusBadge status={b.status} />
            <span className="muted">
              {b.channel_name} · booked {longDayLabel(dayOf(b.created_at, tz))}
            </span>
          </div>

          <dl className="facts">
            <dt>When</dt>
            <dd>
              {longDayLabel(dayOf(b.starts_at, tz))}
              <br />
              {clockIn(b.starts_at, tz)} – {clockIn(b.ends_at, tz)}
            </dd>
            <dt>Service</dt>
            <dd>
              {b.service_name} · {b.variant_label}
            </dd>
            {b.items.map((i, n) => (
              <div key={i.id} className="facts-row">
                <dt>{b.items.length > 1 ? `Guest ${n + 1}` : 'Therapist'}</dt>
                <dd>
                  {b.items.length > 1 && <>{n === 0 ? b.client.name : (i.guest_name ?? 'Guest')}: </>}
                  <strong>{i.therapist_name}</strong>
                  {i.therapist_requested && <span className="tag">requested</span>}
                  {i.preferred_gender && <span className="tag">{i.preferred_gender} preferred</span>}
                  <br />
                  <span className="muted">
                    {i.room_name} · {i.bed_label}
                  </span>
                </dd>
              </div>
            ))}
            <dt>Price</dt>
            <dd>
              {peso(b.total)}
              {b.amount_paid !== null && <span className="muted"> · paid {peso(b.amount_paid)}</span>}
            </dd>
            <dt>Client</dt>
            <dd>
              <Link to={`/clients/${b.client.id}`}>{b.client.name}</Link>{' '}
              <span className="muted">{b.client.mobile}</span>
              {b.client.allergies && <p className="allergy">Allergies: {b.client.allergies}</p>}
              {b.client.notes && <p className="muted small">{b.client.notes}</p>}
            </dd>
            {b.notes && (
              <>
                <dt>Notes</dt>
                <dd>{b.notes}</dd>
              </>
            )}
            {b.cancel_reason && (
              <>
                <dt>Reason</dt>
                <dd>{b.cancel_reason}</dd>
              </>
            )}
          </dl>

          {mode === 'view' && (
            <div className="status-actions">
              {NEXT[b.status].map((n) => (
                <button
                  key={n.to}
                  className={n.primary ? 'primary' : ''}
                  disabled={action.busy}
                  onClick={() => void setStatus(n.to)}
                >
                  {n.label}
                </button>
              ))}
              {b.status === 'in_service' && (
                <button className="primary" onClick={() => setMode('checkout')}>
                  Complete and check out
                </button>
              )}
              {MOVABLE.includes(b.status) && <button onClick={() => setMode('move')}>Change time or therapist</button>}
              {b.status !== 'cancelled' && <button onClick={() => setMode('edit')}>Edit notes or channel</button>}
              {(b.status === 'booked' || b.status === 'confirmed' || b.status === 'checked_in') && (
                <button className="danger" onClick={() => setMode('cancel')}>
                  Cancel booking
                </button>
              )}
              {(b.status === 'booked' || b.status === 'confirmed') && (
                <button disabled={action.busy} onClick={() => void setStatus('no_show')}>
                  No-show
                </button>
              )}
            </div>
          )}
          <ErrorNote message={action.error} />

          {mode === 'checkout' && (
            <Checkout booking={b} busy={action.busy} onCancel={() => setMode('view')} onDone={(payments) => void setStatus('completed', { payments })} />
          )}
          {mode === 'cancel' && (
            <CancelForm busy={action.busy} onBack={() => setMode('view')} onCancel={(reason) => void setStatus('cancelled', { reason: reason || null })} />
          )}
          {mode === 'edit' && <EditForm booking={b} onBack={() => setMode('view')} onSaved={updated} />}
          {mode === 'move' && <MoveForm booking={b} tz={tz} names={names} onBack={() => setMode('view')} onMoved={updated} />}

          {mode === 'view' && (
            <BookingPayments
              booking={b}
              tz={tz}
              onChanged={() => {
                void reload()
                onChanged()
              }}
            />
          )}

          <History events={b.events} tz={tz} names={names} />
        </div>
      )}
    </Drawer>
  )
}

function CancelForm({ busy, onBack, onCancel }: { busy: boolean; onBack: () => void; onCancel: (r: string) => void }) {
  const [reason, setReason] = useState('')
  return (
    <div className="inline-form">
      <Field label="Reason (optional)">
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Client called to cancel" autoFocus />
      </Field>
      <div className="form-actions">
        <button className="danger solid" disabled={busy} onClick={() => onCancel(reason.trim())}>
          Cancel booking
        </button>
        <button onClick={onBack}>Keep it</button>
      </div>
    </div>
  )
}

function EditForm({ booking, onBack, onSaved }: { booking: Booking; onBack: () => void; onSaved: (b: Booking) => void }) {
  const channels = useLoad(() => api<Channel[]>('/channels'), [])
  const [channelId, setChannelId] = useState(booking.channel_id)
  const [notes, setNotes] = useState(booking.notes ?? '')
  const { busy, error, run } = useAction()
  return (
    <div className="inline-form column">
      <Field label="Booking channel">
        <select value={channelId} onChange={(e) => setChannelId(e.target.value)}>
          {(channels.data ?? []).filter((c) => c.active || c.id === channelId).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Notes">
        <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Field>
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            void run(async () =>
              onSaved(
                await api<Booking>(`/bookings/${booking.id}`, {
                  method: 'PATCH',
                  json: { channel_id: channelId, notes: notes.trim() || null },
                }),
              ),
            )
          }
        >
          Save
        </button>
        <button onClick={onBack}>Back</button>
      </div>
      <ErrorNote message={error} />
    </div>
  )
}

function MoveForm({
  booking,
  tz,
  names,
  onBack,
  onMoved,
}: {
  booking: Booking
  tz: string
  names: Names
  onBack: () => void
  onMoved: (b: Booking) => void
}) {
  const prices = useLoad(() => api<Price[]>(`/branches/${booking.branch_id}/prices`), [booking.branch_id])
  const [day, setDay] = useState(dayOf(booking.starts_at, tz))
  const [variantId, setVariantId] = useState(booking.variant_id)
  const [keep, setKeep] = useState(true)
  const [slot, setSlot] = useState<Slot | null>(null)
  const { busy, error, run } = useAction()
  const options = (prices.data ?? []).filter((p) => p.service_id === booking.service_id && p.offered)
  const guests = booking.items.map((i) => ({ therapist_id: keep ? i.therapist_id : null }))

  return (
    <div className="inline-form column">
      <h3>Change time or therapist</h3>
      <div className="pair">
        <Field label="Date">
          <input type="date" value={day} onChange={(e) => setDay(e.target.value)} />
        </Field>
        {options.length > 1 && (
          <Field label="Length">
            <select value={variantId} onChange={(e) => setVariantId(e.target.value)}>
              {options.map((p) => (
                <option key={p.variant_id} value={p.variant_id}>
                  {p.variant_label} · {peso(p.branch_price ?? p.default_price)}
                </option>
              ))}
            </select>
          </Field>
        )}
      </div>
      <label className="check">
        <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
        Keep the same therapist{booking.items.length > 1 ? 's' : ''} (
        {booking.items.map((i) => i.therapist_name).join(' and ')})
      </label>
      <SlotPicker
        branchId={booking.branch_id}
        tz={tz}
        variantId={variantId}
        day={day}
        guests={guests}
        excludeBookingId={booking.id}
        value={slot}
        onChange={setSlot}
        names={names}
        preferTime={day === dayOf(booking.starts_at, tz) ? timeIn(booking.starts_at, tz) : null}
      />
      <div className="form-actions">
        <button
          className="primary"
          disabled={busy || !slot}
          onClick={() =>
            slot &&
            void run(async () =>
              onMoved(
                await api<Booking>(`/bookings/${booking.id}/reschedule`, {
                  method: 'POST',
                  json: {
                    starts_at: slot.starts_at,
                    variant_id: variantId,
                    guests: slot.pairs.map((p) => ({ therapist_id: p.therapist_id, bed_id: p.bed_id })),
                  },
                }),
              ),
            )
          }
        >
          Move booking
        </button>
        <button onClick={onBack}>Back</button>
      </div>
      <ErrorNote message={error} />
    </div>
  )
}

const EVENT_LABEL: Record<string, string> = {
  'booking.created': 'Booked',
  'booking.edited': 'Edited',
  'booking.rescheduled': 'Moved',
}

const PAYMENT_EVENT: Record<string, string> = {
  'payment.recorded': 'Payment logged',
  'payment.corrected': 'Payment corrected',
  'payment.verified': 'Payment verified',
  'payment.flagged': 'Payment flagged',
  'payment.reopened': 'Payment check reopened',
}

function History({ events, tz, names }: { events: BookingEvent[]; tz: string; names: Names }) {
  if (events.length === 0) return null
  return (
    <section className="history">
      <h3>History</h3>
      <ol>
        {events.map((e, i) => (
          <li key={i}>
            <span className="muted small">
              {longDayLabel(dayOf(e.occurred_at, tz))} {clockIn(e.occurred_at, tz)}
            </span>{' '}
            <strong>{eventText(e, tz, names)}</strong>
            {e.user_name && <span className="muted"> by {e.user_name}</span>}
          </li>
        ))}
      </ol>
    </section>
  )
}

function eventText(e: BookingEvent, tz: string, names: Names): string {
  const p = e.payload
  if (e.event_type === 'booking.rescheduled') {
    const to = p.to as { starts_at: string; therapists: string[] }
    return `Moved to ${longDayLabel(dayOf(to.starts_at, tz))} ${clockIn(to.starts_at, tz)} with ${to.therapists
      .map((t) => names.therapist(t))
      .join(' and ')}`
  }
  if (EVENT_LABEL[e.event_type]) return EVENT_LABEL[e.event_type]
  if (e.event_type.startsWith('payment.')) {
    const what = PAYMENT_EVENT[e.event_type] ?? 'Payment changed'
    let text = `${what}: ${peso(String(p.amount))} ${String(p.method)}`
    if (p.reference) text += ` (ref ${String(p.reference)})`
    if (p.note) text += ` · ${String(p.note)}`
    return text
  }
  const status = e.event_type.replace('booking.', '').replace('_', ' ')
  let text = status.charAt(0).toUpperCase() + status.slice(1)
  if (p.cancel_reason) text += `: ${String(p.cancel_reason)}`
  if (p.amount_paid) text += ` · paid ${peso(String(p.amount_paid))}`
  return text
}
