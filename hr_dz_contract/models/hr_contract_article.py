"""
Articles/Clauses de contrat avec support de placeholders.
"""

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import re


class HrContractArticle(models.Model):
    """Article/Clause de contrat"""
    _name = 'hr.contract.article'
    _description = 'Article de contrat'
    _order = 'code, name'

    name = fields.Char(
        string='Nom',
        required=True,
    )
    code = fields.Char(
        string='Code',
        copy=False,
        index=True,
    )
    active = fields.Boolean(
        default=True,
    )

    # Versioning
    parent_id = fields.Many2one(
        'hr.contract.article',
        string='Version précédente',
        readonly=True,
    )
    is_current = fields.Boolean(
        string='Version courante',
        default=True,
        readonly=True,
    )

    # Validity
    date_start = fields.Date(
        string='Date début validité',
        default=fields.Date.context_today,
    )
    date_end = fields.Date(
        string='Date fin validité',
    )

    # Content
    content = fields.Html(
        string='Contenu',
        required=True,
        strip_style=True,
        strip_classes=True,
        help="""Placeholders disponibles:
[[civilite]] [[employe]] [[poste]] [[type_contrat]]
[[date_debut]] [[date_fin]] [[salaire]] [[salaire_lettres]]
[[duree_essai]] [[duree_preavis]] [[departement]]
[[duree_contrat_mois]] [[societe]] [[adresse_societe]]
[[nif]] [[nis]] [[rc]]""",
    )

    company_id = fields.Many2one(
        'res.company',
        string='Société',
        default=lambda self: self.env.company,
    )

    _code_company_uniq = models.Constraint(
        'UNIQUE(code, company_id, is_current)',
        'Le code doit être unique pour les articles en cours!',
    )

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for article in self:
            if article.date_start and article.date_end:
                if article.date_end < article.date_start:
                    raise ValidationError(
                        _('La date de fin doit être après la date de début.')
                    )

    def action_new_revision(self):
        """Créer une nouvelle révision de l'article"""
        self.ensure_one()
        # Marquer l'article actuel comme non courant
        self.is_current = False

        # Créer la nouvelle version
        new_code = self.code
        if self.code:
            # Incrémenter le numéro de version si présent
            match = re.search(r'_V(\d+)$', self.code)
            if match:
                version = int(match.group(1)) + 1
                new_code = re.sub(r'_V\d+$', f'_V{version}', self.code)
            else:
                new_code = f'{self.code}_V2'

        new_article = self.copy({
            'code': new_code,
            'parent_id': self.id,
            'is_current': True,
            'date_start': fields.Date.context_today(self),
            'date_end': False,
        })

        # Mettre à jour les modèles qui utilisent l'ancien article
        self._update_references(new_article)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.contract.article',
            'res_id': new_article.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _update_references(self, new_article):
        """Met à jour les références dans les modèles de contrat"""
        # Mettre à jour les lignes de modèle
        model_lines = self.env['hr.contract.modele.article.line'].search([
            ('article_id', '=', self.id)
        ])
        model_lines.write({'article_id': new_article.id})


class HrContractModele(models.Model):
    """Modèle de contrat avec articles"""
    _name = 'hr.contract.modele'
    _description = 'Modèle de contrat'
    _order = 'name'

    name = fields.Char(
        string='Nom du modèle',
        required=True,
    )
    contract_type_id = fields.Many2one(
        'hr.contract.type',
        string='Type de contrat',
        required=True,
    )
    active = fields.Boolean(
        default=True,
    )

    article_line_ids = fields.One2many(
        'hr.contract.modele.article.line',
        'modele_id',
        string='Articles',
        copy=True,
    )

    company_id = fields.Many2one(
        'res.company',
        string='Société',
        default=lambda self: self.env.company,
    )

    description = fields.Text(
        string='Description',
    )


class HrContractModeleArticleLine(models.Model):
    """Ligne d'article dans un modèle de contrat"""
    _name = 'hr.contract.modele.article.line'
    _description = "Ligne d'article dans un modèle"
    _order = 'sequence, id'

    modele_id = fields.Many2one(
        'hr.contract.modele',
        string='Modèle',
        required=True,
        ondelete='cascade',
    )
    article_id = fields.Many2one(
        'hr.contract.article',
        string='Article',
        required=True,
        domain=[('is_current', '=', True)],
    )
    name = fields.Char(
        related='article_id.name',
        string='Nom',
    )
    code = fields.Char(
        related='article_id.code',
        string='Code',
    )
    sequence = fields.Integer(
        string='Ordre',
        default=10,
    )


class HrContractArticleLine(models.Model):
    """Ligne d'article dans un contrat (hr.version)"""
    _name = 'hr.contract.article.line'
    _description = "Ligne d'article dans un contrat"
    _order = 'sequence, id'

    version_id = fields.Many2one(
        'hr.version',
        string='Contrat',
        required=True,
        ondelete='cascade',
    )
    article_id = fields.Many2one(
        'hr.contract.article',
        string='Article',
        required=True,
        domain=[('is_current', '=', True)],
    )
    name = fields.Char(
        related='article_id.name',
        string='Nom',
    )
    code = fields.Char(
        related='article_id.code',
        string='Code',
    )
    sequence = fields.Integer(
        string='Ordre',
        default=10,
    )
    # Contenu personnalisé (optionnel, sinon utilise article_id.content)
    custom_content = fields.Html(
        string='Contenu personnalisé',
        help='Laisser vide pour utiliser le contenu standard de l\'article',
    )

    def get_content(self, variables=None):
        """Retourne le contenu avec les placeholders remplacés"""
        content = self.custom_content or self.article_id.content or ''
        if variables:
            for key, value in variables.items():
                placeholder = f'[[{key}]]'
                content = content.replace(placeholder, str(value or ''))
        return content
