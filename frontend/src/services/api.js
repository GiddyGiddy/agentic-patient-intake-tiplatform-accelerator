import { config } from "../config";

export class ApiError extends Error {
  constructor(status) {
    super(`Backend error ${status}`);
    this.status = status;
  }
}

async function request(token, method, path, body) {
  let res;
  try {
    res = await fetch(`${config.apiUrl}/api${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      credentials: config.referenceLogin ? "include" : "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0);
  }
  if (!res.ok) throw new ApiError(res.status);
  return res.status === 204 ? null : res.json();
}

// Conversation, triage and consent live on the server; the client only sends decisions.
export const sendChat = (token, message) => request(token, "POST", "/chat", { message });
export const setConsent = (token, granted) => request(token, "POST", "/consent", { granted });
export const resetSession = (token) => request(token, "DELETE", "/session");
