import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  failed: boolean;
}

/** Évite la page blanche : une erreur d'affichage montre un message et un bouton pour recharger. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Erreur d'affichage", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="page" role="alert">
        <h1>Un problème d'affichage est survenu</h1>
        <p className="muted">Vos données ne sont pas perdues. Rechargez la page pour continuer.</p>
        <div>
          <button className="primary compact" onClick={() => window.location.reload()}>Recharger la page</button>
        </div>
      </main>
    );
  }
}
