import { test, expect } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";
import { randomUUID } from "node:crypto";
const env = Object.fromEntries(
  readFileSync(new URL("../../.env", import.meta.url), "utf8")
    .split(/\r?\n/)
    .filter((l) => l && !l.startsWith("#"))
    .map((l) => l.split("=")),
);
test("local dashboard, evidence review, timeline and mobile layout", async ({
  page,
}) => {
  const errors: string[] = [];
  await page.setViewportSize({ width: 1440, height: 1000 });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  mkdirSync(new URL("../../artifacts/", import.meta.url), { recursive: true });
  await page
    .getByRole("heading", { name: "Đăng nhập phòng mô phỏng" })
    .waitFor();
  await page.screenshot({
    path: "../artifacts/login-desktop.png",
    fullPage: false,
  });
  await page.getByLabel("Tên đăng nhập").fill("admin");
  await page.getByLabel("Mật khẩu", { exact: true }).fill(env.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Vào workspace" }).click();
  await expect(
    page.getByRole("heading", { name: "Điều khiển mô phỏng", exact: true }),
  ).toBeVisible();
  await expect(page.locator("#run-picker")).not.toHaveValue("");
  await expect(
    page.getByText("Hoàn thành", { exact: true }).first(),
  ).toBeVisible({ timeout: 30000 });
  await expect(page.locator(".graph-legend small").first()).toContainText(
    /[1-9]\d* nodes/,
    { timeout: 30000 },
  );
  await expect(page.locator(".graph-canvas canvas").first()).toBeVisible();
  await page.screenshot({
    path: "../artifacts/dashboard-desktop.png",
    fullPage: false,
  });
  await page.getByRole("button", { name: "Cảnh báo & đánh giá" }).click();
  await expect(
    page.getByRole("heading", { name: "Hàng đợi xem xét" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../artifacts/alerts-desktop.png",
    fullPage: false,
  });
  await page.getByRole("button", { name: "Xem bằng chứng" }).first().click();
  await expect(
    page.getByRole("heading", { name: /Bằng chứng:/ }),
  ).toBeVisible();
  await expect(page.locator(".evidence-list button").first()).toBeVisible();
  await expect(page.locator(".graph-loading")).toHaveCount(0);
  await expect(page.locator(".explorer-toolbar .subtle-tag")).toContainText(
    /[1-9]\d* NODES/,
  );
  const size = await page.locator(".graph-canvas canvas").first().boundingBox();
  expect(size?.height).toBeGreaterThan(300);
  await page.screenshot({
    path: "../artifacts/evidence-desktop.png",
    fullPage: false,
  });
  const reviewComment = `E2E: inspected causal evidence ${randomUUID()}`;
  await page.getByLabel("Nhận xét analyst").fill(reviewComment);
  await page.getByRole("button", { name: "Lưu đánh giá" }).click();
  await expect(page.getByText(reviewComment, { exact: false })).toBeVisible();
  const slider = page.locator("#timeline");
  await slider.focus();
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowRight");
  await expect(slider).toHaveValue("1");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Khám phá dòng tiền", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../artifacts/dashboard-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("responsive navigation keeps all three views and viewer permissions", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByLabel("Tên đăng nhập").fill("viewer");
  await page.getByLabel("Mật khẩu", { exact: true }).fill(env.VIEWER_PASSWORD);
  await page.getByRole("button", { name: "Vào workspace" }).click();
  const views = [
    ["Điều khiển mô phỏng", "Điều khiển mô phỏng"],
    ["Khám phá dòng tiền", "Khám phá dòng tiền"],
    ["Cảnh báo & đánh giá", "Hàng đợi xem xét"],
  ];
  for (const width of [390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const [name, heading] of views) {
      const control = page
        .getByRole("navigation", { name: "Điều hướng chính" })
        .getByRole("button", { name, exact: true });
      await expect(control).toBeVisible();
      await control.click();
      await expect(control).toHaveAttribute("aria-current", "page");
      await expect(
        page.getByRole("heading", { name: heading, exact: true }),
      ).toBeVisible();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBeTruthy();
    }
  }
  await expect(page.getByRole("button", { name: "Nguồn TI" })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Replay as-known" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Xem bằng chứng" }).first().click();
  await expect(
    page.getByRole("heading", { name: /Bằng chứng:/ }),
  ).toBeVisible();
  await expect(page.locator(".review-form")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Điều khiển mô phỏng", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Bắt đầu mô phỏng" }),
  ).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".graph-canvas canvas").first()).toBeVisible();
  await page.screenshot({
    path: "../artifacts/simulation-mobile.png",
    fullPage: true,
  });
  await page.screenshot({
    path: "../artifacts/simulation-mobile-viewport.png",
    fullPage: false,
  });
});
