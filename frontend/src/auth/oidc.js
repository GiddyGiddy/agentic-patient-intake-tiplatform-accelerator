import { config, redirectUri } from "../config";

const PREFIX = "intake.";
const SESSION_KEY = `${PREFIX}session`;
const PKCE_KEY = `${PREFIX}pkce`;

const b64url = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

const random = (n) => b64url(crypto.getRandomValues(new Uint8Array(n)));

export async function challengeFor(verifier) {
  const data = Uint8Array.from(verifier, (c) => c.charCodeAt(0));
  return b64url(await crypto.subtle.digest("SHA-256", data));
}

function subjectOf(jwt) {
  try {
    const payload = jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(payload)).sub || null;
  } catch {
    return null;
  }
}

export function hasCallbackParams() {
  const p = new URLSearchParams(window.location.search);
  return (p.has("code") && p.has("state")) || p.has("error");
}

export async function login() {
  const verifier = random(48);
  const state = random(16);
  sessionStorage.setItem(PKCE_KEY, JSON.stringify({ verifier, state }));
  const params = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: redirectUri(),
    scope: "openid",
    state,
    code_challenge: await challengeFor(verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(`${config.issuer}/authorize?${params}`);
}

/** Finishes the redirect: validates state, exchanges the code and stores the session. */
export async function completeLogin(search = window.location.search) {
  const p = new URLSearchParams(search);
  if (!p.has("code") && !p.has("error")) return null;

  const stored = JSON.parse(sessionStorage.getItem(PKCE_KEY) || "null");
  sessionStorage.removeItem(PKCE_KEY);
  if (p.has("error")) throw new Error(p.get("error"));
  if (!stored || stored.state !== p.get("state")) throw new Error("invalid_state");

  const res = await fetch(`${config.issuer}/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code: p.get("code"),
      redirect_uri: redirectUri(),
      client_id: config.clientId,
      code_verifier: stored.verifier,
    }),
  });
  if (!res.ok) throw new Error("token_exchange_failed");
  const token = await res.json();
  const session = {
    accessToken: token.access_token,
    expiresAt: Date.now() + token.expires_in * 1000,
    sub: subjectOf(token.access_token),
  };
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  return session;
}

export function getSession() {
  try {
    const session = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
    if (session && session.expiresAt > Date.now()) return session;
  } catch {
    // corrupt entry: fall through and clear
  }
  clearAppData(hasCallbackParams());
  return null;
}

function clearAppData(preservePkce = false) {
  Object.keys(sessionStorage)
    .filter((k) => k.startsWith(PREFIX) && !(preservePkce && k === PKCE_KEY))
    .forEach((k) => sessionStorage.removeItem(k));
}

/** Removes the token and all stored conversation data. */
export function clearSession() {
  clearAppData();
}

export function logout() {
  clearSession();
  const params = new URLSearchParams({ post_logout_redirect_uri: redirectUri() });
  window.location.assign(`${config.issuer}/logout?${params}`);
}
