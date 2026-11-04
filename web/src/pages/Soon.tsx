export function Soon({ title }: { title: string }) {
  return (
    <main className="page">
      <h1>{title}</h1>
      <p className="muted">Cette page arrive bientôt. Elle fait partie des prochaines livraisons de l'interface.</p>
    </main>
  );
}
