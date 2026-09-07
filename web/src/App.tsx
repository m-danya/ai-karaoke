import { useCallback, useEffect, useRef, useState } from "react";
import { App as CapacitorApp } from "@capacitor/app";
import {
  Music2,
  Search,
  Play,
  Pause,
  Mic2,
  ArrowLeft,
  RefreshCw,
  Settings,
  Volume2,
  VolumeX,
  SkipForward,
  Server as ServerIcon,
  X,
  MoreHorizontal,
  Download,
  Plus,
  FolderOpen,
  Wifi,
  ListMusic,
} from "lucide-react";
import {
  Api,
  Catalog,
  Job,
  Lan,
  native,
  normalizeHost,
  Server,
  Track,
} from "./api";
import { StemPlayer, PlaybackState } from "./player";
import {
  Collection,
  defaults,
  loadCollection,
  Preferences,
  read,
  save,
} from "./storage";
import { Lyrics, Scope, time } from "./Karaoke";

const player = new StemPlayer();
const emptyCollection: Collection = {
  playlists: {},
  history: [],
  known: {},
  jobs: [],
};

export default function App() {
  const [api, setApi] = useState<Api | null>(null);
  const [host, setHost] = useState(read<string>("lastHost", ""));
  const [servers, setServers] = useState<Server[]>([]);
  const [scanning, setScanning] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [collection, setCollection] = useState<Collection>(emptyCollection);
  const [track, setTrack] = useState<Track | null>(null);
  const [state, setState] = useState<PlaybackState>({
    position: 0,
    duration: 0,
    playing: false,
  });
  const [gains, setGains] = useState([1, 1]);
  const [muted, setMuted] = useState([false, false]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");
  const [folder, setFolder] = useState("");
  const [karaoke, setKaraoke] = useState(false);
  const [settings, setSettings] = useState(false);
  const [actions, setActions] = useState(false);
  const [processDialog, setProcessDialog] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [showJobs, setShowJobs] = useState(false);
  const [prefs, setPrefs] = useState<Preferences>({
    ...defaults,
    ...read("preferences", {}),
  });
  const [loading, setLoading] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [countdown, setCountdown] = useState<number | null>(null);
  const [finished, setFinished] = useState(false);
  const [loopIn, setLoopIn] = useState<number | null>(null);
  const [loopOut, setLoopOut] = useState<number | null>(null);
  const [online, setOnline] = useState(true);
  const generation = useRef(0);
  const countdownToken = useRef(0);
  const uploadInput = useRef<HTMLInputElement>(null);
  const busyConnect = useRef(false);
  const collectionsKey = catalog ? `collection:${catalog.library_id}` : "";
  const run = useCallback(async (work: () => Promise<unknown>) => {
    try {
      setError("");
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    save("preferences", prefs);
  }, [prefs]);
  useEffect(() => {
    if (collectionsKey) save(collectionsKey, collection);
  }, [collection, collectionsKey]);
  useEffect(() => {
    if (notice) {
      const id = setTimeout(() => setNotice(""), 5000);
      return () => clearTimeout(id);
    }
  }, [notice]);
  useEffect(() => {
    const id = setInterval(() => setState(player.tick()), 50);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    if (native)
      void Lan.keepAwake({ enabled: karaoke || state.playing }).catch(() => {});
    else if ((karaoke || state.playing) && "wakeLock" in navigator) {
      let lock: WakeLockSentinel | undefined;
      let cancelled = false;
      void navigator.wakeLock
        .request("screen")
        .then((value) => {
          if (cancelled) void value.release();
          else lock = value;
        })
        .catch(() => {});
      return () => {
        cancelled = true;
        void lock?.release();
      };
    }
  }, [karaoke, state.playing]);

  async function connect(input: string) {
    if (busyConnect.current) return;
    busyConnect.current = true;
    setLoading("Подключаемся…");
    try {
      const base = normalizeHost(input),
        client = new Api(base);
      const health = await client.request<Server>("/api/health");
      if (health.service !== "ai-karaoke" || health.version !== 1)
        throw new Error("На этом адресе нет совместимого AI Karaoke");
      const data = await client.request<Catalog>("/api/library");
      setCollection(loadCollection(data));
      setCatalog(data);
      setApi(client);
      setHost(base);
      save("lastHost", base);
      setOnline(true);
    } finally {
      busyConnect.current = false;
      setLoading("");
    }
  }
  const scan = useCallback(async () => {
    setScanning(true);
    try {
      if (native) {
        const result = await Lan.discover();
        setServers(result.servers);
      } else
        setNotice(
          "Автопоиск доступен в Android-приложении. В браузере введите адрес компьютера.",
        );
    } finally {
      setScanning(false);
    }
  }, []);
  useEffect(() => {
    if (native) void run(scan);
    else if (location.port === "9595") void run(() => connect(location.origin));
  }, []);

  function cancelCountdown() {
    countdownToken.current++;
    setCountdown(null);
  }
  function disconnect() {
    generation.current++;
    cancelCountdown();
    player.close();
    setApi(null);
    setCatalog(null);
    setTrack(null);
    setJobs([]);
    setKaraoke(false);
    setLoading("");
    setFilter("All");
  }
  function exitKaraoke() {
    cancelCountdown();
    setKaraoke(false);
    if (document.fullscreenElement) void document.exitFullscreen();
  }
  useEffect(() => {
    if (!native) return;
    const listener = CapacitorApp.addListener("backButton", () => {
      if (settings || actions || processDialog || showJobs) {
        setSettings(false);
        setActions(false);
        setProcessDialog(false);
        setShowJobs(false);
      } else if (karaoke) exitKaraoke();
      else if (track) {
        player.pause();
        setTrack(null);
      } else if (api) disconnect();
      else void CapacitorApp.minimizeApp();
    });
    return () => {
      void listener.then((l) => l.remove());
    };
  }, [api, karaoke, track, settings, actions, processDialog, showJobs]);
  useEffect(() => {
    if (!api) return;
    let active = true;
    const timer = setInterval(() => {
      void api
        .request("/api/health", {}, 3000)
        .then(() => {
          if (active) setOnline(true);
        })
        .catch(() => {
          if (active) setOnline(false);
        });
    }, 10000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api]);
  useEffect(() => {
    if (!api || !collection.jobs.length) return;
    let active = true;
    async function poll() {
      const values = await Promise.all(
        collection.jobs.map((id) =>
          api!.request<Job>(`/api/jobs/${id}`).catch((e) => ({
            id,
            operation: "job",
            status: "unavailable",
            error: e.message,
            log: "",
          })),
        ),
      );
      if (active) setJobs(values);
    }
    void poll();
    const interval = setInterval(() => void poll(), 2000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [api, collection.jobs]);

  async function refresh() {
    if (!api) return;
    const data = await api.request<Catalog>("/api/library");
    if (catalog && catalog.library_id !== data.library_id) {
      generation.current++;
      cancelCountdown();
      player.close();
      setTrack(null);
      setKaraoke(false);
      setFilter("All");
      setFolder("");
      setCollection(loadCollection(data));
      setCatalog(data);
      setOnline(true);
      return;
    }
    setCatalog(data);
    setOnline(true);
    setCollection((old) => ({
      ...old,
      known: {
        ...old.known,
        ...Object.fromEntries(data.tracks.map((t) => [t.id, t])),
      },
    }));
  }
  const actualTracks = catalog?.tracks ?? [];
  const trackMap = new Map(actualTracks.map((t) => [t.id, t]));
  const visible = (
    filter === "All"
      ? actualTracks
      : (filter === "History"
          ? collection.history
          : (collection.playlists[filter] ?? [])
        ).map(
          (id) =>
            trackMap.get(id) ?? {
              ...collection.known[id],
              id,
              title: collection.known[id]?.title ?? id,
              missing: true,
            },
        )
  ).filter(
    (t) =>
      t.title.toLocaleLowerCase().includes(query.toLocaleLowerCase()) &&
      (!folder || t.folder === folder),
  );

  async function select(next: Track, auto = false, sing = false) {
    if (!api || next.missing) return;
    const token = ++generation.current;
    cancelCountdown();
    player.pause();
    setFinished(false);
    setLoopIn(null);
    setLoopOut(null);
    setTrack(next);
    setLoading("Загружаем песню…");
    try {
      await player.unlock();
      const detail = await api.request<Track>(`/api/tracks/${next.id}`);
      if (token !== generation.current) return;
      if (detail.lyrics_error)
        setNotice(`Текст караоке: ${detail.lyrics_error}`);
      setTrack(detail);
      const loaded = await player.load(
        [api.base + detail.vocals_url, api.base + detail.instrumental_url],
        (text) => {
          if (token === generation.current) setLoading(text);
        },
      );
      if (loaded && token === generation.current) {
        if (!sing && matchMedia("(max-width: 760px)").matches)
          document
            .querySelector(".player-panel")
            ?.scrollIntoView({ block: "start" });
        if (sing) await startKaraoke(detail);
        else if (auto) await player.play();
      }
    } catch (e) {
      if (token === generation.current) {
        setTrack(null);
        throw e;
      }
    } finally {
      if (token === generation.current) setLoading("");
    }
  }
  async function startKaraoke(selected = track) {
    if (!selected?.lyrics?.length)
      throw new Error(
        "У песни нет текста с таймингами. Запустите обработку или выравнивание.",
      );
    cancelCountdown();
    player.pause();
    setFinished(false);
    setKaraoke(true);
    await player.seek(player.loop ? player.loop[0] : 0);
    const token = ++countdownToken.current;
    if (prefs.countdown) {
      for (let n = 3; n > 0; n--) {
        setCountdown(n);
        await new Promise((resolve) => setTimeout(resolve, 1000));
        if (countdownToken.current !== token) return;
      }
    }
    setCountdown(null);
    await player.play();
    setCollection((old) => ({
      ...old,
      history: [
        selected.id,
        ...old.history.filter((id) => id !== selected.id),
      ].slice(0, 1000),
    }));
  }
  async function nextTrack() {
    const playable = visible.filter((t) => !t.missing),
      index = playable.findIndex((t) => t.id === track?.id);
    const next = playable[index + 1];
    if (next) await select(next, !karaoke, karaoke);
    else setNotice("Это последняя песня в списке");
  }
  useEffect(() => {
    player.onended = () => {
      setFinished(true);
      if (karaoke && prefs.celebration) player.celebrate();
      if (prefs.autoplay) void run(nextTrack);
    };
  });
  function gain(index: number, value: number, mute = muted[index]) {
    setGains((old) => old.map((v, i) => (i === index ? value : v)));
    setMuted((old) => old.map((v, i) => (i === index ? mute : v)));
    player.setGain(index, value, mute);
  }
  async function seek(value: number) {
    cancelCountdown();
    setFinished(false);
    await player.seek(value);
    if (!player.loop && loopOut !== null) {
      setLoopIn(null);
      setLoopOut(null);
    }
  }
  async function setB() {
    if (loopIn === null || player.current() - loopIn < 0.25)
      throw new Error("Поставьте B хотя бы через 0,25 с после A");
    const out = player.current();
    setLoopOut(out);
    await player.setLoop([loopIn, out]);
  }
  async function clearLoop() {
    setLoopIn(null);
    setLoopOut(null);
    await player.setLoop(null);
  }
  function playlist(addCurrent = false) {
    const name = prompt("Название плейлиста")?.trim();
    if (!name) return;
    if (["all", "history"].includes(name.toLowerCase())) {
      setError("Это название зарезервировано");
      return;
    }
    setCollection((old) => ({
      ...old,
      playlists: {
        ...old.playlists,
        [name]: [
          ...new Set([
            ...(Object.hasOwn(old.playlists, name) ? old.playlists[name] : []),
            ...(addCurrent && track ? [track.id] : []),
          ]),
        ],
      },
    }));
    setFilter(name);
    setActions(false);
  }
  function addToPlaylist(name: string) {
    if (track)
      setCollection((old) => ({
        ...old,
        playlists: {
          ...old.playlists,
          [name]: [
            ...new Set([
              ...(Object.hasOwn(old.playlists, name)
                ? old.playlists[name]
                : []),
              track.id,
            ]),
          ],
        },
      }));
    setActions(false);
  }
  function removeFromFilter() {
    if (!track) return;
    setCollection((old) =>
      filter === "History"
        ? { ...old, history: old.history.filter((id) => id !== track.id) }
        : {
            ...old,
            playlists: {
              ...old.playlists,
              [filter]: old.playlists[filter].filter((id) => id !== track.id),
            },
          },
    );
    setActions(false);
  }
  async function submit(body: object) {
    const job = await api!.job(body);
    setCollection((old) => ({
      ...old,
      jobs: [job.id, ...old.jobs].slice(0, 24),
    }));
    setShowJobs(true);
    setActions(false);
    setProcessDialog(false);
  }
  async function transpose() {
    const raw = prompt("Сдвиг в полутонах (от −12 до +12)", "1");
    if (raw === null) return;
    await submit({
      operation: "transpose",
      track_id: track!.id,
      semitones: Number(raw),
    });
  }
  async function deleteTrack() {
    if (
      !confirm(
        `Удалить «${track!.title}» и все её дорожки и тексты из библиотеки сервера?`,
      )
    )
      return;
    await api!.request(`/api/tracks/${track!.id}`, { method: "DELETE" });
    player.pause();
    setTrack(null);
    setActions(false);
    await refresh();
  }

  const settingsContent = (
    <>
      <label>
        Размер текста <output>{prefs.fontSize}</output>
        <input
          type="range"
          min="18"
          max="72"
          value={prefs.fontSize}
          onChange={(e) => setPrefs({ ...prefs, fontSize: +e.target.value })}
        />
      </label>
      <label>
        Строк на экране <output>{prefs.lines}</output>
        <input
          type="range"
          min="1"
          max="8"
          value={prefs.lines}
          onChange={(e) => setPrefs({ ...prefs, lines: +e.target.value })}
        />
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={prefs.countdown}
          onChange={(e) => setPrefs({ ...prefs, countdown: e.target.checked })}
        />
        Отсчёт перед караоке
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={prefs.celebration}
          onChange={(e) =>
            setPrefs({ ...prefs, celebration: e.target.checked })
          }
        />
        Поздравление и звук в конце
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={prefs.autoplay}
          onChange={(e) => setPrefs({ ...prefs, autoplay: e.target.checked })}
        />
        Следующая песня автоматически
      </label>
      <label>
        Сдвиг текста для Bluetooth, мс
        <input
          type="number"
          min="-3000"
          max="3000"
          step="50"
          value={prefs.lyricsOffset}
          onChange={(e) =>
            setPrefs({
              ...prefs,
              lyricsOffset: Math.max(-3000, Math.min(3000, +e.target.value)),
            })
          }
        />
        <small>Отрицательное значение задерживает подсветку слов.</small>
      </label>
    </>
  );
  const mixer = (
    <div className="mixer">
      {["Вокал", "Инструментал"].map((label, i) => (
        <div className="channel" key={label}>
          <label htmlFor={`gain-${i}`}>
            {label}
            <output>
              {muted[i] ? "выкл" : `${Math.round(gains[i] * 100)}%`}
            </output>
          </label>
          <div className="row">
            <button
              className={muted[i] ? "selected icon" : "icon"}
              title={`Включить/выключить ${label.toLowerCase()}`}
              onClick={() => gain(i, gains[i], !muted[i])}
            >
              {muted[i] ? <VolumeX /> : <Volume2 />}
            </button>
            <input
              id={`gain-${i}`}
              type="range"
              min="0"
              max="2"
              step="0.01"
              value={gains[i]}
              onChange={(e) => gain(i, +e.target.value, false)}
            />
            <button onClick={() => gain(i, 1, false)}>100%</button>
          </div>
        </div>
      ))}
    </div>
  );
  const transport = (
    <div className="transport">
      <input
        aria-label="Позиция песни"
        type="range"
        min="0"
        max={state.duration || 1}
        step="0.05"
        value={state.position}
        disabled={!!loading || !state.duration}
        onChange={(e) => void run(() => seek(+e.target.value))}
      />
      <div className="transport-row">
        <span className="time">
          {time(state.position)} / {time(state.duration)}
        </span>
        <div className="row">
          <button
            title="Назад на 10 секунд"
            onClick={() => void run(() => seek(player.current() - 10))}
            disabled={!!loading}
          >
            −10
          </button>
          <button
            className="play primary icon"
            aria-label={state.playing ? "Пауза" : "Воспроизвести"}
            disabled={!!loading || !track || countdown !== null}
            onClick={() =>
              void run(async () => {
                setFinished(false);
                if (player.playing) player.pause();
                else await player.play();
              })
            }
          >
            {state.playing ? (
              <Pause fill="currentColor" />
            ) : (
              <Play fill="currentColor" />
            )}
          </button>
          <button
            title="Вперёд на 10 секунд"
            onClick={() => void run(() => seek(player.current() + 10))}
            disabled={!!loading}
          >
            +10
          </button>
          <button
            className="icon"
            title="Следующая песня"
            disabled={!!loading}
            onClick={() => void run(nextTrack)}
          >
            <SkipForward />
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className={karaoke ? "app karaoke-mode" : "app"}>
      <header>
        <div className="brand">
          <Music2 />
          <div>
            AI Karaoke
            <small>{api ? catalog?.name : "Ваша сцена — где угодно"}</small>
          </div>
        </div>
        <div className="row">
          {api && (
            <button
              className="icon"
              title="Сменить сервер"
              onClick={disconnect}
            >
              <ServerIcon />
            </button>
          )}
          <button
            className="icon"
            title="Настройки"
            onClick={() => setSettings(true)}
          >
            <Settings />
          </button>
        </div>
      </header>
      {error && (
        <div className="banner error" role="alert">
          {error}
          <button
            className="icon"
            title="Закрыть ошибку"
            onClick={() => setError("")}
          >
            <X />
          </button>
        </div>
      )}
      {notice && (
        <div className="banner" role="status">
          {notice}
          <button
            className="icon"
            title="Закрыть сообщение"
            onClick={() => setNotice("")}
          >
            <X />
          </button>
        </div>
      )}
      {api && !online && (
        <div className="banner error">
          Сервер недоступен. Загруженная песня продолжает играть; проверьте
          Wi-Fi и server mode.
        </div>
      )}
      {!api ? (
        <main className="connect">
          <div className="connect-art">
            <Mic2 size={54} />
            <span className="pill">
              <Wifi size={14} /> Звук на вашем устройстве
            </span>
          </div>
          <h1>
            Пойте вместе.
            <br />
            <em>Каждый в своём ритме.</em>
          </h1>
          <p>
            Включите Server mode в AI Karaoke на компьютере и подключите
            устройства к одной сети Wi-Fi.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(() => connect(host));
            }}
          >
            <label htmlFor="host">Адрес компьютера</label>
            <div className="row">
              <input
                id="host"
                value={host}
                onChange={(e) => setHost(e.target.value)}
                placeholder="192.168.1.10"
                autoCapitalize="none"
                autoCorrect="off"
              />
              <button className="primary" disabled={!!loading || !host.trim()}>
                Подключиться
              </button>
            </div>
          </form>
          <div className="section-title">
            <h2>В локальной сети</h2>
            <button onClick={() => void run(scan)} disabled={scanning}>
              <RefreshCw className={scanning ? "spin" : ""} />
              {scanning ? "Поиск…" : "Найти"}
            </button>
          </div>
          {servers.map((s) => (
            <button
              key={s.host}
              className="server-card"
              onClick={() => void run(() => connect(s.host))}
            >
              <ServerIcon />
              <span>
                {s.name}
                <small>{s.host}:9595</small>
              </span>
              <span>Подключить →</span>
            </button>
          ))}
          {!scanning && !servers.length && (
            <p className="muted">
              {native
                ? "Пока ни одного сервера. Повторите поиск или введите адрес вручную."
                : "Автопоиск работает в Android-приложении."}
            </p>
          )}
          {loading && <p role="status">{loading}</p>}
        </main>
      ) : karaoke ? (
        <main className="stage">
          <div className="stage-title">
            <button onClick={exitKaraoke}>
              <ArrowLeft />
              Библиотека
            </button>
            <h1>{track?.title}</h1>
            <button
              className="icon"
              title="На весь экран"
              onClick={() => {
                if (!document.fullscreenElement)
                  void document.documentElement
                    .requestFullscreen?.()
                    .catch(() => {});
                else void document.exitFullscreen();
              }}
            >
              <Mic2 />
            </button>
          </div>
          <div className="stage-lyrics">
            {loading ? (
              <p role="status">{loading}</p>
            ) : countdown !== null ? (
              <div className="countdown" aria-live="assertive">
                {countdown}
              </div>
            ) : finished && prefs.celebration ? (
              <div className="celebration">
                <span>✦</span>
                <h1>Браво!</h1>
                <p>Эта сцена — ваша.</p>
                <button onClick={() => void run(() => startKaraoke())}>
                  Спеть ещё раз
                </button>
              </div>
            ) : (
              <Lyrics
                entries={track?.lyrics ?? []}
                position={state.position}
                preferences={prefs}
                seek={(t) => void run(() => seek(t))}
              />
            )}
          </div>
          <Scope player={player} position={state.position} />
          <div className="loop row">
            <button
              className={loopIn !== null ? "selected" : ""}
              disabled={!!loading}
              onClick={() => {
                setLoopIn(player.current());
                setLoopOut(null);
                void player.setLoop(null);
              }}
            >
              A {loopIn !== null && time(loopIn)}
            </button>
            <button
              className={loopOut !== null ? "selected" : ""}
              disabled={!!loading || loopIn === null}
              onClick={() => void run(setB)}
            >
              B {loopOut !== null && time(loopOut)}
            </button>
            <button
              disabled={loopIn === null}
              onClick={() => void run(clearLoop)}
            >
              Сброс петли
            </button>
            <span className="muted">
              {loopOut !== null
                ? "Повтор A–B"
                : "Нажмите на строку, чтобы перейти к ней"}
            </span>
          </div>
          {transport}
          {mixer}
        </main>
      ) : (
        <main className="workspace">
          <section className="library">
            <div className="section-title">
              <h1>Библиотека</h1>
              <div className="row">
                <button
                  className="icon"
                  title="Обновить библиотеку"
                  onClick={() => void run(refresh)}
                >
                  <RefreshCw />
                </button>
                <button
                  className="icon"
                  title="Новый плейлист"
                  onClick={() => playlist()}
                >
                  <Plus />
                </button>
              </div>
            </div>
            <div className="search">
              <Search />
              <input
                aria-label="Поиск песни"
                placeholder="Песня, исполнитель…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="row filters">
              <select
                aria-label="Плейлист"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              >
                <option value="All">Все песни</option>
                <option value="History">История</option>
                {Object.keys(collection.playlists).map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
              <select
                aria-label="Папка"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
              >
                <option value="">Все папки</option>
                {catalog?.folders.filter(Boolean).map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
            </div>
            <div className="track-count">
              {visible.length} песен
              {filter !== "All" && filter !== "History" && (
                <button
                  onClick={() => {
                    if (confirm(`Удалить плейлист «${filter}»?`)) {
                      setCollection((old) => {
                        const playlists = { ...old.playlists };
                        delete playlists[filter];
                        return { ...old, playlists };
                      });
                      setFilter("All");
                    }
                  }}
                >
                  Удалить плейлист
                </button>
              )}
            </div>
            <div className="track-list">
              {visible.map((t) => (
                <button
                  key={t.id}
                  className={`track ${track?.id === t.id ? "selected" : ""} ${t.missing ? "missing" : ""}`}
                  onClick={() => {
                    if (t.missing) {
                      setTrack(t);
                      player.pause();
                      setActions(true);
                    } else void run(() => select(t));
                  }}
                >
                  <span className="track-art">
                    {t.karaoke ? <Mic2 /> : <Music2 />}
                  </span>
                  <span className="track-copy">
                    {t.title}
                    <small>
                      {t.missing
                        ? "Файлы отсутствуют"
                        : t.karaoke
                          ? "Караоке готово"
                          : "Без таймингов текста"}
                    </small>
                  </span>
                  <span className="track-indicator">
                    {track?.id === t.id && state.playing ? <Volume2 /> : "›"}
                  </span>
                </button>
              ))}
              {!visible.length && (
                <div className="empty">
                  <ListMusic />
                  <p>{query ? "Песни не найдены" : "Здесь пока нет песен"}</p>
                  <small>Импортируйте MP3 и запустите обработку.</small>
                </div>
              )}
            </div>
            <div className="library-actions">
              <button onClick={() => uploadInput.current?.click()}>
                <Plus />
                MP3
              </button>
              <button onClick={() => setProcessDialog(true)}>
                <FolderOpen />
                Обработка
              </button>
              <button onClick={() => setShowJobs(true)}>
                Задания{" "}
                {jobs.filter((j) => ["queued", "running"].includes(j.status))
                  .length || ""}
              </button>
            </div>
            <input
              ref={uploadInput}
              type="file"
              accept="audio/mpeg,.mp3"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file)
                  void run(async () => {
                    const directory = prompt(
                      "Папка в библиотеке (например Исполнитель/Альбом)",
                      folder,
                    );
                    if (directory === null) return;
                    setLoading("Загружаем MP3…");
                    try {
                      await api.request(
                        `/api/import?name=${encodeURIComponent(file.name)}&folder=${encodeURIComponent(directory)}`,
                        {
                          method: "POST",
                          headers: { "Content-Type": "audio/mpeg" },
                          body: file,
                        },
                        300000,
                      );
                      setNotice(
                        "MP3 загружен. Запустите обработку, чтобы создать караоке.",
                      );
                    } finally {
                      setLoading("");
                    }
                  });
                e.target.value = "";
              }}
            />
          </section>
          <section className="player-panel">
            {track && !track.missing ? (
              <>
                <div className="song-cover">
                  <Mic2 size={76} />
                  <span>AI KARAOKE · YOUR STAGE</span>
                </div>
                <div className="section-title">
                  <div>
                    <small className="eyebrow">Сейчас выбрано</small>
                    <h2>{track.title}</h2>
                  </div>
                  <button
                    className="icon"
                    title="Действия с песней"
                    onClick={() => setActions(true)}
                  >
                    <MoreHorizontal />
                  </button>
                </div>
                {loading && <p role="status">{loading}</p>}
                <Scope player={player} position={state.position} />
                {transport}
                {mixer}
                <button
                  className="primary karaoke-start"
                  disabled={!!loading || !track.lyrics?.length}
                  onClick={() => void run(() => startKaraoke())}
                >
                  <Mic2 />
                  Начать караоке
                </button>
                {!loading && !track.lyrics?.length && (
                  <p className="muted">
                    Нет текста с таймингами. Запустите обработку.
                  </p>
                )}
                <details>
                  <summary>Текст песни</summary>
                  <div className="plain-lyrics">
                    {track.lyrics_text ||
                      track.lyrics?.map((l) => l.line).join("\n") ||
                      "Текст пока не найден"}
                  </div>
                </details>
              </>
            ) : (
              <div className="empty">
                <Mic2 size={64} />
                <h2>Выберите свою песню</h2>
                <p>Музыка будет играть на этом устройстве.</p>
              </div>
            )}
          </section>
        </main>
      )}
      {settings && (
        <Modal title="Настройки караоке" close={() => setSettings(false)}>
          {settingsContent}
        </Modal>
      )}
      {actions && track && (
        <Modal title={track.title} close={() => setActions(false)}>
          <div className="action-list">
            <h3>Добавить в плейлист</h3>
            {Object.keys(collection.playlists).map((name) => (
              <button key={name} onClick={() => addToPlaylist(name)}>
                <ListMusic />
                {name}
              </button>
            ))}
            <button onClick={() => playlist(true)}>
              <Plus />
              Новый плейлист
            </button>
            {filter !== "All" && (
              <button onClick={removeFromFilter}>
                Убрать из {filter === "History" ? "истории" : "плейлиста"}
              </button>
            )}
            {!track.missing && (
              <>
                <h3>Песня</h3>
                <a
                  className="button"
                  target="_blank"
                  rel="noreferrer"
                  href={`https://www.google.com/search?q=${encodeURIComponent("genius " + track.title)}`}
                >
                  Найти текст на Genius ↗
                </a>
                <button onClick={() => void run(transpose)}>
                  Транспонировать копию
                </button>
                <button
                  onClick={() =>
                    void run(() =>
                      submit({
                        operation: "export_mp3",
                        track_id: track.id,
                        vocals_gain: gains[0],
                        instrumental_gain: gains[1],
                        vocals_muted: muted[0],
                        instrumental_muted: muted[1],
                      }),
                    )
                  }
                >
                  <Download />
                  MP3 с текущим миксом
                </button>
                <button
                  onClick={() =>
                    void run(() =>
                      submit({ operation: "export_mp3", track_id: track.id }),
                    )
                  }
                >
                  <Download />
                  MP3 с исходными громкостями
                </button>
                <button
                  disabled={!track.karaoke}
                  onClick={() => {
                    setActions(false);
                    setProcessDialog(false);
                    const size = prompt(
                      "MP4: ширина × высота, например 1280x720 или 1920x800",
                      "1280x720",
                    );
                    if (!size) return;
                    const [width, height] = size
                      .toLowerCase()
                      .split("x")
                      .map(Number);
                    const fps = prompt("Частота кадров (1–60)", "30");
                    if (fps !== null)
                      void run(() =>
                        submit({
                          operation: "export_mp4",
                          track_id: track.id,
                          width,
                          height,
                          fps: +fps,
                          font_size: prefs.fontSize,
                          visible_lines: prefs.lines,
                          countdown: prefs.countdown,
                          celebration: prefs.celebration,
                        }),
                      );
                  }}
                >
                  <Download />
                  Видео караоке MP4
                </button>
                <button
                  className="danger"
                  onClick={() => void run(deleteTrack)}
                >
                  Удалить с сервера
                </button>
              </>
            )}
          </div>
        </Modal>
      )}
      {processDialog && (
        <Modal
          title="Обработка библиотеки"
          close={() => setProcessDialog(false)}
        >
          <p>
            Разделение MP3, поиск текста и тайминги выполняются на компьютере.
          </p>
          <form
            className="form"
            onSubmit={(e) => {
              e.preventDefault();
              const data = new FormData(e.currentTarget);
              void run(() =>
                submit({
                  operation: "process",
                  path: data.get("path"),
                  workers: Number(data.get("workers")),
                  genius_delay: Number(data.get("delay")),
                  only_align: data.get("align") === "on",
                }),
              );
            }}
          >
            <label>
              Папка или файл внутри библиотеки
              <input
                name="path"
                defaultValue={folder}
                placeholder="Вся библиотека"
              />
            </label>
            <label>
              Параллельных процессов
              <input
                name="workers"
                type="number"
                min="1"
                max="16"
                defaultValue="1"
                required
              />
            </label>
            <label>
              Пауза между запросами Genius, с
              <input
                name="delay"
                type="number"
                min="0"
                max="300"
                defaultValue="3"
                required
              />
            </label>
            <label className="check">
              <input name="align" type="checkbox" />
              Только выравнивание текста (--only-align)
            </label>
            <button className="primary">Начать обработку</button>
          </form>
        </Modal>
      )}
      {showJobs && (
        <Modal
          title="Задания этого устройства"
          close={() => {
            setShowJobs(false);
            void run(refresh);
          }}
        >
          {!jobs.length && <p>Заданий пока нет.</p>}
          {jobs.map((job) => (
            <div className="job" key={job.id}>
              <div className="section-title">
                <strong>
                  {{
                    process: "Обработка",
                    transpose: "Транспонирование",
                    export_mp3: "Экспорт MP3",
                    export_mp4: "Экспорт MP4",
                  }[job.operation] || job.operation}
                </strong>
                <span>
                  {{
                    done: "Готово",
                    running: "Выполняется",
                    queued: "В очереди",
                    error: "Ошибка",
                    cancelled: "Отменено",
                    unavailable: "Недоступно",
                  }[job.status] || job.status}
                </span>
              </div>
              {job.error && <p className="danger">{job.error}</p>}
              {job.log && (
                <details>
                  <summary>Журнал</summary>
                  <pre>{job.log}</pre>
                </details>
              )}
              {job.result?.download_url && (
                <button
                  onClick={() =>
                    void run(async () => {
                      await api!.download(job);
                      setNotice("Файл передан в загрузки");
                    })
                  }
                >
                  <Download />
                  Скачать
                </button>
              )}
              {job.operation === "process" &&
                ["running", "queued"].includes(job.status) && (
                  <button
                    className="danger"
                    onClick={() =>
                      void run(() =>
                        api!.request(`/api/jobs/${job.id}`, {
                          method: "DELETE",
                        }),
                      )
                    }
                  >
                    Остановить
                  </button>
                )}
              <button
                disabled={["running", "queued"].includes(job.status)}
                onClick={() =>
                  setCollection((old) => ({
                    ...old,
                    jobs: old.jobs.filter((id) => id !== job.id),
                  }))
                }
              >
                Убрать из списка
              </button>
            </div>
          ))}
        </Modal>
      )}
      {error && (settings || actions || processDialog || showJobs) && (
        <Modal title="Ошибка" close={() => setError("")}>
          <p role="alert">{error}</p>
        </Modal>
      )}
    </div>
  );
}
function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        close();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div className="modal-head">
        <h2>{title}</h2>
        <button className="icon" title="Закрыть" onClick={close}>
          <X />
        </button>
      </div>
      <div className="modal-body">{children}</div>
    </dialog>
  );
}
