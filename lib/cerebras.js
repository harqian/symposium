import OpenAI from "openai";

let _client = null;

export function getClient() {
  if (_client) return _client;
  const apiKey = process.env.CEREBRAS_API_KEY;
  if (!apiKey) {
    throw new Error("CEREBRAS_API_KEY not set in environment");
  }
  _client = new OpenAI({
    apiKey,
    baseURL: "https://api.cerebras.ai/v1",
  });
  return _client;
}

export const MODEL = "gpt-oss-120b";
