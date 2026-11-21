import time
from collections import Counter

from config import MAX_PLY, REPETITION_LIMIT, WIN_SCORE

NOM = "Minimax MAX"
ORDRE = 5
PROFONDEUR = False
DESCRIPTION = "Minimax avancé : approfondissement itératif + table de transposition."

TEMPS_MAX_S = 0.5
PROFONDEUR_MAX = 80
VALEUR_NULLE = 60
INF = WIN_SCORE * 10
SEUIL_VICTOIRE = WIN_SCORE - 1000

EXACT, BORNE_BASSE, BORNE_HAUTE = 0, 1, 2


class Fin(Exception):
    pass


class Moteur:
    def __init__(self, observation):
        self.adj = {n: sorted(v) for n, v in observation["adjacence"].items()}
        self.dist = observation["distances"]
        self.centre = observation["centre"]
        self.annule = observation["annule"]
        self.vues = Counter(observation["historique"])
        self.ply_depart = observation["etat"].ply
        self.tt = {}
        self.killers = {}
        self.noeuds = 0
        self.limite = 0.0
        self.chemin = set()


    def coups(self, voleur, policiers, trait):
        if trait == 0:
            return [(voleur, d) for d in self.adj[voleur] if d not in policiers]
        res = []
        for p in policiers:
            for d in self.adj[p]:
                if d != voleur and d not in policiers:
                    res.append((p, d))
        return res

    @staticmethod
    def jouer(voleur, policiers, trait, coup):
        depart, arrivee = coup
        if trait == 0:
            return arrivee, policiers, 1
        return voleur, tuple(sorted(arrivee if p == depart else p for p in policiers)), 0


    def evaluer(self, voleur, policiers):
        """Du point de vue du voleur."""
        adj = self.adj[voleur]
        mobilite = 0
        adjacents = 0
        for n in adj:
            if n in policiers:
                adjacents += 1
            else:
                mobilite += 1
        dist_centre = 0 if self.centre in policiers else self.dist[voleur][self.centre]
        degre = len(adj)
        danger = 1 if degre == 3 and adjacents >= 2 else 0
        return (10 * mobilite
                - 25 * (1 if self.centre in policiers else 0)
                - 15 * dist_centre
                - 8 * adjacents
                - 40 * danger
                + (5 if degre == 4 else 0)
                - 3 * sum(self.dist[voleur][p] for p in policiers))

    def tri_rapide(self, voleur, policiers, trait, coup):
        """Plus petit = à essayer en premier."""
        depart, arrivee = coup
        if trait == 0:
            return (self.dist[arrivee][self.centre], -len(self.adj[arrivee]))
        nouveaux = tuple(arrivee if p == depart else p for p in policiers)
        poursuite = sum(self.dist[voleur][p] for p in nouveaux)
        return (0 if arrivee == self.centre else 1, poursuite)


    def valeur_nulle(self, trait):
        """Valeur de nulle du point de vue de celui qui a le trait."""
        return VALEUR_NULLE if trait == 0 else -VALEUR_NULLE

    def negamax(self, voleur, policiers, trait, profondeur, alpha, beta, ply):
        self.noeuds += 1
        if (self.noeuds & 255) == 0:
            if time.perf_counter() > self.limite or (self.annule is not None and self.annule.is_set()):
                raise Fin()


        if voleur == self.centre and trait == 1:
            return -(WIN_SCORE - ply)
        coups = self.coups(voleur, policiers, trait)
        if not coups:
            if trait == 0:
                return -(WIN_SCORE - ply)
            return self.valeur_nulle(trait)
        if self.ply_depart + ply >= MAX_PLY:
            return self.valeur_nulle(trait)

        cle = (voleur, policiers, trait)
        if cle in self.chemin or self.vues.get(cle, 0) + 1 >= REPETITION_LIMIT:
            return self.valeur_nulle(trait)
        if profondeur <= 0:
            h = self.evaluer(voleur, policiers)
            return h if trait == 0 else -h

        alpha_initial = alpha
        meilleur_tt = None
        entree = self.tt.get(cle)
        if entree is not None:
            prof_tt, drapeau, valeur, meilleur_tt = entree
            if abs(valeur) >= SEUIL_VICTOIRE:
                valeur = valeur - ply if valeur > 0 else valeur + ply
            if prof_tt >= profondeur:
                if drapeau == EXACT:
                    return valeur
                if drapeau == BORNE_BASSE:
                    alpha = max(alpha, valeur)
                else:
                    beta = min(beta, valeur)
                if alpha >= beta:
                    return valeur

        killers = self.killers.get(ply, ())
        coups.sort(key=lambda c: (0 if c == meilleur_tt else (1 if c in killers else 2),
                                  self.tri_rapide(voleur, policiers, trait, c)))

        self.chemin.add(cle)
        meilleur = -INF
        meilleur_coup = coups[0]
        try:
            for coup in coups:
                v2, p2, t2 = self.jouer(voleur, policiers, trait, coup)
                valeur = -self.negamax(v2, p2, t2, profondeur - 1, -beta, -alpha, ply + 1)
                if valeur > meilleur:
                    meilleur = valeur
                    meilleur_coup = coup
                if valeur > alpha:
                    alpha = valeur
                if alpha >= beta:
                    ks = self.killers.setdefault(ply, [])
                    if coup not in ks:
                        ks.insert(0, coup)
                        del ks[2:]
                    break
        finally:
            self.chemin.discard(cle)


        stocke = meilleur
        if abs(stocke) >= SEUIL_VICTOIRE:
            stocke = stocke + ply if stocke > 0 else stocke - ply
        if meilleur <= alpha_initial:
            drapeau = BORNE_HAUTE
        elif meilleur >= beta:
            drapeau = BORNE_BASSE
        else:
            drapeau = EXACT
        ancien = self.tt.get(cle)
        if ancien is None or ancien[0] <= profondeur:
            self.tt[cle] = (profondeur, drapeau, stocke, meilleur_coup)
        return meilleur


def decide(observation):
    etat = observation["etat"]
    moteur = Moteur(observation)
    voleur, policiers, trait = etat.voleur, etat.policiers, etat.trait
    coups = moteur.coups(voleur, policiers, trait)
    if len(coups) == 1:
        return coups[0]

    debut = time.perf_counter()
    moteur.limite = debut + TEMPS_MAX_S
    meilleur_coup = coups[0]
    precedent = None

    try:
        for profondeur in range(1, PROFONDEUR_MAX + 1):
            ordre = sorted(coups, key=lambda c: (0 if c == precedent else 1,
                                                 moteur.tri_rapide(voleur, policiers, trait, c)))
            valeurs = {}
            alpha = -INF
            meilleure_valeur = -INF
            for coup in ordre:
                v2, p2, t2 = moteur.jouer(voleur, policiers, trait, coup)

                valeur = -moteur.negamax(v2, p2, t2, profondeur - 1, -INF, -(alpha - 1), 1)
                valeurs[coup] = valeur
                if valeur > meilleure_valeur:
                    meilleure_valeur = valeur
                alpha = max(alpha, meilleure_valeur)

            egaux = [c for c in ordre if valeurs[c] == meilleure_valeur]

            def nb_vues(c):
                v2, p2, t2 = moteur.jouer(voleur, policiers, trait, c)
                return moteur.vues.get((v2, p2, t2), 0)
            mini = min(nb_vues(c) for c in egaux)
            egaux = [c for c in egaux if nb_vues(c) == mini]
            meilleur_coup = egaux[0]
            precedent = meilleur_coup

            if abs(meilleure_valeur) >= SEUIL_VICTOIRE:
                break
    except Fin:
        pass

    return meilleur_coup
