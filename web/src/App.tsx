import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { BootScreen } from "./components/BootScreen";
import { Shell } from "./components/Shell";
import { Chat } from "./pages/Chat";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { Soon } from "./pages/Soon";

/** Pages réservées aux utilisateurs connectés ; l'attente et l'échec réseau passent par l'écran de chargement. */
function RequireAuth() {
  const { state, retry } = useAuth();
  if (state.status === "loading") return <BootScreen />;
  if (state.status === "unreachable") return <BootScreen error={state.message} onRetry={retry} />;
  if (state.status === "anonymous") return <Navigate to="/connexion" replace />;
  return <Outlet />;
}

/** Connexion et inscription : un utilisateur déjà connecté va directement à la discussion. */
function PublicOnly() {
  const { state, retry } = useAuth();
  if (state.status === "loading") return <BootScreen />;
  if (state.status === "unreachable") return <BootScreen error={state.message} onRetry={retry} />;
  if (state.status === "authenticated") return <Navigate to="/" replace />;
  return <Outlet />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<PublicOnly />}>
        <Route path="/connexion" element={<Login />} />
        <Route path="/inscription" element={<Register />} />
      </Route>
      <Route element={<RequireAuth />}>
        <Route element={<Shell />}>
          <Route path="/" element={<Chat />} />
          <Route path="/agenda" element={<Soon title="Agenda" />} />
          <Route path="/taches" element={<Soon title="Tâches" />} />
          <Route path="/devis" element={<Soon title="Clients et devis" />} />
          <Route path="/prive" element={<Soon title="Vie privée" />} />
          <Route path="/reglages" element={<Soon title="Réglages" />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
