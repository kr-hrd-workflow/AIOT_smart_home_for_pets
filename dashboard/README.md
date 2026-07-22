# PetCare Dashboard

The PetCare dashboard runs on [vinext](https://github.com/cloudflare/vinext).
It provides the public landing page, isolated `/demo`, loopback-only local live
dashboard, and authenticated remote dashboard backed by Cloudflare D1 and R2.

## Prerequisites

- Node.js `>=22.13.0`

## Quick Start

```bash
npm ci
npm run dev
npm run build
```

The project does not use `wrangler.jsonc`; Sites bindings are declared in
`.openai/hosting.json` and simulated by `vite.config.ts` for local tests.

## Included Shape

- `app/` contains the landing, demo, auth, and enrollment routes.
- `components/` contains the shared, local connected, and authenticated remote UI.
- `lib/petcare/` owns tenant-scoped enrollment, proxy, clip, cleanup, and reconciliation logic.
- `db/schema.ts` and `drizzle/` define the D1 tenancy, agent, camera, clip, and cleanup schema.
- `.openai/hosting.json` declares the Sites D1 and R2 bindings.
- `tests/` and `e2e/` cover unit, integration, responsive, accessibility, and network-isolation behavior.

## Workspace Auth Headers

OpenAI workspace sites can read the current user's email from
`oai-authenticated-user-email`.

SIWC-authenticated workspace sites may also receive
`oai-authenticated-user-full-name` when the user's SIWC profile has a non-empty
`name` claim. The full-name value is percent-encoded UTF-8 and is accompanied by
`oai-authenticated-user-full-name-encoding: percent-encoded-utf-8`.

Treat the full name as optional and fall back to email when it is absent:

```tsx
import { headers } from "next/headers";

export default async function Home() {
  const requestHeaders = await headers();
  const email = requestHeaders.get("oai-authenticated-user-email");
  const encodedFullName = requestHeaders.get("oai-authenticated-user-full-name");
  const fullName =
    encodedFullName &&
    requestHeaders.get("oai-authenticated-user-full-name-encoding") ===
      "percent-encoded-utf-8"
      ? decodeURIComponent(encodedFullName)
      : null;

  const displayName = fullName ?? email;
  // ...
}
```

## Optional Dispatch-Owned ChatGPT Sign-In

Import the ready-to-use helpers from `app/chatgpt-auth.ts` when the site needs
optional or required ChatGPT sign-in:

- Use `getChatGPTUser()` for optional signed-in UI.
- Use `requireChatGPTUser(returnTo)` for server-rendered pages that should send
  anonymous visitors through Sign in with ChatGPT.
- Use `chatGPTSignInPath(returnTo)` and `chatGPTSignOutPath(returnTo)` for
  browser links or actions.
- Pass a same-origin relative `returnTo` path for the destination after sign-in
  or sign-out. The helper validates and safely encodes it.
- Mark protected pages with `export const dynamic = "force-dynamic"` because
  they depend on per-request identity headers.

Dispatch owns `/signin-with-chatgpt`, `/signout-with-chatgpt`, `/callback`, the
OAuth cookies, and identity header injection. Do not implement app routes for
those reserved paths. Routes that do not import and call the helper remain
anonymous-compatible.

SIWC establishes identity only; it does not prove workspace membership. Use the
Sites hosting platform's access policy controls for workspace-wide restrictions,
or enforce explicit server-side membership or allowlist checks.

Use SIWC for account pages, user-specific dashboards, saved records, and write
actions tied to the current ChatGPT user. Leave public content anonymous.

## Useful Commands

- `npm run dev`: start local development
- `npm run build`: verify the vinext build output
- `npm test`: run the Vitest dashboard suite
- `npm run test:e2e:demo:dev`: verify the isolated demo against the dev server
- `npm run test:e2e:demo:production`: build and verify the isolated demo against the production server
- `npm run test:e2e:connected`: verify local connected states and ROI editing
- `npm run lint`: run ESLint
- `npm run db:generate`: generate Drizzle migrations after intentional schema changes

## Learn More

- [vinext Documentation](https://github.com/cloudflare/vinext)
- [Drizzle D1 Guide](https://orm.drizzle.team/docs/get-started/d1-new)
