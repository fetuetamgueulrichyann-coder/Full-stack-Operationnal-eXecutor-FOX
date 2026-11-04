import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import { describeError } from "../api/errors";
import { AuthLayout } from "../components/AuthLayout";
import { ErrorBanner, PasswordField, TextField } from "../components/Fields";
import { Icon } from "../components/Icon";
import { useAuth } from "../auth/AuthContext";

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await login(username.trim(), password);
    } catch (e) {
      setError(
        e instanceof ApiError && e.code === "invalid_credentials"
          ? "Identifiant ou mot de passe incorrect."
          : describeError(e),
      );
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Connexion"
      subtitle="Content de vous revoir. Entrez vos identifiants."
      tagline="Votre agenda, vos tâches et vos devis, dans une seule conversation."
      taglineDetail="Chaque action importante attend votre confirmation avant d'être exécutée."
    >
      {error && <ErrorBanner>{error}</ErrorBanner>}
      <form className="form" onSubmit={submit} aria-label="Se connecter">
        <TextField
          label="Identifiant"
          icon="user"
          autoComplete="username"
          placeholder="Votre identifiant"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <PasswordField
          label="Mot de passe"
          autoComplete="current-password"
          placeholder="Votre mot de passe"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Connexion…" : "Se connecter"}
          {!busy && <Icon name="arrow" />}
        </button>
      </form>
      <div className="or">
        <span />
        <em>Nouveau sur FOX ?</em>
        <span />
      </div>
      <Link className="secondary" to="/inscription">
        Créer un compte avec un code d'invitation
      </Link>
    </AuthLayout>
  );
}
