import type { ReactNode } from "react";

export function AuthCard({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <a className="brand" href="/demo">
          <span>PC</span>
          <strong>PetCare</strong>
        </a>
        <h1 id="auth-title">{title}</h1>
        <p>{description}</p>
        {children}
      </section>
    </main>
  );
}
