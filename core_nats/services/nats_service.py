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

    def __init__(self, url: str, registry):
        self._url      = url
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
            self._subscribed.discard(subject)
            self._subscribed_as_js.discard(subject)
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
        except Exception as exc:
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
        except Exception as e:
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

                # Wildcard monitor — sees ALL NATS traffic, no dispatch, for dashboard only.
                await self._nc.subscribe('>', cb=self._monitor_cb)

                with self._lock:
                    subjects = list(self._handlers.keys())
                for subject in subjects:
                    await self._subscribe(subject)

                while self._running:
                    await asyncio.sleep(1)

            except Exception as exc:
                _logger.error(f"NATS connection failed: {exc}")
                self._connected = False
                self._js = None
                if self._running:
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)

    async def _subscribe(self, subject: str) -> None:
        if subject in self._subscribed or not self._nc or self._nc.is_closed:
            return

        # Build a valid JetStream durable name from the subject.
        # Strip wildcards, replace non-alphanum with hyphens, cap at 50 chars.
        durable = "odoo-" + re.sub(
            r'[^a-zA-Z0-9_-]', '-', subject.rstrip('.>*')
        )[:50]

        if self._js:
            try:
                # Attempt to subscribe with DeliverAll so that a brand-new durable
                # consumer replays messages already in the stream (e.g. after a module
                # upgrade that adds new subjects).  If the consumer already exists,
                # NATS ignores the config and resumes from its last ack'd position.
                from nats.js.api import ConsumerConfig, DeliverPolicy
                cfg = ConsumerConfig(deliver_policy=DeliverPolicy.ALL)
            except ImportError:
                cfg = None

            try:
                await self._js.subscribe(
                    subject,
                    cb=self._make_cb(subject, ack=True),
                    durable=durable,
                    manual_ack=True,
                    config=cfg,
                )
                self._subscribed.add(subject)
                self._subscribed_as_js.add(subject)
                _logger.info(f"NATS JS subscribed: '{subject}' (durable={durable})")
                await self._sync_subscription_state(subject, 'active', jetstream=True)
                return
            except Exception as e:
                _logger.debug(
                    f"JetStream subscribe failed for '{subject}': {e} — trying core NATS"
                )

        # Fall back to plain core NATS
        await self._nc.subscribe(subject, cb=self._make_cb(subject, ack=False))
        self._subscribed.add(subject)
        _logger.info(f"NATS core subscribed: '{subject}'")
        await self._sync_subscription_state(subject, 'active', jetstream=False)

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
            except Exception:
                payload = {'_raw': msg.data.decode()}
            loop = asyncio.get_event_loop()
            ok = await loop.run_in_executor(
                None, self._dispatch, actual_subject, payload, pattern
            )
            if ack:
                if ok:
                    await msg.ack()
                else:
                    try:
                        await msg.nak()
                    except Exception:
                        pass
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
            return True  # No handler — ack so it doesn't loop forever

        stat_key = lookup_key or actual_subject
        now_ts = time.time()
        with self._stat_lock:
            if stat_key not in self._stats:
                self._stats[stat_key] = {"count": 0, "last_at": None}
            self._stats[stat_key]["count"] += 1
            self._stats[stat_key]["last_at"] = now_ts
            needs_db_sync = (
                now_ts - self._stat_last_sync.get(stat_key, 0)
            ) >= _STAT_SYNC_INTERVAL
            if needs_db_sync:
                self._stat_last_sync[stat_key] = now_ts

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
        for tryno in range(1, _MAX_NATS_TRIES + 1):
            success = True
            try:
                with self._registry.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
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
                    subs = env['nats.subscription'].sudo().search([
                        ('subject', '=', stat_key),
                        ('state', '=', 'active'),
                    ])
                    with self._stat_lock:
                        st = self._stats.get(stat_key, {})
                    for sub in subs:
                        sub.write({
                            'last_message_at': OdooDatetime.now(),
                            'message_count':   st.get('count', sub.message_count),
                        })
                    cr.commit()
            except Exception as exc:
                _logger.debug(f"NATS stat sync skipped (concurrent update): {exc}")

        return success

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
            except Exception:
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
        except Exception as e:
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
            except Exception as e:
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
        self._connected = True
        self._js = self._nc.jetstream()
        _logger.info("NATS reconnected — re-subscribing")
        self._subscribed.clear()
        self._subscribed_as_js.clear()
        # Re-attach wildcard monitor after reconnect.
        await self._nc.subscribe('>', cb=self._monitor_cb)
        with self._lock:
            subjects = list(self._handlers.keys())
        for subject in subjects:
            await self._subscribe(subject)
        await self._sync_server_state('running')

    # ── DB state sync helpers ─────────────────────────────────────

    async def _sync_server_state(self, state: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._db_set_server_state, state)

    def _db_set_server_state(self, state: str) -> None:
        try:
            from odoo import api, SUPERUSER_ID
            with self._registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                servers = env['nats.server'].sudo().search([])
                servers.write({'state': state})
                if state == 'error':
                    env['nats.subscription'].sudo().search([
                        ('server_id', 'in', servers.ids)
                    ]).write({'state': 'inactive'})
                cr.commit()
        except Exception as exc:
            _logger.debug(f"_db_set_server_state: {exc}")

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
        except Exception as exc:
            _logger.debug(f"_db_set_subscription_state: {exc}")
