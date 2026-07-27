"use client";

import { useEffect, useState } from "react";

type RecoveryTokens = {
  accessToken: string;
  refreshToken: string;
};

export function RecoveryTokenFields() {
  const [tokens, setTokens] = useState<RecoveryTokens | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    if (params.get("type") !== "recovery") {
      return;
    }

    const accessToken = params.get("access_token") ?? "";
    const refreshToken = params.get("refresh_token") ?? "";

    const timeout = window.setTimeout(() => {
      setTokens({ accessToken, refreshToken });
      window.history.replaceState(
        window.history.state,
        "",
        `${window.location.pathname}${window.location.search}`,
      );
    }, 0);
    return () => window.clearTimeout(timeout);
  }, []);

  if (!tokens) {
    return null;
  }

  return (
    <>
      <input type="hidden" name="access_token" value={tokens.accessToken} />
      <input type="hidden" name="refresh_token" value={tokens.refreshToken} />
    </>
  );
}
