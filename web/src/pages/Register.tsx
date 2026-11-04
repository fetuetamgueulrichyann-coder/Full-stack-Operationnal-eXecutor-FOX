import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { describeError } from "../api/errors";
import { AuthLayout } from "../components/AuthLayout";
import { ErrorBanner, PasswordField, TextField } from "../components/Fields";
import { Icon } from "../components/Icon";
import { useAuth } from "../auth/AuthContext";

export function Register() {
  const { register } = useAuth();
  const [code, setCode] = useState("");
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
      await register(code.trim().toUpperCase(), username.trim(), password);
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Créer un compte"
      subtitle="Une minute suffit."
      tagline="FOX est en test sur invitation."
      taglineDetail="Entrez le code reçu pour créer votre compte. Chaque compte a son propre historique, sa mémoire et son agenda."
    >
      {error && <ErrorBanner>{error}</ErrorBanner>}
      <form className="form" onSubmit={submit} aria-label="Créer un compte">
        <TextField
          label="Code d'invitation"
          icon="key"
          className="code"
          autoComplete="off"
          placeholder="XXXXXXXX"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          required
        />
        <TextField
          label="Identifiant"
          icon="user"
          autoComplete="username"
          placeholder="Choisissez un identifiant"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          required
        />
        <PasswordField
          label="Mot de passe"
          autoComplete="new-password"
          placeholder="10 caractères minimum"
          hint="Au moins 10 caractères."
          minLength={10}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Création…" : "Créer mon compte"}
          {!busy && <Icon name="arrow" />}
        </button>
      </form>
      <p className="alt">
        Vous avez déjà un compte ? <Link to="/connexion">Se connecter</Link>
      </p>
    </AuthLayout>
  );
}
