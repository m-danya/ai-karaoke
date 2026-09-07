import { Capacitor, registerPlugin } from "@capacitor/core";

export type Word = { word: string; start_ts: number; end_ts: number };
export type Line = {
  line: string;
  start_ts: number;
  end_ts: number;
  words: Word[];
};
export type Track = {
  id: string;
  title: string;
  folder: string;
  missing: boolean;
  karaoke: boolean;
  vocals_url: string;
  instrumental_url: string;
  lyrics?: Line[];
  lyrics_text?: string;
  lyrics_error?: string;
};
export type Catalog = {
  library_id: string;
  name: string;
  tracks: Track[];
  playlists: Record<string, string[]>;
  history: string[];
  folders: string[];
};
export type Server = {
  host: string;
  name: string;
  library_id: string;
  service: string;
  version: number;
};
export type Job = {
  id: string;
  operation: string;
  status: string;
  log: string;
  error?: string;
  result?: { download_url?: string; filename?: string; track_id?: string };
};
export const native = Capacitor.isNativePlatform();
export const Lan = registerPlugin<{
  discover(): Promise<{ servers: Server[] }>;
  download(options: { url: string; filename: string }): Promise<void>;
  keepAwake(options: { enabled: boolean }): Promise<void>;
}>("KaraokeLan");

export function normalizeHost(input: string) {
  const url = new URL(
    input.includes("://") ? input.trim() : `http://${input.trim()}`,
  );
  if (
    url.protocol !== "http:" ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw new Error("Введите IP или имя компьютера, например 192.168.1.10");
  url.port = "9595";
  return url.origin;
}
export class Api {
  constructor(public base: string) {}
  async request<T>(
    path: string,
    options: RequestInit = {},
    timeout = 15000,
  ): Promise<T> {
    const response = await fetch(this.base + path, {
      ...options,
      signal: options.signal ?? AbortSignal.timeout(timeout),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Сервер ответил ${response.status}`);
    }
    return response.json();
  }
  job(body: object) {
    return this.request<Job>("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }
  async download(job: Job) {
    const url = this.base + job.result!.download_url;
    if (native) await Lan.download({ url, filename: job.result!.filename! });
    else {
      const a = document.createElement("a");
      a.href = url;
      a.download = job.result!.filename!;
      a.click();
    }
  }
}
