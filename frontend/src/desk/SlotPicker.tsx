import { useEffect, useState } from 'react'
import { api } from '../api'
import type { GuestWish, Slot } from '../api'
import { ErrorNote } from '../components/ui'
import { clockIn, timeIn } from '../time'

export interface Names {
  therapist: (id: string) => string
  bed: (id: string) => string
}

/** Free start times for a service, with the therapist and bed the system suggests. */
export default function SlotPicker({
  branchId,
  tz,
  variantId,
  day,
  after,
  guests,
  excludeBookingId,
  preferTime,
  pickFirst,
  limit = 60,
  value,
  onChange,
  names,
}: {
  branchId: string
  tz: string
  variantId: string
  day: string
  after?: string | null
  guests: GuestWish[]
  excludeBookingId?: string
  preferTime?: string | null
  pickFirst?: boolean
  limit?: number
  value: Slot | null
  onChange: (s: Slot | null) => void
  names: Names
}) {
  const [slots, setSlots] = useState<Slot[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const key = JSON.stringify({ branchId, variantId, day, after, guests, excludeBookingId, limit })

  useEffect(() => {
    let stale = false
    setSlots(null)
    setError(null)
    onChange(null)
    api<Slot[]>(`/branches/${branchId}/slots`, {
      method: 'POST',
      json: {
        variant_id: variantId,
        date: after ? null : day,
        after: after ?? null,
        guests,
        exclude_booking_id: excludeBookingId ?? null,
        limit,
      },
    })
      .then((found) => {
        if (stale) return
        setSlots(found)
        const preferred = preferTime ? found.find((s) => timeIn(s.starts_at, tz) === preferTime) : undefined
        if (preferred) onChange(preferred)
        else if (pickFirst && found.length) onChange(found[0])
      })
      .catch((e: Error) => !stale && setError(e.message))
    return () => {
      stale = true
    }
    // The key covers every input that changes the search.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (error) return <ErrorNote message={error} />
  if (!slots) return <p className="muted">Looking for free times…</p>
  if (slots.length === 0)
    return (
      <p className="note">
        {after
          ? 'Nothing free for the rest of today for this request. Try another therapist, gender or service, or book a later day.'
          : 'Nothing free that day for this request. Try another day, therapist or gender.'}
      </p>
    )

  const missedPreferred = preferTime && !slots.some((s) => timeIn(s.starts_at, tz) === preferTime)
  return (
    <div className="slots">
      {missedPreferred && (
        <p className="note">{preferTime} is not free for this request. Pick one of these times instead.</p>
      )}
      <div className="slot-grid" role="radiogroup" aria-label="Free start times">
        {slots.map((s) => (
          <button
            type="button"
            key={s.starts_at}
            role="radio"
            aria-checked={value?.starts_at === s.starts_at}
            className={value?.starts_at === s.starts_at ? 'slot picked' : 'slot'}
            onClick={() => onChange(s)}
          >
            {clockIn(s.starts_at, tz)}
          </button>
        ))}
      </div>
      {value && (
        <p className="slot-detail">
          {value.pairs.map((p, i) => (
            <span key={i}>
              {value.pairs.length > 1 && <span className="muted">Guest {i + 1}: </span>}
              <strong>{names.therapist(p.therapist_id)}</strong> in {names.bed(p.bed_id)}
              {i < value.pairs.length - 1 && <br />}
            </span>
          ))}
        </p>
      )}
    </div>
  )
}
