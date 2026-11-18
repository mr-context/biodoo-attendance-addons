# -*- coding: utf-8 -*-
"""
NATS service — singleton asyncio worker that lives in a daemon thread.

Public API:
  get_service()                              → NatsService | None
  set_service(service)                       → None
  register_handler_subject(model, subjects)  → None
"""
import asyncio
import json
import logging
import random
import re
import threading
import time
from collections import deque
from typing import Optional

from psycopg2 import errors as pg_errors

_logger = logging.getLogger(__name__)

# Erreurs de concurrence PostgreSQL qui justifient un retry de la transaction
# (même politique que la couche HTTP d'Odoo : odoo.service.model.retrying).
_PG_RETRY_ERRORS = (
    pg_errors.SerializationFailure,
    pg_errors.DeadlockDetected,
    pg_errors.LockNotAvailable,
)
_MAX_NATS_TRIES = 5

# {model_name: [subject, ...]}
_handler_registry: dict[str, list[str]] = {}
_registry_lock = threading.Lock()


def nats_subject_matches(pattern: str, subject: str) -> bool:
    """Return True if subject matches a NATS subject pattern.

    NATS rules:
      '*' matches exactly one token (no dots).
      '>' matches one or more trailing tokens; must be the last pattern token.
      Exact match always supported.

    Examples:
      nats_subject_matches('zkteco.ta.cmdresult.>', 'zkteco.ta.cmdresult.SN1') -> True
      nats_subject_matches('zkteco.ta.*.SN1', 'zkteco.ta.device.SN1') -> True
      nats_subject_matches('zkteco.ta.cmdresult.>', 'zkteco.ta') -> False
      nats_subject_matches('zkteco.ta.device.SN1', 'zkteco.ta.device.SN1') -> True
    """
    if pattern == subject:
        return True
    p_parts = pattern.split('.')
    s_parts = subject.split('.')
    i = 0
    while i < len(p_parts):
        pt = p_parts[i]
        if pt == '>':
            # '>' must be last and there must be at least one subject token remaining
            return i == len(p_parts) - 1 and i < len(s_parts)
        if i >= len(s_parts):
            return False
        if pt != '*' and pt != s_parts[i]:
            return False
        i += 1
    return i == len(s_parts)

_instance: Optional['NatsService'] = None
_instance_lock = threading.Lock()

_STAT_SYNC_INTERVAL = 30

# ── Consommation JetStream ──────────────────────────────────────────────────
# Un consommateur durable en mode PULL, partagé par tous les workers Odoo :
# chaque message est traité par UN seul worker, puis acquitté. Pas de repli
# silencieux sur un abonnement sans acquittement.
ACK_WAIT_S = 120          # délai d'acquittement (traitements lents : photos)
MAX_DELIVER = 8           # au-delà, le message part dans « Messages en échec »
MAX_ACK_PENDING = 256
FETCH_BATCH = 16
FETCH_TIMEOUT_S = 5
NAK_DELAY_MAX_S = 300     # délai de relivraison croissant, plafonné

# Période (secondes) à laquelle le service tente de repasser en JetStream les
# sujets abonnés en simple « core » alors qu'un flux les couvre (voir
# NatsService._upgrade_core_subscriptions).
_JS_UPGRADE_INTERVAL = 30


def get_service() -> Optional['NatsService']:
    return _instance


def set_service(service: Optional['NatsService']) -> None:
    global _instance
    with _instance_lock:
        _instance = service


def register_handler_subject(model_name: str, subjects: list[str]) -> None:
    with _registry_lock:
        _handler_registry[model_name] = list(subjects)
    _logger.info(f"NATS registry: {model_name} → {subjects}")
    svc = get_service()
    if svc and svc.is_running:
        for subject in subjects:
            svc.register_handler(subject, model_name)


# ─────────────────────────────────────────────────────────────────────────────


class NatsService:
    """
    Asyncio-based NATS client running in a single daemon thread.
    Subscribes as JetStream durable consumers when a matching stream exists,
    falls back to plain core NATS otherwise.
    """

    def __init__(self, url: str, registry, token: str | None = None):
        self._url      = url
        # Jeton d'authentification du bus (le bridge l'exige quand NATS_TOKEN
        # est défini) ; jamais journalisé.
        self._token    = token or None
        self._registry = registry

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._nc   = None
        self._js   = None   # JetStream context, set after connect
        self._thread: Optional[threading.Thread] = None
        self._running   = False
        self._connected = False

        self._handlers: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._subscribed: set[str] = set()
        self._subscribed_as_js: set[str] = set()
        # Boucles de lecture (mode pull) par sujet.
        self._pull_tasks: dict[str, asyncio.Task] = {}
        # Messages comptés par ce processus, ajoutés en base périodiquement
        # (les compteurs sont donc justes même avec plusieurs workers).
        self._stat_delta: dict[str, int] = {}
        # Abonnement nats-py vivant de chaque sujet. Le garder est ce qui permet
        # de le RÉSILIER : sans cela, se réabonner ne fait qu'en ajouter un
        # second et chaque message est traité deux fois.
        self._subs: dict[str, object] = {}
        self._subscribing: set[str] = set()   # garde contre deux _subscribe concurrents

        self._stats: dict[str, dict] = {}
        self._stat_lock = threading.Lock()
        self._stat_last_sync: dict[str, float] = {}

        # Wildcard monitor: captures ALL NATS traffic (disinterested observer).
        # Populated independently of business handlers — useful for troubleshooting
        # even when no connector module is installed.
        self._msg_log: deque = deque(maxlen=200)
        self._msg_lock = threading.Lock()
        self._seen_subjects: set[str] = set()  # every subject ever seen in this session

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_stats(self) -> dict:
        with self._stat_lock:
            return {s: dict(v) for s, v in self._stats.items()}

    # ── public interface ──────────────────────────────────────────

    def replay_subject(self, subject: str) -> None:
        """Force la re-livraison de tous les messages d'un subject depuis le début du stream.
        Le consumer durable doit avoir été supprimé au préalable (côté NATS).
        Cette méthode retire le subject des ensembles suivis et re-souscrit :
        le nouveau consumer est créé avec DeliverAll et re-délivre tout le stream."""
        if not self._running or not self._loop:
            return

        async def _do_replay():
            await self._unsubscribe(subject)
            await self._subscribe(subject)
            _logger.info(f"NATS replay triggered for '{subject}'")

        asyncio.run_coroutine_threadsafe(_do_replay(), self._loop)

    def register_handler(self, subject: str, model_name: str) -> None:
        with self._lock:
            self._handlers.setdefault(subject, [])
            if model_name not in self._handlers[subject]:
                self._handlers[subject].append(model_name)
            already = subject in self._subscribed
        if self._running and self._loop and not already:
            asyncio.run_coroutine_threadsafe(
                self._subscribe(subject), self._loop
            )

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="nats-service",
        )
        self._thread.start()
        _logger.info(f"NATS service starting → {self._url}")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._connected = False
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._disconnect(), self._loop)
        _logger.info("NATS service stopped")

    def publish(self, subject: str, payload: dict | str) -> None:
        """Core NATS publish — fire and forget."""
        if not self._running or not self._loop:
            _logger.warning(f"NATS publish skipped (not running): {subject}")
            return
        data = (json.dumps(payload).encode()
                if isinstance(payload, dict) else payload.encode())
        asyncio.run_coroutine_threadsafe(self._publish(subject, data), self._loop)

    def publish_js(self, subject: str, payload: dict | str) -> None:
        """JetStream publish — server-acknowledged, persisted in stream. Fire-and-forget."""
        if not self._running or not self._loop or not self._js:
            _logger.warning(f"NATS JS publish skipped (not ready): {subject}")
            return
        data = (json.dumps(payload).encode()
                if isinstance(payload, dict) else payload.encode())
        asyncio.run_coroutine_threadsafe(self._publish_js(subject, data), self._loop)

    def publish_js_sync(self, subject: str, payload: dict | str, timeout: float = 5.0) -> bool:
        """JetStream publish — blocks until server ack or timeout.

        Returns True if the message was accepted by NATS JetStream.
        Returns False if the service is not ready or the publish failed.
        Raises nothing — callers should check the return value.
        """
        if not self._running or not self._loop or not self._js:
            _logger.warning(f"NATS JS publish_sync skipped (not ready): {subject}")
            return False
        data = (json.dumps(payload).encode()
                if isinstance(payload, dict) else payload.encode())
        future = asyncio.run_coroutine_threadsafe(
            self._publish_js_checked(subject, data), self._loop
        )
        try:
            future.result(timeout=timeout)
            return True
        except Exception as exc:
            _logger.error(f"NATS JS publish_sync failed [{subject}]: {exc}")
            return False

    def request_sync(self, subject: str, payload: dict | list | str | bytes = b"",
                     timeout: float = 2.5):
        """Core NATS request/reply — blocks until a responder replies or timeout.

        Returns the decoded JSON reply (dict or list). Returns None if the
        service is not ready, no responder answered within `timeout`, or the
        reply was not valid JSON. Raises nothing — callers check for None.
        """
        if not self._running or not self._loop or not self._nc:
            _logger.warning(f"NATS request skipped (not ready): {subject}")
            return None
        if isinstance(payload, (dict, list)):
            data = json.dumps(payload).encode()
        elif isinstance(payload, bytes):
            data = payload
        else:
            data = str(payload).encode()
        future = asyncio.run_coroutine_threadsafe(
            self._request(subject, data, timeout), self._loop
        )
        try:
            return future.result(timeout=timeout + 1.0)
        except Exception as exc:   # biodoo: except-ok request_sync renvoie None (documenté), journalisé ; l'appelant traite None
            _logger.warning(f"NATS request failed [{subject}]: {exc}")
            return None

    async def _request(self, subject: str, data: bytes, timeout: float):
        msg = await self._nc.request(subject, data, timeout=timeout)
        return json.loads(msg.data.decode())

    def get_dashboard_data(self) -> dict:
        """Return a snapshot of current service state for the live dashboard."""
        stats = self.get_stats()
        total = sum(s.get('count', 0) for s in stats.values())

        now = time.time()
        with self._msg_lock:
            raw = list(self._msg_log)
            rate = sum(1 for m in raw if now - m['_ts'] < 60)
            feed = [
                {"ts": m['ts'], "subject": m['subject']}
                for m in reversed(raw)
            ][:50]
            seen = set(self._seen_subjects)

        with self._lock:
            handler_subjects = set(self._handlers.keys())
            subs = [
                {
                    "subject": subj,
                    "is_js":   subj in self._subscribed_as_js,
                    "count":   stats.get(subj, {}).get('count', 0),
                }
                for subj in self._handlers
            ]

        # Subjects flowing through NATS but with no Odoo handler registered.
        unhandled = sorted(seen - handler_subjects)

        return {
            "connected":        self._connected,
            "url":              self._url,
            "total_messages":   total,
            "rate_per_min":     rate,
            "subscriptions":    subs,
            "recent_messages":  feed,
            "unhandled_subjects": unhandled,
        }

    def get_streams(self) -> list[dict]:
        """Fetch JetStream stream info synchronously (blocks up to 5 s)."""
        if not self._running or not self._loop:
            return []
        future = asyncio.run_coroutine_threadsafe(self._fetch_streams(), self._loop)
        try:
            return future.result(timeout=5)
        except Exception as e:   # biodoo: except-ok liste des flux pour l'écran : vide si NATS indisponible, journalisé
            _logger.warning(f"get_streams: {e}")
            return []

    # ── asyncio internals ─────────────────────────────────────────

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        import nats as nats_lib

        retry_delay = 2
        while self._running:
            try:
                self._nc = await nats_lib.connect(
                    self._url,
                    token=self._token,
                    reconnect_time_wait=2,
                    max_reconnect_attempts=-1,
                    error_cb=self._on_error,
                    disconnected_cb=self._on_disconnected,
                    reconnected_cb=self._on_reconnected,
                )
                self._js = self._nc.jetstream()
                self._connected = True
                retry_delay = 2
                _logger.info(f"NATS connected: {self._url}")

                await self._sync_server_state('running')

                # Connexion NEUVE : aucun abonnement d'une connexion précédente
                # n'existe plus côté serveur, on repart d'un suivi vide.
                for task in self._pull_tasks.values():
                    task.cancel()
                self._pull_tasks.clear()
                self._subs.clear()
                self._subscribed.clear()
                self._subscribed_as_js.clear()

                # Wildcard monitor — sees ALL NATS traffic, no dispatch, for dashboard only.
                await self._nc.subscribe('>', cb=self._monitor_cb)

                # Contrôle de licence par le bridge (services/probe.py) : un seul
                # worker répond à chaque sonde.
                from . import probe
                await self._nc.subscribe(probe.SUBJECT, queue='odoo-probe', cb=self._on_probe)

                with self._lock:
                    subjects = list(self._handlers.keys())
                for subject in subjects:
                    await self._subscribe(subject)

                ticks = 0
                while self._running:
                    await asyncio.sleep(1)
                    ticks += 1
                    if ticks >= _JS_UPGRADE_INTERVAL:
                        ticks = 0
                        await self._upgrade_core_subscriptions()

            except Exception as exc:
                _logger.error(f"NATS connection failed: {exc}")
                self._connected = False
                self._js = None
                if self._running:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)

    async def _on_probe(self, msg) -> None:
        from . import probe
        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(None, probe.answer, self._registry, msg.data)
        if msg.reply:
            await msg.respond(json.dumps(reply).encode())

    async def _subscribe(self, subject: str) -> None:
        """Abonne le sujet une seule fois : en JetStream (consommateur durable)
        si un flux le couvre, sinon en core NATS."""
        if (subject in self._subscribed or subject in self._subscribing
                or not self._nc or self._nc.is_closed):
            return
        self._subscribing.add(subject)
        try:
            if await self._subscribe_js(subject):
                return
            # Aucun flux JetStream ne couvre ce sujet (ex. requêtes) : abonnement
            # simple, en GROUPE (un seul worker reçoit chaque message) ; ces
            # messages ne sont pas persistés côté NATS. Repassé en JetStream dès
            # qu'un flux apparaît (_upgrade_core_subscriptions).
            self._subs[subject] = await self._nc.subscribe(
                subject, queue=self._durable_name(subject), cb=self._make_cb(subject, ack=False)
            )
            self._subscribed.add(subject)
            _logger.warning(f"NATS '{subject}' : aucun flux JetStream — abonnement simple en groupe")
            await self._sync_subscription_state(subject, 'active', jetstream=False)
        finally:
            self._subscribing.discard(subject)

    @staticmethod
    def _durable_name(subject: str) -> str:
        """Nom durable valide dérivé du sujet (même nom pour tous les workers)."""
        return "odoo-pull-" + re.sub(r'[^a-zA-Z0-9_-]', '-', subject.rstrip('.>*'))[:45]

    async def _subscribe_js(self, subject: str) -> bool:
        """Consommateur JetStream durable en mode PULL, partagé entre workers.

        Retourne False si aucun flux ne couvre le sujet. Un ancien consommateur
        « push » du même sujet (versions précédentes) est supprimé : le nouveau
        reprend tout le flux (DeliverAll) ; les traitements sont idempotents.
        """
        if not self._js:
            return False
        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
        from nats.js.errors import NotFoundError
        try:
            stream = await self._js.find_stream_name_by_subject(subject)
        except NotFoundError:
            return False
        durable = self._durable_name(subject)
        legacy = "odoo-" + re.sub(r'[^a-zA-Z0-9_-]', '-', subject.rstrip('.>*'))[:50]
        # Bascule depuis l'ancien consommateur « push » : le nouveau reprend
        # JUSTE APRÈS le dernier message que l'ancien avait acquitté. Sans cela,
        # « tout depuis le début » rejouerait tout le flux conservé (pointages,
        # résultats de commandes, fiches utilisateurs) à la mise à jour.
        start_seq = None
        try:
            info = await self._js.consumer_info(stream, legacy)
            start_seq = (info.ack_floor.stream_seq or 0) + 1
            await self._js.delete_consumer(stream, legacy)
            _logger.info(f"NATS : ancien consommateur push '{legacy}' remplacé par '{durable}' "
                         f"(reprise au message {start_seq})")
        except NotFoundError:
            pass
        if start_seq:
            cfg = ConsumerConfig(
                durable_name=durable, deliver_policy=DeliverPolicy.BY_START_SEQUENCE,
                opt_start_seq=start_seq,
                ack_policy=AckPolicy.EXPLICIT, ack_wait=ACK_WAIT_S,
                max_deliver=MAX_DELIVER, max_ack_pending=MAX_ACK_PENDING,
            )
        else:
            # Installation neuve (aucun ancien consommateur) : tout le flux.
            cfg = ConsumerConfig(
                durable_name=durable, deliver_policy=DeliverPolicy.ALL,
                ack_policy=AckPolicy.EXPLICIT, ack_wait=ACK_WAIT_S,
                max_deliver=MAX_DELIVER, max_ack_pending=MAX_ACK_PENDING,
            )
        psub = await self._js.pull_subscribe(subject, durable=durable, stream=stream, config=cfg)
        self._subs[subject] = psub
        self._subscribed.add(subject)
        self._subscribed_as_js.add(subject)
        self._pull_tasks[subject] = asyncio.get_event_loop().create_task(self._pull_loop(subject, psub))
        _logger.info(f"NATS JetStream (pull) : '{subject}' (durable={durable})")
        await self._sync_subscription_state(subject, 'active', jetstream=True)
        return True

    async def _pull_loop(self, subject: str, psub) -> None:
        """Lit les messages du consommateur partagé tant que le sujet est abonné."""
        from nats.errors import TimeoutError as NatsTimeout
        while self._running and self._subs.get(subject) is psub:
            try:
                msgs = await psub.fetch(FETCH_BATCH, timeout=FETCH_TIMEOUT_S)
            except NatsTimeout:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:   # biodoo: except-ok coupure réseau : journalisée, la boucle réessaie
                _logger.warning(f"NATS : lecture de '{subject}' interrompue, nouvel essai", exc_info=True)
                await asyncio.sleep(2)
                continue
            for msg in msgs:
                await self._handle_js_msg(subject, msg)

    async def _handle_js_msg(self, pattern: str, msg) -> None:
        """Traite un message JetStream : acquitte, redemande plus tard (délai
        croissant), ou le met à l'écart dans « Messages en échec »."""
        try:
            payload = json.loads(msg.data.decode())
        except (ValueError, UnicodeDecodeError):
            await self._run(self._dead_letter, msg.subject, msg.data, 'Message illisible (JSON invalide)', 1)
            await msg.ack()
            return
        ok, poison = await self._run(self._dispatch, msg.subject, payload, pattern)
        if ok:
            await msg.ack()
            return
        delivered = getattr(getattr(msg, 'metadata', None), 'num_delivered', 1) or 1
        if poison or delivered >= MAX_DELIVER:
            reason = poison or f"Échec après {delivered} essais"
            await self._run(self._dead_letter, msg.subject, msg.data, reason, delivered)
            await msg.ack()
        else:
            await msg.nak(delay=min(2 ** delivered, NAK_DELAY_MAX_S))

    async def _run(self, func, *args):
        return await asyncio.get_event_loop().run_in_executor(None, func, *args)

    def _dead_letter(self, subject: str, data: bytes, reason: str, deliveries: int) -> None:
        """Enregistre un message mis à l'écart (visible par l'administrateur)."""
        from odoo import api, SUPERUSER_ID
        try:
            with self._registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env['nats.dead.letter'].create({
                    'subject': subject,
                    'payload': data.decode(errors='replace')[:100000],
                    'reason': reason[:500],
                    'deliveries': deliveries,
                })
        except Exception:   # biodoo: except-ok ne doit pas bloquer la lecture ; journalisé avec le contenu
            _logger.exception("NATS : message en échec non enregistré [%s] %s", subject, data[:500])

    async def _unsubscribe(self, subject: str) -> None:
        """Résilie l'abonnement vivant du sujet et l'oublie. Le consommateur
        durable JetStream reste en place côté serveur, avec sa position."""
        sub = self._subs.pop(subject, None)
        self._subscribed.discard(subject)
        self._subscribed_as_js.discard(subject)
        task = self._pull_tasks.pop(subject, None)
        if task:
            task.cancel()
        if sub is not None:
            try:
                await sub.unsubscribe()
            except Exception:   # biodoo: except-ok abonnement déjà fermé côté serveur : sans effet
                _logger.debug("NATS : désabonnement de '%s' déjà effectif", subject, exc_info=True)

    async def _upgrade_core_subscriptions(self) -> None:
        """Repasse en JetStream les sujets abonnés en core alors qu'un flux les
        couvre désormais.

        Un sujet se retrouve en core à tort quand JetStream n'était pas prêt au
        moment de l'abonnement : Odoo démarré avant que le bridge ait créé ses
        flux, ou serveur NATS tout juste relancé. Sans ce rattrapage il y
        restait jusqu'au redémarrage d'Odoo, en perdant la persistance et
        l'acquittement des messages.

        L'abonnement JetStream est établi AVANT de résilier le core : aucun
        message n'est manqué (au pire un message arrive par les deux, ce que
        les handlers tolèrent déjà), et un échec laisse le core intact.
        """
        if not self._connected or not self._js or not self._nc or self._nc.is_closed:
            return
        for subject in sorted(self._subscribed - self._subscribed_as_js):
            try:
                await self._js.find_stream_name_by_subject(subject)
            except Exception:   # biodoo: except-ok flux absent pour ce sujet : l'abonnement simple reste le bon mode
                continue  # aucun flux ne couvre ce sujet : core est le bon mode
            core_sub = self._subs.get(subject)
            if not await self._subscribe_js(subject):
                continue
            if core_sub is not None:
                try:
                    await core_sub.unsubscribe()
                except Exception as e:   # biodoo: except-ok désabonnement d'un abonnement déjà fermé : sans effet, journalisé
                    _logger.debug(f"NATS unsubscribe '{subject}': {e}")
            _logger.info(f"NATS '{subject}' repassé de core à JetStream")

    async def _monitor_cb(self, msg) -> None:
        """Wildcard '>' subscriber — logs every subject for the dashboard feed, no dispatch."""
        subject = msg.subject
        if subject.startswith('$') or subject.startswith('_INBOX'):
            return
        with self._msg_lock:
            self._msg_log.append({
                "_ts":     time.time(),
                "ts":      time.strftime("%H:%M:%S"),
                "subject": subject,
            })
            self._seen_subjects.add(subject)

    def _make_cb(self, pattern: str, ack: bool = False):
        async def _cb(msg):
            actual_subject = msg.subject
            try:
                payload = json.loads(msg.data.decode())
            except (ValueError, UnicodeDecodeError):
                payload = {'_raw': msg.data.decode(errors='replace')}
            loop = asyncio.get_event_loop()
            ok, poison = await loop.run_in_executor(
                None, self._dispatch, actual_subject, payload, pattern
            )
            if not ok:
                # Abonnement simple : rien à acquitter ; on garde une trace.
                await loop.run_in_executor(None, self._dead_letter, actual_subject, msg.data,
                                           poison or 'Traitement en échec (abonnement simple)', 1)
        return _cb

    def _dispatch(self, actual_subject: str, payload: dict, pattern: str = None) -> bool:
        """Dispatch payload to all handlers matching pattern/actual_subject.

        Returns True if all handlers succeeded (caller should ack).
        Returns False if any handler failed (caller should nak for retry).
        """
        # Use the subscription pattern for the lookup; fall back to actual subject
        # or wildcard matching if no exact pattern key is found.
        lookup_key = pattern
        with self._lock:
            if lookup_key and lookup_key in self._handlers:
                model_names = list(self._handlers[lookup_key])
            else:
                # Fallback: match all registered patterns against the actual subject.
                model_names = []
                for p, models in self._handlers.items():
                    if nats_subject_matches(p, actual_subject):
                        model_names = list(models)
                        lookup_key = p
                        break

        if not model_names:
            return True, None  # aucun traitement : acquitter pour ne pas boucler

        stat_key = lookup_key or actual_subject
        now_ts = time.time()
        with self._stat_lock:
            if stat_key not in self._stats:
                self._stats[stat_key] = {"count": 0, "last_at": None}
            self._stats[stat_key]["count"] += 1
            self._stats[stat_key]["last_at"] = now_ts
            self._stat_delta[stat_key] = self._stat_delta.get(stat_key, 0) + 1
            needs_db_sync = (
                now_ts - self._stat_last_sync.get(stat_key, 0)
            ) >= _STAT_SYNC_INTERVAL
            if needs_db_sync:
                self._stat_last_sync[stat_key] = now_ts
                delta = self._stat_delta.pop(stat_key, 0)

        # ── transaction 1 : logique métier ───────────────────────────
        # Pass the ACTUAL subject (e.g. zkteco.ta.cmdresult.SN1) to handlers
        # so they can extract the serial number correctly.
        #
        # Retry sur erreur de concurrence PostgreSQL : plusieurs messages NATS
        # sont traités en parallèle (run_in_executor) ; deux transactions qui
        # touchent la même ligne (ex. verrou FK sur zkteco_device pendant un
        # heartbeat) peuvent échouer en SerializationFailure sous REPEATABLE
        # READ. On retente la transaction entière, comme la couche HTTP d'Odoo.
        from odoo import api, SUPERUSER_ID
        success = True
        poison = None
        for tryno in range(1, _MAX_NATS_TRIES + 1):
            success = True
            try:
                with self._registry.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    # Message mal formé pour ce sujet : jamais rejoué.
                    for model_name in model_names:
                        poison = env[model_name]._nats_validate(actual_subject, payload)
                        if poison:
                            return False, poison
                    for model_name in model_names:
                        try:
                            env[model_name].handle_nats_event(actual_subject, payload)
                        except _PG_RETRY_ERRORS:
                            raise  # concurrence → géré par la boucle de retry
                        except Exception as exc:
                            _logger.error(
                                f"NATS dispatch [{model_name}@'{actual_subject}']: {exc}",
                                exc_info=True,
                            )
                            success = False
                    if success:
                        cr.commit()
                    else:
                        cr.rollback()
                break
            except _PG_RETRY_ERRORS as exc:
                # le `with cursor` a déjà rollback ; on retente avec backoff
                if tryno >= _MAX_NATS_TRIES:
                    _logger.warning(
                        "NATS '%s' abandonné après %d tentatives (concurrence): %s",
                        actual_subject, tryno, exc.__class__.__name__,
                    )
                    success = False
                    break
                wait = random.uniform(0.0, min(2 ** tryno, 2.0) * 0.1)
                _logger.debug(
                    "NATS '%s' concurrence (essai %d/%d), retry dans %.3fs",
                    actual_subject, tryno, _MAX_NATS_TRIES, wait,
                )
                time.sleep(wait)
            except Exception as exc:
                _logger.error(f"NATS dispatch cursor error: {exc}", exc_info=True)
                success = False
                break

        # ── transaction 2 : stats (best-effort, indépendante) ────────
        if needs_db_sync:
            try:
                from odoo import api, SUPERUSER_ID
                from odoo.fields import Datetime as OdooDatetime
                with self._registry.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    # Ajout (et non écrasement) : chaque worker apporte ses messages.
                    cr.execute(
                        "UPDATE nats_subscription SET message_count = COALESCE(message_count, 0) + %s, "
                        "last_message_at = %s WHERE subject = %s AND state = 'active'",
                        (delta, OdooDatetime.now(), stat_key))
                    cr.commit()
            except _PG_RETRY_ERRORS:   # biodoo: except-ok statistiques : on les rajoute au prochain envoi
                with self._stat_lock:
                    self._stat_delta[stat_key] = self._stat_delta.get(stat_key, 0) + delta
                _logger.debug("NATS : statistiques reportées (accès concurrent) pour %s", stat_key)

        return success, None

    async def _publish(self, subject: str, data: bytes) -> None:
        if self._nc and not self._nc.is_closed:
            await self._nc.publish(subject, data)

    async def _publish_js(self, subject: str, data: bytes) -> None:
        """Fire-and-forget JS publish — errors are logged but not raised."""
        if self._js:
            try:
                await self._js.publish(subject, data)
            except Exception as e:
                _logger.error(f"NATS JS publish error [{subject}]: {e}")

    async def _publish_js_checked(self, subject: str, data: bytes) -> None:
        """JS publish that raises on error — used by publish_js_sync."""
        if not self._js:
            raise RuntimeError("JetStream context not available")
        await self._js.publish(subject, data)

    async def _disconnect(self) -> None:
        if self._nc and not self._nc.is_closed:
            try:
                await self._nc.drain()
            except Exception:   # biodoo: except-ok fermeture de connexion au mieux pendant l'arrêt
                pass

    async def _fetch_streams(self) -> list[dict]:
        """Query the JetStream management API for all stream info."""
        if not self._nc or self._nc.is_closed:
            return []
        try:
            resp = await self._nc.request("$JS.API.STREAM.NAMES", b'{}', timeout=2)
            data = json.loads(resp.data)
            if data.get("error"):
                return []
            names = data.get("streams", [])
        except Exception as e:   # biodoo: except-ok écran des flux : vide si NATS indisponible, journalisé
            _logger.debug(f"NATS stream list failed: {e}")
            return []

        streams = []
        for name in names:
            try:
                resp = await self._nc.request(
                    f"$JS.API.STREAM.INFO.{name}", b'{}', timeout=2
                )
                info = json.loads(resp.data)
                config = info.get("config", {})
                state  = info.get("state", {})
                streams.append({
                    "name":      name,
                    "subjects":  config.get("subjects", []),
                    "messages":  state.get("messages", 0),
                    "bytes":     state.get("bytes", 0),
                    "consumers": state.get("consumer_count", 0),
                })
            except Exception as e:   # biodoo: except-ok un flux illisible n'empêche pas d'afficher les autres, journalisé
                _logger.debug(f"NATS stream info '{name}': {e}")
        return streams

    # ── NATS callbacks ────────────────────────────────────────────

    async def _on_error(self, exc) -> None:
        _logger.error(f"NATS error: {exc}")

    async def _on_disconnected(self) -> None:
        self._connected = False
        _logger.warning("NATS disconnected — will reconnect")
        await self._sync_server_state('error')

    async def _on_reconnected(self) -> None:
        """Rétablit les abonnements après une reconnexion (typiquement : le
        bridge, qui embarque le serveur NATS, vient de redémarrer).

        nats-py restaure DE LUI-MÊME tous les abonnements de la connexion —
        le moniteur '>', les abonnements core et ceux des consommateurs
        JetStream. Se réabonner par-dessus, comme le faisait ce code, doublait
        donc chaque abonnement : JetStream refusait le second (« consumer is
        already bound »), on retombait en core, et chaque message était traité
        deux fois en parallèle jusqu'au redémarrage d'Odoo — d'où des erreurs
        de sérialisation PostgreSQL à chaque battement de pointeuse.

        - moniteur et abonnements core : laissés tels quels, déjà restaurés ;
        - abonnements JetStream : résiliés puis refaits, pour se rattacher au
          consommateur durable — ou le recréer si le serveur a perdu ses
          données. Si JetStream n'est pas encore prêt, le sujet passe en core
          et _upgrade_core_subscriptions le repasse en JetStream ensuite.
        """
        self._connected = True
        self._js = self._nc.jetstream()
        js_subjects = sorted(self._subscribed_as_js)
        _logger.info(
            "NATS reconnected — %d abonnement(s) JetStream à rétablir, "
            "%d abonnement(s) core restaurés par le client",
            len(js_subjects), len(self._subscribed) - len(js_subjects),
        )
        for subject in js_subjects:
            await self._unsubscribe(subject)
        with self._lock:
            subjects = list(self._handlers.keys())
        for subject in subjects:
            await self._subscribe(subject)   # sans effet pour les sujets encore suivis
        await self._sync_server_state('running')

    # ── DB state sync helpers ─────────────────────────────────────

    async def _sync_server_state(self, state: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._db_set_server_state, state)

    def _db_set_server_state(self, state: str) -> None:
        """Aligne `nats.server.state` sur l'état réel du service.

        Au démarrage d'Odoo, deux transactions veulent écrire la même ligne en
        même temps : le démarrage automatique (`action_start`, qui pose
        'running' puis synchronise les abonnements avant de valider) et ce
        thread, dès que la connexion est établie. Une écriture directe perdait
        la course à chaque démarrage : une erreur « could not serialize access »
        dans le journal (et dans la télémétrie d'erreurs), pour deux écritures
        de la même valeur.

        On prend donc le verrou sans attendre et sans trace d'erreur ; si la
        ligne est tenue ou vient d'être modifiée, on recommence sur une
        transaction neuve, qui voit alors la valeur validée par l'autre — et n'a
        le plus souvent plus rien à écrire.
        """
        for attempt in range(_MAX_NATS_TRIES):
            try:
                from odoo import api, SUPERUSER_ID
                with self._registry.cursor() as cr:
                    cr.execute(
                        'SELECT id FROM nats_server '
                        'WHERE state IS DISTINCT FROM %s FOR UPDATE NOWAIT',
                        (state,), log_exceptions=False,
                    )
                    ids = [row[0] for row in cr.fetchall()]
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    if ids:
                        env['nats.server'].browse(ids).write({'state': state})
                    if state == 'error':
                        env['nats.subscription'].sudo().search([
                            ('state', '!=', 'inactive'),
                        ]).write({'state': 'inactive'})
                    cr.commit()
                return
            except _PG_RETRY_ERRORS:
                time.sleep(0.1 * (attempt + 1) + random.uniform(0, 0.05))
            except Exception as exc:   # biodoo: except-ok état du serveur sur l'écran : au mieux, journalisé, sans bloquer le bus
                _logger.debug(f"_db_set_server_state: {exc}")
                return
        _logger.warning(
            "NATS : état du serveur (%s) non enregistré après %d essais — "
            "il le sera au prochain changement d'état", state, _MAX_NATS_TRIES)

    async def _sync_subscription_state(
        self, subject: str, state: str, jetstream: bool = False
    ) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._db_set_subscription_state, subject, state, jetstream
        )

    def _db_set_subscription_state(
        self, subject: str, state: str, jetstream: bool = False
    ) -> None:
        try:
            from odoo import api, SUPERUSER_ID
            with self._registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env['nats.subscription'].sudo().search([
                    ('subject', '=', subject)
                ]).write({'state': state, 'is_jetstream': jetstream})
                cr.commit()
        except Exception as exc:   # biodoo: except-ok état d'un abonnement sur l'écran : au mieux, journalisé
            _logger.debug(f"_db_set_subscription_state: {exc}")
