"""Journal lisible des actions de FOX (phase B2)."""
import db
from tools import TZ

STATUS_LABELS = {
    "pending": "En attente",
    "confirmed": "Confirmée",
    "executed": "Exécutée",
    "cancelled": "Annulée",
    "expired": "Expirée",
    "failed": "Échec",
    "undone": "Exécutée puis annulée",
}
EVENT_LABELS = {
    "task_created": "Tâche ajoutée",
    "task_updated": "Tâche modifiée",
    "task_deleted": "Tâche supprimée",
    "memory_added": "Information mémorisée",
    "memory_deleted": "Information oubliée",
    "memory_cleared": "Mémoire effacée",
    "chat_deleted": "Conversation supprimée",
    "history_deleted": "Historique supprimé",
    "mail_draft": "E-mail rédigé",
    "mail_improve": "E-mail amélioré",
    "mail_summary": "E-mail résumé",
    "client_created": "Client ajouté",
    "client_updated": "Client modifié",
    "client_deleted": "Client supprimé",
    "quote_created": "Devis créé",
    "quote_status": "Statut de devis modifié",
    "quote_deleted": "Devis supprimé",
}


def describe(entry):
    if entry["kind"] == "event":
        return EVENT_LABELS.get(entry["status"], entry["status"])
    first_line = (entry["summary"] or entry["tool"] or "").split("\n")[0]
    return f"{STATUS_LABELS.get(entry['status'], entry['status'])} : {first_line}"


def entries(user_id, limit=15):
    return [
        {"at": e["at"].astimezone(TZ).isoformat(timespec="minutes"), "text": describe(e)}
        for e in db.journal_entries(user_id, limit)
    ]
