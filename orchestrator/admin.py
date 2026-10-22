"""Administration des comptes de FOX (à lancer dans le conteneur de l'orchestrateur).

    docker compose exec orchestrator python admin.py create-invite --days 7 --uses 1 --note "Marie"
    docker compose exec orchestrator python admin.py list-invites
    docker compose exec orchestrator python admin.py revoke-invite 3
    docker compose exec orchestrator python admin.py list-users
    docker compose exec orchestrator python admin.py disable-user marie
    docker compose exec orchestrator python admin.py enable-user marie
    docker compose exec -it orchestrator python admin.py set-password owner
    docker compose exec orchestrator python admin.py purge

Le code d'invitation n'est affiché qu'une seule fois : seul son empreinte est stockée.
"""
import argparse
import getpass
import sys

import accounts
import db


def fmt(value):
    return "-" if value is None else str(value)[:19]


def cmd_create_invite(args):
    code = accounts.create_invitation(days=args.days, max_uses=args.uses, note=args.note)
    print(f"Code d'invitation (valable {args.days} jour(s), {args.uses} utilisation(s)) :")
    print(f"  {code}")
    print("Il ne sera plus affiché : transmets-le maintenant à la personne invitée.")
    return 0


def cmd_list_invites(args):
    rows = accounts.list_invitations()
    if not rows:
        print("Aucun code d'invitation.")
    for id_, note, uses, max_uses, expires_at, revoked_at in rows:
        state = "révoqué" if revoked_at else "actif"
        print(f"#{id_} {state} {uses}/{max_uses} utilisations, expire {fmt(expires_at)}  {note or ''}")
    return 0


def cmd_revoke_invite(args):
    if accounts.revoke_invitation(args.id):
        print("Code révoqué.")
        return 0
    print("Code introuvable ou déjà révoqué.")
    return 1


def cmd_purge(args):
    result = db.purge_history()  # durées : RETENTION_DAYS_OWNER / RETENTION_DAYS_TESTER
    print(
        f"{result['chats']} conversation(s) supprimée(s), {result['private']} contexte(s) privé(s), "
        f"{result['tasks']} tâche(s) close(s)."
    )
    return 0


def cmd_list_users(args):
    for id_, username, role, telegram_id, created_at, last_login, disabled_at in accounts.list_users():
        state = "désactivé" if disabled_at else "actif"
        print(
            f"#{id_} {username} ({role}, {state}) telegram={fmt(telegram_id)} "
            f"créé {fmt(created_at)} dernière connexion {fmt(last_login)}"
        )
    return 0


def cmd_disable_user(args):
    if accounts.set_disabled(args.username, True):
        print("Compte désactivé et sessions fermées.")
        return 0
    print("Utilisateur introuvable.")
    return 1


def cmd_enable_user(args):
    if accounts.set_disabled(args.username, False):
        print("Compte réactivé.")
        return 0
    print("Utilisateur introuvable.")
    return 1


def cmd_set_password(args):
    password = getpass.getpass("Nouveau mot de passe : ")
    if password != getpass.getpass("Confirme : "):
        print("Les deux saisies diffèrent.")
        return 1
    try:
        done = accounts.set_password(args.username, password)
    except accounts.AuthError as exc:
        print(exc.message)
        return 1
    print("Mot de passe modifié, sessions fermées." if done else "Utilisateur introuvable.")
    return 0 if done else 1


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    invite = sub.add_parser("create-invite", help="crée un code d'invitation")
    invite.add_argument("--days", type=int, default=7)
    invite.add_argument("--uses", type=int, default=1)
    invite.add_argument("--note", default=None)
    invite.set_defaults(func=cmd_create_invite)

    sub.add_parser("list-invites").set_defaults(func=cmd_list_invites)
    revoke = sub.add_parser("revoke-invite")
    revoke.add_argument("id", type=int)
    revoke.set_defaults(func=cmd_revoke_invite)

    sub.add_parser("list-users").set_defaults(func=cmd_list_users)
    sub.add_parser("purge", help="supprime l'historique trop ancien").set_defaults(func=cmd_purge)
    for name, func in (("disable-user", cmd_disable_user), ("enable-user", cmd_enable_user),
                       ("set-password", cmd_set_password)):
        p = sub.add_parser(name)
        p.add_argument("username")
        p.set_defaults(func=func)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if getattr(args, "days", 1) <= 0 or getattr(args, "uses", 1) <= 0:
        print("--days et --uses doivent être positifs.")
        return 2
    try:
        return args.func(args)
    finally:
        db.pool.closeall()


if __name__ == "__main__":
    sys.exit(main())
