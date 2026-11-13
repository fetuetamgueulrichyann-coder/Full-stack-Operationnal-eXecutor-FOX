import { createDetector } from "./bargein";

function run(levels: number[], step = 50, opts = {}) {
  const d = createDetector(opts);
  return levels.map((level, i) => d.push(level, i * step));
}

describe("détecteur d'interruption", () => {
  it("ne déclenche jamais pendant la calibration", () => {
    expect(run(Array(8).fill(0.9)).slice(0, 8).some(Boolean)).toBe(false);
  });

  it("ignore l'écho constant et le bruit de fond", () => {
    const echo = Array(8).fill(0.2); // calibration : écho de FOX
    expect(run([...echo, ...Array(40).fill(0.22)]).some(Boolean)).toBe(false);
  });

  it("déclenche sur une parole soutenue bien au-dessus de l'écho", () => {
    const out = run([...Array(8).fill(0.2), ...Array(12).fill(0.8)]);
    expect(out.some(Boolean)).toBe(true);
    expect(out.indexOf(true)).toBeGreaterThanOrEqual(8 + 6); // au moins ~320 ms de parole
  });

  it("ignore un claquement bref", () => {
    const out = run([...Array(8).fill(0.05), 0.9, 0.9, ...Array(20).fill(0.05)]);
    expect(out.some(Boolean)).toBe(false);
  });

  it("un silence entre deux sursauts remet le compteur à zéro", () => {
    const burst = [0.9, 0.9, 0.9, 0.9, 0.9];
    expect(run([...Array(8).fill(0.05), ...burst, 0.05, ...burst, 0.05, ...burst]).some(Boolean)).toBe(false);
  });

  it("garde un seuil minimal dans une pièce silencieuse", () => {
    expect(run([...Array(8).fill(0), ...Array(20).fill(0.1)]).some(Boolean)).toBe(false);
    expect(run([...Array(8).fill(0), ...Array(20).fill(0.3)]).some(Boolean)).toBe(true);
  });
});
