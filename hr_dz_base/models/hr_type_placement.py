
from odoo import models, fields


class HrTypePlacement(models.Model):
    _name = 'hr.type.placement'
    _description = 'Type de placement'
    _order = 'sequence, name'

    name = fields.Char(
        string='Type de placement',
        required=True,
    )
    code = fields.Char(
        string='Code',
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
    )
    cnas_reduction = fields.Float(
        string='Réduction CNAS (%)',
        default=0.0,
        help='Pourcentage de réduction des charges CNAS pour ce type de placement'
    )
    description = fields.Text(
        string='Description',
    )

    _name_uniq = models.Constraint(
        'UNIQUE(name)',
        'Ce type de placement existe déjà!',
    )
