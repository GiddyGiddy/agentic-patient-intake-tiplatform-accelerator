import { challengeFor, clearSession, completeLogin, getSession, login } from "./oidc";

const realLocation = window.location;

beforeEach(() => {
  sessionStorage.clear();
  delete window.location;
  window.location = { origin: "http://localhost:3000", pathname: "/", search: "", assign: jest.fn() };
  global.fetch = jest.fn();
});

afterEach(() => {
  window.location = realLocation;
});

test("PKCE S256 challenge matches the RFC 7636 test vector", async () => {
  const challenge = await challengeFor("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk");
  expect(challenge).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
});

test("login redirects to the authorize endpoint with PKCE and state", async () => {
  await login();
  const url = new URL(window.location.assign.mock.calls[0][0]);
  expect(url.pathname).toBe("/authorize");
  expect(url.searchParams.get("response_type")).toBe("code");
  expect(url.searchParams.get("code_challenge_method")).toBe("S256");
  expect(url.searchParams.get("redirect_uri")).toBe("http://localhost:3000/");

  const stored = JSON.parse(sessionStorage.getItem("intake.pkce"));
  expect(stored.state).toBe(url.searchParams.get("state"));
  expect(await challengeFor(stored.verifier)).toBe(url.searchParams.get("code_challenge"));
});

function fakeJwt(sub) {
  return `h.${btoa(JSON.stringify({ sub })).replace(/=+$/, "")}.s`;
}

test("completeLogin exchanges the code with the stored verifier and keeps a session", async () => {
  sessionStorage.setItem("intake.pkce", JSON.stringify({ verifier: "v", state: "s1" }));
  fetch.mockResolvedValue({
    ok: true,
    json: async () => ({ access_token: fakeJwt("X110000001"), expires_in: 3600 }),
  });

  const session = await completeLogin("?code=abc&state=s1");

  const body = fetch.mock.calls[0][1].body;
  expect(body.get("code_verifier")).toBe("v");
  expect(body.get("code")).toBe("abc");
  expect(session.sub).toBe("X110000001");
  expect(getSession().accessToken).toBe(session.accessToken);
  expect(sessionStorage.getItem("intake.pkce")).toBeNull();
});

test("completeLogin rejects a state mismatch without calling the token endpoint", async () => {
  sessionStorage.setItem("intake.pkce", JSON.stringify({ verifier: "v", state: "s1" }));
  await expect(completeLogin("?code=abc&state=other")).rejects.toThrow("invalid_state");
  expect(fetch).not.toHaveBeenCalled();
});

test("completeLogin surfaces an authorization error", async () => {
  await expect(completeLogin("?error=access_denied&state=x")).rejects.toThrow("access_denied");
});

test("an expired session is discarded together with chat data", () => {
  sessionStorage.setItem("intake.session", JSON.stringify({ accessToken: "t", expiresAt: Date.now() - 1 }));
  sessionStorage.setItem("intake.chat", "{}");
  expect(getSession()).toBeNull();
  expect(sessionStorage.getItem("intake.chat")).toBeNull();
});

test("session initialization preserves pending PKCE on a login callback but clears stale chat", async () => {
  window.location.search = "?code=abc&state=s1";
  sessionStorage.setItem("intake.pkce", JSON.stringify({ verifier: "v", state: "s1" }));
  sessionStorage.setItem("intake.chat", "{}");
  fetch.mockResolvedValue({
    ok: true,
    json: async () => ({ access_token: fakeJwt("X110000001"), expires_in: 3600 }),
  });

  expect(getSession()).toBeNull();
  expect(sessionStorage.getItem("intake.chat")).toBeNull();
  expect((await completeLogin()).sub).toBe("X110000001");
  expect(sessionStorage.getItem("intake.pkce")).toBeNull();
});

test("session initialization discards stale PKCE when not processing a callback", () => {
  sessionStorage.setItem("intake.pkce", "{}");
  expect(getSession()).toBeNull();
  expect(sessionStorage.getItem("intake.pkce")).toBeNull();
});

test("clearSession only removes app keys", () => {
  sessionStorage.setItem("intake.chat", "{}");
  sessionStorage.setItem("other", "keep");
  clearSession();
  expect(sessionStorage.getItem("other")).toBe("keep");
  expect(sessionStorage.getItem("intake.chat")).toBeNull();
});
