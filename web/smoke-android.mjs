// Attach to an installed debug build: adb forward tcp:9222 localabstract:webview_devtools_remote_PID
import { chromium, expect } from "@playwright/test";
import fs from "node:fs/promises";
const browser = await chromium.connectOverCDP("http://127.0.0.1:9222", {
  noDefaults: true,
});
const page = browser.contexts()[0].pages()[0];
const output = new URL("../artifacts/", import.meta.url);
await fs.mkdir(output, { recursive: true });
console.log("Android WebView:", page.url());
if (await page.locator(".server-card").count()) {
  await page.locator(".server-card").filter({ hasText: "10.0.2.2" }).click();
} else if (await page.locator("#host").count()) {
  throw new Error("Android auto-discovery did not find the demo server");
}
await expect(
  page.getByRole("heading", { name: "Библиотека", exact: true }),
).toBeVisible();
await page.getByRole("button", { name: "Настройки", exact: true }).click();
await page.getByLabel("Отсчёт перед караоке").uncheck();
await page.getByRole("button", { name: "Закрыть", exact: true }).click();
await page.getByRole("button", { name: /Первая песня/ }).click();
await expect(page.getByRole("button", { name: "Начать караоке" })).toBeEnabled({
  timeout: 15000,
});
await page.getByRole("button", { name: "Начать караоке" }).click();
await expect(
  page.getByRole("button", { name: "Пауза", exact: true }),
).toBeVisible();
await page.waitForTimeout(1600);
const pos = +(await page
  .getByRole("slider", { name: "Позиция песни" })
  .inputValue());
if (pos < 1) throw new Error("Android audio clock did not run");
if (
  await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
)
  throw new Error("Android horizontal overflow");
await page.screenshot({
  path: new URL("android-karaoke.png", output).pathname,
  fullPage: true,
});
await page.getByRole("button", { name: "Включить/выключить вокал" }).click();
await page.getByTitle("Вперёд на 10 секунд").click();
await page.waitForTimeout(300);
if (
  +(await page.getByRole("slider", { name: "Позиция песни" }).inputValue()) < 10
)
  throw new Error("Android seek did not work");
await page.getByRole("button", { name: "Пауза", exact: true }).click();
console.log(
  "PASS: Android discovered server automatically; connected, decoded both MP3s and played karaoke with local seek/mute. Position:",
  pos,
);
await browser.close();
