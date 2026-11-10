import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { BootScreen } from "./components/BootScreen";
import { Shell } from "./components/Shell";
import { Agenda } from "./pages/Agenda";
import { Chat } from "./pages/Chat";
import { Login } from "./pages/Login";
import { Privacy } from "./pages/Privacy";
import { Quotes } from "./pages/Quotes";
import { Register } from "./pages/Register";
import { Settings } from "./pages/Settings";
import { Sites } from "./pages/Sites";
import { Tasks } from "./pages/Tasks";

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
          <Route path="/agenda" element={<Agenda />} />
          <Route path="/taches" element={<Tasks />} />
          <Route path="/devis" element={<Quotes />} />
          <Route path="/chantiers" element={<Sites />} />
          <Route path="/prive" element={<Privacy />} />
          <Route path="/reglages" element={<Settings />} />
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
