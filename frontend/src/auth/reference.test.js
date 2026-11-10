import { getSession, logout, login } from "./reference";
import { config } from "../config";

const originalLocation = window.location;

beforeEach(() => {
  delete window.location;
  window.location = { assign: jest.fn() };
  global.fetch = jest.fn();
});

afterEach(() => {
  window.location = originalLocation;
});

test("login navigates to the backend without putting token material in the browser", () => {
  login();
  expect(window.location.assign).toHaveBeenCalledWith(`${config.apiUrl}/auth/reference/start`);
});

test("session retrieval uses the HttpOnly cookie and returns no token", async () => {
  fetch.mockResolvedValue({ ok: true, json: async () => ({ sub: "test-sub", expiresAt: 123 }) });
  expect(await getSession()).toEqual({ sub: "test-sub", expiresAt: 123 });
  expect(fetch).toHaveBeenCalledWith(`${config.apiUrl}/auth/reference/session`, { credentials: "include" });
});

test("session retrieval distinguishes unauthenticated and backend failure", async () => {
  fetch.mockResolvedValueOnce({ status: 401 }).mockResolvedValueOnce({ status: 503, ok: false });
  expect(await getSession()).toBeNull();
  await expect(getSession()).rejects.toThrow("reference_session_failed");
});

test("logout revokes the backend session using credentials", async () => {
  fetch.mockResolvedValue({ ok: true });
  await logout();
  expect(fetch).toHaveBeenCalledWith(`${config.apiUrl}/auth/reference/logout`, {
    method: "POST", credentials: "include",
  });
});
