# -*- coding: utf-8 -*-
import base64
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    _zkteco_pin_unique = models.Constraint(
        'UNIQUE(zkteco_pin)',
        'Le PIN ZKTeco doit être unique (un seul employé par UserID device).',
    )

    zkteco_pin = fields.Char(
        string='ZKTeco PIN',
        index=True,
        copy=False,
        help='UserID on the ZKTeco device (ADMS UserID field). Must be unique.',
    )
    zkteco_privilege = fields.Selection(
        [
            ('0', 'Utilisateur'),
            ('2', 'Enrôleur'),
            ('6', 'Administrateur'),
            ('14', 'Super Admin'),
        ],
        string='Niveau de droit ZKTeco',
        default='0',
        required=True,
        copy=False,
        help="Privilège appliqué sur TOUTES les pointeuses autorisées (champ Privilege ADMS) :\n"
             "• Utilisateur : pointe uniquement\n"
             "• Enrôleur : peut enregistrer de nouveaux users sur le device\n"
             "• Administrateur : accès au menu du device\n"
             "• Super Admin : contrôle total du device",
    )
    zkteco_last_seen = fields.Datetime(
        string='Dernier pointage ZKTeco',
        readonly=True,
        copy=False,
    )
    zkteco_linked = fields.Boolean(
        compute='_compute_zkteco_linked',
        store=True,
        string='Lié ZKTeco',
    )
    zkteco_authorized_device_ids = fields.Many2many(
        'zkteco.device',
        'hr_employee_zkteco_device_rel',
        'employee_id', 'device_id',
        string='Pointeuses autorisées',
        help='L\'employé est enrôlé uniquement sur ces pointeuses. Ajouter une pointeuse déclenche la synchronisation biométrique automatiquement.',
    )
    zkteco_available_device_ids = fields.Many2many(
        'zkteco.device',
        'hr_employee_zkteco_avail_rel',   # différent de hr_employee_zkteco_device_rel
        'employee_id', 'device_id',
        compute='_compute_available_devices',
        string='Pointeuses disponibles',
    )
    zkteco_device_user_ids = fields.One2many(
        'zkteco.device.user', 'employee_id',
        string="Enrôlements par pointeuse",
        readonly=True,
        help="État d'enrôlement biométrique de l'employé sur chaque pointeuse "
             "(empreintes / paume / visage). La biométrie est propre à chaque device.",
    )

    biometric_matrix_html = fields.Html(
        compute='_compute_biometric_matrix',
        string='Biométrie réelle par pointeuse',
        sanitize=False,
        help="Vérité terrain : ce qui est RÉELLEMENT enrôlé sur chaque pointeuse "
             "(la biométrie est propre à chaque device). Les pointeuses sans "
             "biométrie sont masquées.",
    )

    @api.depends('zkteco_pin')
    def _compute_zkteco_linked(self):
        for emp in self:
            emp.zkteco_linked = bool(emp.zkteco_pin)

    # ── Matrice biométrique (pointeuse × biométrie) ───────────────

    _MATRIX_FINGERS = [
        (0, 'AuG'), (1, 'AnG'), (2, 'MaG'), (3, 'InG'), (4, 'PoG'),
        (5, 'PoD'), (6, 'InD'), (7, 'MaD'), (8, 'AnD'), (9, 'AuD'),
    ]

    @api.depends(
        'zkteco_pin',
        'zkteco_device_user_ids.biodata_ids.bio_type',
        'zkteco_device_user_ids.biodata_ids.finger_id',
        'zkteco_device_user_ids.biodata_ids.valid',
        'zkteco_authorized_device_ids',
    )
    def _compute_biometric_matrix(self):
        DOT = ('<span style="display:inline-block;width:12px;height:12px;'
               'border-radius:50%;vertical-align:middle;{bg}"></span>')
        styles = {
            'ok':   'background:#22c55e;',
            'bad':  'background:#f59e0b;',
            None:   'background:transparent;border:1px solid #cbd5e1;',
        }

        def dot(state):
            return DOT.format(bg=styles[state])

        for emp in self:
            if not emp.zkteco_pin:
                emp.biometric_matrix_html = False
                continue

            device_users = emp.zkteco_device_user_ids
            rows_du = device_users.filtered(lambda du: du.biodata_ids)
            empty_du = device_users - rows_du
            authorized_wo_du = (
                emp.zkteco_authorized_device_ids - device_users.mapped('device_id'))
            empty_count = len(empty_du) + len(authorized_wo_du)
            denom = len(rows_du) + empty_count

            if not rows_du:
                emp.biometric_matrix_html = (
                    '<div style="color:#64748b;font-style:italic">'
                    'Aucune biométrie enrôlée sur les %s pointeuse(s) autorisée(s). '
                    'Lancez un enrôlement depuis une pointeuse.</div>' % denom
                )
                continue

            cols = [lbl for _fid, lbl in emp._MATRIX_FINGERS] + ['PaG', 'PaD', 'Vis']
            totals = [0] * len(cols)

            ths = ''.join(
                f'<th style="padding:4px 5px;font-size:10px;color:#64748b;'
                f'font-weight:600;text-align:center">{c}</th>' for c in cols
            )
            header = (
                '<tr>'
                '<th style="padding:4px 8px;text-align:left;font-size:11px;'
                'color:#64748b">Pointeuse</th>'
                + ths +
                '<th style="padding:4px 8px;font-size:10px;color:#64748b">Tot.</th>'
                '</tr>'
            )

            body = ''
            for du in rows_du.sorted(lambda d: d.device_id.display_name or ''):
                bio = du.biodata_ids
                fp_valid = {b.finger_id for b in bio if b.bio_type == '1' and b.valid}
                fp_all   = {b.finger_id for b in bio if b.bio_type == '1'}
                palm_valid = {b.finger_id for b in bio if b.bio_type == '8' and b.valid}
                palm_all   = {b.finger_id for b in bio if b.bio_type == '8'}
                face_valid = any(b.bio_type in ('2', '9') and b.valid for b in bio)
                face_all   = any(b.bio_type in ('2', '9') for b in bio)

                cells = ''
                ci = 0

                def cell(valid_set, all_set, key, _ci):
                    if key in valid_set:
                        totals[_ci] += 1
                        return 'ok'
                    if key in all_set:
                        totals[_ci] += 1
                        return 'bad'
                    return None

                for fid, _lbl in emp._MATRIX_FINGERS:
                    st = cell(fp_valid, fp_all, fid, ci)
                    cells += f'<td style="text-align:center;padding:3px">{dot(st)}</td>'
                    ci += 1
                for pid in (0, 1):
                    st = cell(palm_valid, palm_all, pid, ci)
                    cells += f'<td style="text-align:center;padding:3px">{dot(st)}</td>'
                    ci += 1
                # visage
                if face_valid:
                    st = 'ok'; totals[ci] += 1
                elif face_all:
                    st = 'bad'; totals[ci] += 1
                else:
                    st = None
                cells += f'<td style="text-align:center;padding:3px">{dot(st)}</td>'

                body += (
                    '<tr style="border-top:1px solid #f1f5f9">'
                    '<td style="padding:4px 8px;font-size:12px;white-space:nowrap">'
                    '<i class="fa fa-clock-o" style="color:#94a3b8;margin-right:4px"></i>'
                    f'{du.device_id.display_name or ""}</td>'
                    + cells +
                    '<td style="text-align:center;font-size:11px;font-weight:600;'
                    f'color:#15803d">{du.biodata_count}</td>'
                    '</tr>'
                )

            tcells = ''.join(
                '<td style="text-align:center;font-size:10px;color:#475569;'
                f'font-weight:600">{t}/{denom}</td>' for t in totals
            )
            footer = (
                '<tr style="border-top:2px solid #e2e8f0">'
                '<td style="padding:4px 8px;font-size:10px;color:#64748b;'
                'font-weight:600">Présent sur</td>'
                + tcells + '<td></td></tr>'
            )

            legend = (
                '<div style="margin-bottom:6px;font-size:11px;color:#64748b">'
                f'{dot("ok")} valide &#160;&#160; {dot("bad")} présent (invalide) '
                f'&#160;&#160; {dot(None)} absent</div>'
            )
            note = ''
            if empty_count:
                note = (
                    '<div style="margin-top:6px;font-size:11px;color:#94a3b8">'
                    f'+ {empty_count} pointeuse(s) autorisée(s) sans biométrie '
                    '(masquées).</div>'
                )

            emp.biometric_matrix_html = (
                legend +
                '<div style="overflow-x:auto"><table style="border-collapse:collapse;'
                'width:100%;font-family:ui-sans-serif,system-ui,sans-serif">'
                '<thead>' + header + '</thead>'
                '<tbody>' + body + footer + '</tbody>'
                '</table></div>'
                + note
            )

    @api.depends('zkteco_authorized_device_ids')
    def _compute_available_devices(self):
        all_approved = self.env['zkteco.device'].sudo().search([
            ('state', 'in', ('approved', 'offline')),
        ])
        for emp in self:
            emp.zkteco_available_device_ids = all_approved - emp.zkteco_authorized_device_ids

    # ── attribution du PIN canonique (modèle BioTime : Odoo = maître) ──

    @api.model
    def _next_zkteco_pin(self):
        """Retourne le prochain PIN libre = max(PINs employés ∪ PINs sas) + 1.

        On considère À LA FOIS les zkteco_pin déjà attribués aux employés ET tous
        les zkteco.device.user.pin remontés des pointeuses (le sas contient déjà
        tous les PIN existant physiquement sur les devices). Garantit un empcode
        libre PARTOUT sans interroger les pointeuses en direct.
        """
        used = {0}
        self.env.cr.execute(
            "SELECT zkteco_pin FROM hr_employee WHERE zkteco_pin ~ '^[0-9]+$'")
        used |= {int(r[0]) for r in self.env.cr.fetchall()}
        for p in self.env['zkteco.device.user'].sudo().search([]).mapped('pin'):
            if p and str(p).isdigit():
                used.add(int(p))
        return str(max(used) + 1)

    @api.model_create_multi
    def create(self, vals_list):
        reserved = None
        for vals in vals_list:
            if not vals.get('zkteco_pin'):
                # incrémente dans le lot pour éviter les collisions multi-create
                reserved = (int(self._next_zkteco_pin()) if reserved is None
                            else reserved + 1)
                vals['zkteco_pin'] = str(reserved)
        return super().create(vals_list)

    # ── write override — détecte les changements d'autorisation ──

    def write(self, vals):
        old_device_sets = {}
        if 'zkteco_authorized_device_ids' in vals:
            for emp in self:
                old_device_sets[emp.id] = set(emp.zkteco_authorized_device_ids.ids)

        result = super().write(vals)

        if 'zkteco_authorized_device_ids' in vals:
            for emp in self:
                old_ids = old_device_sets.get(emp.id, set())
                new_ids = set(emp.zkteco_authorized_device_ids.ids)
                added   = new_ids - old_ids
                removed = old_ids - new_ids

                for device_id in added:
                    device = self.env['zkteco.device'].browse(device_id)
                    emp._sync_to_device(device, soft=True)

                for device_id in removed:
                    device = self.env['zkteco.device'].browse(device_id)
                    if emp.zkteco_pin:
                        device._send_command(f'DELETE_USER PIN={emp.zkteco_pin}', soft=True)
                        _logger.info(f'[zkteco] auto-DELETE_USER PIN={emp.zkteco_pin} sur {device.serial_number}')

        # Changement de niveau de droit → re-pousse la fiche user (Privilege)
        # sur toutes les pointeuses autorisées. Inutile de re-sync la biométrie.
        if 'zkteco_privilege' in vals:
            for emp in self:
                if not emp.zkteco_pin:
                    continue
                for device in emp.zkteco_authorized_device_ids:
                    device._send_command(device._build_enroll_user_cmd(emp), soft=True)
                    _logger.info(
                        f'[zkteco] auto-ENROLL_USER (privilège={emp.zkteco_privilege}) '
                        f'PIN={emp.zkteco_pin} sur {device.serial_number}'
                    )

        return result

    # ── synchronisation biométrique vers un device ────────────────

    def _sync_to_device(self, device, soft=False):
        """Enrôle l'employé sur le device et pousse les données biométriques disponibles.

        Stratégie (§3.1 spec ZKTeco PUSH) :
        - Toujours : ENROLL_USER (crée/met à jour la fiche utilisateur)
        - Empreintes (type 1) : SYNC_BIODATA si version algo compatible
        - Visage (type 2/9)   : SYNC_BIODATA si version algo compatible,
                                sinon SYNC_BIOPHOTO+PostBackTmpFlag=1 si biophoto disponible

        soft=True (auto-sync déclenché par une écriture RH) : une panne NATS ne
        lève pas — les commandes partent en outbox (voir _send_command)."""
        self.ensure_one()
        if not self.zkteco_pin:
            return

        # 1. Enrôler la fiche utilisateur + créer le mirror sas lié à l'employé
        #    IMMÉDIATEMENT (top-down : ça vient d'Odoo, on connaît déjà l'employé).
        device._send_command(device._build_enroll_user_cmd(self), soft=soft)
        self.env['zkteco.device.user'].sudo()._upsert(
            device.serial_number, self.zkteco_pin, self.name,
            int(self.zkteco_privilege or 0), '', employee=self)
        _logger.info(f'[zkteco] auto-ENROLL_USER PIN={self.zkteco_pin} sur {device.serial_number}')

        # 2. Chercher les données biométriques existantes de l'employé
        all_biodata = self.env['zkteco.device.biodata'].sudo().search([
            ('employee_id', '=', self.id),
        ])
        biophoto = self.env['zkteco.device.biophoto'].sudo().search([
            ('employee_id', '=', self.id),
        ], limit=1)

        for bd in all_biodata:
            bio_type = int(bd.bio_type)
            target_algo = device._get_bio_algo_version(bio_type)
            # Certains devices uploadent le template sans MajorVer (=0). On retombe
            # alors sur l'algo déclaré par le device SOURCE pour ce type biométrique
            # (ex. empreinte FaceDepot = 10.0 même si le BIODATA remontait MajorVer=0).
            source_algo = bd.major_ver
            if not source_algo and bd.device_user_id.device_id:
                source_algo = bd.device_user_id.device_id._get_bio_algo_version(bio_type)

            # Vérifie la compatibilité algo (0 = inconnu → pas de garantie)
            algo_compat = (source_algo > 0 and target_algo > 0 and source_algo == target_algo)

            if algo_compat and bd.template:
                tmpl = bd.template
                tmpl_b64 = tmpl.decode() if isinstance(tmpl, bytes) else (tmpl or '')
                if not tmpl_b64:
                    continue
                cmd = (
                    f'SYNC_BIODATA PIN={self.zkteco_pin} TYPE={bio_type} '
                    f'NO={bd.finger_id} INDEX=0 VALID=1 '
                    f'MAJOR={source_algo} MINOR={bd.minor_ver} '
                    f'FMT={bd.format_code} TMP={tmpl_b64}'
                )
                device._send_command(cmd, soft=soft)
                _logger.info(
                    f'[zkteco] SYNC_BIODATA PIN={self.zkteco_pin} type={bio_type} '
                    f'no={bd.finger_id} sur {device.serial_number}'
                )

            elif bio_type in (2, 9) and biophoto and biophoto.photo:
                # Visage : utilise la biophoto pour une re-génération côté device
                photo = biophoto.photo
                photo_b64 = photo.decode() if isinstance(photo, bytes) else (photo or '')
                if not photo_b64:
                    continue
                try:
                    photo_size = len(base64.b64decode(photo_b64))
                except Exception:
                    continue
                cmd = (
                    f'SYNC_BIOPHOTO PIN={self.zkteco_pin} TYPE={bio_type} '
                    f'NO=0 INDEX=0 SIZE={photo_size} '
                    f'CONTENT={photo_b64} POSTBACK=1'
                )
                device._send_command(cmd, soft=soft)
                _logger.info(
                    f'[zkteco] SYNC_BIOPHOTO PIN={self.zkteco_pin} type={bio_type} '
                    f'size={photo_size} sur {device.serial_number}'
                )
            else:
                _logger.info(
                    f'[zkteco] sync skip PIN={self.zkteco_pin} type={bio_type} '
                    f'no={bd.finger_id} sur {device.serial_number} '
                    f'(algo incompatible source={source_algo} target={target_algo}, pas de biophoto)'
                )

        # 3. Demander au device de renvoyer ce qu'il a RÉELLEMENT gardé.
        # Le miroir local (panneau "Biométrie enrôlée") reflète ainsi la vérité
        # du device, pas ce qu'on a poussé : le device POST sa BIODATA en retour
        # → _process_biodata → upsert. La commande est mise en file APRÈS les
        # SYNC_* donc le device les traite avant de répondre.
        device._send_command(f'QUERY_BIODATA PIN={self.zkteco_pin}', soft=soft)
        _logger.info(f'[zkteco] QUERY_BIODATA PIN={self.zkteco_pin} sur {device.serial_number}')

    # ── enrollment actions ────────────────────────────────────────

    def action_zkteco_enroll_face(self):
        """Ouvre le wizard d'enrôlement visage (webcam → SYNC_BIOPHOTO)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enrôlement visage',
            'res_model': 'zkteco.enroll.face.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_employee_id': self.id},
        }

    def action_zkteco_enroll(self):
        """Ouvre le wizard d'enrôlement (fp / face / card au choix)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Enrôlement ZKTeco',
            'res_model': 'zkteco.enroll.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_employee_id': self.id,
                'default_enroll_type': 'fp',
            },
        }

    def action_zkteco_sync(self):
        """Sync données — envoie DATA UPDATE USERINFO directement, sans wizard."""
        self.ensure_one()
        if not self.zkteco_pin:
            return
        devices = self.zkteco_authorized_device_ids or self.env['zkteco.device'].sudo().search(
            [('state', 'in', ('approved', 'offline'))]
        )
        for device in devices:
            device._send_command(device._build_enroll_user_cmd(self))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sync envoyé',
                'message': f'{self.name} → {len(devices)} device(s).',
                'type': 'success',
                'sticky': False,
            },
        }

    def action_zkteco_remove(self):
        """Supprime l'employé des devices autorisés."""
        self.ensure_one()
        if not self.zkteco_pin:
            return
        devices = self.zkteco_authorized_device_ids or self.env['zkteco.device'].sudo().search(
            [('state', 'in', ('approved', 'offline'))]
        )
        for device in devices:
            device._send_command(f'DELETE_USER PIN={self.zkteco_pin}')
        if self.zkteco_authorized_device_ids:
            self.env.cr.execute(
                'DELETE FROM hr_employee_zkteco_device_rel WHERE employee_id = %s',
                (self.id,)
            )
            self.invalidate_recordset(['zkteco_authorized_device_ids'])
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Suppression envoyée',
                'message': f'{self.name} supprimé de {len(devices)} device(s).',
                'type': 'warning',
                'sticky': False,
            },
        }


class HrEmployeePublic(models.Model):
    _inherit = 'hr.employee.public'

    zkteco_pin    = fields.Char(readonly=True)
    zkteco_linked = fields.Boolean(readonly=True)
