import { afterEach, describe, expect, it, vi } from "vitest";
import { ACCEPT, fitSize, formatSize, hasTouchCamera, photoName, shrinkImage } from "./attachments";

afterEach(() => vi.unstubAllGlobals());

describe("pièces jointes : calculs", () => {
  it("affiche des tailles lisibles", () => {
    expect(formatSize(512)).toBe("512 o");
    expect(formatSize(2048)).toBe("2 Ko");
    expect(formatSize(3.5 * 1024 * 1024)).toBe("3,5 Mo");
  });

  it("réduit le grand côté en gardant les proportions", () => {
    expect(fitSize(4000, 3000)).toEqual({ width: 1600, height: 1200 });
    expect(fitSize(3000, 4000)).toEqual({ width: 1200, height: 1600 });
    expect(fitSize(800, 600)).toEqual({ width: 800, height: 600 });
    expect(fitSize(10000, 1)).toEqual({ width: 1600, height: 1 });
  });

  it("nomme les photos par date et heure", () => {
    expect(photoName(new Date(2026, 10, 18, 14, 5))).toBe("photo-2026-11-18-1405.jpg");
  });

  it("accepte photos, PDF, Word, Excel, texte et archives", () => {
    for (const part of ["image/*", ".pdf", ".docx", ".xlsx", ".zip", ".gz"]) expect(ACCEPT).toContain(part);
  });

  it("reconnaît un appareil tactile, et reste sûr si matchMedia manque", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    expect(hasTouchCamera()).toBe(true);
    vi.stubGlobal("matchMedia", () => { throw new Error("absent"); });
    expect(hasTouchCamera()).toBe(false);
  });
});

function fakeImage(type: string, size: number, name = "IMG_0001.heic") {
  return new File([new Uint8Array(size)], name, { type });
}

function stubCanvas(blobSize: number | null) {
  const drawn: number[][] = [];
  const canvas = { width: 0, height: 0, getContext: () => ({ drawImage: (_: unknown, ...a: number[]) => drawn.push(a) }), toBlob: (cb: (b: Blob | null) => void) => cb(blobSize === null ? null : new Blob([new Uint8Array(blobSize)], { type: "image/jpeg" })) };
  const real = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation(((tag: string) => (tag === "canvas" ? canvas : real(tag))) as typeof document.createElement);
  return { canvas, drawn };
}

describe("réduction des photos", () => {
  it("renvoie le fichier d'origine pour un GIF, un PDF ou sans createImageBitmap", async () => {
    const gif = fakeImage("image/gif", 10, "a.gif");
    expect(await shrinkImage(gif)).toBe(gif);
    const pdf = fakeImage("application/pdf", 10, "a.pdf");
    expect(await shrinkImage(pdf)).toBe(pdf);
    const photo = fakeImage("image/jpeg", 10, "a.jpg");
    vi.stubGlobal("createImageBitmap", undefined);
    expect(await shrinkImage(photo)).toBe(photo);
  });

  it("renvoie le fichier d'origine si le navigateur ne sait pas le décoder (HEIC)", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn(async () => { throw new Error("format"); }));
    const heic = fakeImage("image/heic", 5000);
    expect(await shrinkImage(heic)).toBe(heic);
  });

  it("réduit une grande photo en JPEG de 1 600 px au plus", async () => {
    const close = vi.fn();
    vi.stubGlobal("createImageBitmap", vi.fn(async () => ({ width: 4000, height: 3000, close })));
    const { canvas, drawn } = stubCanvas(400_000);
    const out = await shrinkImage(fakeImage("image/jpeg", 5_000_000, "chantier.jpeg"));
    expect(out.name).toBe("chantier.jpg");
    expect(out.type).toBe("image/jpeg");
    expect(out.size).toBe(400_000);
    expect([canvas.width, canvas.height]).toEqual([1600, 1200]);
    expect(drawn[0]).toEqual([0, 0, 1600, 1200]);
    expect(close).toHaveBeenCalled();
  });

  it("convertit une PNG en JPEG même quand elle est petite (le nom change d'extension)", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn(async () => ({ width: 1000, height: 800, close: vi.fn() })));
    stubCanvas(50_000);
    const out = await shrinkImage(fakeImage("image/png", 600_000, "plan.png"));
    expect(out.name).toBe("plan.jpg");
    expect(out.type).toBe("image/jpeg");
  });

  it("garde un petit JPEG tel quel, et la copie plus lourde que l'original n'est pas utilisée", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn(async () => ({ width: 1000, height: 800, close: vi.fn() })));
    const small = fakeImage("image/jpeg", 100_000, "p.jpg");
    expect(await shrinkImage(small)).toBe(small);
    stubCanvas(900_000);
    const medium = fakeImage("image/jpeg", 500_000, "m.jpg");
    expect(await shrinkImage(medium)).toBe(medium);
  });

  it("garde l'original si le canevas ne répond pas", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn(async () => ({ width: 3000, height: 2000, close: vi.fn() })));
    stubCanvas(null);
    const big = fakeImage("image/jpeg", 2_000_000, "g.jpg");
    expect(await shrinkImage(big)).toBe(big);
  });
});
