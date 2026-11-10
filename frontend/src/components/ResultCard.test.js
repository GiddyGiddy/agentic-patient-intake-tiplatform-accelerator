import React from "react";
import { render, screen } from "@testing-library/react";
import ResultCard from "./ResultCard";

const result = {
  triage_score: "Grün",
  slot_start: "2030-01-01T10:00:00Z",
  appointment_id: "a1",
  kim_sent: true,
};

test("shows the server-provided anamnesis summary as text", () => {
  render(<ResultCard result={{ ...result, anamnesis_summary: "Husten seit drei Tagen.\nKein Fieber." }} />);

  expect(screen.getByRole("heading", { name: "Zusammenfassung Ihrer Angaben (Anamnese)" })).toBeInTheDocument();
  expect(screen.getByText(/Husten seit drei Tagen/)).toHaveTextContent("Kein Fieber.");
});

test.each([undefined, null, "", "   "])("shows a fallback for an absent summary (%s)", (summary) => {
  render(<ResultCard result={{ ...result, anamnesis_summary: summary }} />);

  expect(screen.getByText("Keine Zusammenfassung verfügbar.")).toBeInTheDocument();
});
