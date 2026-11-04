import type { ReactNode } from "react";
import logo from "../assets/fox-logo.png";
import illustration from "../assets/fond-ghibli.jpg";

interface Props {
  title: string;
  subtitle: string;
  tagline: string;
  taglineDetail: string;
  children: ReactNode;
}

/** Disposition commune de la connexion et de l'inscription : illustration à gauche, formulaire à droite. */
export function AuthLayout({ title, subtitle, tagline, taglineDetail, children }: Props) {
  return (
    <div className="auth">
      <section className="auth-art" aria-label="Illustration">
        <img src={illustration} alt="Un renard roux assis sur un rocher au bord d'un lac" />
        <div className="auth-tagline">
          <strong>{tagline}</strong>
          <span>{taglineDetail}</span>
        </div>
      </section>
      <main className="auth-main">
        <div className="auth-box">
          <div className="auth-head">
            <img src={logo} alt="Logo FOX" width={116} />
            <h1>{title}</h1>
            <p>{subtitle}</p>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}
