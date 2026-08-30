from odoo import models, fields, api


class HrPrimeType(models.Model):
    """Catalogue des types de primes contractuelles."""
    _name = 'hr.prime.type'
    _description = 'Type de prime contractuelle'
    _order = 'sequence, name'

    name = fields.Char(string='Libellé', required=True, translate=True)
    code = fields.Char(
        string='Code règle salariale',
        required=True,
        index=True,
        help=(
            'Doit correspondre au code de la règle salariale associée '
            '(ex : PRIME_RESP pour la règle PRIME_RESP).'
        ),
    )
    is_cotisable = fields.Boolean(
        string='Cotisable (CNAS)',
        default=True,
        help='Incluse dans l\'assiette de cotisation CNAS (Brut Cotisable).',
    )
    is_imposable = fields.Boolean(
        string='Imposable (IRG)',
        default=True,
        help='Incluse dans l\'assiette imposable IRG.',
    )
    default_amount = fields.Float(
        string='Montant par défaut',
        digits=(16, 2),
        help='Montant mensuel pré-rempli lors de l\'ajout sur un contrat. Toujours modifiable.',
    )
    description = fields.Text(string='Description')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint(
        'UNIQUE(code)',
        'Le code de prime doit être unique.',
    )


class HrVersionPrimeLine(models.Model):
    """Ligne de prime attachée à un contrat (hr.version)."""
    _name = 'hr.version.prime.line'
    _description = 'Prime contractuelle'
    _order = 'sequence, id'

    version_id = fields.Many2one(
        'hr.version',
        string='Contrat',
        required=True,
        ondelete='cascade',
        index=True,
    )
    prime_type_id = fields.Many2one(
        'hr.prime.type',
        string='Type de prime',
        required=True,
        ondelete='restrict',
    )
    name = fields.Char(
        string='Libellé',
        compute='_compute_name',
        store=True,
        readonly=False,
    )
    code = fields.Char(
        related='prime_type_id.code',
        store=True,
        string='Code',
        readonly=True,
    )
    is_cotisable = fields.Boolean(
        related='prime_type_id.is_cotisable',
        store=True,
        string='Cotisable',
        readonly=True,
    )
    is_imposable = fields.Boolean(
        related='prime_type_id.is_imposable',
        store=True,
        string='Imposable',
        readonly=True,
    )
    amount = fields.Float(
        string='Montant mensuel (DA)',
        digits=(16, 2),
        required=True,
    )
    sequence = fields.Integer(default=10)

    @api.depends('prime_type_id')
    def _compute_name(self):
        for line in self:
            line.name = line.prime_type_id.name if line.prime_type_id else ''

    @api.onchange('prime_type_id')
    def _onchange_prime_type_id(self):
        if not self.prime_type_id:
            return
        # Vérifier doublon sur le même contrat
        existing_types = self.version_id.prime_line_ids.filtered(
            lambda l: l != self
        ).mapped('prime_type_id')
        if self.prime_type_id in existing_types:
            self.prime_type_id = False
            return {'warning': {
                'title': 'Prime déjà ajoutée',
                'message': 'Ce type de prime est déjà présent sur ce contrat.',
            }}
        self.name = self.prime_type_id.name
        if self.prime_type_id.default_amount:
            self.amount = self.prime_type_id.default_amount
