// 假数据：后端还没好时，前端照 docs/API.md 的例子在这里写返回值。
// 开关：frontend/.env.local 里设 VITE_USE_MOCKS=1
import type { GenerateResponse, HealthResponse } from "../api";

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export const mocks = {
  async health(): Promise<HealthResponse> {
    await delay(200);
    return { status: "ok", model: "mock", mock: true };
  },
  async generate(prompt: string): Promise<GenerateResponse> {
    await delay(800); // 模拟 AI 思考时间，方便调 loading 样式
    return { result: `（前端假数据）你输入了：${prompt}` };
  },
};
