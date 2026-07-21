import { describe, expect, it, vi } from "vitest";

import {
  CloudflareApiError,
  CloudflareClient,
} from "../lib/petcare/cloudflare";

const config = {
  accountId: "acct",
  zoneId: "zone",
  zoneName: "agents.example.com",
  accessTeamName: "petcare",
  apiToken: "scoped-token",
  serviceTokenId: "service-token-id",
  accessClientId: "access-client-id",
  accessClientSecret: "access-client-secret",
};

type Call = { url: string; method: string; body: string };

function queuedFetch(
  responses: Array<{ status?: number; result?: unknown; success?: boolean }>,
) {
  const calls: Call[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer scoped-token");
    calls.push({
      url,
      method: init?.method ?? "GET",
      body: typeof init?.body === "string" ? init.body : "",
    });

    const response = responses.shift();
    if (!response) throw new Error("unexpected fetch");
    return new Response(
      JSON.stringify({
        success: response.success ?? (response.status ?? 200) < 400,
        result: response.result ?? null,
        errors: [{ message: "scoped-token internal-host" }],
      }),
      { status: response.status ?? 200 },
    );
  };
  return { calls, fetchImpl };
}

describe("CloudflareClient", () => {
  it("exposes only the non-secret DNS zone needed for enrollment labels", () => {
    const { fetchImpl } = queuedFetch([]);
    expect(new CloudflareClient(config, fetchImpl).zoneName).toBe(
      "agents.example.com",
    );
  });

  it("sends the exact provisioning requests without placing secrets in URLs or bodies", async () => {
    const { calls, fetchImpl } = queuedFetch([
      { result: { id: "tunnel-1" } },
      { result: { id: "dns-1" } },
      { result: { id: "app-1", aud: "aud-1" } },
      { result: { id: "policy-1" } },
      { result: {} },
      { result: "connector-token" },
    ]);
    const client = new CloudflareClient(config, fetchImpl);

    await expect(client.createTunnel("petcare-home-a")).resolves.toEqual({
      id: "tunnel-1",
    });
    await expect(
      client.createDnsRecord("home-a.agents.example.com", "tunnel-1"),
    ).resolves.toEqual({ id: "dns-1" });
    await expect(
      client.createAccessApp("home-a.agents.example.com", "PetCare home-a"),
    ).resolves.toEqual({ id: "app-1", aud: "aud-1" });
    await expect(client.createAccessPolicy("app-1")).resolves.toEqual({
      id: "policy-1",
    });
    await expect(
      client.configureTunnel(
        "tunnel-1",
        "home-a.agents.example.com",
        "aud-1",
      ),
    ).resolves.toBeUndefined();
    await expect(client.getConnectorToken("tunnel-1")).resolves.toBe(
      "connector-token",
    );

    expect(
      calls.map(
        (call) => `${call.method} ${new URL(call.url).pathname}`,
      ),
    ).toEqual([
      "POST /client/v4/accounts/acct/cfd_tunnel",
      "POST /client/v4/zones/zone/dns_records",
      "POST /client/v4/accounts/acct/access/apps",
      "POST /client/v4/accounts/acct/access/apps/app-1/policies",
      "PUT /client/v4/accounts/acct/cfd_tunnel/tunnel-1/configurations",
      "GET /client/v4/accounts/acct/cfd_tunnel/tunnel-1/token",
    ]);
    expect(JSON.parse(calls[0].body)).toEqual({
      name: "petcare-home-a",
      config_src: "cloudflare",
    });
    expect(JSON.parse(calls[1].body)).toEqual({
      type: "CNAME",
      name: "home-a.agents.example.com",
      content: "tunnel-1.cfargotunnel.com",
      proxied: true,
      ttl: 1,
    });
    expect(JSON.parse(calls[2].body)).toMatchObject({
      name: "PetCare home-a",
      domain: "home-a.agents.example.com",
      type: "self_hosted",
      service_auth_401_redirect: true,
    });
    expect(JSON.parse(calls[3].body)).toEqual({
      name: "PetCare Sites BFF",
      decision: "non_identity",
      include: [{ service_token: { token_id: "service-token-id" } }],
      precedence: 1,
    });
    expect(JSON.parse(calls[4].body)).toEqual({
      config: {
        ingress: [
          {
            hostname: "home-a.agents.example.com",
            service: "http://127.0.0.1:8000",
            originRequest: {
              access: {
                required: true,
                teamName: "petcare",
                audTag: ["aud-1"],
              },
            },
          },
          { service: "http_status:404" },
        ],
      },
    });
    expect(calls.every(({ url, body }) => !`${url}${body}`.includes("scoped-token"))).toBe(true);
  });

  it("discovers only exact deterministic orphan resources", async () => {
    const { calls, fetchImpl } = queuedFetch([
      {
        result: [
          {
            id: "tunnel-ignore",
            name: "petcare-home-ab",
            config_src: "cloudflare",
          },
          {
            id: "tunnel-1",
            name: "petcare-home-a",
            config_src: "cloudflare",
          },
        ],
      },
      {
        result: [
          {
            id: "dns-ignore",
            name: "home-a.agents.example.com",
            type: "A",
            content: "tunnel-1.cfargotunnel.com",
            proxied: true,
          },
          {
            id: "dns-1",
            name: "home-a.agents.example.com",
            type: "CNAME",
            content: "tunnel-1.cfargotunnel.com",
            proxied: true,
          },
        ],
      },
      {
        result: [
          {
            id: "app-ignore",
            aud: "aud-ignore",
            name: "PetCare home-a",
            domain: "other.invalid",
            type: "self_hosted",
            service_auth_401_redirect: true,
          },
          {
            id: "app-1",
            aud: "aud-1",
            name: "PetCare home-a",
            domain: "home-a.agents.example.com",
            type: "self_hosted",
            service_auth_401_redirect: true,
          },
        ],
      },
      {
        result: [
          {
            id: "policy-ignore",
            name: "PetCare Sites BFF",
            decision: "allow",
            precedence: 1,
            include: [
              { service_token: { token_id: "service-token-id" } },
            ],
          },
          {
            id: "policy-1",
            name: "PetCare Sites BFF",
            decision: "non_identity",
            precedence: 1,
            include: [
              { service_token: { token_id: "service-token-id" } },
            ],
          },
        ],
      },
    ]);
    const client = new CloudflareClient(config, fetchImpl);

    await expect(client.findTunnelByName("petcare-home-a")).resolves.toEqual({
      id: "tunnel-1",
    });
    await expect(
      client.findDnsRecordByHostname(
        "home-a.agents.example.com",
        "tunnel-1",
      ),
    ).resolves.toEqual({ id: "dns-1" });
    await expect(
      client.findAccessAppByDomain(
        "home-a.agents.example.com",
        "PetCare home-a",
      ),
    ).resolves.toEqual({ id: "app-1", aud: "aud-1" });
    await expect(
      client.findAccessPolicyByName("app-1", "PetCare Sites BFF"),
    ).resolves.toEqual({ id: "policy-1" });

    expect(calls.map(({ url }) => url)).toEqual([
      "https://api.cloudflare.com/client/v4/accounts/acct/cfd_tunnel?name=petcare-home-a&is_deleted=false",
      "https://api.cloudflare.com/client/v4/zones/zone/dns_records?type=CNAME&name.exact=home-a.agents.example.com&content.exact=tunnel-1.cfargotunnel.com&proxied=true&match=all",
      "https://api.cloudflare.com/client/v4/accounts/acct/access/apps?domain=home-a.agents.example.com&name=PetCare+home-a&exact=true",
      "https://api.cloudflare.com/client/v4/accounts/acct/access/apps/app-1/policies",
    ]);
    expect(calls.every(({ method, body }) => method === "GET" && body === "")).toBe(true);
  });

  it("treats empty, 404, and inexact discovery results as absent", async () => {
    const { fetchImpl } = queuedFetch([
      { result: [] },
      { status: 404 },
      {
        result: [
          {
            id: "app-ignore",
            aud: "aud-ignore",
            name: "PetCare home-a",
            domain: "other.invalid",
            type: "self_hosted",
            service_auth_401_redirect: true,
          },
        ],
      },
      {
        result: [
          {
            id: "policy-ignore",
            name: "PetCare Sites BFF",
            decision: "allow",
            precedence: 1,
            include: [
              { service_token: { token_id: "service-token-id" } },
            ],
          },
        ],
      },
    ]);
    const client = new CloudflareClient(config, fetchImpl);

    await expect(client.findTunnelByName("petcare-home-a")).resolves.toBeNull();
    await expect(
      client.findDnsRecordByHostname(
        "home-a.agents.example.com",
        "tunnel-1",
      ),
    ).resolves.toBeNull();
    await expect(
      client.findAccessAppByDomain(
        "home-a.agents.example.com",
        "PetCare home-a",
      ),
    ).resolves.toBeNull();
    await expect(
      client.findAccessPolicyByName("app-1", "PetCare Sites BFF"),
    ).resolves.toBeNull();
  });

  it("rejects malicious near-matches instead of adopting foreign resources", async () => {
    const { fetchImpl } = queuedFetch([
      {
        result: [
          {
            id: "foreign-tunnel",
            name: "petcare-home-a",
            config_src: "local",
            token: "connector-secret",
          },
        ],
      },
      {
        result: [
          {
            id: "foreign-dns",
            name: "home-a.agents.example.com",
            type: "CNAME",
            content: "attacker.cfargotunnel.com",
            proxied: true,
            api_token: "scoped-token",
          },
        ],
      },
      {
        result: [
          {
            id: "foreign-app",
            aud: "foreign-aud",
            name: "PetCare home-a",
            domain: "home-a.agents.example.com",
            type: "self_hosted",
            service_auth_401_redirect: false,
            secret: "access-client-secret",
          },
        ],
      },
      {
        result: [
          {
            id: "foreign-policy",
            name: "PetCare Sites BFF",
            decision: "non_identity",
            precedence: 1,
            include: [
              { service_token: { token_id: "foreign-service-token" } },
            ],
          },
        ],
      },
    ]);
    const client = new CloudflareClient(config, fetchImpl);

    await expect(client.findTunnelByName("petcare-home-a")).resolves.toBeNull();
    await expect(
      client.findDnsRecordByHostname(
        "home-a.agents.example.com",
        "tunnel-1",
      ),
    ).resolves.toBeNull();
    await expect(
      client.findAccessAppByDomain(
        "home-a.agents.example.com",
        "PetCare home-a",
      ),
    ).resolves.toBeNull();
    await expect(
      client.findAccessPolicyByName("app-1", "PetCare Sites BFF"),
    ).resolves.toBeNull();
  });

  it("rejects ambiguous exact provider matches with a safe error", async () => {
    const exactTunnel = {
      name: "petcare-home-a",
      config_src: "cloudflare",
    };
    const { fetchImpl } = queuedFetch([
      {
        result: [
          { id: "tunnel-1", ...exactTunnel },
          { id: "tunnel-2", ...exactTunnel },
        ],
      },
    ]);

    await expect(
      new CloudflareClient(config, fetchImpl).findTunnelByName(
        "petcare-home-a",
      ),
    ).rejects.toMatchObject({
      code: "cloudflare_api_error",
      status: 502,
    });
  });

  it("bounds every provider request at ten seconds and redacts timeout details", async () => {
    const aborted = AbortSignal.abort(new DOMException("scoped-token", "AbortError"));
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(aborted);
    const fetchImpl: typeof fetch = async (_input, init) => {
      expect(init?.signal).toBe(aborted);
      throw aborted.reason;
    };

    try {
      const error = await new CloudflareClient(config, fetchImpl)
        .createTunnel("petcare-home-a")
        .catch((caught: unknown) => caught);
      expect(timeout).toHaveBeenCalledWith(10_000);
      expect(error).toMatchObject({
        code: "cloudflare_api_error",
        status: 502,
      });
      expect(String(error)).not.toMatch(/scoped-token|AbortError/);
    } finally {
      timeout.mockRestore();
    }
  });

  it("exposes only a fixed error for HTTP and Cloudflare envelope failures", async () => {
    for (const response of [
      { status: 403, result: null },
      { status: 200, success: false, result: null },
    ]) {
      const { fetchImpl } = queuedFetch([response]);
      const error = await new CloudflareClient(config, fetchImpl)
        .createTunnel("petcare-home-a")
        .catch((caught: unknown) => caught);

      expect(error).toBeInstanceOf(CloudflareApiError);
      expect(error).toMatchObject({ code: "cloudflare_api_error" });
      expect(JSON.stringify(error)).not.toMatch(
        /scoped-token|internal-host|access-client-secret/,
      );
      expect(String(error)).toBe("CloudflareApiError: cloudflare_api_error");
    }
  });

  it("deletes policy, app, DNS, and tunnel in order and treats 404 as deleted", async () => {
    const { calls, fetchImpl } = queuedFetch([
      { status: 404 },
      { status: 404 },
      { status: 404 },
      { status: 404 },
    ]);
    const client = new CloudflareClient(config, fetchImpl);

    await client.deleteAccessPolicy("app-1", "policy-1");
    await client.deleteAccessApp("app-1");
    await client.deleteDnsRecord("dns-1");
    await client.deleteTunnel("tunnel-1");

    expect(
      calls.map(
        (call) => `${call.method} ${new URL(call.url).pathname}`,
      ),
    ).toEqual([
      "DELETE /client/v4/accounts/acct/access/apps/app-1/policies/policy-1",
      "DELETE /client/v4/accounts/acct/access/apps/app-1",
      "DELETE /client/v4/zones/zone/dns_records/dns-1",
      "DELETE /client/v4/accounts/acct/cfd_tunnel/tunnel-1",
    ]);
  });
});
