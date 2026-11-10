import { config } from "../config";

export function login() {
  window.location.assign(`${config.apiUrl}/auth/reference/start`);
}

export async function getSession() {
  const response = await fetch(`${config.apiUrl}/auth/reference/session`, {
    credentials: "include",
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error("reference_session_failed");
  return response.json();
}

export async function logout() {
  const response = await fetch(`${config.apiUrl}/auth/reference/logout`, {
    method: "POST",
    credentials: "include",
  });
  if (!response.ok) throw new Error("reference_logout_failed");
}
