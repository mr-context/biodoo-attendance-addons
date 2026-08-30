{
    "name": "BioDoo Attendance Suite",
    "version": "19.0.1.0.0",
    "category": "Human Resources/Attendances",
    "summary": "Méta-module : installe le produit Présence/Pointage ZKTeco (sans paie)",
    "description": """
BioDoo Attendance Suite
========================
Module « parapluie » sans code propre, pour le produit "Présence seule" :
pointage ZKTeco via bridge NATS, prestations de travail dérivées des
présences, détection d'anomalies, et gestion RH/contrats de base — sans
aucune dépendance vers la paie (payroll, hr_dz_payroll, hr_dz_prime...).

Désinstaller ce module NE désinstalle PAS les autres — Odoo ne retire pas
automatiquement les dépendances.
""",
    "author": "MESSAOUDI ABDERRAOUF",
    "website": "https://www.smoothtechnology.net",
    "license": "LGPL-3",
    "application": True,
    "installable": True,
    "auto_install": False,
    "depends": [
        # Socle & localisation Algérie
        "l10n_dz_base",
        "l10n_dz_company",
        "hr_dz_base",
        # Contrats (gestion RH sans paie)
        "hr_dz_contract",
        # Présence & pointage
        "hr_dz_work_entry",
        "hr_dz_attendance_anomaly",
        "hr_dz_attendance_dashboard",
        "hr_face_attendance",
        # Connecteur ZKTeco (bridge NATS)
        "core_nats",
        "zkteco_connector",
        # Backend
        "web_enterprise",
    ],
    "data": [],
}
