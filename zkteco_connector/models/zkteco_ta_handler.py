# -*- coding: utf-8 -*-
import logging
import re
from datetime import datetime, timedelta, timezone

from psycopg2 import OperationalError

from odoo import models, api, fields
from odoo.exceptions import MissingError, ValidationError, UserError

_logger = logging.getLogger(__name__)

# Doit correspondre à adms.ExpiredReturnCode côté bridge Go.
_CMD_EXPIRED_RC = -9999
# Durée après laquelle une commande jamais confirmée est considérée expirée.
_CMD_LOG_MAX_AGE_HOURS = 24

# I-10 : reconnaître une suppression user aboutie dans le wire command échoé.
# `DATA DELETE USERINFO PIN=x` = un user ; sans PIN = tous (spec 12.1.2.1).
_DELETE_USER_RE = re.compile(r'DATA\s+DELETE\s+USERINFO\b', re.I)
_PIN_RE = re.compile(r'\bPIN=(\S+)', re.I)

# F-2 : libellés lisibles des codes de retour d'enrôlement (Annexe 1). Les codes
# non listés (dont les négatifs -1001..-1008) tombent sur un libellé générique.
_ENROLL_RC_LABELS = {
    2: "L'utilisateur existe déjà",
    4: "Qualité biométrique insuffisante",
    5: "Doublon — déjà enrôlé",
    6: "Enrôlement annulé",
    7: "Périphérique occupé",
}


def _enroll_rc_label(rc):
    """Libellé d'un code de retour d'enrôlement (jamais vide)."""
    return _ENROLL_RC_LABELS.get(rc, f"Échec de l'enrôlement (code {rc})")


class ZktecoTaHandler(models.AbstractModel):
    _name = 'zkteco.ta.handler'
    _inherit = 'nats.handler'
    _description = 'ZKTeco T&A NATS Handler'

    _nats_subjects = [
        'zkteco.ta.attendance.>',
        'zkteco.ta.device.>',
        'zkteco.ta.userinfo.>',
        'zkteco.ta.attphoto.>',
        'zkteco.ta.operlog.>',
        'zkteco.ta.biodata.>',
        'zkteco.ta.cmdresult.>',
        'zkteco.ta.errorlog.>',
    ]

    @api.model
    def handle_nats_event(self, subject: str, payload: dict):
        try:
            if 'attphoto'   in subject: self._process_attphoto(subject, payload)
            elif 'attendance'  in subject: self._process_attendance(subject, payload)
            elif 'device'      in subject: self._process_device_event(subject, payload)
            elif 'userinfo'    in subject: self._process_userinfo(subject, payload)
            elif 'operlog'     in subject: self._process_operlog(subject, payload)
            elif 'biodata'     in subject: self._process_biodata(subject, payload)
            elif 'errorlog'    in subject: self._process_errorlog(subject, payload)
            elif 'cmdresult'   in subject: self._process_cmdresult(subject, payload)
        except OperationalError:
            # Erreurs de concurrence/sérialisation (verrou FK sur zkteco_device
            # pendant un heartbeat concurrent) : on les laisse remonter pour que
            # le dispatcher NATS retente proprement la transaction.
            raise
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            # Payload « poison » : une donnée malformée qui ne réussira JAMAIS,
            # même rejouée. On logue et on avale (ack) pour ne pas boucler en
            # redelivery à l'infini.
            _logger.error(f"[zkteco_ta] payload invalide sur '{subject}' — ignoré: {exc}",
                          exc_info=True)
        except (MissingError, ValidationError, UserError) as exc:
            # Erreurs ORM DÉTERMINISTES (record supprimé/inexistant, contrainte
            # métier, garde utilisateur) : rejouer le même message donnera TOUJOURS
            # la même erreur → nak = tempête de redelivery infinie (bug observé sur
            # ATTLOG). On logue avec la trace complète et on ack. Le pointage brut
            # est déjà persisté côté attlog (_stage) → la journée reste ré-résoluble
            # (mapping PIN, cron _cron_resolve_recent) une fois la cause corrigée.
            _logger.error(f"[zkteco_ta] erreur déterministe ({type(exc).__name__}) sur "
                          f"'{subject}' — ackée pour éviter la boucle: {exc}",
                          exc_info=True)
        except Exception:
            # Erreur inattendue (infra/transitoire) : on la laisse remonter pour
            # que le dispatcher rollback + nak → JetStream redélivre. Avaler ici
            # acquittait un message en échec = perte silencieuse (l'ancien bug).
            _logger.error(f"[zkteco_ta] erreur inattendue sur '{subject}' — redelivery",
                          exc_info=True)
            raise

    # ── device heartbeat ──────────────────────────────────────────

    @api.model
    def _process_device_event(self, subject: str, payload: dict):
        sn = payload.get('SerialNumber') or subject.rsplit('.', 1)[-1]
        if not sn:
            return
        self.env['zkteco.device']._upsert_device(sn, info=payload)

    # ── attphoto — photo de vérification au pointage ──────────────

    @api.model
    def _process_attphoto(self, subject: str, payload: dict):
        self.env['zkteco.attphoto']._create_from_nats(payload)

    # ── userinfo — users depuis le device → sas ───────────────────

    @api.model
    def _process_userinfo(self, subject: str, payload: dict):
        """
        USER record depuis OPERLOG : atterrit dans zkteco.device.user (sas).
        Jamais de création directe dans hr.employee.
        """
        sn        = str(payload.get('SerialNumber', '')).strip()
        pin       = str(payload.get('PIN', '')).strip()
        name      = str(payload.get('Name', '')).strip()
        privilege = int(payload.get('Privilege', 0))
        card      = str(payload.get('Card', '')).strip()

        if not pin or not sn:
            return

        self.env['zkteco.device.user']._upsert(sn, pin, name, privilege, card)

    # ── attendance — routage direct ou quarantaine ────────────────

    @api.model
    def _process_attendance(self, subject: str, payload: dict):
        sn     = str(payload.get('SerialNumber', '')).strip()
        pin    = str(payload.get('UserID', '')).strip()
        ts_raw = payload.get('Timestamp', '')
        status = int(payload.get('Status', 0))
        verify = str(payload.get('VerifyMode', ''))

        if not pin or not ts_raw or not sn:
            _logger.warning(f"[zkteco_ta] champs manquants: {payload}")
            return

        device = self.env['zkteco.device']._upsert_device(sn)
        if device.state == 'rejected':
            return
        # For pending devices we still store the record in quarantine so it can
        # be replayed when an admin approves the device.  Silently dropping
        # would cause permanent data loss (NATS MaxAge=24h).

        try:
            ts = datetime.fromisoformat(ts_raw.replace('Z', '+00:00'))
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, AttributeError):
            _logger.warning(f"[zkteco_ta] Timestamp invalide '{ts_raw}'")
            return

        self.env['zkteco.device.attlog']._store_or_process(sn, pin, ts, status, verify)

    # ── operlog — BIOPHOTO et autres enregistrements ──────────────

    @api.model
    def _process_operlog(self, subject: str, payload: dict):
        sn       = str(payload.get('SerialNumber', '')).strip()
        rec_type = str(payload.get('Type', '')).strip()
        flds     = payload.get('Fields', {})

        if rec_type == 'BIOPHOTO':
            pin      = str(flds.get('pin', '')).strip()
            filename = str(flds.get('filename', '')).strip()
            content  = str(flds.get('content', '')).strip()
            if pin and content:
                self.env['zkteco.device.biophoto']._upsert(sn, pin, filename, content)

        elif rec_type == 'FP':
            # Template empreinte digitale (format OPERLOG ancien)
            pin       = str(flds.get('pin', '')).strip()
            finger_id = int(flds.get('fid', 0))
            template  = str(flds.get('tmp', '')).strip()
            valid     = str(flds.get('valid', '1')) == '1'
            if pin and template:
                self.env['zkteco.device.biodata']._upsert(
                    sn, pin, 1, finger_id, template, valid, 0)
                self._push_enroll_result(sn, pin, 1, finger_id, valid)

        elif rec_type == 'FACE':
            # Template visage (format OPERLOG ancien)
            pin      = str(flds.get('pin', '')).strip()
            template = str(flds.get('tmp', '')).strip()
            valid    = str(flds.get('valid', '1')) == '1'
            if pin and template:
                self.env['zkteco.device.biodata']._upsert(
                    sn, pin, 2, 0, template, valid, 0)
                self._push_enroll_result(sn, pin, 2, 0, valid)

    # ── cmdresult — retour de commande device ─────────────────────

    @api.model
    def _process_cmdresult(self, subject: str, payload: dict):
        sn          = str(payload.get('SerialNumber', '')).strip()
        return_code = int(payload.get('ReturnCode', 0))
        cmd         = str(payload.get('QueuedCommand', payload.get('Command', ''))).strip()
        bridge_id   = int(payload.get('ID', 0))
        client_uuid = str(payload.get('ClientCmdUUID', '')).strip()

        if not sn:
            return

        result_at = fields.Datetime.now()
        # -9999 = résultat synthétique du bridge (commande jamais confirmée par
        # le device : lease expiré ou purge). On ferme la ligne en 'expired',
        # pas en 'error' (ce n'est pas un rejet device).
        if return_code == _CMD_EXPIRED_RC:
            new_state = 'expired'
        else:
            new_state = 'ok' if return_code == 0 else 'error'

        # I-10 : refléter dans le sas (miroir zkteco.device.user) une suppression
        # user confirmée par le device, quel que soit le déclencheur (bouton,
        # désautorisation employé, WIPE). Avant, seul action_delete_from_device
        # posait 'deleted' → le miroir divergeait du device.
        if return_code == 0 and cmd:
            self._reflect_user_deletion(sn, cmd)

        # F-2 : un enrôlement rejeté par le device (Return≠0, hors -9999 expiré) →
        # prévenir le moniteur OWL avec un libellé Annexe 1. Avant, seul le succès
        # (arrivée d'un template) était signalé ; l'échec restait invisible.
        if return_code not in (0, _CMD_EXPIRED_RC) and cmd:
            self._push_enroll_cmd_failure(sn, cmd, return_code)

        # Balayage défensif, event-driven (pas de cron) : à chaque cmdresult on
        # ferme les commandes de CE device restées 'published'/'requested' au-delà
        # de 24h — le device ne les exécutera jamais (le bridge les a expirées).
        self._expire_stale_cmd_logs(sn)

        if client_uuid:
            log = self.env['zkteco.device.cmd.log'].sudo().search(
                [('cmd_uuid', '=', client_uuid)], limit=1
            )
            if log:
                log.write({
                    'state':         new_state,
                    'return_code':   return_code,
                    'wire_cmd':      cmd[:512],
                    'bridge_cmd_id': bridge_id,
                    'result_at':     result_at,
                })
                if return_code != 0 and new_state == 'error':
                    _logger.warning(
                        f"[zkteco_ta] commande rejetée par {sn}: return={return_code} cmd={cmd[:80]}"
                    )
                return

        # Les réponses INFO orphelines (ack de heartbeat, return=0, aucune
        # commande à tracer) sont du pur bruit : on ne les journalise pas. Ça
        # réduit drastiquement la contention FK sur zkteco_device.
        if not cmd or (cmd.upper() == 'INFO' and return_code == 0):
            return

        # No UUID match (bridge restart, manual command, legacy) — create orphan record.
        device = self.env['zkteco.device'].sudo().search(
            [('serial_number', '=', sn)], limit=1
        )
        self.env['zkteco.device.cmd.log'].sudo().create({
            'device_id':    device.id if device else False,
            'cmd':          cmd[:512],
            'wire_cmd':     cmd[:512],
            'bridge_cmd_id': bridge_id,
            'return_code':  return_code,
            'state':        new_state,
            'result_at':    result_at,
        })
        if return_code != 0:
            _logger.warning(
                f"[zkteco_ta] commande rejetée par {sn}: return={return_code} cmd={cmd[:80]}"
            )

    @api.model
    def _expire_stale_cmd_logs(self, sn):
        """Ferme en 'expired' les commandes de ce device restées ouvertes
        au-delà de _CMD_LOG_MAX_AGE_HOURS. Event-driven (appelé sur cmdresult),
        borné, sans cron — filet de sécurité au cas où le bridge ne remonte pas
        le résultat 'expired' (ex. bridge redémarré avant l'expiration)."""
        cutoff = fields.Datetime.now() - timedelta(hours=_CMD_LOG_MAX_AGE_HOURS)
        stale = self.env['zkteco.device.cmd.log'].sudo().search([
            ('device_id.serial_number', '=', sn),
            ('state', 'in', ('requested', 'published')),
            ('create_date', '<', cutoff),
        ], limit=200)
        if stale:
            stale.write({'state': 'expired', 'result_at': fields.Datetime.now()})
            _logger.info("[zkteco_ta] %d commande(s) expirée(s) pour %s", len(stale), sn)

    @api.model
    def _reflect_user_deletion(self, sn, cmd):
        """(I-10) Sur un `DATA DELETE USERINFO` confirmé (Return=0), passe le(s)
        user(s) miroir de CE device en 'deleted'. Avec PIN → un user ; sans PIN
        → tous les users du device (spec 12.1.2.1)."""
        if not _DELETE_USER_RE.search(cmd):
            return
        domain = [('device_id.serial_number', '=', sn), ('state', '!=', 'deleted')]
        m = _PIN_RE.search(cmd)
        if m:
            domain.append(('pin', '=', m.group(1)))
        users = self.env['zkteco.device.user'].sudo().search(domain)
        if users:
            users.write({'state': 'deleted'})
            _logger.info(
                "[zkteco_ta] miroir user: %d passé(s) 'deleted' sur %s "
                "(suppression device confirmée)", len(users), sn)

    # ── errorlog — motifs d'échec device (F-1) ────────────────────

    @api.model
    def _process_errorlog(self, subject: str, payload: dict):
        """(I-4 / F-1) Un ERRORLOG device (ex. D01E0001 « face detection failed »,
        Annexe 9). On le corrèle à la commande d'origine via CmdID = bridge_cmd_id,
        on marque la ligne cmd.log en erreur avec le détail, et — si c'était un
        enrôlement — on prévient le moniteur OWL (F-2)."""
        sn       = str(payload.get('SerialNumber', '')).strip()
        err_code = str(payload.get('ErrCode', '')).strip()
        err_msg  = str(payload.get('ErrMsg', '')).strip()
        cmd_id   = payload.get('CmdID') or payload.get('CmdId')
        if not sn:
            return
        detail = (f"{err_code} {err_msg}").strip() or err_code or err_msg

        log = None
        try:
            bid = int(cmd_id)
        except (ValueError, TypeError):
            bid = 0
        if bid:
            log = self.env['zkteco.device.cmd.log'].sudo().search([
                ('bridge_cmd_id', '=', bid),
                ('device_id.serial_number', '=', sn),
            ], limit=1)
        if log:
            log.write({
                'state':        'error',
                'error_detail': detail[:512],
                'result_at':    fields.Datetime.now(),
            })
        _logger.warning("[zkteco_ta] ERRORLOG %s sur %s: %s (cmd_id=%s)",
                        err_code or '?', sn, err_msg or '-', cmd_id or '-')
        self._push_enroll_error(sn, log, detail)

    @api.model
    def _push_enroll_error(self, sn, log, detail):
        """F-2 : si l'ERRORLOG concerne une commande d'enrôlement, pousse un échec
        sur le bus 'zkteco_enroll' pour que le dialog OWL bascule en échec avec le
        motif device. Best-effort : silencieux si non corrélé à un enrôlement."""
        if not log or not log.cmd:
            return
        verb = (log.cmd.split() or [''])[0].upper()
        if not verb.startswith('ENROLL'):
            return
        m = _PIN_RE.search(log.cmd)
        pin = m.group(1) if m else ''
        self.env['bus.bus']._sendone('zkteco_enroll', 'zkteco_enroll_result', {
            'serial_number': sn,
            'pin':           pin,
            'valid':         False,
            'reason':        detail or 'échec',
        })

    @api.model
    def _push_enroll_cmd_failure(self, sn, cmd, return_code):
        """F-2 : un cmdresult d'enrôlement (ENROLL_*) avec Return≠0 → échec au
        moniteur OWL avec un libellé Annexe 1. Silencieux si ce n'est pas un enrôlement."""
        verb = (cmd.split() or [''])[0].upper()
        if not verb.startswith('ENROLL'):
            return
        m = _PIN_RE.search(cmd)
        pin = m.group(1) if m else ''
        self.env['bus.bus']._sendone('zkteco_enroll', 'zkteco_enroll_result', {
            'serial_number': sn,
            'pin':           pin,
            'valid':         False,
            'reason':        _enroll_rc_label(return_code),
        })

    # ── biodata — templates empreintes / visages ──────────────────

    @api.model
    def _process_biodata(self, subject: str, payload: dict):
        sn        = str(payload.get('SerialNumber', '')).strip()
        pin       = str(payload.get('PIN', '')).strip()
        bio_type  = int(payload.get('Type', 0))
        finger_id = int(payload.get('No', 0))
        template  = payload.get('Template', '')
        valid     = bool(payload.get('Valid', False))
        fmt       = int(payload.get('Format', 0))

        # 1=empreinte, 2=visage NIR, 8=paume, 9=visage VL
        if not pin or bio_type not in (1, 2, 8, 9):
            return

        major_ver = int(payload.get('MajorVer', 0))
        minor_ver = int(payload.get('MinorVer', 0))

        self.env['zkteco.device.biodata']._upsert(
            sn, pin, bio_type, finger_id, template, valid, fmt, major_ver, minor_ver)
        self._push_enroll_result(sn, pin, bio_type, finger_id, valid)

    # ── enrôlement : retour live vers le dialog OWL (bus) ─────────

    _ENROLL_FINGER_NAMES = {
        0: 'Auriculaire G', 1: 'Annulaire G', 2: 'Majeur G', 3: 'Index G', 4: 'Pouce G',
        5: 'Pouce D', 6: 'Index D', 7: 'Majeur D', 8: 'Annulaire D', 9: 'Auriculaire D',
    }

    @api.model
    def _push_enroll_result(self, sn, pin, bio_type, finger_id, valid):
        """Pousse le résultat d'un enrôlement biométrique sur le bus.

        Le dialog OWL `zkteco_enroll_monitor` (lancé par le wizard) écoute le
        canal 'zkteco_enroll' et bascule en succès / échec dès réception.
        """
        pin = str(pin or '').strip()
        if not pin:
            return
        bio_type  = int(bio_type or 0)
        finger_id = int(finger_id or 0)
        if bio_type == 1:
            label = self._ENROLL_FINGER_NAMES.get(finger_id, f'Doigt {finger_id}')
        elif bio_type == 8:
            label = {0: 'Paume gauche', 1: 'Paume droite'}.get(finger_id, 'Paume')
        else:
            label = 'Visage'
        employee = self.env['zkteco.device.user'].sudo().search([
            ('device_id.serial_number', '=', sn),
            ('pin', '=', pin),
        ], limit=1).employee_id
        self.env['bus.bus']._sendone('zkteco_enroll', 'zkteco_enroll_result', {
            'serial_number': sn,
            'pin':           pin,
            'bio_type':      bio_type,
            'finger_id':     finger_id,
            'valid':         bool(valid),
            'finger_label':  label,
            'employee_name': employee.name or '',
        })