import { API_BASE } from "../api/client";
import type { FileInfo } from "../api/types";
import { formatSize } from "../lib/attachments";
import type { AttachmentItem } from "../lib/useAttachments";
import { Icon } from "./Icon";

/** Pièces jointes en cours d'envoi, sous le champ de saisie : aperçu, état, croix pour retirer. */
export function AttachmentTray({ items, onRemove }: { items: AttachmentItem[]; onRemove: (key: string) => void }) {
  if (items.length === 0) return null;
  return (
    <ul className="attach-tray" aria-label="Fichiers joints au message">
      {items.map((item) => (
        <li key={item.key} className={`attach-chip ${item.status}`}>
          {item.previewUrl ? <img src={item.previewUrl} alt="" /> : <span className="attach-icon"><Icon name="file" size={20} /></span>}
          <span className="attach-text">
            <span className="attach-name">{item.name}</span>
            <span className="attach-state" role={item.status === "error" ? "alert" : undefined}>
              {item.status === "uploading" ? "Envoi…" : item.status === "error" ? item.message || "Échec de l'envoi" : formatSize(item.size)}
            </span>
          </span>
          <button type="button" className="icon-btn" aria-label={`Retirer ${item.name}`} onClick={() => onRemove(item.key)}>
            <Icon name="close" size={16} />
          </button>
        </li>
      ))}
    </ul>
  );
}

export interface BubbleFile {
  name: string;
  kind: FileInfo["kind"];
  size: number;
  id?: string; // absent si le fichier n'est pas conservé (mode privé)
  url?: string; // aperçu local d'une image qui vient d'être envoyée
}

export function fileUrl(id: string): string {
  return `${API_BASE}/files/${id}`;
}

export function toBubbleFile(info: FileInfo): BubbleFile {
  return { name: info.name, kind: info.kind, size: info.size, id: info.id, url: info.kind === "image" ? fileUrl(info.id) : undefined };
}

/** Fichiers d'un message déjà envoyé : miniatures pour les photos, pastilles pour le reste. */
export function BubbleFiles({ files }: { files: BubbleFile[] }) {
  if (files.length === 0) return null;
  return (
    <div className="bubble-files">
      {files.map((file, index) =>
        file.kind === "image" && file.url ? (
          <a key={index} className="bubble-photo" href={file.url} target="_blank" rel="noreferrer" aria-label={`Ouvrir la photo ${file.name}`}>
            <img src={file.url} alt={file.name} loading="lazy" />
          </a>
        ) : file.id ? (
          <a key={index} className="bubble-file" href={fileUrl(file.id)} download={file.name}>
            <Icon name="file" size={16} /> {file.name} <small>{formatSize(file.size)}</small>
          </a>
        ) : (
          <span key={index} className="bubble-file">
            <Icon name="file" size={16} /> {file.name} <small>{formatSize(file.size)}</small>
          </span>
        ),
      )}
    </div>
  );
}
