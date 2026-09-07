import { chromium, expect } from "@playwright/test";
import fs from "node:fs/promises";
const output = new URL("../artifacts/", import.meta.url);
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  args: ["--autoplay-policy=no-user-gesture-required"],
});
const contexts = [];
const failures = [];
try {
  for (const [name, width, height] of [
    ["phone", 390, 844],
    ["tablet", 1280, 800],
  ]) {
    const context = await browser.newContext({
      viewport: { width, height },
      deviceScaleFactor: 1,
    });
    contexts.push(context);
    const page = await context.newPage();
    page.on("pageerror", (e) => failures.push(e.message));
    await page.addInitScript(() => {
      const Original = window.AudioContext;
      window.AudioContext = class extends Original {
        constructor(...args) {
          super(...args);
          window.smokeAudio = this;
          window.smokeStarts = [];
          window.smokeGains = [];
          const source = this.createBufferSource.bind(this);
          this.createBufferSource = () => {
            const node = source(),
              start = node.start.bind(node);
            node.start = (...values) => {
              window.smokeStarts.push(values);
              return start(...values);
            };
            return node;
          };
          const gain = this.createGain.bind(this);
          this.createGain = () => {
            const node = gain();
            window.smokeGains.push(node);
            return node;
          };
        }
      };
    });
    await page.goto("http://127.0.0.1:9595");
    await expect(
      page.getByRole("heading", { name: "Библиотека" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Настройки", exact: true }).click();
    await page.getByLabel("Отсчёт перед караоке").uncheck();
    await page.getByRole("button", { name: "Закрыть", exact: true }).click();
    await page.getByRole("button", { name: /Первая песня/ }).click();
    await expect(
      page.getByRole("button", { name: "Начать караоке" }),
    ).toBeEnabled({ timeout: 15000 });
    await page.screenshot({
      path: new URL(`${name}-library.png`, output).pathname,
      fullPage: true,
    });
    await page.getByRole("button", { name: "Начать караоке" }).click();
    await expect(
      page.getByRole("button", { name: "Пауза", exact: true }),
    ).toBeEnabled();
    await page.waitForTimeout(1300);
    const starts = await page.evaluate(() => window.smokeStarts);
    if (
      starts.length !== 2 ||
      starts[0][0] !== starts[1][0] ||
      starts[0][1] !== starts[1][1]
    )
      throw new Error("Stems did not share a sample clock/start offset");
    const position = Number(
      await page.getByRole("slider", { name: "Позиция песни" }).inputValue(),
    );
    if (position < 0.5) throw new Error("Device audio clock did not advance");
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    );
    if (overflow) throw new Error(`${name}: horizontal overflow`);
    await page.screenshot({
      path: new URL(`${name}-karaoke.png`, output).pathname,
      fullPage: true,
    });
    console.log(
      name,
      "karaoke running",
      position,
      "seconds; synchronized starts:",
      starts,
    );
  }
  const first = contexts[0].pages()[0],
    second = contexts[1].pages()[0];
  await first.getByRole("button", { name: "Пауза", exact: true }).click();
  const paused = +(await first
    .getByRole("slider", { name: "Позиция песни" })
    .inputValue());
  await first.getByRole("button", { name: "Включить/выключить вокал" }).click();
  await second.getByTitle("Вперёд на 10 секунд").click();
  await second.waitForTimeout(600);
  if (
    Math.abs(
      +(await first
        .getByRole("slider", { name: "Позиция песни" })
        .inputValue()) - paused,
    ) > 0.1
  )
    throw new Error("Other client changed paused client");
  if (
    +(await second
      .getByRole("slider", { name: "Позиция песни" })
      .inputValue()) < 10
  )
    throw new Error("Seek did not apply independently");
  await expect(
    second.getByRole("button", { name: "Пауза", exact: true }),
  ).toBeVisible();
  await first
    .getByRole("button", { name: "Воспроизвести", exact: true })
    .click();
  await first.waitForTimeout(300);
  const muted = await first.evaluate(() => window.smokeGains.at(-2).gain.value);
  const independent = await second.evaluate(
    () => window.smokeGains.at(-2).gain.value,
  );
  if (muted > 0.01 || independent !== 1)
    throw new Error("Independent gain check failed");
  await second.getByRole("button", { name: /^A$/ }).click();
  await second.waitForTimeout(600);
  await second.getByRole("button", { name: /^B$/ }).click();
  await expect(second.getByText("Повтор A–B", { exact: true })).toBeVisible();
  const loop = +(await second
    .getByRole("slider", { name: "Позиция песни" })
    .inputValue());
  await second.waitForTimeout(1500);
  const loopNow = +(await second
    .getByRole("slider", { name: "Позиция песни" })
    .inputValue());
  if (Math.abs(loopNow - loop) > 1) throw new Error("Loop did not repeat");
  await second.getByRole("button", { name: "Сброс петли" }).click();
  await first.getByRole("button", { name: "Библиотека", exact: true }).click();
  await first.getByLabel("Плейлист", { exact: true }).selectOption("History");
  await expect(
    first.getByRole("button", { name: /Первая песня/ }),
  ).toBeVisible();
  if (failures.length) throw new Error(failures.join("\n"));
  console.log(
    "PASS: two independent clients, local audio clocks, mix, seek, loop, history and responsive layouts",
  );
} finally {
  await browser.close();
}
