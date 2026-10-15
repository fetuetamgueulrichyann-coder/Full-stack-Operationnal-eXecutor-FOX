"""Obtient le jeton de rafraîchissement Google Calendar.

Usage (sur ta machine, hors Docker) :
    pip install google-auth-oauthlib
    python scripts/get_google_token.py client_secret.json
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def main():
    if len(sys.argv) != 2:
        print("Usage : python get_google_token.py client_secret.json")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        print("Aucun jeton de rafraîchissement reçu : relance en révoquant d'abord l'accès de l'application dans ton compte Google.")
        sys.exit(1)

    print("\nCopie ces trois lignes dans ton fichier .env :\n")
    print(f"GOOGLE_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
