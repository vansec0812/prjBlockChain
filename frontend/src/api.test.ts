import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const json = (body: unknown, status = 200) => Response.json(body, { status });
const credentials = { username: "admin", password: "test-only-password" };
const session = (token: string) => json({ user: null, csrf_token: token });
const authenticated = () =>
  json({
    user: { username: "admin", role: "Admin" },
    csrf_token: "authenticated",
  });

describe("session and login CSRF lifecycle", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => vi.unstubAllGlobals());

  it("shares concurrent session initialization instead of racing anonymous cookies", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const fetchMock = vi.fn(async () => {
      await gate;
      return session("initial");
    });
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./api");
    const first = api("/session");
    const second = api("/session");
    release();
    await Promise.all([first, second]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/session",
      expect.objectContaining({
        cache: "no-store",
        credentials: "same-origin",
      }),
    );
  });

  it("reads the current cookie's token immediately before login", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(session("old-tab"))
      .mockResolvedValueOnce(session("current-cookie"))
      .mockResolvedValueOnce(authenticated());
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./api");
    await api("/session");
    const result = await api<{ user: { username: string } }>(
      "/login",
      "POST",
      credentials,
    );
    expect(result.user.username).toBe("admin");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/session",
      "/api/session",
      "/api/login",
    ]);
    expect(fetchMock.mock.calls[2][1].headers["X-CSRF-Token"]).toBe(
      "current-cookie",
    );
  });

  it("recovers once if another tab rotates the token during login", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(session("before-rotation"))
      .mockResolvedValueOnce(json({ message: "Invalid CSRF token" }, 403))
      .mockResolvedValueOnce(session("after-rotation"))
      .mockResolvedValueOnce(authenticated())
      .mockResolvedValueOnce(json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./api");
    await api("/login", "POST", credentials);
    expect(fetchMock.mock.calls[1][1].headers["X-CSRF-Token"]).toBe(
      "before-rotation",
    );
    expect(fetchMock.mock.calls[3][1].headers["X-CSRF-Token"]).toBe(
      "after-rotation",
    );
    expect(fetchMock.mock.calls[3][1].body).toBe(
      fetchMock.mock.calls[1][1].body,
    );
    await api("/logout", "POST");
    expect(fetchMock.mock.calls[4][1].headers["X-CSRF-Token"]).toBe(
      "authenticated",
    );
  });

  it.each([
    [401, "Tên đăng nhập hoặc mật khẩu không đúng."],
    [403, "Origin is not allowed"],
  ])(
    "does not retry a different login rejection (%s)",
    async (status, message) => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(session("current"))
        .mockResolvedValueOnce(json({ message }, status));
      vi.stubGlobal("fetch", fetchMock);
      const { api } = await import("./api");
      await expect(api("/login", "POST", credentials)).rejects.toThrow(message);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    },
  );

  it("stops after one CSRF recovery attempt", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(session("first"))
      .mockResolvedValueOnce(json({ message: "Invalid CSRF token" }, 403))
      .mockResolvedValueOnce(session("second"))
      .mockResolvedValueOnce(json({ message: "Invalid CSRF token" }, 403));
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./api");
    await expect(api("/login", "POST", credentials)).rejects.toThrow(
      "Invalid CSRF token",
    );
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("never automatically resends a rejected data mutation", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(session("initial"))
      .mockResolvedValueOnce(json({ message: "Invalid CSRF token" }, 403));
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("./api");
    await api("/session");
    await expect(
      api("/alerts/example/review", "PATCH", { status: "closed" }),
    ).rejects.toThrow("Invalid CSRF token");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
