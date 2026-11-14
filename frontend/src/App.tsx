import { useEffect, useState } from "react";
import { api, type HealthResponse } from "./api";

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthError, setHealthError] = useState("");
  const [prompt, setPrompt] = useState("");
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setHealthError(e.message));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setResult("");
    try {
      const data = await api.generate(prompt);
      setResult(data.result);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page">
      <header>
        <h1>Hack The Nest 2026</h1>
        <p className="status">
          {health
            ? `后端已连接 · ${health.model}${health.mock ? " · MOCK" : ""}`
            : healthError || "正在连接后端…"}
        </p>
      </header>

      <form onSubmit={onSubmit}>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="输入点什么，让 Gemini 回答"
          rows={4}
        />
        <button type="submit" disabled={loading || !prompt.trim()}>
          {loading ? "生成中…" : "生成"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}
      {result && <pre className="result">{result}</pre>}
    </main>
  );
}
