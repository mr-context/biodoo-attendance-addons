
from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    # =========================================================================
    # CONFIGURATION SEQUENCE CONTRATS
    # =========================================================================
    contract_sequence_id = fields.Many2one(
        'ir.sequence',
        string='Sequence Contrat',
        help='Sequence utilisee pour generer le numero de contrat',
    )
    contract_pattern = fields.Selection([
        ('prefix_seq', 'Prefixe-Sequence (ex: CTR-2024-00001)'),
        ('year_seq', 'Annee/Sequence (ex: 2024/00001)'),
        ('type_seq', 'Type-Sequence (ex: CDI-00001)'),
        ('seq_only', 'Sequence seule (ex: 00001)'),
    ], string='Format N° Contrat', default='prefix_seq',
       help='Format de generation du numero de contrat'
    )
    contract_prefix = fields.Char(
        string='Prefixe Contrat',
        default='CTR',
        help='Prefixe utilise pour la codification des contrats'
    )
    contract_separator = fields.Char(
        string='Separateur',
        default='-',
        help='Separateur entre les parties du numero de contrat'
    )
