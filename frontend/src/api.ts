// Thin client for the Banahaw Reservations API.

export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

const TOKEN_KEY = 'banahaw.token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // Private mode or blocked storage: the session simply won't survive a reload.
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn
}

function messageFrom(status: number, body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { loc?: unknown[]; msg?: string }
      const field = first.loc ? String(first.loc[first.loc.length - 1]) : ''
      return field ? `${field}: ${first.msg}` : (first.msg ?? 'Invalid input')
    }
  }
  return `Request failed (${status})`
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  let body = init.body
  if (init.json !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(init.json)
  }
  const res = await fetch(`${API_URL}${path}`, { ...init, headers, body })
  if (res.status === 204) return undefined as T
  const data: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    if (res.status === 401 && token) onUnauthorized()
    throw new ApiError(res.status, messageFrom(res.status, data))
  }
  return data as T
}

// --- Types mirrored from the API ------------------------------------------------------

export type Role = 'admin' | 'manager' | 'front_desk'

export interface BranchRef {
  id: string
  name: string
}

export interface Me {
  id: string
  name: string
  email: string
  role: Role
  tenant_id: string
  tenant_name: string
  branches: BranchRef[]
}

export interface Branch {
  id: string
  name: string
  address: string | null
  timezone: string
  opens_at: string
  closes_at: string
  active: boolean
}

export interface BedType {
  id: string
  name: string
}

export interface Bed {
  id: string
  room_id: string
  label: string
  bed_type_id: string
  active: boolean
}

export type RoomType = 'single' | 'couples' | 'wet'

export interface Room {
  id: string
  branch_id: string
  name: string
  room_type: RoomType
  active: boolean
  beds: Bed[]
}

export interface Variant {
  id: string
  service_id: string
  label: string
  duration_min: number
  default_price: string
  active: boolean
}

export interface Service {
  id: string
  name: string
  category: string | null
  bed_type_id: string
  buffer_before_min: number
  buffer_after_min: number
  guest_count: 1 | 2
  active: boolean
  variants: Variant[]
}

export interface Price {
  service_id: string
  service_name: string
  variant_id: string
  variant_label: string
  default_price: string
  branch_price: string | null
  offered: boolean
}

export type Gender = 'female' | 'male' | 'other'

export interface Therapist {
  id: string
  branch_id: string
  name: string
  gender: Gender
  employment_status: string
  display_colour: string | null
  active: boolean
  skill_service_ids: string[]
}

export interface Shift {
  id: string
  therapist_id: string
  work_date: string
  starts_at: string
  ends_at: string
}

export type BlockReason = 'break' | 'leave' | 'maintenance' | 'other'

export interface TimeBlock {
  id: string
  therapist_id: string | null
  bed_id: string | null
  starts_at: string
  ends_at: string
  reason: BlockReason
}

export interface Channel {
  id: string
  code: string
  name: string
  active: boolean
}

export interface StaffUser {
  id: string
  email: string
  name: string
  role: Role
  active: boolean
  branch_ids: string[]
}
