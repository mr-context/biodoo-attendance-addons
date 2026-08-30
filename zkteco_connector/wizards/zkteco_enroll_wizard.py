# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# NB : l'enrôlement visage ne passe PLUS par ce wizard. Il se fait via le
# bouton caméra (zkteco.enroll.face.wizard) qui capture une photo et l'upload.
ENROLL_TYPES = [
    ('sync',   'Sync données (nom, PIN, carte)'),
    ('fp',     'Empreinte digitale'),
    ('card',   'Carte / Badge'),
    ('remove', 'Supprimer du device'),
]

FINGER_LABELS = [
    # Left hand (FID 0-4): 0=auriculaire → 4=pouce (spec officielle ZKTeco v5.4)
    (0,  'Auriculaire gauche'),
    (1,  'Annulaire gauche'),
    (2,  'Majeur gauche'),
    (3,  'Index gauche'),
    (4,  'Pouce gauche'),
    # Right hand (FID 5-9): 5=pouce → 9=auriculaire
    (5,  'Pouce droit'),
    (6,  'Index droit'),
    (7,  'Majeur droit'),
    (8,  'Annulaire droit'),
    (9,  'Auriculaire droit'),
]


class ZktecoEnrollWizard(models.TransientModel):
    _name = 'zkteco.enroll.wizard'
    _description = 'ZKTeco Enrollment Wizard'

    employee_id = fields.Many2one(
        'hr.employee', string='Employé', required=True, readonly=True,
    )
    enroll_type = fields.Selection(
        ENROLL_TYPES,
        string='Type d\'enrôlement',
        required=True,
        default='sync',
    )
    finger_index = fields.Selection(
        [(str(i), lbl) for i, lbl in FINGER_LABELS],
        string='Doigt',
        default='1',
    )
    card_number = fields.Char(string='N° de carte / Badge')
    overwrite = fields.Boolean(
        string='Écraser si existant',
        default=True,
    )

    all_devices = fields.Boolean(string='Tous les devices approuvés', default=True)
    device_ids = fields.Many2many(
        'zkteco.device',
        string='Devices',
        domain=[('state', 'in', ('approved', 'offline'))],
    )

    # Display
    employee_name = fields.Char(related='employee_id.name', readonly=True)
    employee_pin  = fields.Char(related='employee_id.zkteco_pin', readonly=True)

    @api.onchange('all_devices')
    def _onchange_all_devices(self):
        if self.all_devices:
            self.device_ids = self.env['zkteco.device'].search(
                [('state', 'in', ('approved', 'offline'))]
            )

    def action_confirm(self):
        self.ensure_one()

        if not self.employee_id.zkteco_pin:
            raise UserError(
                f"L'employé {self.employee_id.name} n'a pas de ZKTeco PIN.\n"
                "Renseignez-le dans l'onglet ZKTeco de la fiche employé avant l'enrôlement."
            )

        devices = (
            self.env['zkteco.device'].search([('state', 'in', ('approved', 'offline'))])
            if self.all_devices
            else self.device_ids
        )
        if not devices:
            raise UserError(
                "Aucun device approuvé sélectionné.\n"
                "Approuvez d'abord un device dans l'écran ZKTeco > Devices."
            )

        from odoo.addons.core_nats.services.nats_service import get_service
        svc = get_service()
        if not svc:
            raise UserError(
                "Le service NATS n'est pas démarré. "
                "Démarrez-le d'abord depuis le menu ZKTeco."
            )

        pin  = str(self.employee_id.zkteco_pin).strip()
        cmds = self._build_commands(pin)

        count = 0
        for device in devices:
            for cmd in cmds:
                device._send_command(cmd)
                _logger.info(f"[zkteco] enroll {self.enroll_type} {self.employee_id.name} → {device.serial_number}: {cmd[:60]}")
            # Top-down : on connaît l'employé → mirror sas lié immédiatement
            # (sauf suppression, où on retire l'user du device).
            if self.enroll_type != 'remove':
                self.env['zkteco.device.user'].sudo()._upsert(
                    device.serial_number, pin, self.employee_id.name,
                    int(self.employee_id.zkteco_privilege or 0),
                    (self.card_number or '').strip(), employee=self.employee_id)
            count += 1

        # Empreinte : on ouvre le moniteur live qui scanne et attend le retour
        # du template (poussé par zkteco.ta.handler sur le bus).
        if self.enroll_type == 'fp':
            fid = int(self.finger_index or 1)
            return {
                'type': 'ir.actions.client',
                'tag': 'zkteco_enroll_monitor',
                'params': {
                    'pin':          pin,
                    'fid':          fid,
                    'bioType':      1,
                    'fingerLabel':  dict(FINGER_LABELS).get(fid, ''),
                    'employeeName': self.employee_id.name,
                    'deviceNames':  devices.mapped('display_name'),
                },
            }

        label = dict(ENROLL_TYPES).get(self.enroll_type, self.enroll_type)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': f'{label} lancé',
                'message': f'{self.employee_id.name} → {count} device(s)',
                'type': 'success' if self.enroll_type != 'remove' else 'warning',
                'sticky': False,
            },
        }

    def _build_commands(self, pin: str) -> list[str]:
        emp  = self.employee_id
        t    = self.enroll_type
        over = '1' if self.overwrite else '0'

        if t == 'sync':
            return [self.env['zkteco.device']._build_enroll_user_cmd(emp)]

        if t == 'fp':
            idx = str(self.finger_index or '1')
            return [
                self.env['zkteco.device']._build_enroll_user_cmd(emp),
                f'ENROLL_FP PIN={pin} FID={idx} RETRY=3 OVERWRITE={over}',
            ]

        if t == 'card':
            card = (self.card_number or '').strip()
            if not card:
                return [
                    self.env['zkteco.device']._build_enroll_user_cmd(emp),
                    f'ENROLL_MF PIN={pin} RETRY=3',
                ]
            return [self.env['zkteco.device']._build_enroll_user_cmd(emp, card=card)]

        if t == 'remove':
            return [f'DELETE_USER PIN={pin}']

        return []
