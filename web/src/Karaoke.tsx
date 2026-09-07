import { useEffect, useRef } from "react";
import type { Line } from "./api";
import type { Preferences } from "./storage";
import { StemPlayer } from "./player";

export function time(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
export function Lyrics({
  entries,
  position,
  preferences,
  seek,
}: {
  entries: Line[];
  position: number;
  preferences: Preferences;
  seek: (t: number) => void;
}) {
  const t = Math.max(0, position + preferences.lyricsOffset / 1000);
  let index = entries.findIndex((line) => t < line.end_ts);
  if (index < 0) index = Math.max(0, entries.length - 1);
  const start = Math.max(0, index - Math.floor((preferences.lines - 1) / 2));
  return (
    <div
      className="lyrics"
      style={{ fontSize: `${preferences.fontSize}px` }}
      aria-label="Текст караоке"
    >
      {entries.slice(start, start + preferences.lines).map((line, row) => {
        const active = start + row === index;
        return (
          <button
            key={start + row}
            className={`lyric-line ${active ? "active" : ""}`}
            onClick={() => seek(line.start_ts)}
            title={`Перейти к ${time(line.start_ts)}`}
          >
            {line.words?.length ? (
              line.words.map((word, i) => {
                const progress =
                  t >= word.end_ts
                    ? 100
                    : t <= word.start_ts
                      ? 0
                      : (100 * (t - word.start_ts)) /
                        (word.end_ts - word.start_ts);
                return (
                  <span
                    key={i}
                    className="word"
                    style={{
                      backgroundImage: `linear-gradient(90deg, var(--mint) ${progress}%, ${active ? "var(--text)" : "var(--muted)"} ${progress}%)`,
                    }}
                  >
                    {word.word}{" "}
                  </span>
                );
              })
            ) : (
              <span className={t >= line.end_ts ? "sung" : ""}>
                {line.line || "♫"}
              </span>
            )}
            {active && t < line.start_ts && (
              <small className="lyric-wait">
                Вступление · {Math.ceil(line.start_ts - t)} с
              </small>
            )}
          </button>
        );
      })}
    </div>
  );
}
export function Scope({
  player,
  position,
}: {
  player: StemPlayer;
  position: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const width = canvas.clientWidth,
      height = canvas.clientHeight,
      ratio = devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const ctx = canvas.getContext("2d")!;
    ctx.scale(ratio, ratio);
    const center = width * 0.3;
    for (let x = 0; x < width; x += 3) {
      const at = position + ((x - center) / width) * 10;
      const peak = player.envelope[Math.floor(at * 20)] || 0;
      ctx.fillStyle = x < center ? "#568c85" : "#74f0c0";
      ctx.fillRect(
        x,
        (height - peak * height) / 2,
        2,
        Math.max(2, peak * height),
      );
    }
    ctx.fillStyle = "#fafafa";
    ctx.fillRect(center, 0, 1, height);
  }, [player, position]);
  return (
    <canvas
      ref={ref}
      className="scope"
      aria-label="Громкость вокала и текущая позиция"
    />
  );
}
