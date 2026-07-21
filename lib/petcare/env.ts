import type { AuthEnv } from "../auth/require-auth";

export interface PetCareEnv extends AuthEnv {
  DB: D1Database;
  CLIPS: R2Bucket;
  CF_ACCOUNT_ID: string;
  CF_ZONE_ID: string;
  CF_ZONE_NAME: string;
  CF_ACCESS_TEAM_NAME: string;
  CF_TUNNEL_API_TOKEN: string;
  CF_ACCESS_SERVICE_TOKEN_ID: string;
  CF_ACCESS_CLIENT_ID: string;
  CF_ACCESS_CLIENT_SECRET: string;
}

export function readPetCareConfig(env: PetCareEnv) {
  const keys = [
    "CF_ACCOUNT_ID",
    "CF_ZONE_ID",
    "CF_ZONE_NAME",
    "CF_ACCESS_TEAM_NAME",
    "CF_TUNNEL_API_TOKEN",
    "CF_ACCESS_SERVICE_TOKEN_ID",
    "CF_ACCESS_CLIENT_ID",
    "CF_ACCESS_CLIENT_SECRET",
  ] as const;
  for (const key of keys) {
    if (!env[key]) throw new Error(`missing_runtime_secret:${key}`);
  }
  return {
    accountId: env.CF_ACCOUNT_ID,
    zoneId: env.CF_ZONE_ID,
    zoneName: env.CF_ZONE_NAME,
    accessTeamName: env.CF_ACCESS_TEAM_NAME,
    apiToken: env.CF_TUNNEL_API_TOKEN,
    serviceTokenId: env.CF_ACCESS_SERVICE_TOKEN_ID,
    accessClientId: env.CF_ACCESS_CLIENT_ID,
    accessClientSecret: env.CF_ACCESS_CLIENT_SECRET,
  };
}
