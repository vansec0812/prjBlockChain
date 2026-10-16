let csrfToken = "";
let sessionRequest: Promise<unknown> | null = null;
export function query(
  params: Record<string, string | number | undefined>,
): string {
  return new URLSearchParams(
    Object.entries(params)
      .filter(
        (entry): entry is [string, string | number] => entry[1] !== undefined,
      )
      .map(([k, v]) => [k, String(v)]),
  ).toString();
}
async function send(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
  headers: Record<string, string> = {},
) {
  const response = await fetch("/api" + path, {
    method,
    signal,
    credentials: "same-origin",
    cache: path === "/session" ? "no-store" : undefined,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  return { response, result };
}

function unwrap<T>({ response, result }: Awaited<ReturnType<typeof send>>): T {
  if (!response.ok)
    throw new Error(
      typeof result.message === "string"
        ? result.message
        : typeof result.detail === "string"
          ? result.detail
          : JSON.stringify(result.detail ?? result),
    );
  if (result.csrf_token) csrfToken = result.csrf_token;
  return result as T;
}

function currentSession(): Promise<unknown> {
  // Share anonymous initialization, including React StrictMode's two effects.
  // A later call always reads the cookie again; no token is cached on disk.
  if (!sessionRequest) {
    sessionRequest = send("/session", "GET")
      .then((reply) => unwrap<unknown>(reply))
      .finally(() => {
        sessionRequest = null;
      });
  }
  return sessionRequest;
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
  headers: Record<string, string> = {},
): Promise<T> {
  const verb = method.toUpperCase();
  signal?.throwIfAborted();
  if (path === "/session" && verb === "GET") {
    const result = await currentSession();
    signal?.throwIfAborted();
    return result as T;
  }

  const login = path === "/login" && verb === "POST";
  if (login) {
    // Cookies are shared across tabs, while csrfToken belongs to this tab.
    await currentSession();
    signal?.throwIfAborted();
  }
  let reply = await send(path, verb, body, signal, headers);
  if (
    login &&
    reply.response.status === 403 &&
    (reply.result.message ?? reply.result.detail) === "Invalid CSRF token"
  ) {
    // Only the rejected login can be retried, once. Never replay data writes.
    await currentSession();
    signal?.throwIfAborted();
    reply = await send(path, verb, body, signal, headers);
  }
  return unwrap<T>(reply);
}
