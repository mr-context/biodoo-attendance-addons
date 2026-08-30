
from odoo import models, fields


class HrJobMission(models.Model):
    _name = 'hr.job.mission'
    _description = 'Mission du poste'
    _order = 'sequence, id'

    name = fields.Char(
        string='Mission',
        required=True,
    )
    job_id = fields.Many2one(
        'hr.job',
        string='Poste',
        required=True,
        ondelete='cascade',
    )
    sequence = fields.Integer(
        string='Séquence',
        default=10,
    )


class HrJob(models.Model):
    _inherit = 'hr.job'

    code = fields.Char(
        string='Code poste',
        help='Code interne du poste'
    )
    csp_id = fields.Many2one(
        'hr.csp',
        string='Catégorie Socio-Prof.',
        help='Catégorie socio-professionnelle par défaut pour ce poste'
    )
    preavis_duree = fields.Integer(
        string='Durée préavis',
        default=1,
    )
    preavis_uom = fields.Selection([
        ('days', 'Jours'),
        ('weeks', 'Semaines'),
        ('months', 'Mois'),
    ], string='Unite preavis', default='months')

    is_hazard = fields.Boolean(
        string='Poste à risque',
        help='Cocher si le poste comporte des risques professionnels '
             '(prime de risque, médecine du travail renforcée)'
    )
    mission_ids = fields.One2many(
        'hr.job.mission',
        'job_id',
        string='Missions',
    )

    _code_uniq = models.Constraint(
        'UNIQUE(code, company_id)',
        'Ce code de poste existe déjà dans cette société!',
    )
