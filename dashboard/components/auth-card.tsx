import type { ReactNode } from "react";
import Link from "next/link";
import { AuthSceneShell } from "./landing/auth-scene-shell";

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
    <AuthSceneShell>
      <section className="auth-card" aria-labelledby="auth-title">
        <Link className="brand" href="/">
          <span>PC</span>
          <strong>PetCare</strong>
        </Link>
        <h1 id="auth-title">{title}</h1>
        <p>{description}</p>
        {children}
      </section>
    </AuthSceneShell>
  );
}
