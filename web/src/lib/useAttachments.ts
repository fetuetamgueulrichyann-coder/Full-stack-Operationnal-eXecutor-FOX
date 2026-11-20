import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiUpload } from "../api/client";
import { describeError } from "../api/errors";
import type { FileInfo } from "../api/types";
import { MAX_ATTACHMENTS, MAX_FILE_MB, shrinkImage } from "./attachments";

export interface AttachmentItem {
  key: string;
  name: string;
  size: number;
  kind?: FileInfo["kind"];
  previewUrl?: string; // aperçu local d'une image (jamais renvoyé par le serveur)
  status: "uploading" | "ready" | "error";
  id?: string;
  message?: string;
}

let counter = 0;

/** Pièces jointes du message en cours d'écriture : envoi au serveur dès le choix, aperçu, retrait. */
export function useAttachments() {
  const [items, setItems] = useState<AttachmentItem[]>([]);
  const [notice, setNotice] = useState("");
  const itemsRef = useRef<AttachmentItem[]>([]);
  const controllers = useRef(new Map<string, AbortController>());
  itemsRef.current = items;

  const patch = useCallback((key: string, change: Partial<AttachmentItem>) => {
    setItems((current) => current.map((i) => (i.key === key ? { ...i, ...change } : i)));
  }, []);

  const upload = useCallback(
    async (key: string, original: File) => {
      const controller = new AbortController();
      controllers.current.set(key, controller);
      try {
        const file = await shrinkImage(original);
        if (file.size > MAX_FILE_MB * 1024 * 1024) {
          patch(key, { status: "error", message: `Fichier trop gros (${MAX_FILE_MB} Mo maximum).` });
          return;
        }
        const form = new FormData();
        form.append("file", file, file.name);
        const data = await apiUpload<{ file: FileInfo }>("/files", form, controller.signal);
        patch(key, { status: "ready", id: data.file.id, kind: data.file.kind, size: data.file.size, name: data.file.name });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        patch(key, { status: "error", message: describeError(error) });
      } finally {
        controllers.current.delete(key);
      }
    },
    [patch],
  );

  const add = useCallback(
    (picked: File[]) => {
      setNotice("");
      const room = MAX_ATTACHMENTS - itemsRef.current.length;
      if (picked.length > room) setNotice(`${MAX_ATTACHMENTS} fichiers au plus par message.`);
      const accepted = picked.slice(0, Math.max(0, room));
      if (accepted.length === 0) return;
      const created: AttachmentItem[] = accepted.map((file) => ({
        key: `att-${++counter}`,
        name: file.name || "fichier",
        size: file.size,
        previewUrl: file.type.startsWith("image/") && typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : undefined,
        status: "uploading",
      }));
      itemsRef.current = [...itemsRef.current, ...created];
      setItems(itemsRef.current);
      created.forEach((item, index) => void upload(item.key, accepted[index]));
    },
    [upload],
  );

  const remove = useCallback((key: string) => {
    const item = itemsRef.current.find((i) => i.key === key);
    if (!item) return;
    controllers.current.get(key)?.abort();
    if (item.status === "ready" && item.id) void api(`/files/${item.id}`, { method: "DELETE" }).catch(() => undefined);
    if (item.previewUrl) URL.revokeObjectURL?.(item.previewUrl);
    setNotice("");
    itemsRef.current = itemsRef.current.filter((i) => i.key !== key);
    setItems(itemsRef.current);
  }, []);

  /** Vide la zone après l'envoi du message ; les aperçus restent utilisables dans la bulle du message. */
  const release = useCallback(() => {
    itemsRef.current = [];
    setItems([]);
    setNotice("");
  }, []);

  // Si la page se ferme avec des fichiers non envoyés, on libère leurs aperçus.
  useEffect(
    () => () => {
      itemsRef.current.forEach((i) => i.previewUrl && URL.revokeObjectURL?.(i.previewUrl));
      controllers.current.forEach((c) => c.abort());
    },
    [],
  );

  return {
    items,
    notice,
    ready: items.filter((i) => i.status === "ready"),
    busy: items.some((i) => i.status === "uploading"),
    add,
    remove,
    release,
  };
}
