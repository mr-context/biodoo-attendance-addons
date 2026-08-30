
from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    # =========================================================================
    # CONFIGURATION MATRICULE EMPLOYES (RH interne)
    # Les identifiants légaux (NIF, NIS, RC…) sont dans l10n_dz_company
    # =========================================================================

    num_employeur_cnas = fields.Char(
        string='N° Employeur CNAS',
        size=10,
        help='10 chiffres : Wilaya(2) + Numéro(6) + Clé(2). Exemple : 1600123456',
    )
    centre_payeur_cnas = fields.Char(
        string='Centre Payeur CNAS',
        size=5,
        help='Code agence CNAS (5 chiffres). Fourni par votre agence CNAS.',
    )
    matricule_employeur = fields.Char(
        string='Matricule employeur',
        help='Préfixe pour génération des matricules employés (ex: 12345)',
    )
    employee_sequence_id = fields.Many2one(
        'ir.sequence',
        string='Séquence Matricule',
        help='Séquence utilisée pour générer le matricule employé',
    )
    matricule_pattern = fields.Selection([
        ('prefix_seq', 'Préfixe-Séquence (ex: 12345-00001)'),
        ('seq_only', 'Séquence seule (ex: EMP00001)'),
        ('year_seq', 'Année/Séquence (ex: 2024/00001)'),
        ('custom', 'Personnalisé'),
    ], string='Format matricule', default='prefix_seq',
       help='Format de génération du matricule employé',
    )
    matricule_separator = fields.Char(
        string='Séparateur',
        default='-',
        help='Séparateur entre préfixe et numéro',
    )
