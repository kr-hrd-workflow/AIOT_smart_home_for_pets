import type { readPetCareConfig } from "./env";

export type CloudflareConfig = ReturnType<typeof readPetCareConfig>;

export class CloudflareApiError extends Error {
  readonly code = "cloudflare_api_error";

  constructor(readonly status: number) {
    super("cloudflare_api_error");
    this.name = "CloudflareApiError";
  }
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
      response = await this.fetchImpl(
        `https://api.cloudflare.com/client/v4${path}`,
        {
          ...init,
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
