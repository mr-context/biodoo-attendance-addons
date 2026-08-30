# -*- coding: utf-8 -*-
import pytz

from odoo import models, fields, api

# Correspondance ADMS autoritaire (alignée sur le bridge Go adms/types.go).
VERIFY_MODE_LABELS = {
    0:  'Mot de passe',
    1:  'Empreinte',
    2:  'Carte',                         # code carte legacy de certains firmwares
    3:  'Mot de passe',                  # code mdp alternatif
    4:  'Carte',                         # code carte ADMS principal
    5:  'Empreinte + Carte',
    6:  'Empreinte + Mot de passe',
    7:  'Carte + Mot de passe',
    8:  'Carte + Empreinte + Mot de passe',
    9:  'Autre',
    15: 'Visage',
    25: 'Paume',
}


def _verify_label(code):
    """Libellé lisible d'un code de vérification ADMS (jamais vide si code posé)."""
    if code in (None, '', False):
        return ''
    try:
        return VERIFY_MODE_LABELS.get(int(code), f'Inconnu ({code})')
    except (ValueError, TypeError):
        return f'Inconnu ({code})'


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    # Ajoute "Pointeuse ZKTeco" aux modes natifs (kiosk/systray/manual/technical)
    # pour que la présence ne soit plus marquée "Manuel" alors qu'elle vient du device.
    in_mode = fields.Selection(
        selection_add=[('zkteco', 'Pointeuse ZKTeco')],
        ondelete={'zkteco': 'set default'})
    out_mode = fields.Selection(
        selection_add=[('zkteco', 'Pointeuse ZKTeco')],
        ondelete={'zkteco': 'set default'})

    zkteco_transaction_id = fields.Char(
        string='ZKTeco Transaction ID',
        readonly=True,
        copy=False,
        index=True,
        help='Deduplication key: SerialNumber + Timestamp + UserID.',
    )
    zkteco_device_sn = fields.Char(
        string='Device S/N',
        readonly=True,
        copy=False,
        index=True,
    )
    zkteco_device_id = fields.Many2one(
        'zkteco.device',
        string='Pointeuse',
        readonly=True,
        copy=False,
        index=True,
        help="Appareil ZKTeco d'où provient le pointage.",
    )
    zkteco_device_name = fields.Char(
        string='Nom pointeuse (entrée)',
        related='zkteco_device_id.device_name',
        readonly=True,
    )
    # Sortie = événement biométrique distinct (peut être un autre device / mode).
    zkteco_out_device_id = fields.Many2one(
        'zkteco.device',
        string='Pointeuse (sortie)',
        readonly=True,
        copy=False,
        help="Appareil ZKTeco d'où provient le pointage de sortie.",
    )
    zkteco_out_device_name = fields.Char(
        string='Nom pointeuse (sortie)',
        related='zkteco_out_device_id.device_name',
        readonly=True,
    )
    zkteco_out_verify_mode = fields.Char(
        string='Code vérification (sortie)', readonly=True, copy=False)
    # Touche réellement pressée (Entrée / Sortie / Pause / Heures sup…).
    zkteco_in_operation = fields.Char(
        string='Opération (entrée)',
        readonly=True,
        copy=False,
        help="Touche pressée à l'entrée sur la pointeuse.",
    )
    zkteco_out_operation = fields.Char(
        string='Opération (sortie)',
        readonly=True,
        copy=False,
        help="Touche pressée à la sortie sur la pointeuse.",
    )
    # Code brut envoyé par le device (open-ended) + libellé calculé robuste.
    zkteco_verify_mode = fields.Char(
        string='Code vérification', readonly=True, copy=False)
    zkteco_verify_label = fields.Char(
        string='Vérification', compute='_compute_verify_labels')
    zkteco_out_verify_label = fields.Char(
        string='Vérification (sortie)', compute='_compute_verify_labels')

    @api.depends('zkteco_verify_mode', 'zkteco_out_verify_mode')
    def _compute_verify_labels(self):
        for att in self:
            att.zkteco_verify_label = _verify_label(att.zkteco_verify_mode)
            att.zkteco_out_verify_label = _verify_label(att.zkteco_out_verify_mode)
    zkteco_imported = fields.Boolean(
        compute='_compute_zkteco_imported',
        store=True,
        string='From ZKTeco',
    )
    zkteco_manual_edit = fields.Boolean(
        string='Corrigé manuellement',
        readonly=True,
        copy=False,
        help="Posé automatiquement quand un RH modifie l'heure d'arrivée ou de "
             "sortie d'une présence issue d'une pointeuse. Tant qu'il est actif, "
             "le moteur de résolution (et le cron) ne reconstruit plus cette "
             "présence : la correction humaine est protégée.",
    )
    attphoto_ids = fields.One2many(
        'zkteco.attphoto', 'attendance_id',
        string='Photos de vérification',
    )
    attphoto_count = fields.Integer(
        compute='_compute_attphoto_count',
        string='Nb photos',
    )
    break_ids = fields.One2many(
        'zkteco.attendance.break', 'attendance_id',
        string='Pauses',
        help="Pauses rattachées à cette présence. Les pauses saisies à la main "
             "(non pointées) sont déduites des heures travaillées.",
    )

    @api.depends('zkteco_transaction_id')
    def _compute_zkteco_imported(self):
        for att in self:
            att.zkteco_imported = bool(att.zkteco_transaction_id)

    def _zkteco_punch_state_on(self):
        """L'employé est-il sur un horaire en « punch state obligatoire » ?
        Dans ce mode, la pause est POINTÉE (le trou entre segments la matérialise),
        donc on ne déduit PAS la pause théorique du temps travaillé."""
        self.ensure_one()
        cal = self._get_employee_calendar()
        return bool(cal and cal.zkteco_use_punch_state)

    @api.depends('check_in', 'check_out',
                 'break_ids.break_start', 'break_ids.break_end', 'break_ids.source')
    def _compute_worked_hours(self):
        """En punch state ON : worked_hours = check_out − check_in (pas de
        déduction de la pause théorique de l'horaire, sinon double déduction avec
        le trou réel). En punch OFF : comportement natif inchangé.
        Dans les deux cas, on déduit les PAUSES MANUELLES (non pointées) saisies
        par le RH et qui tombent dans la fenêtre de présence."""
        punch_on = self.filtered(
            lambda a: a.check_in and a.check_out and a.employee_id
            and a._zkteco_punch_state_on())
        for att in punch_on:
            att.worked_hours = (att.check_out - att.check_in).total_seconds() / 3600.0
        super(HrAttendance, self - punch_on)._compute_worked_hours()
        for att in self:
            if att.check_in and att.check_out:
                manual = att._manual_break_hours()
                if manual:
                    att.worked_hours = max(0.0, att.worked_hours - manual)

    def _manual_break_hours(self):
        """Somme (heures) des pauses MANUELLES terminées qui chevauchent la
        fenêtre [check_in, check_out]. Le test de chevauchement garantit que les
        pauses pointées (trous entre segments, hors fenêtre) ne sont jamais
        déduites deux fois."""
        self.ensure_one()
        if not (self.check_in and self.check_out):
            return 0.0
        total = 0.0
        for b in self.break_ids:
            if b.source != 'manual' or not b.break_end:
                continue
            start = max(b.break_start, self.check_in)
            end = min(b.break_end, self.check_out)
            if end > start:
                total += (end - start).total_seconds() / 3600.0
        return total

    def write(self, vals):
        """Toute modification manuelle d'une heure (check_in/check_out) sur une
        présence d'origine pointeuse pose `zkteco_manual_edit` → le moteur ne la
        reconstruira plus. Le moteur lui-même passe `zkteco_skip_manual_flag`."""
        to_flag = self.env['hr.attendance']
        if (('check_in' in vals or 'check_out' in vals)
                and not self.env.context.get('zkteco_skip_manual_flag')):
            for att in self:
                if not att.zkteco_transaction_id or att.zkteco_manual_edit:
                    continue
                changed = (
                    ('check_in' in vals
                     and fields.Datetime.to_datetime(vals['check_in']) != att.check_in)
                    or ('check_out' in vals
                        and fields.Datetime.to_datetime(vals['check_out']) != att.check_out)
                )
                if changed:
                    to_flag |= att
        res = super().write(vals)
        if to_flag:
            to_flag.with_context(zkteco_skip_manual_flag=True).write(
                {'zkteco_manual_edit': True})
        return res

    def action_zkteco_rearm(self):
        """Rend la main au moteur : efface le flag de correction manuelle et
        re-résout la journée (utile si le RH a flaggé par erreur)."""
        Attlog = self.env['zkteco.device.attlog'].sudo()
        self.with_context(zkteco_skip_manual_flag=True).write(
            {'zkteco_manual_edit': False})
        for att in self:
            if att.employee_id and att.check_in:
                tz = Attlog._employee_tz(att.employee_id)
                day = pytz.utc.localize(att.check_in).astimezone(tz).date()
                Attlog._resolve_one_day(att.employee_id, day)

    def _compute_attphoto_count(self):
        for att in self:
            att.attphoto_count = len(att.attphoto_ids)