"""Contrôle de licence par le bridge : exécution des SONDES.

Le bridge envoie des requêtes SQL qu'il génère au hasard ; ce module les
exécute SANS savoir ce qu'elles cherchent et renvoie le résultat brut. Aucune
réponse n'est écrite ici : c'est le bridge qui juge (voir
zkteco-push/internal/compliance).

Garde-fous :
- seules les sondes signées par LA clé du bridge sont exécutées (Ed25519,
  clé épinglée à la première sonde reçue : ir.config_parameter
  « biodoo.probe_pubkey », réinitialisable par l'administrateur) ;
- transaction PostgreSQL en LECTURE SEULE, délai limité, une seule instruction,
  50 lignes au plus : une sonde ne peut rien modifier ni bloquer Odoo ;
- jeton à usage unique et horodatage : une sonde ne se rejoue pas.
"""
import base64
import collections
import json
import logging
import threading
import time

_logger = logging.getLogger(__name__)

SUBJECT = 'biodoo.odoo.probe'
PIN_KEY = 'biodoo.probe_pubkey'
MAX_SKEW_S = 120
STATEMENT_TIMEOUT_MS = 5000
MAX_ROWS = 50

_seen = collections.OrderedDict()
_seen_lock = threading.Lock()


def _signed_bytes(nonce, ts, sql):
    return ('biodoo-probe-v1\n%s\n%d\n%s' % (nonce, ts, sql)).encode()


def _fresh(nonce):
    with _seen_lock:
        if nonce in _seen:
            return False
        _seen[nonce] = time.time()
        while len(_seen) > 5000:
            _seen.popitem(last=False)
    return True


def _cursor(registry):
    """Curseur NEUF, sur sa propre connexion : « SET TRANSACTION READ ONLY »
    doit être la première instruction de la transaction."""
    from odoo.sql_db import db_connect
    return db_connect(registry.db_name).cursor()


def _pinned_key(registry, offered):
    """Clé épinglée ; à la première sonde, la clé offerte est épinglée."""
    with _cursor(registry) as cr:
        cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (PIN_KEY,))
        row = cr.fetchone()
        if row and row[0]:
            return row[0]
        cr.execute("""INSERT INTO ir_config_parameter (key, value, create_date, write_date)
                      VALUES (%s, %s, now() at time zone 'UTC', now() at time zone 'UTC')
                      ON CONFLICT (key) DO NOTHING""", (PIN_KEY, offered))
        cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (PIN_KEY,))
        _logger.info("Contrôle de licence : clé du bridge épinglée")
        return cr.fetchone()[0]


def _verify(pub_b64, nonce, ts, sql, sig_b64):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
        key.verify(base64.b64decode(sig_b64), _signed_bytes(nonce, ts, sql))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def _jsonable(v):
    if isinstance(v, (int, float, bool, str)) or v is None:
        return v
    return str(v)


def answer(registry, data, now=None):
    """Réponse (dict) à une sonde (bytes JSON)."""
    now = now or time.time()
    try:
        req = json.loads(data)
        nonce, ts, sql = str(req['nonce']), int(req['ts']), str(req['sql'])
        pub, sig = str(req['pub']), str(req['sig'])
    except (ValueError, KeyError, TypeError):
        return {'nonce': '', 'ok': False, 'error': 'sonde illisible'}

    def refuse(reason):
        return {'nonce': nonce, 'ok': False, 'error': reason}

    if abs(now - ts) > MAX_SKEW_S:
        return refuse('horodatage hors délai')
    pinned = _pinned_key(registry, pub)
    if pub != pinned:
        return refuse('clé inconnue (bridge différent : réinitialiser la clé dans Odoo)')
    if not _verify(pub, nonce, ts, sql, sig):
        return refuse('signature invalide')
    if not _fresh(nonce):
        return refuse('sonde déjà jouée')
    if ';' in sql.rstrip().rstrip(';'):
        return refuse('une seule instruction')
    try:
        with _cursor(registry) as cr:
            cr.execute("SET TRANSACTION READ ONLY")
            cr.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
            cr.execute(sql)  # biodoo: sql-ok sonde signée, transaction en lecture seule
            rows = [[_jsonable(c) for c in r] for r in cr.fetchmany(MAX_ROWS)]
            cr.rollback()
        return {'nonce': nonce, 'ok': True, 'rows': rows}
    except Exception as exc:  # biodoo: except-ok la sonde échoue, le bridge juge (aucune donnée modifiée)
        return refuse(type(exc).__name__)
