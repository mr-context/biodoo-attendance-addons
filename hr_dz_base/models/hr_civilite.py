
from odoo import models, fields


class HrCivilite(models.Model):
    _name = 'hr.civilite'
    _description = 'Civilité'
    _order = 'sequence, name'

    name = fields.Char(
        string='Civilité',
        required=True,
        translate=True,
    )
    code = fields.Char(
        string='Abréviation',
        required=True,
        help='Ex: M., Mme, Mlle'
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
    )

    _name_uniq = models.Constraint(
        'UNIQUE(name)',
        'Cette civilité existe déjà!',
    )
    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        'Cette abréviation existe déjà!',
    )
