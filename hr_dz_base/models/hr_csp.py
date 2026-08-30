
from odoo import models, fields


class HrCsp(models.Model):
    _name = 'hr.csp'
    _description = 'Catégorie Socio-Professionnelle'
    _order = 'sequence, name'

    name = fields.Char(
        string='Catégorie',
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
    description = fields.Text(
        string='Description',
    )

    _name_uniq = models.Constraint(
        'UNIQUE(name)',
        'Cette catégorie existe déjà!',
    )
