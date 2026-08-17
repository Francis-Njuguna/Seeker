import { createContext, useContext, useEffect, useState } from 'react'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [token, setToken] = useState(localStorage.getItem('token'))
  const [loading, setLoading] = useState(true)
  const [workspace, setWorkspaceState] = useState(localStorage.getItem('workspace') || 'owner')

  useEffect(() => {
    if (!token) {
      setLoading(false)
      return
    }
    fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setUser)
      .catch(() => {
        localStorage.removeItem('token')
        setToken(null)
      })
      .finally(() => setLoading(false))
  }, [token])

  const login = (accessToken, userData) => {
    localStorage.setItem('token', accessToken)
    setToken(accessToken)
    setUser(userData)
  }

  const logout = () => {
    localStorage.removeItem('token')
    setToken(null)
    setUser(null)
  }

  const setWorkspace = (value) => {
    localStorage.setItem('workspace', value)
    setWorkspaceState(value)
  }

  const refreshUser = async () => {
    const response = await fetch('/api/auth/me', { headers: authHeaders(token) })
    if (!response.ok) throw new Error('Could not refresh account')
    const data = await response.json()
    setUser(data)
    return data
  }

  return (
    <AuthContext.Provider value={{
      user, token, loading, login, logout, refreshUser, workspace, setWorkspace,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}

export function authHeaders(token) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}
