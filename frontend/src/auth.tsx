import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, getToken, setToken, setUnauthorizedHandler } from './api'
import type { BranchRef, Me } from './api'

interface AuthState {
  me: Me | null
  loading: boolean
  branch: BranchRef | null
  setBranchId: (id: string) => void
  login: (spa: string, email: string, password: string) => Promise<void>
  logout: () => void
  refresh: () => Promise<void>
  canManage: boolean
  isAdmin: boolean
}

const AuthContext = createContext<AuthState | null>(null)
const BRANCH_KEY = 'banahaw.branch'

function readBranch(): string | null {
  try {
    return localStorage.getItem(BRANCH_KEY)
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(Boolean(getToken()))
  const [branchId, setBranchIdState] = useState<string | null>(readBranch())

  const logout = useCallback(() => {
    setToken(null)
    setMe(null)
  }, [])

  const refresh = useCallback(async () => {
    if (!getToken()) return
    try {
      setMe(await api<Me>('/auth/me'))
    } catch {
      logout()
    } finally {
      setLoading(false)
    }
  }, [logout])

  useEffect(() => {
    setUnauthorizedHandler(logout)
    void refresh()
  }, [logout, refresh])

  const login = useCallback(async (spa: string, email: string, password: string) => {
    const res = await api<{ access_token: string; user: Me }>('/auth/login', {
      method: 'POST',
      json: { spa, email, password },
    })
    setToken(res.access_token)
    setMe(res.user)
  }, [])

  const setBranchId = useCallback((id: string) => {
    setBranchIdState(id)
    try {
      localStorage.setItem(BRANCH_KEY, id)
    } catch {
      // ignore
    }
  }, [])

  const value = useMemo<AuthState>(() => {
    const branches = me?.branches ?? []
    const branch = branches.find((b) => b.id === branchId) ?? branches[0] ?? null
    return {
      me,
      loading,
      branch,
      setBranchId,
      login,
      logout,
      refresh,
      canManage: me?.role === 'admin' || me?.role === 'manager',
      isAdmin: me?.role === 'admin',
    }
  }, [me, loading, branchId, setBranchId, login, logout, refresh])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
