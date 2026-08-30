
from odoo import models, fields


class HrServiceNational(models.Model):
    _name = 'hr.service.national'
    _description = 'Situation Service National'
    _order = 'sequence, name'

    name = fields.Char(
        string='Situation',
        required=True,
        translate=True,
    )
    code = fields.Char(
        string='Code',
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
    )

    _name_uniq = models.Constraint(
        'UNIQUE(name)',
        'Cette situation existe déjà!',
    )
