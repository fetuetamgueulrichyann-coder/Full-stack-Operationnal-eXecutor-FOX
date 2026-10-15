import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

READ = "read"
WRITE = "write"
IRREVERSIBLE = "irreversible"

TZ_NAME = os.environ.get("TIMEZONE", "Africa/Douala")
TZ = ZoneInfo(TZ_NAME)

CALENDAR_ID = "primary"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: Optional[dict]
    classification: str
    run: Callable[..., Any]
    describe: Optional[Callable[[dict], str]] = None
    done: Optional[Callable[[Any], str]] = None
    undo: Optional[Callable[[dict, Any], None]] = None
    expose: bool = True

    @property
    def needs_confirmation(self) -> bool:
        return self.classification != READ


REGISTRY: dict = {}


def register(tool: Tool) -> None:
    if tool.needs_confirmation and (tool.describe is None or tool.done is None):
        raise ValueError(f"l'outil sensible {tool.name} doit définir describe et done")
    REGISTRY[tool.name] = tool


# --- Utilitaires de dates ----------------------------------------------------
def parse_dt(value, end_of_day=False):
    text = str(value).strip()
    dt = datetime.fromisoformat(text)
    if len(text) == 10 and end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def bounds(debut, fin=None):
    start = parse_dt(debut)
    end = parse_dt(fin) if fin else start + timedelta(hours=1)
    if end <= start:
        raise ValueError("la fin doit être postérieure au début")
    return start, end


def fmt_range(start, end):
    if start.date() == end.date():
        return f"le {start:%d/%m/%Y} de {start:%H:%M} à {end:%H:%M}"
    return f"du {start:%d/%m/%Y %H:%M} au {end:%d/%m/%Y %H:%M}"


def time_field(dt):
    return {"dateTime": dt.isoformat(), "timeZone": TZ_NAME}


# --- Google Calendar ---------------------------------------------------------
def calendar_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=[CALENDAR_SCOPE],
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def event_title(event_id):
    try:
        event = (
            calendar_service()
            .events()
            .get(calendarId=CALENDAR_ID, eventId=event_id, fields="summary")
            .execute()
        )
        return event.get("summary", "(sans titre)")
    except Exception:
        return event_id


def lire_agenda(date_debut, date_fin=None):
    start = parse_dt(date_debut)
    if date_fin:
        end = parse_dt(date_fin, end_of_day=True)
    else:
        end = start.replace(hour=23, minute=59, second=59)

    result = (
        calendar_service()
        .events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=25,
        )
        .execute()
    )
    return [
        {
            "id": e["id"],
            "titre": e.get("summary", "(sans titre)"),
            "debut": e["start"].get("dateTime", e["start"].get("date")),
            "fin": e["end"].get("dateTime", e["end"].get("date")),
            "lieu": e.get("location"),
        }
        for e in result.get("items", [])
    ]


def creer_evenement(titre, debut, fin=None, lieu=None, description=None):
    start, end = bounds(debut, fin)
    body = {"summary": titre, "start": time_field(start), "end": time_field(end)}
    if lieu:
        body["location"] = lieu
    if description:
        body["description"] = description
    created = calendar_service().events().insert(calendarId=CALENDAR_ID, body=body).execute()
    return {"id": created["id"], "lien": created.get("htmlLink")}


def modifier_evenement(event_id, titre=None, debut=None, fin=None, lieu=None):
    service = calendar_service()
    current = service.events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()
    patch = {}
    if titre:
        patch["summary"] = titre
    if lieu:
        patch["location"] = lieu

    if debut:
        start = parse_dt(debut)
        if fin:
            end = parse_dt(fin)
        else:
            old_start = current["start"].get("dateTime")
            old_end = current["end"].get("dateTime")
            if not old_start or not old_end:
                raise ValueError("événement sur la journée entière : indiquer aussi la fin")
            end = start + (parse_dt(old_end) - parse_dt(old_start))
        patch["start"] = time_field(start)
        patch["end"] = time_field(end)
    elif fin:
        patch["end"] = time_field(parse_dt(fin))

    if not patch:
        raise ValueError("aucune modification demandée")

    avant = {
        "summary": current.get("summary", ""),
        "location": current.get("location", ""),
        "start": current["start"],
        "end": current["end"],
    }
    service.events().patch(calendarId=CALENDAR_ID, eventId=event_id, body=patch).execute()
    return {"id": event_id, "avant": avant}


def supprimer_evenement(event_id):
    calendar_service().events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
    return {"id": event_id}


def undo_creer(args, result):
    supprimer_evenement(result["id"])


def undo_modifier(args, result):
    calendar_service().events().patch(
        calendarId=CALENDAR_ID, eventId=result["id"], body=result["avant"]
    ).execute()


def describe_creer(args):
    start, end = bounds(args["debut"], args.get("fin"))
    text = f"Créer l'événement « {args['titre']} » {fmt_range(start, end)}"
    if args.get("lieu"):
        text += f" ({args['lieu']})"
    return text


def describe_modifier(args):
    changes = []
    if args.get("titre"):
        changes.append(f"nouveau titre « {args['titre']} »")
    if args.get("debut"):
        changes.append(f"nouveau début {parse_dt(args['debut']):%d/%m/%Y %H:%M}")
    if args.get("fin"):
        changes.append(f"nouvelle fin {parse_dt(args['fin']):%d/%m/%Y %H:%M}")
    if args.get("lieu"):
        changes.append(f"nouveau lieu « {args['lieu']} »")
    if not changes:
        raise ValueError("aucune modification demandée")
    return f"Modifier l'événement « {event_title(args['event_id'])} » : " + ", ".join(changes)


def describe_supprimer(args):
    return f"Supprimer l'événement « {event_title(args['event_id'])} »"


def done_creer(result):
    message = "Événement créé."
    if result.get("lien"):
        message += f"\n{result['lien']}"
    return message


DATE_HELP = "Date ou date-heure ISO 8601, par exemple 2026-10-12 ou 2026-10-12T09:00"


def register_calendar_tools():
    register(
        Tool(
            name="lire_agenda",
            description="Liste les événements de l'agenda entre deux dates.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "date_debut": {"type": "STRING", "description": DATE_HELP},
                    "date_fin": {"type": "STRING", "description": "Fin de la période (même format). Facultatif : par défaut la fin de la journée de date_debut."},
                },
                "required": ["date_debut"],
            },
            classification=READ,
            run=lire_agenda,
        )
    )
    register(
        Tool(
            name="creer_evenement",
            description="Crée un événement dans l'agenda.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "titre": {"type": "STRING", "description": "Titre de l'événement"},
                    "debut": {"type": "STRING", "description": DATE_HELP},
                    "fin": {"type": "STRING", "description": "Fin de l'événement. Facultatif : une heure après le début."},
                    "lieu": {"type": "STRING", "description": "Lieu (facultatif)"},
                    "description": {"type": "STRING", "description": "Description (facultatif)"},
                },
                "required": ["titre", "debut"],
            },
            classification=WRITE,
            run=creer_evenement,
            describe=describe_creer,
            done=done_creer,
            undo=undo_creer,
        )
    )
    register(
        Tool(
            name="modifier_evenement",
            description="Modifie un événement existant. Retrouver d'abord son identifiant avec lire_agenda.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "event_id": {"type": "STRING", "description": "Identifiant de l'événement, tel que retourné par lire_agenda"},
                    "titre": {"type": "STRING", "description": "Nouveau titre"},
                    "debut": {"type": "STRING", "description": "Nouveau début"},
                    "fin": {"type": "STRING", "description": "Nouvelle fin"},
                    "lieu": {"type": "STRING", "description": "Nouveau lieu"},
                },
                "required": ["event_id"],
            },
            classification=WRITE,
            run=modifier_evenement,
            describe=describe_modifier,
            done=lambda result: "Événement modifié.",
            undo=undo_modifier,
        )
    )
    register(
        Tool(
            name="supprimer_evenement",
            description="Supprime un événement. Retrouver d'abord son identifiant avec lire_agenda.",
            parameters={
                "type": "OBJECT",
                "properties": {
                    "event_id": {"type": "STRING", "description": "Identifiant de l'événement, tel que retourné par lire_agenda"},
                },
                "required": ["event_id"],
            },
            classification=IRREVERSIBLE,
            run=supprimer_evenement,
            describe=describe_supprimer,
            done=lambda result: "Événement supprimé.",
        )
    )


if all(os.environ.get(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")):
    register_calendar_tools()
