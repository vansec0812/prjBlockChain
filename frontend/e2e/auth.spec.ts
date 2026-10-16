import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const env = Object.fromEntries(
  readFileSync(new URL("../../.env", import.meta.url), "utf8")
    .split(/\r?\n/)
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => line.split("=")),
);

test("login refreshes CSRF after a different tab signs in", async ({
  page,
  context,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Đăng nhập phòng mô phỏng" }),
  ).toBeVisible();
  const otherTab = await context.newPage();
  await otherTab.goto("/");
  await otherTab.getByLabel("Tên đăng nhập").fill("viewer");
  await otherTab
    .getByLabel("Mật khẩu", { exact: true })
    .fill(env.VIEWER_PASSWORD);
  await otherTab.getByRole("button", { name: "Vào workspace" }).click();
  await expect(
    otherTab.getByRole("heading", { name: "Điều khiển mô phỏng", exact: true }),
  ).toBeVisible();

  await page.getByLabel("Tên đăng nhập").fill("admin");
  await page.getByLabel("Mật khẩu", { exact: true }).fill(env.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Vào workspace" }).click();
  await expect(
    page.getByRole("heading", { name: "Điều khiển mô phỏng", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Nguồn TI", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Invalid CSRF token", { exact: true }),
  ).toHaveCount(0);
});

test("login initializes a fresh session if the browser cookie disappeared", async ({
  page,
  context,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Đăng nhập phòng mô phỏng" }),
  ).toBeVisible();
  await context.clearCookies();
  await page.getByLabel("Tên đăng nhập").fill("admin");
  await page.getByLabel("Mật khẩu", { exact: true }).fill(env.ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Vào workspace" }).click();
  await expect(
    page.getByRole("heading", { name: "Điều khiển mô phỏng", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Nguồn TI", exact: true }),
  ).toBeVisible();
});
