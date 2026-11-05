import { render, screen } from "@testing-library/react";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("met le gras en forme sans afficher les astérisques", () => {
    const { container } = render(<Markdown text="Le **Point** est à 9h" />);
    expect(container.querySelector("strong")?.textContent).toBe("Point");
    expect(container.textContent).toBe("Le Point est à 9h");
  });

  it("transforme les lignes à tiret en liste", () => {
    render(<Markdown text={"- 09:00 : **Point**\n- 16:15 : RDV"} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getAllByRole("listitem")[0].querySelector("strong")?.textContent).toBe("Point");
  });

  it("gère listes numérotées, titres, italique et code", () => {
    const { container } = render(<Markdown text={"## Plan\n1. un\n2. deux\n\nUn *mot* et `code`"} />);
    expect(container.querySelector("ol")?.children).toHaveLength(2);
    expect(container.querySelector(".md-title")?.textContent).toBe("Plan");
    expect(container.querySelector("em")?.textContent).toBe("mot");
    expect(container.querySelector("code")?.textContent).toBe("code");
  });

  it("n'interprète jamais de HTML", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> <b>gras</b>'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("<b>gras</b>");
  });

  it("laisse intacts les astérisques isolés et les multiplications", () => {
    const { container } = render(<Markdown text="3 * 4 = 12 et 2*5" />);
    expect(container.textContent).toBe("3 * 4 = 12 et 2*5");
    expect(container.querySelector("em")).toBeNull();
  });
});

describe("Markdown : textes absents", () => {
  it("ne plante pas avec un texte vide, null ou undefined", () => {
    for (const value of [null, undefined, ""]) {
      const { container, unmount } = render(<Markdown text={value} />);
      expect(container.textContent).toBe("");
      unmount();
    }
  });
});
