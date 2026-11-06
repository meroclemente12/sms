import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import type { Booking, Channel, Client, GuestWish, PreferredGender, Price, Service, Slot, Therapist } from '../api'
import { Drawer, ErrorNote, Field } from '../components/ui'
import { useAction, useLoad } from '../components/hooks'
import { longDayLabel, peso, todayIn } from '../time'
import ClientPicker from './ClientPicker'
import SlotPicker from './SlotPicker'
import type { Names } from './SlotPicker'

export interface BookingStart {
  walkIn: boolean
  day: string
  time?: string | null
  therapistId?: string | null
  bedId?: string | null
}

interface GuestForm {
  therapistId: string
  gender: '' | PreferredGender
  name: string
}

const NO_GUEST: GuestForm = { therapistId: '', gender: '', name: '' }
const MAX_GROUP = 12

type GenderFilter = '' | PreferredGender

/** All / Male / Female: filters the therapist list and is the guest's gender wish. */
function GenderChoice({ value, onChange, label }: { value: GenderFilter; onChange: (g: GenderFilter) => void; label: string }) {
  const options: [GenderFilter, string][] = [
    ['', 'All'],
    ['male', 'Male'],
    ['female', 'Female'],
  ]
  return (
    <div className="field">
      <span className="field-label">{label}</span>
      <div className="segmented-buttons" role="group" aria-label={label}>
        {options.map(([g, text]) => (
          <button type="button" key={text} className={value === g ? 'on' : ''} aria-pressed={value === g} onClick={() => onChange(g)}>
            {text}
          </button>
        ))}
      </div>
    </div>
  )
}

function Count({ label, value, max, onChange }: { label: string; value: number; max: number; onChange: (n: number) => void }) {
  return (
    <Field label={label}>
      <input
        type="number"
        inputMode="numeric"
        min={0}
        max={max}
        value={value}
        onChange={(e) => onChange(Math.max(0, Math.min(max, Math.floor(Number(e.target.value) || 0))))}
      />
    </Field>
  )
}

/** New booking (phone, email) or walk-in, in one panel. */
export default function NewBooking({
  branchId,
  tz,
  start,
  names,
  onClose,
  onBooked,
}: {
  branchId: string
  tz: string
  start: BookingStart
  names: Names
  onClose: () => void
  onBooked: (b: Booking) => void
}) {
  const setup = useLoad(
    async () => {
      const [channels, prices, services, therapists] = await Promise.all([
        api<Channel[]>('/channels'),
        api<Price[]>(`/branches/${branchId}/prices`),
        api<Service[]>('/services'),
        api<Therapist[]>(`/branches/${branchId}/therapists`),
      ])
      return { channels: channels.filter((c) => c.active), prices: prices.filter((p) => p.offered), services, therapists }
    },
    [branchId],
  )

  const walkInChannel = setup.data?.channels.find((c) => c.code === 'walk_in')
  const [channelId, setChannelId] = useState('')
  const [client, setClient] = useState<Client | null>(null)
  const [variantId, setVariantId] = useState('')
  const [day, setDay] = useState(start.walkIn ? todayIn(tz) : start.day)
  const [guests, setGuests] = useState<GuestForm[]>([{ ...NO_GUEST, therapistId: start.therapistId ?? '' }])
  const [size, setSize] = useState(1)
  const [males, setMales] = useState(0)
  const [females, setFemales] = useState(0)
  const [slot, setSlot] = useState<Slot | null>(null)
  const [notes, setNotes] = useState('')
  const { busy, error, run } = useAction()

  const channel = channelId || (start.walkIn ? walkInChannel?.id : setup.data?.channels.find((c) => c.code === 'phone')?.id) || ''
  const price = setup.data?.prices.find((p) => p.variant_id === variantId)
  const service = setup.data?.services.find((s) => s.id === price?.service_id)
  const couples = service?.guest_count === 2
  // A group: several guests, each with their own therapist and bed, all starting together.
  const group = !couples && size > 1
  const guestCount = couples ? 2 : size
  const anyCount = Math.max(0, size - males - females)
  const shownGuests = useMemo(
    () =>
      Array.from({ length: guestCount }, (_, i) => {
        const g = guests[i] ?? NO_GUEST
        if (!group) return g
        // In a group the male / female counts decide each guest's therapist gender.
        const gender: GenderFilter = i < males ? 'male' : i < males + females ? 'female' : ''
        return { ...g, gender }
      }),
    [guests, guestCount, group, males, females],
  )
  const wishes: GuestWish[] = useMemo(
    () =>
      shownGuests.map((g, i) => ({
        therapist_id: g.therapistId || null,
        preferred_gender: g.gender || null,
        bed_id: i === 0 && guestCount === 1 && start.bedId ? start.bedId : null,
      })),
    [shownGuests, guestCount, start.bedId],
  )
  const skilled = (setup.data?.therapists ?? []).filter(
    (t) => t.active && (!service || t.skill_service_ids.includes(service.id)),
  )
  const unitPrice = price ? Number(price.branch_price ?? price.default_price) : 0
  const total = couples ? unitPrice : unitPrice * size

  function changeSize(n: number) {
    const next = Math.max(1, Math.min(MAX_GROUP, n))
    setSize(next)
    const m = Math.min(males, next)
    setMales(m)
    setFemales(Math.min(females, next - m))
  }

  function changeCount(which: 'male' | 'female', n: number) {
    if (which === 'male') {
      setMales(n)
      setFemales((f) => Math.min(f, size - n))
    } else {
      setFemales(n)
      setMales((m) => Math.min(m, size - n))
    }
    // A named therapist of the other gender no longer fits; let the system pick again.
    setGuests((gs) => gs.map((g) => ({ ...g, therapistId: '' })))
  }

  function chooseGender(i: number, gender: GenderFilter) {
    const current = skilled.find((t) => t.id === shownGuests[i].therapistId)
    setGuest(i, { gender, therapistId: current && gender && current.gender !== gender ? '' : (current?.id ?? '') })
  }

  function setGuest(i: number, patch: Partial<GuestForm>) {
    setGuests((gs) => {
      const next = Array.from({ length: Math.max(gs.length, i + 1) }, (_, j) => gs[j] ?? NO_GUEST)
      next[i] = { ...next[i], ...patch }
      return next
    })
  }

  // Group the menu by service so the list reads "Swedish massage: 60 min, 90 min".
  const menu = useMemo(() => {
    const groups = new Map<string, Price[]>()
    for (const p of setup.data?.prices ?? []) {
      groups.set(p.service_name, [...(groups.get(p.service_name) ?? []), p])
    }
    return [...groups.entries()]
  }, [setup.data])

  const isToday = day === todayIn(tz)
  const after = start.walkIn && isToday ? new Date().toISOString() : null

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!client || !slot || !price) return
    await run(async () => {
      const b = await api<Booking>('/bookings', {
        method: 'POST',
        json: {
          branch_id: branchId,
          client_id: client.id,
          channel_id: channel,
          variant_id: price.variant_id,
          starts_at: slot.starts_at,
          guests: slot.pairs.map((p, i) => ({
            therapist_id: p.therapist_id,
            bed_id: p.bed_id,
            therapist_requested: Boolean(shownGuests[i].therapistId),
            preferred_gender: shownGuests[i].gender || null,
            guest_name: i > 0 ? shownGuests[i].name || null : null,
            guest_client_id: i === 0 ? client.id : null,
          })),
          notes: notes.trim() || null,
          check_in_now: start.walkIn,
        },
      })
      onBooked(b)
    })
  }

  const title = start.walkIn ? 'Walk-in' : 'New booking'
  return (
    <Drawer title={title} onClose={onClose}>
      <ErrorNote message={setup.error} />
      {setup.data && (
        <form className="booking-form" onSubmit={submit}>
          {!start.walkIn && (
            <fieldset className="segmented">
              <legend>How did they book?</legend>
              {setup.data.channels
                .filter((c) => c.code !== 'walk_in')
                .sort((a, b) => Number(b.code === 'phone') - Number(a.code === 'phone'))
                .map((c) => (
                  <label key={c.id} className={channel === c.id ? 'seg on' : 'seg'}>
                    <input
                      type="radio"
                      name="channel"
                      value={c.id}
                      checked={channel === c.id}
                      onChange={() => setChannelId(c.id)}
                    />
                    {c.name}
                  </label>
                ))}
            </fieldset>
          )}

          <section>
            <h3>Client</h3>
            <ClientPicker value={client} onChange={setClient} autoFocus />
          </section>

          <section>
            <h3>Service</h3>
            <Field label="Service">
              <select
                value={variantId}
                onChange={(e) => {
                  setVariantId(e.target.value)
                  setSlot(null)
                }}
                required
              >
                <option value="">Choose a service…</option>
                {menu.map(([name, options]) => (
                  <optgroup key={name} label={name}>
                    {options.map((p) => (
                      <option key={p.variant_id} value={p.variant_id}>
                        {p.service_name} · {p.variant_label} · {peso(p.branch_price ?? p.default_price)}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </Field>
            {!couples && (
              <div className="group-size">
                <Count label="Guests" value={size} max={MAX_GROUP} onChange={(n) => changeSize(Math.max(1, n))} />
                {group && (
                  <>
                    <Count label="Male therapists" value={males} max={size} onChange={(n) => changeCount('male', n)} />
                    <Count label="Female therapists" value={females} max={size} onChange={(n) => changeCount('female', n)} />
                    <p className="muted small group-any">
                      {anyCount === 0 ? 'Every guest has a gender set.' : `${anyCount} with any therapist.`}
                    </p>
                  </>
                )}
              </div>
            )}
            {shownGuests.map((g, i) => {
              const choices = skilled.filter((t) => !g.gender || t.gender === g.gender || t.id === g.therapistId)
              return (
                <div className="guest-row" key={i}>
                  {guestCount > 1 && (
                    <p className="guest-title">
                      Guest {i + 1}
                      {i === 0 && client ? ` (${client.name})` : ''}
                      {group && <span className="tag">{g.gender ? `${g.gender} therapist` : 'any therapist'}</span>}
                    </p>
                  )}
                  {i > 0 && (
                    <Field label="Guest name">
                      <input value={g.name} onChange={(e) => setGuest(i, { name: e.target.value })} placeholder="Optional" />
                    </Field>
                  )}
                  {!group && <GenderChoice label="Therapist gender" value={g.gender} onChange={(gender) => chooseGender(i, gender)} />}
                  <Field label="Therapist">
                    <select value={g.therapistId} onChange={(e) => setGuest(i, { therapistId: e.target.value })}>
                      <option value="">{g.gender ? `Any ${g.gender} therapist` : 'Any therapist'}</option>
                      {choices.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name} ({t.gender === 'female' ? 'F' : t.gender === 'male' ? 'M' : '–'})
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
              )
            })}
          </section>

          <section>
            <h3>{start.walkIn ? 'Earliest free times' : 'When'}</h3>
            {!start.walkIn && (
              <Field label="Date">
                <input type="date" value={day} onChange={(e) => setDay(e.target.value)} required />
              </Field>
            )}
            {!start.walkIn && <p className="muted small">{longDayLabel(day)}</p>}
            {variantId ? (
              <SlotPicker
                branchId={branchId}
                tz={tz}
                variantId={variantId}
                day={day}
                after={after}
                guests={wishes}
                preferTime={start.walkIn ? null : (start.time ?? null)}
                pickFirst={start.walkIn}
                limit={start.walkIn ? 8 : 60}
                value={slot}
                onChange={setSlot}
                names={names}
              />
            ) : (
              <p className="muted">Choose a service to see free times.</p>
            )}
          </section>

          <Field label="Notes (optional)">
            <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>

          <div className="form-actions sticky-actions">
            <button className="primary" disabled={busy || !client || !slot || !channel}>
              {start.walkIn ? 'Book and check in' : 'Book'}
              {group && ` ${size} guests`}
              {price && ` · ${peso(total)}`}
            </button>
            <button type="button" onClick={onClose}>
              Cancel
            </button>
          </div>
          {!client && <p className="muted small">Pick or add the client to book.</p>}
          <ErrorNote message={error} />
        </form>
      )}
    </Drawer>
  )
}
