import { SOURCE_USER_AGENT } from "./config";

export async function fetchJson<T>(
  url: string,
  options: RequestInit & { timeoutMs?: number; retries?: number } = {},
): Promise<T> {
  const { timeoutMs = 12_000, retries = 1, ...request } = options;
  let lastError: unknown;

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        ...request,
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          "User-Agent": SOURCE_USER_AGENT,
          ...request.headers,
        },
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText} from ${url}`);
      }
      return (await response.json()) as T;
    } catch (error) {
      lastError = error;
      if (attempt < retries) await new Promise((resolve) => setTimeout(resolve, 350));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}

export async function fetchText(
  url: string,
  options: RequestInit & { timeoutMs?: number; retries?: number } = {},
): Promise<string> {
  const { timeoutMs = 12_000, retries = 1, ...request } = options;
  let lastError: unknown;

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        ...request,
        signal: controller.signal,
        headers: { "User-Agent": SOURCE_USER_AGENT, ...request.headers },
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText} from ${url}`);
      }
      return await response.text();
    } catch (error) {
      lastError = error;
      if (attempt < retries) await new Promise((resolve) => setTimeout(resolve, 350));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}
