import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth.jsx'
import LoginPage from './pages/LoginPage.jsx'
import RegisterPage from './pages/RegisterPage.jsx'
import DashboardPage from './pages/DashboardPage.jsx'
import PostFoundPage from './pages/PostFoundPage.jsx'
import SearchPage from './pages/SearchPage.jsx'
import AlertsPage from './pages/AlertsPage.jsx'
import MatchesPage from './pages/MatchesPage.jsx'
import Nav from './components/Nav.jsx'
import AdminLayout, { RequireAdmin } from './admin/AdminLayout.jsx'
import AdminOverview from './admin/AdminOverview.jsx'
import AdminTransactions from './admin/AdminTransactions.jsx'
import AdminClaims from './admin/AdminClaims.jsx'
import AdminFlagged from './admin/AdminFlagged.jsx'

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth()
  if (loading) return <div className="loading">Loading...</div>
  return user ? children : <Navigate to="/login" replace />
}

export default function App() {
  const { user, loading } = useAuth()

  if (loading) return <div className="loading">Loading...</div>

  return (
    <div className="app">
      {user && <Nav />}
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <DashboardPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/post-found"
          element={
            <ProtectedRoute>
              <PostFoundPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/search"
          element={
            <ProtectedRoute>
              <SearchPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/alerts"
          element={
            <ProtectedRoute>
              <AlertsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="/matches"
          element={
            <ProtectedRoute>
              <MatchesPage />
            </ProtectedRoute>
          }
        />
        {/* RequireAdmin wraps the layout, so every nested admin route inherits
            the role check — a non-admin typing /admin/transactions is bounced
            the same as one typing /admin. */}
        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <AdminLayout />
            </RequireAdmin>
          }
        >
          <Route index element={<AdminOverview />} />
          <Route path="transactions" element={<AdminTransactions />} />
          <Route path="claims" element={<AdminClaims />} />
          <Route path="flagged" element={<AdminFlagged />} />
        </Route>
      </Routes>
    </div>
  )
}
