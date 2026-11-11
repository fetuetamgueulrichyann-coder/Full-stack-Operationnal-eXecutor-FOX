"""Service d'envoi des notifications push : `python push_worker.py` (service « push_worker » de docker-compose)."""
import logging
import os
import time

import push_delivery
import webpush

POLL_SECONDS = int(os.environ.get("PUSH_POLL_SECONDS", "30"))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    log = logging.getLogger("push")
    if not webpush.configured():
        log.warning("VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY absents : les notifications push sont désactivées (le service attend).")
    while True:
        try:
            if webpush.configured():
                push_delivery.run_once()
        except Exception:  # noqa: BLE001 - le service ne doit jamais s'arrêter sur une erreur ponctuelle
            log.exception("passage en échec, nouvel essai au prochain tour")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
