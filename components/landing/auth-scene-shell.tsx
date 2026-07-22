import type { ReactNode } from "react";

export function AuthSceneShell({ children }: { children: ReactNode }) {
  return (
    <main className="auth-scene-shell">
      <div className="auth-scene-still" aria-hidden="true" />
      <div className="auth-scene-content">{children}</div>
    </main>
  );
}
