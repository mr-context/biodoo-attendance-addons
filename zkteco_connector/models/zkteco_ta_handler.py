# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta, timezone

from psycopg2 import OperationalError

from odoo import models, api, fields

_logger = logging.getLogger(__name__)

# Doit correspondre à adms.ExpiredReturnCode côté bridge Go.
_CMD_EXPIRED_RC = -9999
# Durée après laquelle une commande jamais confirmée est considérée expirée.
_CMD_LOG_MAX_AGE_HOURS = 24


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