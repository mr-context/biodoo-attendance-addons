/** @odoo-module **/

// Traduit l'état de licence remonté par le bridge (PanelStatus) en ce qu'un
// utilisateur d'Odoo doit lire, et en ce qu'il doit FAIRE.
//
// Pourquoi ce module existe : le panneau n'affichait qu'« Actif » ou
// « Inactif ». Une licence suspendue par le revendeur, révoquée, transférée ou
// jamais activée donnaient donc exactement le même écran — alors que ces cas
// n'appellent ni la même inquiétude ni la même action. Le plus important est la
// suspension : elle est RÉVERSIBLE, et les pointeuses gardent leurs pointages
// pour les livrer à la réactivation. Un client qui ne le sait pas croit avoir
// tout perdu.
//
// Contrat avec le bridge (champs de PanelStatus, tous facultatifs) :
//   inactive_reason   ""            licence active
//                     "suspended"   suspendue par le revendeur (réversible)
//                     "revoked"     révoquée (revendeur ou administrateur)
//                     "transferred" transférée vers une autre machine
//                     "expired"     date d'expiration dépassée
//                     "grace_expired" serveur injoignable au-delà de la grâce
//                     "unrecognized" le serveur ne reconnaît plus la licence
//                     "none"        jamais activée
// (source : internal/license/inactive.go du bridge — mêmes codes, même sens)
//   inactive_message  texte du serveur, affiché tel quel en complément
//   inactive_since    RFC3339 — depuis quand
//
// Un bridge antérieur n'envoie AUCUN de ces champs : on retombe alors sur
// l'affichage historique (« Inactif », sans encart), sans rien inventer.
//
// Module PUR, sans dépendance à Odoo : c'est ce qui permet de l'éprouver seul.

const STATES = {
    suspended: {
        label: "Licence suspendue",
        level: "warning",
        icon: "fa-pause-circle",
        title: "Licence suspendue",
        message:
            "Votre revendeur a suspendu la licence de cette installation. Les pointeuses " +
            "conservent leurs pointages et les transmettront automatiquement dès la réactivation.",
        action: "Contactez votre revendeur pour la réactiver.",
    },
    revoked: {
        label: "Licence révoquée",
        level: "danger",
        icon: "fa-ban",
        title: "Licence révoquée",
        message:
            "La licence de cette installation a été révoquée : les nouveaux pointages ne sont plus " +
            "admis. Les pointeuses les conservent jusqu'à l'activation d'une nouvelle licence.",
        action: "Contactez votre revendeur.",
    },
    transferred: {
        label: "Licence transférée",
        level: "danger",
        icon: "fa-exchange",
        title: "Licence transférée vers une autre machine",
        message:
            "La licence a été déplacée vers un autre serveur : cette installation ne reçoit plus " +
            "les pointages.",
        action: "Si ce transfert n'était pas voulu, contactez votre revendeur.",
    },
    expired: {
        label: "Licence expirée",
        level: "danger",
        icon: "fa-calendar-times-o",
        title: "Licence expirée",
        message:
            "La licence a expiré : les nouveaux pointages ne sont plus admis. Les pointeuses " +
            "les conservent jusqu'au renouvellement.",
        action: "Contactez votre revendeur pour la renouveler.",
    },
    unrecognized: {
        label: "Licence non reconnue",
        level: "danger",
        icon: "fa-question-circle",
        title: "Licence non reconnue par le serveur",
        message:
            "Le serveur de licences ne reconnaît plus la licence de cette installation : " +
            "l'admission des pointages est en pause. Les pointeuses conservent leurs pointages.",
        action: "Contactez votre revendeur.",
    },
    grace_expired: {
        label: "Hors ligne trop longtemps",
        level: "danger",
        icon: "fa-plug",
        title: "Serveur de licences injoignable depuis trop longtemps",
        message:
            "Le bridge n'a pas pu confirmer la licence dans le délai autorisé : l'admission des " +
            "pointages est en pause. Les pointeuses conservent leurs pointages.",
        action: "Vérifiez l'accès Internet de ce serveur ; tout reprend dès le premier contact.",
    },
    none: {
        label: "Sans licence",
        level: "secondary",
        icon: "fa-key",
        title: "Aucune licence activée",
        message: "Aucune licence n'est activée sur ce bridge : les pointages ne sont pas encore admis.",
        action: "Activez la licence depuis l'interface du bridge (port 8090).",
    },
};

// Formate une date RFC3339 en français ; rend "" si elle est absente ou
// illisible — une date fausse est pire qu'une date manquante.
export function formatSince(iso) {
    if (!iso) {
        return "";
    }
    const d = new Date(iso);
    if (Number.isNaN(d.getTime()) || d.getFullYear() < 2000) {
        return "";
    }
    return d.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

// licenceState : ce que le panneau affiche pour un PanelStatus donné.
// Renvoie null tant qu'aucun statut n'est connu.
export function licenceState(status) {
    if (!status) {
        return null;
    }
    if (status.active) {
        return { key: "active", label: "Actif", level: "success", message: "" };
    }

    const reason = status.inactive_reason || "";
    const since = formatSince(status.inactive_since);
    const detail = (status.inactive_message || "").trim();
    const known = STATES[reason];

    if (known) {
        // Le texte du serveur n'est montré que s'il APPORTE quelque chose : répéter
        // « Licence suspendue » sous le titre « Licence suspendue » serait du bruit.
        const extra = detail && detail.toLowerCase() !== known.title.toLowerCase() ? detail : "";
        return { key: reason, ...known, since, detail: extra };
    }

    if (reason) {
        // Motif inconnu de cette version d'Odoo (bridge plus récent) : on ne le
        // traduit pas, mais on ne le cache pas non plus.
        return {
            key: "other",
            label: "Inactif",
            level: "secondary",
            icon: "fa-info-circle",
            title: "Licence inactive",
            message: detail || `Motif signalé par le bridge : ${reason}.`,
            action: "",
            since,
            detail: "",
        };
    }

    // Bridge antérieur au contrat : comportement historique, aucun encart.
    return { key: "inactive", label: "Inactif", level: "secondary", message: "" };
}
