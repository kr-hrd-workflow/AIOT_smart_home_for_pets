import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { DatabaseSync, type StatementSync } from "node:sqlite";

class FakeD1Statement {
  constructor(
    private readonly database: DatabaseSync,
    private readonly query: string,
    private readonly values: unknown[] = [],
    private readonly beforeExecute?: (query: string) => void,
  ) {}

  bind(...values: unknown[]) {
    return new FakeD1Statement(this.database, this.query, values, this.beforeExecute);
  }

  private statement(): StatementSync {
    return this.database.prepare(this.query);
  }

  async first<T>(column?: string): Promise<T | null> {
    const row = this.statement().get(...this.values) as Record<string, unknown> | undefined;
    if (!row) return null;
    return (column ? row[column] : row) as T;
  }

  async run<T>(): Promise<D1Result<T>> {
    const result = this.statement().run(...this.values);
    return {
      success: true,
      results: [],
      meta: { changes: Number(result.changes) },
    } as D1Result<T>;
  }

  async all<T>(): Promise<D1Result<T>> {
    return {
      success: true,
      results: this.statement().all(...this.values) as T[],
      meta: { changes: 0 },
    } as D1Result<T>;
  }

  async raw<T>(): Promise<T[]> {
    return (this.statement().all(...this.values) as Record<string, unknown>[]).map(
      (row) => Object.values(row) as T,
    );
  }

  async execute(): Promise<D1Result<unknown>> {
    this.beforeExecute?.(this.query);
    return this.run();
  }
}

export class FakeD1 {
  private readonly database = new DatabaseSync(":memory:");
  private failure: RegExp | null = null;
  private batchTail: Promise<void> = Promise.resolve();

  constructor() {
    this.database.exec("PRAGMA foreign_keys = ON");
    const drizzle = resolve(import.meta.dirname, "../../drizzle");
    for (const migration of ["0000_petcare_tenancy.sql", "0001_petcare_tunnels_clips.sql"]) {
      this.database.exec(
        readFileSync(resolve(drizzle, migration), "utf8").replaceAll(
          "--> statement-breakpoint",
          "",
        ),
      );
    }
  }

  prepare(query: string) {
    return new FakeD1Statement(this.database, query, [], (candidate) => {
      if (this.failure?.test(candidate)) {
        this.failure = null;
        throw new Error("synthetic failure");
      }
    });
  }

  failOnce(pattern: RegExp): void {
    this.failure = pattern;
  }

  async batch<T>(statements: FakeD1Statement[]): Promise<D1Result<T>[]> {
    const previous = this.batchTail;
    let release!: () => void;
    this.batchTail = new Promise<void>((resolve) => {
      release = resolve;
    });
    await previous;
    try {
      this.database.exec("BEGIN IMMEDIATE");
      const results: D1Result<T>[] = [];
      for (const statement of statements) {
        results.push((await statement.execute()) as D1Result<T>);
      }
      this.database.exec("COMMIT");
      return results;
    } catch (error) {
      this.database.exec("ROLLBACK");
      throw error;
    } finally {
      release();
    }
  }

  async exec(query: string): Promise<D1ExecResult> {
    this.database.exec(query);
    return { count: 0, duration: 0 };
  }

  get rows(): Record<string, unknown[]> {
    const tables = this.database
      .prepare("SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
      .all() as Array<{ name: string }>;
    return Object.fromEntries(
      tables.map(({ name }) => [
        name,
        this.database.prepare(`SELECT * FROM "${name.replaceAll('"', '""')}"`).all(),
      ]),
    );
  }

  dispose(): void {
    this.database.close();
  }
}

export class FakeR2 {
  readonly objects = new Map<string, Uint8Array>();

  async put(key: string, value: string | ArrayBuffer | ArrayBufferView): Promise<void> {
    const bytes =
      typeof value === "string"
        ? new TextEncoder().encode(value)
        : value instanceof ArrayBuffer
          ? new Uint8Array(value)
          : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    this.objects.set(key, bytes.slice());
  }

  async get(key: string): Promise<{ arrayBuffer(): Promise<ArrayBuffer> } | null> {
    const value = this.objects.get(key);
    if (!value) return null;
    return { arrayBuffer: async () => value.slice().buffer as ArrayBuffer };
  }

  async delete(key: string): Promise<void> {
    this.objects.delete(key);
  }
}

export function jsonRequest(
  url: string,
  body: unknown,
  init: Omit<RequestInit, "body"> = {},
): Request {
  const encoded = JSON.stringify(body);
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  headers.set("Content-Length", String(new TextEncoder().encode(encoded).byteLength));
  return new Request(url, { ...init, method: init.method ?? "POST", headers, body: encoded });
}

export function fixedClock(iso: string): () => Date {
  return () => new Date(iso);
}
