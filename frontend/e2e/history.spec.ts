import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const env = Object.fromEntries(
  readFileSync(new URL("../../.env", import.meta.url), "utf8")
    .split(/\r?\n/)
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => line.split("=")),
);

test("timeline applies the same cutoff to graph, alert queue and ledger table", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Tên đăng nhập").fill("viewer");
  await page.getByLabel("Mật khẩu", { exact: true }).fill(env.VIEWER_PASSWORD);
  await page.getByRole("button", { name: "Vào workspace" }).click();
  await expect(page.locator("#run-picker")).not.toHaveValue("");
  await expect(
    page.getByText("Hoàn thành", { exact: true }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Khám phá dòng tiền", exact: true })
    .click();
  const transactions = page.waitForResponse(
    (response) =>
      response.url().includes("/api/transactions?") &&
      new URL(response.url()).searchParams.get("as_of_index") === "1",
  );
  const alerts = page.waitForResponse(
    (response) =>
      response.url().includes("/api/alerts?") &&
      new URL(response.url()).searchParams.get("as_of_index") === "1",
  );
  const graph = page.waitForResponse(
    (response) =>
      response.url().includes("/api/graph?") &&
      new URL(response.url()).searchParams.get("as_of_index") === "1",
  );
  await page.locator("#timeline").focus();
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowRight");
  const [txReply, alertReply, graphReply] = await Promise.all([
    transactions,
    alerts,
    graph,
  ]);
  const txData = await txReply.json();
  expect(txData.items).toHaveLength(2);
  expect(
    txData.items.every(
      (item: { global_index: number }) => item.global_index <= 1,
    ),
  ).toBeTruthy();
  expect(
    (await alertReply.json()).items.every(
      (item: { detected_order: number }) => item.detected_order <= 1,
    ),
  ).toBeTruthy();
  expect(
    (await graphReply.json()).edges.every(
      (item: { global_index: number }) => item.global_index <= 1,
    ),
  ).toBeTruthy();
  await page
    .getByRole("button", { name: "Điều khiển mô phỏng", exact: true })
    .click();
  await expect(page.locator(".transactions tbody tr")).toHaveCount(2);
});

test("an earlier refresh cannot overwrite a later response", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options);
        window.addEventListener("test:aml-progress", () =>
          this.dispatchEvent(new MessageEvent("progress", { data: "{}" })),
        );
      }
    };
  });
  await page.goto("/");
  await page.getByLabel("Tên đăng nhập").fill("viewer");
  await page.getByLabel("Mật khẩu", { exact: true }).fill(env.VIEWER_PASSWORD);
  await page.getByRole("button", { name: "Vào workspace" }).click();
  await expect(page.locator(".transactions tbody tr")).toHaveCount(20);
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let firstArrived = () => {};
  const arrived = new Promise<void>((resolve) => {
    firstArrived = resolve;
  });
  let calls = 0;
  await page.route("**/api/transactions?**", async (route) => {
    const current = ++calls;
    const response = await route.fetch();
    const data = await response.json();
    if (current === 1) {
      firstArrived();
      await gate;
      await route.fulfill({
        response,
        json: { ...data, items: [] },
        headers: { ...response.headers(), "X-Test-Stale": "1" },
      });
    } else {
      await route.fulfill({
        response,
        json: { ...data, items: data.items.slice(0, 2) },
      });
    }
  });
  try {
    await page.evaluate(() =>
      window.dispatchEvent(new Event("test:aml-progress")),
    );
    await arrived;
    await page.evaluate(() =>
      window.dispatchEvent(new Event("test:aml-progress")),
    );
    await expect(page.locator(".transactions tbody tr")).toHaveCount(2);
    const stale = page.waitForResponse(
      (response) => response.headers()["x-test-stale"] === "1",
    );
    release();
    await (await stale).finished();
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    await expect(page.locator(".transactions tbody tr")).toHaveCount(2);
  } finally {
    release();
  }
});
