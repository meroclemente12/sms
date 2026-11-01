// Date and time helpers. Days are YYYY-MM-DD strings in the branch's own calendar, and
// every time is shown in the branch's timezone (Philippine time for Banahaw), whatever
// the computer's own clock is set to.

export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

export function todayIn(tz: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date())
}

export function mondayOf(day: string): string {
  const dow = new Date(`${day}T00:00:00Z`).getUTCDay() // 0 = Sunday
  return addDays(day, -((dow + 6) % 7))
}

/** "14:30" for an ISO timestamp, in the branch timezone. */
export function timeIn(iso: string, tz: string): string {
  return new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit' }).format(new Date(iso))
}

/** "2:30 PM" for an ISO timestamp, in the branch timezone. */
export function clockIn(iso: string, tz: string): string {
  return new Intl.DateTimeFormat('en-PH', { timeZone: tz, hour: 'numeric', minute: '2-digit' }).format(new Date(iso))
}

/** The branch-calendar day (YYYY-MM-DD) of an ISO timestamp. */
export function dayOf(iso: string, tz: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz }).format(new Date(iso))
}

export function dayLabel(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString('en-PH', {
    timeZone: 'UTC',
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

export function longDayLabel(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString('en-PH', {
    timeZone: 'UTC',
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  })
}

/** ISO timestamp for a wall-clock date and time in the given timezone. */
export function zonedIso(day: string, time: string, tz: string): string {
  const guess = new Date(`${day}T${time}:00Z`)
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(guess)
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value)
  const shown = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'))
  return new Date(guess.getTime() - (shown - guess.getTime())).toISOString()
}

/** Minutes since midnight of "HH:MM" or "HH:MM:SS". */
export function minutesOf(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

export function hhmm(minutes: number): string {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

export function peso(amount: string | number): string {
  return new Intl.NumberFormat('en-PH', { style: 'currency', currency: 'PHP', maximumFractionDigits: 0 }).format(
    Number(amount),
  )
}
