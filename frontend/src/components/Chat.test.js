import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Chat from "./Chat";
import { ApiError, resetSession, sendChat, setConsent } from "../services/api";

jest.mock("../services/api", () => ({
  sendChat: jest.fn(),
  setConsent: jest.fn(),
  resetSession: jest.fn(),
  ApiError: class ApiError extends Error {
    constructor(status) {
      super(`Backend error ${status}`);
      this.status = status;
    }
  },
}));

const RED_PENDING = {
  reply: "Bitte rufen Sie sofort den Notruf 112.",
  triage_score: "Rot",
  anamnesis_summary: "Brustschmerzen",
  iam_required: true,
  iam_message: "Bitte Zugriff freigeben.",
  consent_granted: false,
};
const BOOKED = {
  reply: null,
  triage_score: "Rot",
  anamnesis_summary: "Brustschmerzen",
  iam_required: false,
  consent_granted: true,
  appointment_id: "a1",
  slot_start: "2030-01-01T10:00:00Z",
  kim_sent: true,
  medication_plan: ["Ramipril 5 mg (1-0-0)"],
};

async function send(text) {
  await userEvent.type(screen.getByLabelText("Ihre Nachricht"), text);
  await userEvent.click(screen.getByRole("button", { name: "Senden" }));
}

beforeEach(() => {
  sessionStorage.clear();
  sendChat.mockReset();
  setConsent.mockReset();
  resetSession.mockReset().mockResolvedValue(null);
});

test("emergency: banner, consent dialog, then server-side consent resumes and shows the result", async () => {
  sendChat.mockResolvedValueOnce(RED_PENDING);
  setConsent.mockResolvedValueOnce(BOOKED);
  render(<Chat token="tok" onUnauthorized={jest.fn()} />);

  await send("Ich habe Brustschmerzen");

  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "112 anrufen" })).toHaveAttribute("href", "tel:112");
  expect(sendChat).toHaveBeenCalledWith("tok", "Ich habe Brustschmerzen");

  await userEvent.click(screen.getByRole("button", { name: "Mit Gesundheits-ID freigeben" }));

  expect(await screen.findByRole("heading", { name: "Ihre Aufnahme ist abgeschlossen" })).toBeInTheDocument();
  expect(setConsent).toHaveBeenCalledWith("tok", true);
  expect(screen.getByText(/Rot – Sofort/)).toBeInTheDocument();
  expect(screen.getByText("Ramipril 5 mg (1-0-0)")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Zusammenfassung Ihrer Angaben (Anamnese)" })).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Ihre Aufnahme ist abgeschlossen" })).getByText("Brustschmerzen")).toBeInTheDocument();
  expect(screen.getByText(/per KIM/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Ihre Nachricht")).not.toBeInTheDocument();
});

test("reference mock consent labels the simulation through the result", async () => {
  sendChat.mockResolvedValueOnce(RED_PENDING);
  setConsent.mockResolvedValueOnce(BOOKED);
  render(<Chat onUnauthorized={jest.fn()} referenceMockEpa />);
  await send("Brustschmerzen");
  expect(await screen.findByText(/keine echte ePA-Freigabe/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Demo-Daten freigeben" }));
  expect(await screen.findByText("Demo-Datenfreigabe erteilt")).toBeInTheDocument();
  expect(screen.getByText("Synthetischer Medikationsplan aus dem ePA-Mock")).toBeInTheDocument();
  expect(screen.getByText(/lokale KIM-Testmodul übergeben/)).toBeInTheDocument();
  expect(setConsent).toHaveBeenCalledWith(undefined, true);
});

test("reference mock result reports incomplete ePA data", async () => {
  sendChat.mockResolvedValueOnce(RED_PENDING);
  setConsent.mockResolvedValueOnce({ ...BOOKED, epa_status: "partial", medication_plan: [] });
  render(<Chat onUnauthorized={jest.fn()} referenceMockEpa />);
  await send("Brustschmerzen");
  await userEvent.click(await screen.findByRole("button", { name: "Demo-Daten freigeben" }));
  expect(await screen.findByText(/ePA-Mock-Daten konnten nicht vollständig geladen werden/)).toBeInTheDocument();
});

test("a failed KIM hand-over is announced", async () => {
  sendChat.mockResolvedValueOnce(RED_PENDING);
  setConsent.mockResolvedValueOnce({ ...BOOKED, kim_sent: false });
  render(<Chat token="tok" onUnauthorized={jest.fn()} />);
  await send("Brustschmerzen");
  await userEvent.click(await screen.findByRole("button", { name: "Mit Gesundheits-ID freigeben" }));
  expect(await screen.findByText(/noch nicht per KIM/)).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Ihre Aufnahme ist abgeschlossen" })).getByText("Brustschmerzen")).toBeInTheDocument();
});

test("declining shows feedback, tells the server nothing and allows reconsidering", async () => {
  sendChat.mockResolvedValueOnce(RED_PENDING);
  render(<Chat token="tok" onUnauthorized={jest.fn()} />);
  await send("Brustschmerzen");

  await userEvent.click(await screen.findByRole("button", { name: "Nicht freigeben" }));

  expect(screen.getByText(/Keine Freigabe erteilt/)).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(setConsent).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: "Freigabe erneut prüfen" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

test("follow-up questions only send the new message (history is server-side)", async () => {
  sendChat
    .mockResolvedValueOnce({ reply: "Seit wann?", iam_required: false })
    .mockResolvedValueOnce({ reply: "Danke.", triage_score: "Gelb", iam_required: true });
  render(<Chat token="tok" onUnauthorized={jest.fn()} />);

  await send("Husten");
  expect(await screen.findByText("Seit wann?")).toBeInTheDocument();
  await send("seit drei Tagen");

  await screen.findByRole("dialog");
  expect(sendChat).toHaveBeenLastCalledWith("tok", "seit drei Tagen");
  expect(screen.queryByRole("link", { name: "112 anrufen" })).not.toBeInTheDocument();
});

test("stopped intake hides the composer and allows restart", async () => {
  sendChat.mockResolvedValueOnce({
    reply: "Die automatische Aufnahme wurde beendet, weil sich Fragen wiederholt haben. Bitte wenden Sie sich an Ihre Praxis.",
    intake_stopped: true,
    iam_required: false,
  });
  render(<Chat token="tok" onUnauthorized={jest.fn()} />);
  await send("Kopfschmerzen");
  await screen.findByText(/automatische Aufnahme wurde beendet/);
  expect(screen.queryByLabelText("Ihre Nachricht")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Neues Gespräch" }));
  await waitFor(() => expect(screen.getByLabelText("Ihre Nachricht")).toBeInTheDocument());
  expect(resetSession).toHaveBeenCalledWith("tok");
});

test("errors are announced and can be retried", async () => {
  sendChat.mockRejectedValueOnce(new ApiError(500)).mockResolvedValueOnce({ reply: "Hallo", iam_required: false });
  render(<Chat token="tok" onUnauthorized={jest.fn()} />);
  await send("Hallo");

  expect(await screen.findByRole("alert")).toHaveTextContent("Code 500");
  await userEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));

  await waitFor(() => expect(screen.getAllByText("Hallo")).toHaveLength(2));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a TI outage during consent can be retried", async () => {
  sendChat.mockResolvedValueOnce(RED_PENDING);
  setConsent.mockRejectedValueOnce(new ApiError(502)).mockResolvedValueOnce(BOOKED);
  render(<Chat token="tok" onUnauthorized={jest.fn()} />);
  await send("Brustschmerzen");
  await userEvent.click(await screen.findByRole("button", { name: "Mit Gesundheits-ID freigeben" }));

  expect(await screen.findByText(/Code 502/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
  expect(await screen.findByRole("heading", { name: "Ihre Aufnahme ist abgeschlossen" })).toBeInTheDocument();
});

test("network failure shows a connection message", async () => {
  sendChat.mockRejectedValueOnce(new ApiError(0));
  render(<Chat token="tok" onUnauthorized={jest.fn()} />);
  await send("Hallo");
  expect(await screen.findByRole("alert")).toHaveTextContent(/nicht erreichbar/);
});

test("a 401 from the backend triggers re-login", async () => {
  sendChat.mockRejectedValueOnce(new ApiError(401));
  const onUnauthorized = jest.fn();
  render(<Chat token="tok" onUnauthorized={onUnauthorized} />);
  await send("Hallo");
  await screen.findByLabelText("Ihre Nachricht");
  expect(onUnauthorized).toHaveBeenCalled();
});

test("consent can be revoked after granting and the server is told", async () => {
  sendChat.mockResolvedValueOnce(RED_PENDING);
  setConsent.mockResolvedValueOnce(BOOKED).mockResolvedValueOnce({ ...BOOKED, consent_granted: false });
  render(<Chat token="tok" onUnauthorized={jest.fn()} />);
  await send("Brustschmerzen");
  await userEvent.click(await screen.findByRole("button", { name: "Mit Gesundheits-ID freigeben" }));
  await screen.findByText("Datenfreigabe erteilt");

  await userEvent.click(screen.getByRole("button", { name: "Freigabe widerrufen" }));

  await waitFor(() => expect(screen.queryByText("Datenfreigabe erteilt")).not.toBeInTheDocument());
  expect(setConsent).toHaveBeenLastCalledWith("tok", false);
  expect(screen.getByText(/Freigabe wurde widerrufen/)).toBeInTheDocument();
});

test("conversation survives a reload and restart resets the server session", async () => {
  sendChat.mockResolvedValueOnce({ reply: "Seit wann?", iam_required: false });
  const { unmount } = render(<Chat token="tok" onUnauthorized={jest.fn()} />);
  await send("Husten");
  await screen.findByText("Seit wann?");
  unmount();

  render(<Chat token="tok" onUnauthorized={jest.fn()} />);
  expect(screen.getByText("Husten")).toBeInTheDocument();
  expect(screen.getByText("Seit wann?")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Neues Gespräch" }));
  await waitFor(() => expect(screen.queryByText("Husten")).not.toBeInTheDocument());
  expect(resetSession).toHaveBeenCalledWith("tok");
});
