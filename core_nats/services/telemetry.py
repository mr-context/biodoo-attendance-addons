"""
Télémétrie biodoo — Phase 1 (santé & erreurs).

Capture les erreurs Odoo (niveau ERROR et au-dessus) et les publie sur NATS
(sujet core ``zkteco.telemetry.error``, fire-and-forget). Le bridge les relaie
ensuite vers le serveur central pour analyse.

Principes de conception (volontairement explicites, pas « minimaux ») :
  - Ne JAMAIS casser ni bloquer l'application : toute exception interne est avalée.
  - Pas de récursion : un thread-local protège contre les boucles, et les logs
    de l'infra NATS elle-même sont ignorés (sinon un échec de publication
    relogguerait… une erreur, à l'infini).
  - Pseudonymisation : on n'ajoute ici aucun nom / PIN / biométrie. Le message
    applicatif brut peut en contenir — le scrubbing fin est prévu en Phase 2.
"""
import hashlib
import logging
import socket
import threading
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)
_local = threading.local()
_handler = None  # singleton

TELEMETRY_SUBJECT_ERROR = "zkteco.telemetry.error"

# Loggers à ne jamais réémettre (évite les boucles avec l'infra NATS/télémétrie).
_IGNORED_PREFIXES = ("odoo.addons.core_nats",)

# Bruit de contention transactionnelle : ``odoo.sql_db`` logge la requête en
# ERROR *avant* que la boucle de retry de core_nats (_dispatch) ne rattrape et
# rejoue la transaction avec succès. Ces erreurs sont donc des faux positifs —
# la même politique que odoo.service.model.retrying — et pollueraient la
# télémétrie (une rafale par heartbeat concurrent sur la même ligne device).
_RETRYABLE_SQL_MARKERS = (
    "could not serialize access",   # SerializationFailure (REPEATABLE READ)
    "deadlock detected",            # DeadlockDetected
    "TransactionRollbackError",     # classe psycopg des deux ci-dessus
)


def _is_retryable_sql_noise(record) -> bool:
    """Vrai si l'enregistrement est une erreur SQL de contention déjà rattrapée
    par le retry de core_nats — à ne pas remonter en télémétrie."""
    if record.name != "odoo.sql_db":
        return False
    msg = record.getMessage() or ""
    return any(marker in msg for marker in _RETRYABLE_SQL_MARKERS)


def install_id() -> str:
    """Identifiant d'installation pseudonyme et stable (aucune PII).

    Phase 1 : dérivé du nom d'hôte. À remplacer par l'empreinte de licence
    une fois la Phase 1 validée, pour coller à l'``install_id`` du bridge.
    """
    raw = socket.gethostname() or "unknown"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class NatsTelemetryHandler(logging.Handler):
    """Handler de logs : pousse chaque erreur sur NATS, sans jamais lever."""

    def emit(self, record):
        # garde anti-récursion (publication qui re-logue une erreur)
        if getattr(_local, "busy", False):
            return
        if record.name.startswith(_IGNORED_PREFIXES):
            return
        if _is_retryable_sql_noise(record):
            return
        try:
            _local.busy = True
            from odoo.addons.core_nats.services.nats_service import get_service
            svc = get_service()
            if not svc or not svc.is_running:
                return
            exc_type = record.exc_info[0].__name__ if record.exc_info else None
            payload = {
                "install_id": install_id(),
                "ts": datetime.now(timezone.utc).isoformat(),
                "component": "odoo",
                "level": record.levelname,
                "logger": record.name,
                "exc_type": exc_type,
                "message": (record.getMessage() or "")[:2000],
                "where": f"{record.module}.{record.funcName}:{record.lineno}",
            }
            svc.publish(TELEMETRY_SUBJECT_ERROR, payload)
        except Exception:
            # la télémétrie ne doit jamais faire tomber l'application
            pass
        finally:
            _local.busy = False


def register(level=logging.ERROR):
    """Attache le handler au logger racine Odoo (idempotent)."""
    global _handler
    if _handler is not None:
        return
    _handler = NatsTelemetryHandler()
    _handler.setLevel(level)
    logging.getLogger().addHandler(_handler)
    _logger.info("Télémétrie biodoo activée (erreurs → %s)", TELEMETRY_SUBJECT_ERROR)


def unregister():
    """Détache le handler (appelé quand le service NATS s'arrête)."""
    global _handler
    if _handler is None:
        return
    logging.getLogger().removeHandler(_handler)
    _handler = None
    _logger.info("Télémétrie biodoo désactivée")