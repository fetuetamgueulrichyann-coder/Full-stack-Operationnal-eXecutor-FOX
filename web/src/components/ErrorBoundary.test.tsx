import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Broken(): never {
  throw new Error("boum");
}

describe("ErrorBoundary", () => {
  it("affiche un message au lieu d'une page blanche", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("problème d'affichage");
    expect(screen.getByRole("button", { name: "Recharger la page" })).toBeInTheDocument();
  });

  it("laisse passer les enfants quand tout va bien", () => {
    render(
      <ErrorBoundary>
        <p>ok</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("ok")).toBeInTheDocument();
  });
});
