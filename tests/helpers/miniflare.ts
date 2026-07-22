export function miniflarePort(shard: number): number {
  return 20_000 + shard * 1_000 + (process.pid % 1_000);
}
