// 前端调用后端的唯一入口。新接口照着 generate() 加一个函数，类型照 docs/API.md 写。
import { mocks } from "./mocks";

const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "1";
const TIMEOUT_MS = 60_000;

export type HealthResponse = { status: string; model: string; mock: boolean };
export type GenerateResponse = { result: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(BASE + path, { ...init, signal: ctrl.signal });
  } catch {
    throw new Error(
      ctrl.signal.aborted ? "请求超时，请再试一次" : "连不上后端，确认后端已启动（make backend）",
    );
  } finally {
    clearTimeout(timer);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败（${res.status}）`);
  return data as T;
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const api = {
  health: (): Promise<HealthResponse> =>
    USE_MOCKS ? mocks.health() : request("/api/health"),

  generate: (prompt: string): Promise<GenerateResponse> =>
    USE_MOCKS ? mocks.generate(prompt) : postJson("/api/generate", { prompt }),
};
