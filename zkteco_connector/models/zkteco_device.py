# -*- coding: utf-8 -*-
import json
import logging
import os
from datetime import timedelta

import pytz

from odoo import models, fields, api

_logger = logging.getLogger(__name__)

# Illustrations par modèle : dossier + URL statique + registre JSON.
_DEVICE_IMG_DIR = os.path.join(os.path.dirname(__file__), '..', 'static', 'src', 'img', 'devices')
_DEVICE_IMG_URL = '/zkteco_connector/static/src/img/devices/'
_DEVICE_IMG_DEFAULT = 'default.png'


def _tz_get(self):
    """Liste des fuseaux IANA (pattern standard Odoo, cf. res.partner.tz)."""
    return [(tz, tz) for tz in sorted(pytz.all_timezones)]


def _device_image_map():
    """Charge le registre modèle→image (models.json). Clés normalisées en
    minuscules ; les clés `_…` (readme) sont ignorées. Relu à chaque affichage
    (petit fichier, une lecture par chargement de vue)."""
    try:
        with open(os.path.join(_DEVICE_IMG_DIR, 'models.json'), encoding='utf-8') as f:
            raw = json.load(f)
        return {k.strip().lower(): v for k, v in raw.items() if not k.startswith('_')}
    except Exception:  # noqa: BLE001 — fichier absent/invalide → pas d'illustration custom
        return {}

OFFLINE_THRESHOLD_MINUTES = 2

# A device polls every few seconds; a bare heartbeat that only bumps last_seen
# is debounced to at most one write per this window. This kills the FK-lock
# contention (SerializationFailure) caused by concurrent heartbeats racing on
# the same row under REPEATABLE READ.
LAST_SEEN_DEBOUNCE_SECONDS = 30

# Integer-typed device fields (parsed from the ADMS info map).
_INT_FIELDS = frozenset({
    'user_count', 'fp_count', 'face_count', 'admin_count',
    'attlog_count', 'transaction_count', 'lock_count',
    'max_user_count', 'max_fp_count', 'max_face_count', 'max_attlog_count',
})
ONLINE_THRESHOLD_MINUTES  = 2   # last_seen < 2 min → considered online (device poll ~10-30s + debounce 30s → sûr, pas de clignotement)

# ADMS field names → Odoo field names
_INFO_MAP = {
    # Identity
    'DeviceName':       'device_name',
    'OEMVendor':        'oem_vendor',
    'ProductTime':      'product_time',
    'DeviceID':         'device_id_raw',
    'Language':         'language_code',
    # Hardware
    'Platform':         'platform',
    'FirmwareVer':      'firmware_version',
    'FWVersion':        'firmware_version',
    # Network
    'IPAddress':        'ip_address',
    'MACAddress':       'mac_address',
    'MAC':              'mac_address',
    'PushVer':          'push_version',
    'PushVersion':      'push_version',
    # Capacity — current
    'UserCount':        'user_count',
    'FPCount':          'fp_count',
    'FaceCount':        'face_count',
    'AttLogCount':      'attlog_count',
    'TransactionCount': 'transaction_count',
    'LockCount':        'lock_count',
    'AdminCount':       'admin_count',
    # Capacity — maximum
    'MaxUserCount':     'max_user_count',
    'MaxFingerCount':   'max_fp_count',
    'MaxFaceCount':     'max_face_count',
    'MaxAttLogCount':   'max_attlog_count',
    # Biometric algo versions (colon-separated, index = bio_type)
    'MultiBioVersion':  'multi_bio_version',
}


class ZktecoDevice(models.Model):
    _name = 'zkteco.device'
    _description = 'ZKTeco Device'
    _rec_name = 'display_name'
    _order = 'last_seen desc'

    # ── Identity ──────────────────────────────────────────────────
    serial_number = fields.Char(string='N° Série', required=True, index=True, copy=False)
    custom_name   = fields.Char(
        string='Nom personnalisé', copy=False,
        help="Nom donné par l'utilisateur (ex: « Pointeuse salle de réunion »). "
             "S'il est renseigné, il remplace le modèle + n° série partout dans l'app.",
    )
    device_name   = fields.Char(string='Modèle',           readonly=True)
    oem_vendor    = fields.Char(string='Fabricant',        readonly=True)
    product_time  = fields.Char(string='Date fabrication', readonly=True)
    device_id_raw = fields.Char(string='Device ID',        readonly=True)
    language_code = fields.Char(string='Code langue',      readonly=True)

    display_name = fields.Char(compute='_compute_display_name', store=True, string='Nom')
    # Illustration du modèle : mappée via models.json, sinon mockup par défaut.
    device_image_url = fields.Char(compute='_compute_device_image_url', string='Illustration')

    # ── Hardware ──────────────────────────────────────────────────
    platform         = fields.Char(string='Plateforme', readonly=True)
    firmware_version = fields.Char(string='Firmware',   readonly=True)

    # ── Network ───────────────────────────────────────────────────
    ip_address   = fields.Char(string='Adresse IP',  readonly=True)
    mac_address  = fields.Char(string='Adresse MAC', readonly=True)
    push_version = fields.Char(string='Push Proto',  readonly=True)

    # ── Fuseau horaire (F-8) ──────────────────────────────────────
    # Fuseau propre à CETTE pointeuse (multi-sites : une pointeuse à Sydney et une
    # à Alger n'ont pas le même). Poussé au bridge (SET_TZ) → le bloc d'init annonce
    # le bon TimeZone (horloge/affichage device) et les ATTLOG sont interprétés dans
    # ce fuseau. Défaut = fuseau de la société / de l'utilisateur.
    device_timezone = fields.Selection(
        _tz_get, string='Fuseau horaire',
        default=lambda self: (self.env.company.partner_id.tz
                              or self.env.user.tz or 'UTC'),
        help="Fuseau horaire de la pointeuse. Détermine l'heure affichée sur "
             "l'appareil et l'interprétation des pointages. À régler par site.")
    device_time_preview = fields.Char(
        string='Heure appliquée', compute='_compute_device_time_preview',
        help="Heure qui sera affichée sur la pointeuse avec ce fuseau "
             "(heure serveur convertie). Aperçu au moment de l'affichage.")

    # ── Localisation (vue carte) ──────────────────────────────────
    latitude  = fields.Float(string='Latitude',  digits=(10, 7))
    longitude = fields.Float(string='Longitude', digits=(10, 7))
    location_address = fields.Char(string='Adresse / site')
    # Partenaire technique porteur des coordonnées GPS : la vue carte (web_map)
    # trace un res.partner via partner_latitude/longitude. On le synchronise
    # depuis les coords saisies ici (pas de géocodage : on écrit les coords brutes).
    partner_id = fields.Many2one('res.partner', string='Point carte',
                                 ondelete='set null', copy=False)

    # ── Capacity: current ─────────────────────────────────────────
    user_count        = fields.Integer(string='Utilisateurs',  readonly=True)
    fp_count          = fields.Integer(string='Empreintes',    readonly=True)
    face_count        = fields.Integer(string='Visages',       readonly=True)
    admin_count       = fields.Integer(string='Admins',        readonly=True)
    attlog_count      = fields.Integer(string='Logs présence', readonly=True)
    transaction_count = fields.Integer(string='Transactions',  readonly=True)
    lock_count        = fields.Integer(string='Serrures',      readonly=True)

    # ── Capacity: maximum ─────────────────────────────────────────
    max_user_count   = fields.Integer(string='Max utilisateurs',  readonly=True)
    max_fp_count     = fields.Integer(string='Max empreintes',    readonly=True)
    max_face_count   = fields.Integer(string='Max visages',       readonly=True)
    max_attlog_count = fields.Integer(string='Max logs présence', readonly=True)

    # ── Biometric algo versions ────────────────────────────────────
    multi_bio_version = fields.Char(
        string='Version algo bio', readonly=True,
        help='MultiBioVersion reçu à l\'enregistrement — index = bio_type, valeur = version algo. Ex: 0:10:0:7:0:0:0:0:0:0',
    )

    # ── Lifecycle ─────────────────────────────────────────────────
    state = fields.Selection([
        ('pending',  'En attente'),
        ('approved', 'Approuvé'),
        ('offline',  'Hors ligne'),
        ('rejected', 'Rejeté'),
    ], default='pending', readonly=True, index=True)

    is_online = fields.Boolean(
        string='En ligne',
        compute='_compute_is_online',
        help='Vrai si le device a été vu il y a moins de 5 minutes.',
    )

    first_seen    = fields.Datetime(string='Première connexion', readonly=True)
    last_seen     = fields.Datetime(string='Dernière activité',  readonly=True)
    approved_by   = fields.Many2one('res.users', string='Approuvé par',   readonly=True, copy=False)
    approved_date = fields.Datetime(string='Date approbation',            readonly=True, copy=False)

    attendance_count     = fields.Integer(compute='_compute_counts', string='Pointages')
    device_user_count    = fields.Integer(compute='_compute_counts', string='Users device')
    pending_user_count   = fields.Integer(compute='_compute_counts', string='En attente')
    pending_attlog_count = fields.Integer(compute='_compute_counts', string='Quarantaine')
    raw_attlog_count     = fields.Integer(compute='_compute_counts', string='Pointages bruts')
    anomaly_attlog_count = fields.Integer(compute='_compute_counts', string='Anomalies')
    cmd_error_count      = fields.Integer(compute='_compute_counts', string='Erreurs cmds')

    _serial_unique = models.Constraint(
        'UNIQUE(serial_number)',
        'Le numéro de série doit être unique.',
    )

    # ── computed ──────────────────────────────────────────────────

    @api.depends('custom_name', 'device_name', 'serial_number')
    def _compute_display_name(self):
        for d in self:
            if d.custom_name:
                d.display_name = d.custom_name
            elif d.device_name:
                d.display_name = f"{d.device_name} ({d.serial_number})"
            else:
                d.display_name = d.serial_number

    @api.depends('device_name', 'platform')
    def _compute_device_image_url(self):
        mapping = _device_image_map()
        for d in self:
            fname = None
            for key in (d.device_name, d.platform):
                if key and key.strip().lower() in mapping:
                    fname = mapping[key.strip().lower()]
                    break
            # image mappée ET présente sur le disque, sinon mockup par défaut
            if fname and os.path.exists(os.path.join(_DEVICE_IMG_DIR, fname)):
                d.device_image_url = _DEVICE_IMG_URL + fname
            else:
                d.device_image_url = _DEVICE_IMG_URL + _DEVICE_IMG_DEFAULT

    def _compute_is_online(self):
        threshold = fields.Datetime.now() - timedelta(minutes=ONLINE_THRESHOLD_MINUTES)
        for d in self:
            d.is_online = bool(
                d.state == 'approved'
                and d.last_seen
                and d.last_seen >= threshold
            )

    @api.depends('serial_number')
    def _compute_counts(self):
        Att    = self.env['hr.attendance'].sudo()
        DU     = self.env['zkteco.device.user'].sudo()
        Attlog = self.env['zkteco.device.attlog'].sudo()
        CmdLog = self.env['zkteco.device.cmd.log'].sudo()
        for d in self:
            d.attendance_count     = Att.search_count([('zkteco_device_sn', '=', d.serial_number)])
            d.device_user_count    = DU.search_count([('device_id', '=', d.id)])
            d.pending_user_count   = DU.search_count([('device_id', '=', d.id), ('state', '=', 'new')])
            d.pending_attlog_count = Attlog.search_count([('device_id', '=', d.id), ('state', '=', 'pending')])
            d.raw_attlog_count     = Attlog.search_count([('device_id', '=', d.id)])
            d.anomaly_attlog_count = Attlog.search_count([('device_id', '=', d.id), ('state', '=', 'anomaly')])
            d.cmd_error_count      = CmdLog.search_count([('device_id', '=', d.id), ('is_error', '=', True)])

    # ── smart buttons ─────────────────────────────────────────────

    def action_view_attendances(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Pointages — {self.display_name}',
            'res_model': 'hr.attendance',
            'view_mode': 'list,form',
            'domain': [('zkteco_device_sn', '=', self.serial_number)],
        }

    def action_view_device_users(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Users — {self.display_name}',
            'res_model': 'zkteco.device.user',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id)],
            'context': {'default_device_id': self.id},
        }

    def action_view_pending_users(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Users à mapper — {self.display_name}',
            'res_model': 'zkteco.device.user',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id), ('state', '=', 'new')],
            'context': {'default_device_id': self.id},
        }

    def action_view_cmd_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Journal commandes — {self.display_name}',
            'res_model': 'zkteco.device.cmd.log',
            'view_mode': 'list',
            'domain': [('device_id', '=', self.id)],
            'context': {'default_device_id': self.id},
        }

    def action_view_attlog_quarantine(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Quarantaine — {self.display_name}',
            'res_model': 'zkteco.device.attlog',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id), ('state', '=', 'pending')],
        }

    def action_view_raw_attlog(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Pointages bruts — {self.display_name}',
            'res_model': 'zkteco.device.attlog',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id)],
            'context': {'search_default_group_state': 1},
        }

    def action_view_attlog_anomalies(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Anomalies — {self.display_name}',
            'res_model': 'zkteco.device.attlog',
            'view_mode': 'list,form',
            'domain': [('device_id', '=', self.id), ('state', '=', 'anomaly')],
        }

    # ── approval ──────────────────────────────────────────────────

    def action_open_approve_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Approuver le device',
            'res_model': 'zkteco.approve.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_device_id': self.id},
        }

    def action_reject(self):
        self.ensure_one()
        self.write({'state': 'rejected'})
        return self._notif('Device rejeté', f'{self.display_name} ignoré.', 'warning')

    def action_reset_to_pending(self):
        self.ensure_one()
        self.write({'state': 'pending'})

    # ── remote commands (per ADMS protocol) ───────────────────────

    def action_reboot(self):
        """REBOOT — redémarre le device (§12.7.1)."""
        self.ensure_one()
        self._send_command('REBOOT')
        return self._notif('Redémarrage', f'{self.display_name} va redémarrer.', 'warning')

    def action_refresh_info(self):
        """INFO — demande au device d'envoyer sa configuration (§12.4.3)."""
        self.ensure_one()
        self._send_command('INFO')
        return self._notif('Infos demandées', 'Le device va envoyer ses informations.')

    def action_purge_commands(self):
        """PURGE — vide la file de commandes du device côté bridge (équivalent
        BioTime TerminalClearCommand). Remède à une file bloquée : le bridge
        supprime les commandes en attente/non confirmées et remonte un résultat
        'expired' pour chacune (les lignes du journal se ferment)."""
        self.ensure_one()
        self._send_command('PURGE')
        return self._notif('File purgée',
                           f'Les commandes en attente de {self.display_name} ont été purgées.',
                           'warning')

    def action_query_employees(self):
        """QUERY_USERS — récupère les utilisateurs enrôlés."""
        self.ensure_one()
        self._send_command('QUERY_USERS')
        return self._notif('Synchro lancée', f'{self.display_name} va envoyer ses utilisateurs.')

    def action_enroll_all_employees(self):
        """Pousse tous les employés Odoo vers le device via ENROLL_USER."""
        self.ensure_one()
        employees = self.env['hr.employee'].sudo().search([
            ('zkteco_pin', '!=', False), ('active', '=', True),
        ])
        for emp in employees:
            self._send_command(self._build_enroll_user_cmd(emp))
        return self._notif('Enrollement', f'{len(employees)} employé(s) → {self.serial_number}')

    def action_remove_all_employees(self):
        """Ouvre le wizard de confirmation avant de vider le device."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Vider le device',
            'res_model': 'zkteco.wipe.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_device_id': self.id},
        }

    def action_full_sync(self):
        """FULL_SYNC — force le device à tout re-envoyer (users, empreintes, pointages).
        Côté bridge : stamps remis à 0 + REBOOT → le device re-pousse tout, qui
        retransite normalement par les handlers NATS.
        (I-11) L'ancien `svc.replay_subject('zkteco.ta.operlog.>')` était un no-op :
        sur un consumer durable, JetStream reprend au dernier ack, donc rien n'était
        rejoué. Le FULL_SYNC device suffit ; l'appel trompeur est retiré."""
        self.ensure_one()
        self._send_command('FULL_SYNC')
        return self._notif(
            'Sync complet lancé',
            f'{self.display_name} : reboot device + renvoi complet en cours.',
        )

    def action_query_attlog(self):
        """QUERY_ATTLOG — demande au device de renvoyer ses pointages sur les 30 derniers jours."""
        self.ensure_one()
        from datetime import datetime, timedelta
        now = datetime.now()
        start = (now - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%S')
        end   = now.strftime('%Y-%m-%dT%H:%M:%S')
        self._send_command(f'QUERY_ATTLOG START={start} END={end}')
        return self._notif('Pointages demandés', f'{self.display_name} va renvoyer ses pointages des 30 derniers jours.')

    def action_clear_logs(self):
        """CLEAR LOG — efface les logs de pointage sur le device."""
        self.ensure_one()
        self._send_command('CLEAR LOG')
        return self._notif('Logs effacés', f'{self.display_name} : logs supprimés du device.', 'warning')

    # ── fuseau horaire (F-8) ──────────────────────────────────────

    @api.depends('device_timezone')
    def _compute_device_time_preview(self):
        # fields.Datetime.now() = maintenant en UTC (naïf) → on localise puis convertit.
        utc_now = pytz.utc.localize(fields.Datetime.now())
        for d in self:
            if d.device_timezone:
                try:
                    local = utc_now.astimezone(pytz.timezone(d.device_timezone))
                    d.device_time_preview = local.strftime('%H:%M — %d/%m/%Y (%Z, UTC%z)')
                except Exception:  # noqa: BLE001 — fuseau invalide
                    d.device_time_preview = ''
            else:
                d.device_time_preview = ''

    def write(self, vals):
        res = super().write(vals)
        if 'device_timezone' in vals:
            self._push_device_timezone()
        if {'latitude', 'longitude', 'location_address', 'custom_name'} & set(vals):
            self._sync_map_partner()
        return res

    def _sync_map_partner(self):
        """Synchronise le partenaire technique (point carte) avec les coordonnées
        GPS saisies. La vue carte web_map place le marqueur directement sur
        partner_latitude/longitude (pas de géocodage). Créé à la demande."""
        Partner = self.env['res.partner'].sudo()
        for d in self:
            if not (d.latitude or d.longitude):
                continue
            vals = {
                'name': d.display_name or d.serial_number or 'Pointeuse',
                'partner_latitude': d.latitude,
                'partner_longitude': d.longitude,
            }
            if d.location_address:
                vals['street'] = d.location_address
            if d.partner_id:
                d.partner_id.write(vals)
            else:
                d.partner_id = Partner.create(vals)

    def action_apply_timezone(self):
        """F-8 — applique le fuseau sur la pointeuse en 2 temps :
        1) `settz` SYNCHRONE : le bridge enregistre le fuseau ET confirme la valeur
           appliquée → on vérifie qu'elle égale bien ce qu'Odoo annonce (pas de
           reboot dans le vide si le device est hors ligne ou le fuseau invalide) ;
        2) REBOOT : le device relit le TimeZone à sa ré-inscription."""
        self.ensure_one()
        from odoo.exceptions import UserError
        from odoo.addons.core_nats.services.nats_service import get_service
        if not self.device_timezone:
            raise UserError("Choisissez d'abord un fuseau horaire.")
        svc = get_service()
        if not svc or not svc.is_running:
            raise UserError("Le service NATS n'est pas démarré.")
        resp = svc.request_sync(
            'zkteco.ta.bridge.settz',
            {'sn': self.serial_number, 'tz': self.device_timezone}, timeout=3)
        if not resp:
            raise UserError("Le bridge n'a pas répondu (hors ligne ?). Réessayez.")
        if not resp.get('ok') or resp.get('timezone') != self.device_timezone:
            reason = resp.get('error') or resp.get('timezone') or '—'
            raise UserError(
                "Le bridge n'a pas pu appliquer le fuseau (%s). La pointeuse est "
                "peut-être encore hors ligne — réessayez quand elle est connectée." % reason)
        # Vérifié côté bridge → redémarrage pour appliquer sur l'appareil.
        self._send_command('REBOOT')
        return self._notif(
            'Fuseau appliqué',
            "%s : fuseau %s vérifié côté bridge (%s) — redémarrage envoyé." % (
                self.display_name, self.device_timezone, self.device_time_preview))

    def _push_device_timezone(self):
        """F-8 — pousse le fuseau de chaque device au bridge (SET_TZ).
        Publication DIRECTE (pas via _send_command) : SET_TZ est une config interne
        au bridge, acquittée sans retour device — la tracer dans cmd.log créerait
        des lignes 'requested' orphelines à chaque réconciliation. Si NATS est
        arrêté, on ignore : la réconciliation (bridge.online / cron) re-poussera.
        Le device applique le nouvel offset à sa prochaine ré-inscription
        (ou via FULL_SYNC pour forcer)."""
        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc or not svc.is_running:
            return
        for d in self:
            if d.serial_number and d.device_timezone:
                svc.publish_js_sync(f'zkteco.ta.cmd.{d.serial_number}',
                                    {'cmd': f'SET_TZ TZ={d.device_timezone}'})

    # ── helpers ───────────────────────────────────────────────────

    def _send_command(self, cmd_str: str, soft: bool = False):
        """Publie une commande sémantique vers le bridge via NATS JetStream.

        soft=False (défaut, actions interactives) : lève UserError si NATS est
        indisponible — feedback direct à l'opérateur.
        soft=True (auto-sync déclenché par une écriture RH) : n'échoue JAMAIS.
        La commande est journalisée en 'pending_publish' (outbox) et republiée
        automatiquement au prochain `bridge.online`. Ainsi une panne NATS ne
        bloque plus l'édition d'un employé."""
        import uuid as _uuid
        from odoo.addons.core_nats.services.nats_service import get_service
        from odoo.exceptions import UserError
        svc = get_service()

        client_uuid = str(_uuid.uuid4())
        now = fields.Datetime.now()

        log = self.env['zkteco.device.cmd.log'].sudo().create({
            'device_id':    self.id,
            'cmd':          cmd_str[:512],
            'state':        'requested',
            'cmd_uuid':     client_uuid,
            'requested_by': self.env.uid,
            'requested_at': now,
        })

        def _defer(reason):
            log.sudo().write({'state': 'pending_publish'})
            _logger.warning("[zkteco] commande différée (outbox) — %s: %s sur %s",
                            reason, cmd_str[:80], self.serial_number)

        if not svc or not svc.is_running:
            if soft:
                _defer('NATS arrêté')
                return
            raise UserError(
                "Le service NATS n'est pas démarré. "
                "Démarrez-le depuis le menu ZKTeco avant d'envoyer des commandes."
            )

        ok = svc.publish_js_sync(
            f'zkteco.ta.cmd.{self.serial_number}',
            {'cmd': cmd_str, 'client_cmd_uuid': client_uuid},
        )
        if not ok:
            if soft:
                _defer('publication NATS échouée')
                return
            log.sudo().write({'state': 'expired'})
            raise UserError(
                f"La commande n'a pas pu être publiée sur NATS JetStream "
                f"(device={self.serial_number}). Vérifiez la connexion NATS."
            )

        log.sudo().write({'state': 'published', 'published_at': fields.Datetime.now()})
        _logger.info(f"[zkteco] → {self.serial_number}: {cmd_str[:80]} (uuid={client_uuid})")

    @api.model
    def _republish_pending_commands(self):
        """Republie les commandes restées en 'pending_publish' (outbox) quand
        NATS était indisponible. Appelé sur l'évènement `bridge.online` — pas de
        cron. La commande d'origine est ré-émise avec le même UUID de corrélation."""
        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc or not svc.is_running:
            return
        pending = self.env['zkteco.device.cmd.log'].sudo().search([
            ('state', '=', 'pending_publish'),
            ('device_id', '!=', False),
        ], order='create_date asc', limit=500)
        sent = 0
        for log in pending:
            device = log.device_id
            if not device.serial_number:
                continue
            ok = svc.publish_js_sync(
                f'zkteco.ta.cmd.{device.serial_number}',
                {'cmd': log.cmd, 'client_cmd_uuid': log.cmd_uuid},
            )
            if ok:
                log.write({'state': 'published', 'published_at': fields.Datetime.now()})
                sent += 1
            else:
                break  # NATS retombé — on réessaiera au prochain bridge.online
        if sent:
            _logger.info("[zkteco] outbox: %d commande(s) en attente republiée(s)", sent)

    @staticmethod
    def _build_enroll_user_cmd(employee, card: str = '') -> str:
        """Construit la commande sémantique ENROLL_USER pour le bridge Go.
        NAME doit être en dernier (peut contenir des espaces)."""
        pin  = str(employee.zkteco_pin).strip()
        name = (employee.name or '')[:24].replace('\t', ' ')
        pri  = employee.zkteco_privilege or '0'   # 0=user 2=enrôleur 6=admin 14=super admin
        return f'ENROLL_USER PIN={pin} PRI={pri} CARD={card} NAME={name}'

    @staticmethod
    def _notif(title: str, message: str, ntype: str = 'success') -> dict:
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'type': ntype, 'sticky': False},
        }

    @staticmethod
    def _employee_form_action(employee_id: int) -> dict:
        """Retourne une act_window pointant sur la fiche employé pour forcer son rechargement."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.employee',
            'res_id': employee_id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def action_add_to_authorized(self):
        """Ajoute ce device aux pointeuses autorisées de l'employé (context employee_id).
        Déclenche ENROLL_USER + sync biométrique via le write override sur hr.employee."""
        self.ensure_one()
        employee_id = self.env.context.get('employee_id')
        if not employee_id:
            return False
        employee = self.env['hr.employee'].sudo().browse(employee_id)
        if not employee.exists():
            return False
        employee.write({'zkteco_authorized_device_ids': [(4, self.id)]})
        return self._employee_form_action(employee_id)

    def action_remove_from_authorized(self):
        """Retire ce device des pointeuses autorisées de l'employé (context employee_id).
        Déclenche DELETE_USER via le write override sur hr.employee."""
        self.ensure_one()
        employee_id = self.env.context.get('employee_id')
        if not employee_id:
            return False
        employee = self.env['hr.employee'].sudo().browse(employee_id)
        if not employee.exists():
            return False
        employee.write({'zkteco_authorized_device_ids': [(3, self.id)]})
        return self._employee_form_action(employee_id)

    def _get_bio_algo_version(self, bio_type: int) -> int:
        """Retourne la version d'algo pour un bio_type donné en lisant MultiBioVersion.
        bio_type est utilisé comme index dans le tableau colon-séparé.
        Retourne 0 si inconnu (pas de contrainte de compatibilité)."""
        if not self.multi_bio_version:
            return 0
        parts = self.multi_bio_version.split(':')
        if bio_type >= len(parts):
            return 0
        try:
            # Le device renvoie la version comme "12.0" / "39.3" ; la version
            # majeure (avant le point) est l'identité d'algo qui doit matcher.
            return int(parts[bio_type].split('.')[0])
        except (ValueError, IndexError):
            return 0

    # ── upsert (called from NATS handler) ─────────────────────────

    @api.model
    def _upsert_device(self, serial_number: str, info: dict = None) -> 'ZktecoDevice':
        Device = self.sudo()
        now = fields.Datetime.now()

        # Parse the substantive field values from the ADMS info map (everything
        # except last_seen, which is handled separately for the debounce).
        field_vals = {}
        if info:
            for adms_key, odoo_field in _INFO_MAP.items():
                raw = str(info.get(adms_key, '')).strip()
                if not raw:
                    continue
                if odoo_field in _INT_FIELDS:
                    try:
                        field_vals[odoo_field] = int(raw)
                    except ValueError:
                        pass
                else:
                    field_vals[odoo_field] = raw

        # Non-locking lookup first — the common case (a bare heartbeat on an
        # already-online device) must never take a row lock.
        self.env.cr.execute(
            'SELECT id, last_seen, state FROM zkteco_device WHERE serial_number = %s',
            (serial_number,)
        )
        row = self.env.cr.fetchone()

        if row:
            dev_id, cur_last_seen, cur_state = row
            device = Device.browse(dev_id)
            substantive = (
                cur_state == 'offline'
                or any(device[f] != v for f, v in field_vals.items())
            )
            if not substantive:
                # Pure heartbeat → debounce. A cheap, unlocked UPDATE (no ORM
                # write, no FOR UPDATE, no write_date churn) eliminates the
                # lock contention entirely; skip it outright if very recent.
                if cur_last_seen and (now - cur_last_seen).total_seconds() < LAST_SEEN_DEBOUNCE_SECONDS:
                    return device
                self.env.cr.execute(
                    'UPDATE zkteco_device SET last_seen = %s WHERE id = %s',
                    (now, dev_id),
                )
                device.invalidate_recordset(['last_seen'])
                # A — le device réapparaît après avoir été affiché hors ligne
                # (last_seen plus vieux que le seuil « en ligne ») → push live.
                if (not cur_last_seen
                        or (now - cur_last_seen).total_seconds() >= ONLINE_THRESHOLD_MINUTES * 60):
                    device._notify_status_change()
                return device

            # Substantive change (new info fields or offline→online): take the
            # row lock and write through the ORM as before.
            self.env.cr.execute(
                'SELECT id FROM zkteco_device WHERE serial_number = %s FOR UPDATE',
                (serial_number,)
            )
            vals = dict(field_vals, last_seen=now)
            back_online = cur_state == 'offline'
            if back_online:
                vals['state'] = 'approved'
            device.write(vals)
            if back_online:
                device._notify_status_change()  # A — offline→online : push live
            return device

        # New device — create under a savepoint, re-locking on a concurrent insert.
        vals = dict(field_vals, last_seen=now, first_seen=now)
        try:
            with self.env.cr.savepoint():
                device = Device.create({
                    'serial_number': serial_number,
                    'state': 'pending',
                    **vals,
                })
            _logger.info(f"[zkteco] new device: {serial_number} — awaiting approval")
        except Exception:
            self.env.cr.execute(
                'SELECT id FROM zkteco_device WHERE serial_number = %s FOR UPDATE',
                (serial_number,)
            )
            row = self.env.cr.fetchone()
            device = Device
            if row:
                device = Device.browse(row[0])
                device.write(vals)

        return device

    # ── offline cron ──────────────────────────────────────────────

    @api.model
    @api.model
    def _cron_cache_bridge_license(self):
        """Rafraîchit le cache de l'expiration de la licence biodoo (ICP
        'biodoo.license_expiration') depuis le bridge, pour que « Paramètres →
        À propos » affiche la vraie date sans dépendre de l'ouverture du dashboard.
        Silencieux si NATS/bridge indisponible (garde la dernière valeur connue)."""
        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc or not svc.is_running:
            return
        status = svc.request_sync('zkteco.ta.bridge.status', b'', timeout=3.0)
        if not (isinstance(status, dict) and status.get('active') and status.get('expires_at')):
            return
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(str(status['expires_at']).replace('Z', '+00:00'))
            self.env['ir.config_parameter'].sudo().set_param(
                'biodoo.license_expiration', dt.strftime('%Y-%m-%d %H:%M:%S'))
        except (ValueError, TypeError):
            pass

    def _cron_mark_offline(self):
        threshold = fields.Datetime.now() - timedelta(minutes=OFFLINE_THRESHOLD_MINUTES)
        stale = self.sudo().search([
            ('state', '=', 'approved'),
            ('last_seen', '<', threshold),
        ])
        if stale:
            stale.write({'state': 'offline'})
            _logger.info(f"[zkteco] offline: {', '.join(stale.mapped('serial_number'))}")
            # A — push live : ces devices viennent de passer hors ligne → notifier
            # les vues ouvertes pour qu'elles rechargent (l'indicateur bascule sans
            # rechargement manuel).
            stale._notify_status_change()

    # ── indicateur en ligne / hors ligne — push live (A) ──────────

    def _notify_status_change(self):
        """Pousse une notif bus quand le statut en ligne/hors ligne d'un device
        change, pour que les vues kanban/liste ouvertes se rechargent en direct.
        Émis UNIQUEMENT sur transition (pas à chaque heartbeat) → trafic minimal."""
        if not self:
            return
        self.env['bus.bus']._sendone('zkteco_device_status', 'zkteco_device_status',
                                     {'ids': self.ids})
