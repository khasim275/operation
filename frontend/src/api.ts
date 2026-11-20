export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly data: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function csrfToken(): string {
  const entry = document.cookie.split("; ").find((cookie) => cookie.startsWith("dental_crm_csrf="));
  return entry ? decodeURIComponent(entry.slice("dental_crm_csrf=".length)) : "";
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const method = (options.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD"].includes(method)) {
    const token = csrfToken();
    if (token) headers.set("X-CSRF-Token", token);
  }

  let response: Response;
  try {
    response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError("The clinic server could not be reached. Check your connection and try again.", 0);
  }

  const contentType = response.headers.get("content-type") ?? "";
  const body: unknown = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? (body as { detail: unknown }).detail
        : null;
    const message = Array.isArray(detail)
      ? detail
          .map((item) =>
            typeof item === "object" && item !== null && "msg" in item
              ? String((item as { msg: unknown }).msg)
              : String(item),
          )
          .join("; ")
      : typeof detail === "string"
        ? detail
        : `Request failed (${response.status}).`;
    throw new ApiError(message, response.status, body);
  }
  return body as T;
}

