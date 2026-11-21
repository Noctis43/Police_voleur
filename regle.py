from collections import Counter, namedtuple

from config import (
    CENTER,
    MAX_PLY,
    REPETITION_LIMIT,
    START_COPS,
    START_THIEF,
)


NODES = {
    0: (411, 401), 1: (411, 277), 2: (531, 401), 3: (411, 522),
    4: (291, 401), 5: (411, 185), 6: (612, 401), 7: (411, 607),
    8: (210, 401), 9: (411, 25), 10: (762, 400), 11: (411, 760),
    12: (57, 400), 13: (210, 92), 14: (612, 92), 15: (730, 242),
    16: (730, 560), 17: (612, 695), 18: (210, 695), 19: (92, 560),
    20: (92, 242),
}

ARETES_AXES = [
    (0, 1), (1, 5), (5, 9), (0, 3), (3, 7), (7, 11),
    (0, 2), (2, 6), (6, 10), (0, 4), (4, 8), (8, 12),
]
ARETES_INTERIEURES = [(1, 2), (2, 3), (3, 4), (4, 1)]
ARETES_EXTERIEURES = [
    (9, 14), (14, 15), (15, 10), (10, 16), (16, 17),
    (17, 11), (11, 18), (18, 19), (19, 12), (12, 20),
    (20, 13), (13, 9),
]
ARETES_LENTILLES = [
    (13, 5), (5, 14), (15, 6), (6, 16),
    (17, 7), (7, 18), (19, 8), (8, 20),
]

TYPE_ARETE = {}
for _type, _liste in (("axe", ARETES_AXES), ("interieur", ARETES_INTERIEURES),
                      ("exterieur", ARETES_EXTERIEURES), ("lentille", ARETES_LENTILLES)):
    for _a, _b in _liste:
        TYPE_ARETE[frozenset((_a, _b))] = _type

ARETES = sorted(tuple(sorted(k)) for k in TYPE_ARETE)

_adj = {n: set() for n in NODES}
for _a, _b in ARETES:
    _adj[_a].add(_b)
    _adj[_b].add(_a)
ADJ = {n: frozenset(v) for n, v in _adj.items()}


def _bfs(depart):
    dist = [-1] * len(NODES)
    dist[depart] = 0
    file = [depart]
    for n in file:
        for v in ADJ[n]:
            if dist[v] == -1:
                dist[v] = dist[n] + 1
                file.append(v)
    return dist


DIST = [_bfs(n) for n in range(len(NODES))]


VOLEUR = 0
POLICE = 1


Etat = namedtuple("Etat", "voleur policiers trait ply")


def etat_initial():
    return Etat(START_THIEF, tuple(sorted(START_COPS)), VOLEUR, 0)


def cle(etat):
    """Identifie une position (sans le numéro de demi-coup)."""
    return (etat.voleur, etat.policiers, etat.trait)


def est_libre(etat, case):
    return case != etat.voleur and case not in etat.policiers


def coups_legaux(etat):
    """Liste des coups (depart, arrivee) possibles pour celui qui a le trait."""
    if etat.trait == VOLEUR:
        return [(etat.voleur, d) for d in sorted(ADJ[etat.voleur]) if est_libre(etat, d)]
    coups = []
    for p in etat.policiers:
        for d in sorted(ADJ[p]):
            if est_libre(etat, d):
                coups.append((p, d))
    return coups


def simuler(etat, coup):
    """Applique un coup SANS le vérifier (rapide, pour les bots)."""
    depart, arrivee = coup
    if etat.trait == VOLEUR:
        return Etat(arrivee, etat.policiers, POLICE, etat.ply + 1)
    policiers = tuple(sorted(arrivee if p == depart else p for p in etat.policiers))
    return Etat(etat.voleur, policiers, VOLEUR, etat.ply + 1)


def resultat(etat):
    """'VOLEUR', 'POLICIERS', 'NUL' ou None si la partie continue."""
    if etat.voleur == CENTER and etat.trait == POLICE:
        return "VOLEUR"
    if etat.trait == VOLEUR and not coups_legaux(etat):
        return "POLICIERS"
    if etat.trait == POLICE and not coups_legaux(etat):
        return "NUL"
    if etat.ply >= MAX_PLY:
        return "NUL"
    return None


class Partie:
    """Une partie en cours : état, historique, détection des répétitions."""

    def __init__(self):
        self.etat_depart = etat_initial()
        self.reinitialiser()

    def reinitialiser(self):
        self.etat = self.etat_depart
        self.historique = []
        self.cles = [cle(self.etat)]
        self.compteur = Counter(self.cles)
        self.resultat = None
        self.raison = None
        fin = resultat(self.etat)
        if fin is not None:
            self._terminer(fin)

    @property
    def terminee(self):
        return self.resultat is not None

    def coups(self):
        return [] if self.terminee else coups_legaux(self.etat)

    def jouer(self, coup):
        if self.terminee or coup not in coups_legaux(self.etat):
            raise ValueError("coup illégal")
        self.etat = simuler(self.etat, coup)
        self.historique.append((coup, self.etat))
        k = cle(self.etat)
        self.cles.append(k)
        self.compteur[k] += 1

        fin = resultat(self.etat)
        if fin is not None:
            self._terminer(fin)
        elif self.compteur[k] >= REPETITION_LIMIT:
            self.resultat = "NUL"
            self.raison = "REPETITION"

    def annuler(self):
        if not self.historique:
            return False
        self.historique.pop()
        k = self.cles.pop()
        self.compteur[k] -= 1
        self.etat = self.historique[-1][1] if self.historique else self.etat_depart
        self.resultat = None
        self.raison = None
        return True

    def _terminer(self, fin):
        self.resultat = fin
        if fin == "VOLEUR":
            self.raison = "CENTRE"
        elif fin == "POLICIERS":
            self.raison = "BLOQUE"
        elif self.etat.trait == POLICE and not coups_legaux(self.etat):
            self.raison = "AUCUN_COUP"
        else:
            self.raison = "MAX_COUPS"


REGLES = {
    "adjacence": ADJ,
    "distances": DIST,
    "centre": CENTER,
    "etat_initial": etat_initial(),
    "fn_coups_legaux": coups_legaux,
    "fn_simuler": simuler,
    "fn_resultat": resultat,
    "fn_cle": cle,
}


def creer_observation(partie, role, profondeur, annule):
    etat = partie.etat
    observation = dict(REGLES)
    observation.update({
        "role": role,
        "camp": VOLEUR if role == "voleur" else POLICE,
        "etat": etat,
        "voleur": etat.voleur,
        "policiers": etat.policiers,
        "tour": etat.ply,
        "coups": coups_legaux(etat),
        "historique": list(partie.cles),
        "profondeur": profondeur,
        "annule": annule,
    })
    return observation
