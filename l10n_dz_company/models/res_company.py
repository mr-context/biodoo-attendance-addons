from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    # =========================================================================
    # IDENTIFIANTS LEGAUX ALGERIE
    # =========================================================================

    nif = fields.Char(
        string='NIF',
        help="Numéro d'Identification Fiscale",
    )
    nis = fields.Char(
        string='NIS',
        help="Numéro d'Identification Statistique",
    )
    rc = fields.Char(
        string='Registre de Commerce',
        help='Numéro du Registre de Commerce (ex : 24/00-0123456B19)',
    )
    art_impot = fields.Char(
        string="Article d'imposition",
    )
    num_employeur_cnas = fields.Char(
        string='N° Employeur CNAS',
        size=10,
        help="Numéro d'employeur CNAS (10 chiffres)",
    )
    casnos_num = fields.Char(
        string='N° CASNOS',
        help='Numéro CASNOS (pour gérants associés)',
    )
