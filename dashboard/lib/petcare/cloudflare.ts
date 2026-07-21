import type { readPetCareConfig } from "./env";

export type CloudflareConfig = ReturnType<typeof readPetCareConfig>;

export class CloudflareApiError extends Error {
  readonly code = "cloudflare_api_error";

  constructor(readonly status: number) {
    super("cloudflare_api_error");
    this.name = "CloudflareApiError";
  }
}

function hasExactServiceTokenRule(
  include: unknown,
  serviceTokenId: string,
): boolean {
  if (!Array.isArray(include) || include.length !== 1) return false;
  const rule = include[0];
  if (!rule || typeof rule !== "object" || Array.isArray(rule)) return false;
  const entries = Object.entries(rule);
  if (entries.length !== 1 || entries[0][0] !== "service_token") return false;
  const serviceToken = entries[0][1];
  if (
    !serviceToken ||
    typeof serviceToken !== "object" ||
    Array.isArray(serviceToken)
  ) {
    return false;
  }
  const tokenEntries = Object.entries(serviceToken);
  return (
    tokenEntries.length === 1 &&
    tokenEntries[0][0] === "token_id" &&
    tokenEntries[0][1] === serviceTokenId
  );
}

export class CloudflareClient {
  constructor(
    private readonly config: CloudflareConfig,
    private readonly fetchImpl: typeof fetch = fetch,
  ) {}

  get zoneName(): string {
    return this.config.zoneName;
  }

  private async request<T>(
    path: string,
    init: RequestInit,
    allowNotFound = false,
  ): Promise<T | undefined> {
    let response: Response;
    try {
      const timeout = AbortSignal.timeout(10_000);
      const signal = init.signal
        ? AbortSignal.any([init.signal, timeout])
        : timeout;
      response = await this.fetchImpl(
        `https://api.cloudflare.com/client/v4${path}`,
        {
          ...init,
          signal,
          headers: {
            Authorization: `Bearer ${this.config.apiToken}`,
            "Content-Type": "application/json",
            ...init.headers,
          },
        },
      );
    } catch {
      throw new CloudflareApiError(502);
    }

    if (allowNotFound && response.status === 404) return undefined;
    if (!response.ok) throw new CloudflareApiError(response.status);

    try {
      const payload = (await response.json()) as {
        success: boolean;
        result: T;
      };
      if (!payload.success) throw new CloudflareApiError(502);
      return payload.result;
    } catch (error) {
      if (error instanceof CloudflareApiError) throw error;
      throw new CloudflareApiError(502);
    }
  }

  async createTunnel(name: string): Promise<{ id: string }> {
    return (await this.request<{ id: string }>(
      `/accounts/${this.config.accountId}/cfd_tunnel`,
      {
        method: "POST",
        body: JSON.stringify({ name, config_src: "cloudflare" }),
      },
    ))!;
  }

  async createDnsRecord(
    hostname: string,
    tunnelId: string,
  ): Promise<{ id: string }> {
    return (await this.request<{ id: string }>(
      `/zones/${this.config.zoneId}/dns_records`,
      {
        method: "POST",
        body: JSON.stringify({
          type: "CNAME",
          name: hostname,
          content: `${tunnelId}.cfargotunnel.com`,
          proxied: true,
          ttl: 1,
        }),
      },
    ))!;
  }

  async createAccessApp(
    hostname: string,
    name: string,
  ): Promise<{ id: string; aud: string }> {
    return (await this.request<{ id: string; aud: string }>(
      `/accounts/${this.config.accountId}/access/apps`,
      {
        method: "POST",
        body: JSON.stringify({
          name,
          domain: hostname,
          type: "self_hosted",
          service_auth_401_redirect: true,
        }),
      },
    ))!;
  }

  async createAccessPolicy(appId: string): Promise<{ id: string }> {
    return (await this.request<{ id: string }>(
      `/accounts/${this.config.accountId}/access/apps/${appId}/policies`,
      {
        method: "POST",
        body: JSON.stringify({
          name: "PetCare Sites BFF",
          decision: "non_identity",
          include: [
            { service_token: { token_id: this.config.serviceTokenId } },
          ],
          precedence: 1,
        }),
      },
    ))!;
  }

  async configureTunnel(
    tunnelId: string,
    hostname: string,
    aud: string,
  ): Promise<void> {
    await this.request(
      `/accounts/${this.config.accountId}/cfd_tunnel/${tunnelId}/configurations`,
      {
        method: "PUT",
        body: JSON.stringify({
          config: {
            ingress: [
              {
                hostname,
                service: "http://127.0.0.1:8000",
                originRequest: {
                  access: {
                    required: true,
                    teamName: this.config.accessTeamName,
                    audTag: [aud],
                  },
                },
              },
              { service: "http_status:404" },
            ],
          },
        }),
      },
    );
  }

  async getConnectorToken(tunnelId: string): Promise<string> {
    return (await this.request<string>(
      `/accounts/${this.config.accountId}/cfd_tunnel/${tunnelId}/token`,
      { method: "GET" },
    ))!;
  }

  async findTunnelByName(name: string): Promise<{ id: string } | null> {
    const query = new URLSearchParams({ name, is_deleted: "false" });
    const tunnels =
      (await this.request<
        Array<{ id?: unknown; name?: unknown; config_src?: unknown }>
      >(
        `/accounts/${this.config.accountId}/cfd_tunnel?${query}`,
        { method: "GET" },
        true,
      )) ?? [];
    const matches = tunnels.filter(
      (item) =>
        item.name === name &&
        item.config_src === "cloudflare" &&
        typeof item.id === "string",
    );
    if (matches.length > 1) throw new CloudflareApiError(502);
    const id = matches[0]?.id;
    return typeof id === "string" ? { id } : null;
  }

  async findDnsRecordByHostname(
    hostname: string,
    tunnelId: string,
  ): Promise<{ id: string } | null> {
    const content = `${tunnelId}.cfargotunnel.com`;
    const query = new URLSearchParams([
      ["type", "CNAME"],
      ["name.exact", hostname],
      ["content.exact", content],
      ["proxied", "true"],
      ["match", "all"],
    ]);
    const records =
      (await this.request<
        Array<{
          id?: unknown;
          name?: unknown;
          type?: unknown;
          content?: unknown;
          proxied?: unknown;
        }>
      >(
        `/zones/${this.config.zoneId}/dns_records?${query}`,
        { method: "GET" },
        true,
      )) ?? [];
    const matches = records.filter(
      (item) =>
        item.name === hostname &&
        item.type === "CNAME" &&
        item.content === content &&
        item.proxied === true &&
        typeof item.id === "string",
    );
    if (matches.length > 1) throw new CloudflareApiError(502);
    const id = matches[0]?.id;
    return typeof id === "string" ? { id } : null;
  }

  async findAccessAppByDomain(
    domain: string,
    name: string,
  ): Promise<{ id: string; aud: string } | null> {
    const query = new URLSearchParams({ domain, name, exact: "true" });
    const apps =
      (await this.request<
        Array<{
          id?: unknown;
          aud?: unknown;
          name?: unknown;
          domain?: unknown;
          type?: unknown;
          service_auth_401_redirect?: unknown;
        }>
      >(
        `/accounts/${this.config.accountId}/access/apps?${query}`,
        { method: "GET" },
        true,
      )) ?? [];
    const matches = apps.filter(
      (item) =>
        item.name === name &&
        item.domain === domain &&
        item.type === "self_hosted" &&
        item.service_auth_401_redirect === true &&
        typeof item.id === "string" &&
        typeof item.aud === "string",
    );
    if (matches.length > 1) throw new CloudflareApiError(502);
    const app = matches[0];
    return typeof app?.id === "string" && typeof app.aud === "string"
      ? { id: app.id, aud: app.aud }
      : null;
  }

  async findAccessPolicyByName(
    appId: string,
    name = "PetCare Sites BFF",
  ): Promise<{ id: string } | null> {
    const policies =
      (await this.request<
        Array<{
          id?: unknown;
          name?: unknown;
          decision?: unknown;
          precedence?: unknown;
          include?: unknown;
        }>
      >(
        `/accounts/${this.config.accountId}/access/apps/${appId}/policies`,
        { method: "GET" },
        true,
      )) ?? [];
    const matches = policies.filter(
      (item) =>
        item.name === name &&
        item.decision === "non_identity" &&
        item.precedence === 1 &&
        hasExactServiceTokenRule(item.include, this.config.serviceTokenId) &&
        typeof item.id === "string",
    );
    if (matches.length > 1) throw new CloudflareApiError(502);
    const id = matches[0]?.id;
    return typeof id === "string" ? { id } : null;
  }

  async deleteAccessPolicy(appId: string, policyId: string): Promise<void> {
    await this.delete(
      `/accounts/${this.config.accountId}/access/apps/${appId}/policies/${policyId}`,
    );
  }

  async deleteAccessApp(appId: string): Promise<void> {
    await this.delete(
      `/accounts/${this.config.accountId}/access/apps/${appId}`,
    );
  }

  async deleteDnsRecord(recordId: string): Promise<void> {
    await this.delete(`/zones/${this.config.zoneId}/dns_records/${recordId}`);
  }

  async deleteTunnel(tunnelId: string): Promise<void> {
    await this.delete(
      `/accounts/${this.config.accountId}/cfd_tunnel/${tunnelId}`,
    );
  }

  private async delete(path: string): Promise<void> {
    await this.request(path, { method: "DELETE" }, true);
  }
}
