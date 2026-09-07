import type { Catalog, Track } from "./api";
export type Preferences = {
  fontSize: number;
  lines: number;
  countdown: boolean;
  celebration: boolean;
  autoplay: boolean;
  lyricsOffset: number;
};
export const defaults: Preferences = {
  fontSize: 36,
  lines: 3,
  countdown: true,
  celebration: true,
  autoplay: false,
  lyricsOffset: 0,
};
export type Collection = {
  playlists: Record<string, string[]>;
  history: string[];
  known: Record<string, Track>;
  jobs: string[];
};
export function read<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) || "null") ?? fallback;
  } catch {
    return fallback;
  }
}
export function save(key: string, value: unknown) {
  localStorage.setItem(key, JSON.stringify(value));
}
export function loadCollection(catalog: Catalog): Collection {
  const old = read<Collection>(`collection:${catalog.library_id}`, {
    playlists: catalog.playlists,
    history: [],
    known: {},
    jobs: [],
  });
  return {
    ...old,
    known: {
      ...old.known,
      ...Object.fromEntries(catalog.tracks.map((track) => [track.id, track])),
    },
  };
}
