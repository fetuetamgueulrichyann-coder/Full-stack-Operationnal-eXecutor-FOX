import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import logo from "../assets/fox-logo.png";
import { Icon, type IconName } from "./Icon";

const ITEMS: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Discussion", icon: "chat", end: true },
  { to: "/agenda", label: "Agenda", icon: "agenda" },
  { to: "/taches", label: "Tâches", icon: "tasks" },
  { to: "/devis", label: "Clients et devis", icon: "quotes" },
  { to: "/prive", label: "Vie privée", icon: "privacy" },
  { to: "/reglages", label: "Réglages", icon: "settings" },
];

const MOBILE_ITEMS = ITEMS.slice(0, 5);

export function Shell() {
  const { state, logout } = useAuth();
  if (state.status !== "authenticated") return null;
  const { user } = state;

  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Navigation principale">
        <div className="brand">
          <img src={logo} alt="" width={36} />
          <span>FOX</span>
        </div>
        {ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `nav${isActive ? " on" : ""}`}>
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </NavLink>
        ))}
        <div className="grow" />
        <div className="me">
          <div className="avatar" aria-hidden="true">
            {user.username.slice(0, 1).toUpperCase()}
          </div>
          <div className="me-text">
            <strong>{user.username}</strong>
            <span>{user.role === "owner" ? "propriétaire" : "testeur"}</span>
          </div>
          <button type="button" className="icon-btn" aria-label="Se déconnecter" onClick={() => void logout()}>
            <Icon name="arrow" />
          </button>
        </div>
      </nav>
      <div className="content">
        <Outlet />
      </div>
      <nav className="tabbar" aria-label="Navigation mobile">
        {MOBILE_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `tab${isActive ? " on" : ""}`}>
            <Icon name={item.icon} size={22} />
            <span>{item.label.split(" ")[0]}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
