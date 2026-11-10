import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import * as oidc from "./auth/oidc";
import * as reference from "./auth/reference";
import { config } from "./config";

jest.mock("./components/Chat", () => ({ token, onUnauthorized }) => (
  <div>
    chat for {token}
    <button onClick={onUnauthorized}>unauthorized</button>
  </div>
));

function storeSession(expiresAt = Date.now() + 60_000) {
  sessionStorage.setItem(
    "intake.session",
    JSON.stringify({ accessToken: "tok", expiresAt, sub: "X110000001" })
  );
}

beforeEach(() => sessionStorage.clear());

test("without a session the login screen is shown", () => {
  render(<App />);
  expect(screen.getByRole("button", { name: "Mit Gesundheits-ID anmelden" })).toBeInTheDocument();
});

test("with a valid session the chat and user are shown", () => {
  storeSession();
  render(<App />);
  expect(screen.getByText("chat for tok")).toBeInTheDocument();
  expect(screen.getByText(/X110000001/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Abmelden" })).toBeInTheDocument();
});

test("an expired stored session leads to login", () => {
  storeSession(Date.now() - 1);
  render(<App />);
  expect(screen.getByRole("button", { name: "Mit Gesundheits-ID anmelden" })).toBeInTheDocument();
});

test("the session expires while the page is open", () => {
  jest.useFakeTimers();
  storeSession(Date.now() + 1000);
  render(<App />);
  expect(screen.getByText("chat for tok")).toBeInTheDocument();

  act(() => {
    jest.advanceTimersByTime(1500);
  });

  expect(screen.getByRole("status")).toHaveTextContent(/Sitzung ist abgelaufen/);
  expect(screen.getByRole("button", { name: "Mit Gesundheits-ID anmelden" })).toBeInTheDocument();
  jest.useRealTimers();
});

test("a 401 from the chat forces re-login", async () => {
  storeSession();
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "unauthorized" }));
  expect(screen.getByRole("button", { name: "Mit Gesundheits-ID anmelden" })).toBeInTheDocument();
  expect(sessionStorage.getItem("intake.session")).toBeNull();
});

test("a failed login callback shows an error", async () => {
  window.history.pushState({}, "", "/?error=access_denied&state=x");
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent(/Anmeldung ist fehlgeschlagen/);
  await waitFor(() => expect(window.location.search).toBe(""));
});

test("a login callback exchanges the code and displays the chat", async () => {
  const token = `h.${btoa(JSON.stringify({ sub: "X110000001" })).replace(/=+$/, "")}.s`;
  sessionStorage.setItem("intake.pkce", JSON.stringify({ verifier: "v", state: "s1" }));
  window.history.pushState({}, "", "/?code=abc&state=s1");
  const fetchSpy = jest.spyOn(global, "fetch").mockResolvedValue({
    ok: true,
    json: async () => ({ access_token: token, expires_in: 3600 }),
  });
  try {
    render(<App />);
    expect(await screen.findByText(`chat for ${token}`)).toBeInTheDocument();
    expect(fetchSpy.mock.calls[0][1].body.get("code_verifier")).toBe("v");
    expect(sessionStorage.getItem("intake.pkce")).toBeNull();
    await waitFor(() => expect(window.location.search).toBe(""));
  } finally {
    fetchSpy.mockRestore();
  }
});

test("login button starts the OIDC flow", async () => {
  const spy = jest.spyOn(oidc, "login").mockResolvedValue();
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "Mit Gesundheits-ID anmelden" }));
  expect(spy).toHaveBeenCalled();
});

test("reference login restores the BFF session without exposing a token", async () => {
  config.referenceLogin = true;
  const spy = jest.spyOn(reference, "getSession").mockResolvedValue({
    sub: "reference-sub", expiresAt: Date.now() + 60_000,
  });
  try {
    render(<App />);
    expect(await screen.findByText("chat for")).toBeInTheDocument();
    expect(screen.getByText(/reference-sub/)).toBeInTheDocument();
    expect(sessionStorage.getItem("intake.session")).toBeNull();
  } finally {
    spy.mockRestore();
    config.referenceLogin = false;
  }
});

test("reference login clears chat belonging to a different subject", async () => {
  config.referenceLogin = true;
  sessionStorage.setItem("intake.referenceSubject", "previous-subject");
  sessionStorage.setItem("intake.chat", "old chat");
  const spy = jest.spyOn(reference, "getSession").mockResolvedValue({
    sub: "new-subject", expiresAt: Date.now() + 60_000,
  });
  try {
    render(<App />);
    expect(await screen.findByText(/new-subject/)).toBeInTheDocument();
    expect(sessionStorage.getItem("intake.chat")).toBeNull();
  } finally {
    spy.mockRestore();
    config.referenceLogin = false;
  }
});
