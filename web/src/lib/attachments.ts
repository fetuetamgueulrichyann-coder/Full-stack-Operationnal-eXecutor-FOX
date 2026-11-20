/** Fichiers joints : réduction des photos avant l'envoi, tailles, types acceptés. */

export const MAX_ATTACHMENTS = 4;
export const MAX_FILE_MB = 10;
export const MAX_IMAGE_SIDE = 1600; // assez pour lire un plan ou un document photographié
export const JPEG_QUALITY = 0.82;
export const SKIP_SHRINK_BELOW = 300 * 1024; // une petite image est envoyée telle quelle
export const ACCEPT = "image/*,.pdf,.docx,.xlsx,.txt,.csv,.md,.json,.zip,.gz,.tgz";

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} Mo`;
}

/** Dimensions réduites pour que le grand côté ne dépasse pas `max`, en gardant les proportions. */
export function fitSize(width: number, height: number, max = MAX_IMAGE_SIDE): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= max) return { width, height };
  const ratio = max / longest;
  return { width: Math.max(1, Math.round(width * ratio)), height: Math.max(1, Math.round(height * ratio)) };
}

function jpegName(name: string): string {
  const base = name.replace(/\.[^./\\]+$/, "") || "photo";
  return `${base}.jpg`;
}

/**
 * Réduit une photo (grand côté 1 600 px, JPEG) : moins de données envoyées, moins de quota d'IA consommé,
 * et les informations de l'appareil (position GPS comprise) disparaissent à la recompression.
 * Retourne le fichier d'origine si l'image n'est pas décodable, déjà petite, animée (GIF) ou si la copie n'est pas plus légère.
 */
export async function shrinkImage(file: File): Promise<File> {
  if (!file.type.startsWith("image/") || file.type === "image/gif") return file;
  if (typeof createImageBitmap !== "function") return file;
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    return file; // format que ce navigateur ne décode pas (HEIC…) : le serveur dira s'il l'accepte
  }
  try {
    const { width, height } = fitSize(bitmap.width, bitmap.height);
    const resized = width !== bitmap.width || height !== bitmap.height;
    if (!resized && file.size <= SKIP_SHRINK_BELOW && file.type === "image/jpeg") return file;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return file;
    context.drawImage(bitmap, 0, 0, width, height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
    if (!blob || (blob.size >= file.size && file.type === "image/jpeg")) return file;
    return new File([blob], jpegName(file.name), { type: "image/jpeg", lastModified: Date.now() });
  } finally {
    bitmap.close?.();
  }
}

/** Nom d'une photo prise par l'appareil : photo-2026-11-18-1432.jpg */
export function photoName(date = new Date()): string {
  const two = (n: number) => String(n).padStart(2, "0");
  return `photo-${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())}-${two(date.getHours())}${two(date.getMinutes())}.jpg`;
}

/** Sur téléphone et tablette, « Photo » ouvre directement l'appareil photo ; sur ordinateur, la webcam. */
export function hasTouchCamera(): boolean {
  try {
    return window.matchMedia("(pointer: coarse)").matches;
  } catch {
    return false;
  }
}

export function canUseWebcam(): boolean {
  return typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getUserMedia === "function";
}
