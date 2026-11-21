import argparse
import glob
import importlib
import math
import os
import queue
import sys
import threading

import pygame

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DEFAULT_DEPTH
from reseau import SessionReseau, ip_locale
from serveur import PORT_DEFAUT
from regles import (
    ARETES,
    NODES,
    POLICE,
    TYPE_ARETE,
    VOLEUR,
    Partie,
    REGLES,
    creer_observation,
)


LARGEUR = 1280
HAUTEUR = 800
FPS = 60

ZONE_PLATEAU = (20, 20, 780, 760)
ZONE_PANNEAU = (820, 20, 440, 760)

FOND = (245, 247, 250)
TRAIT = (25, 25, 25)
NOEUD_REMPLI = (255, 255, 255)
NOEUD_CONTOUR = (25, 25, 25)
VOLEUR_COULEUR = (214, 40, 40)
VOLEUR_FONCE = (60, 10, 10)
POLICE_COULEUR = (30, 90, 200)
POLICE_CLAIR = (235, 240, 255)
VERT_OK = (46, 160, 67)
SELECTION = (240, 190, 0)
DERNIER_COUP = (255, 140, 0)
TEXTE = (30, 30, 40)
TEXTE_PALE = (110, 115, 130)
PANNEAU_FOND = (255, 255, 255)
GRIS_NUL = (120, 120, 120)

EPAISSEUR_ARETE = 5
RAYON_NOEUD = 0.026
RAYON_PION = 0.045
RAYON_CLIC = 0.04

PROFONDEUR_MIN = 2
PROFONDEUR_MAX = 8
DELAI_IA_MS = 50


def charger_bots():
    """Retourne {nom: module} pour chaque bots/bot_xxx.py qui définit decide()."""
    dossier = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bots")
    bots = {}
    for chemin in sorted(glob.glob(os.path.join(dossier, "bot_*.py"))):
        nom = os.path.basename(chemin)[4:-3]
        try:
            module = importlib.import_module(f"bots.bot_{nom}")
        except Exception as exc:
            print(f"[bots] bot_{nom}.py ignoré : {exc}")
            continue
        if not callable(getattr(module, "decide", None)):
            print(f"[bots] bot_{nom}.py ignoré : pas de fonction decide(observation)")
            continue
        bots[nom] = module
    ordre = sorted(bots.items(), key=lambda kv: (getattr(kv[1], "ORDRE", 99), kv[0]))
    return dict(ordre)


BOTS = {}


def nom_bot(type_joueur, profondeur=None):
    if type_joueur == "humain":
        return "Humain"
    module = BOTS[type_joueur]
    nom = getattr(module, "NOM", type_joueur)
    if getattr(module, "PROFONDEUR", False) and profondeur:
        nom += f" (profondeur {profondeur})"
    return nom


def bot_par_defaut(*preferes):
    for nom in preferes:
        if nom in BOTS:
            return nom
    return next(iter(BOTS), "humain")


_polices = {}


def police(taille, gras=False):
    cle_police = (taille, gras)
    if cle_police not in _polices:
        try:
            _polices[cle_police] = pygame.font.SysFont("arial", taille, bold=gras)
        except pygame.error:
            _polices[cle_police] = pygame.font.Font(None, taille)
    return _polices[cle_police]


def decouper_texte(texte, fonte, largeur_max):
    lignes = []
    for paragraphe in texte.split("\n"):
        mots = paragraphe.split()
        if not mots:
            lignes.append("")
            continue
        courant = mots[0]
        for mot in mots[1:]:
            essai = f"{courant} {mot}"
            if fonte.size(essai)[0] <= largeur_max:
                courant = essai
            else:
                lignes.append(courant)
                courant = mot
        lignes.append(courant)
    return lignes


def dans_rect(rect, pos):
    return rect[0] <= pos[0] <= rect[0] + rect[2] and rect[1] <= pos[1] <= rect[1] + rect[3]


class Bouton:
    def __init__(self, rect, texte, action=None, couleur=POLICE_COULEUR):
        self.rect = rect
        self.texte = texte
        self.action = action
        self.couleur = couleur
        self.actif = True
        self.survole = False
        self.enfonce = False

    def evenement(self, event):
        if not self.actif:
            return False
        if event.type == pygame.MOUSEMOTION:
            self.survole = dans_rect(self.rect, event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if dans_rect(self.rect, event.pos):
                self.enfonce = True
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.enfonce:
                self.enfonce = False
                if dans_rect(self.rect, event.pos):
                    if self.action is not None:
                        self.action()
                    return True
        return False

    def dessiner(self, surface):
        if not self.actif:
            couleur = GRIS_NUL
        elif self.enfonce:
            couleur = tuple(max(0, c - 35) for c in self.couleur)
        elif self.survole:
            couleur = tuple(min(255, c + 20) for c in self.couleur)
        else:
            couleur = self.couleur
        pygame.draw.rect(surface, couleur, self.rect, border_radius=10)
        pygame.draw.rect(surface, (220, 220, 225), self.rect, 2, border_radius=10)
        image = police(18, True).render(self.texte, True, POLICE_CLAIR if self.actif else (245, 245, 245))
        surface.blit(image, image.get_rect(center=(self.rect[0] + self.rect[2] // 2,
                                                   self.rect[1] + self.rect[3] // 2)))


class Plateau:
    def __init__(self, zone=ZONE_PLATEAU, marge=48):
        x, y, w, h = zone
        xs = [p[0] for p in NODES.values()]
        ys = [p[1] for p in NODES.values()]
        self.zone = zone
        self.echelle = min((w - 2 * marge) / (max(xs) - min(xs)),
                           (h - 2 * marge) / (max(ys) - min(ys)))
        self.dx = x + (w - (max(xs) - min(xs)) * self.echelle) / 2 - min(xs) * self.echelle
        self.dy = y + (h - (max(ys) - min(ys)) * self.echelle) / 2 - min(ys) * self.echelle
        petit = min(w, h)
        self.rayon_noeud = RAYON_NOEUD * petit
        self.rayon_pion = RAYON_PION * petit
        self.rayon_clic = RAYON_CLIC * petit
        self.courbes = {frozenset((a, b)): self._points_arete(a, b) for a, b in ARETES}

    def pos(self, noeud):
        x, y = NODES[noeud]
        return (x * self.echelle + self.dx, y * self.echelle + self.dy)

    def pos_int(self, noeud):
        x, y = self.pos(noeud)
        return (int(round(x)), int(round(y)))

    def noeud_en(self, pos):
        """Case sous la souris (ou None)."""
        meilleur, meilleure_dist = None, math.inf
        for n in NODES:
            nx, ny = self.pos(n)
            d = math.hypot(pos[0] - nx, pos[1] - ny)
            if d <= self.rayon_clic and d < meilleure_dist:
                meilleur, meilleure_dist = n, d
        return meilleur

    def _points_arete(self, a, b, echantillons=24):
        type_arete = TYPE_ARETE[frozenset((a, b))]
        pa, pb = self.pos(a), self.pos(b)
        if type_arete == "axe":
            return [pa, pb]

        cx, cy = self.pos(0)
        if type_arete in ("interieur", "exterieur"):
            mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
            dx, dy = mx - cx, my - cy
            dm = math.hypot(dx, dy)
            ux, uy = (dx / dm, dy / dm) if dm else (0.0, 0.0)
            rayon = (math.hypot(pa[0] - cx, pa[1] - cy) + math.hypot(pb[0] - cx, pb[1] - cy)) / 2
            controle = (cx + ux * (2 * rayon - dm), cy + uy * (2 * rayon - dm))
        else:
            milieu = a if a in (5, 6, 7, 8) else b
            autre = b if milieu == a else a
            pm, po = self.pos(milieu), self.pos(autre)
            if milieu in (5, 7):
                controle = ((po[0] + pm[0]) / 2, pm[1])
            else:
                controle = (pm[0], (po[1] + pm[1]) / 2)

        points = []
        for i in range(echantillons):
            t = i / (echantillons - 1)
            x = (1 - t) ** 2 * pa[0] + 2 * (1 - t) * t * controle[0] + t ** 2 * pb[0]
            y = (1 - t) ** 2 * pa[1] + 2 * (1 - t) * t * controle[1] + t ** 2 * pb[1]
            points.append((x, y))
        return points

    @staticmethod
    def _entiers(points):
        return [(round(x), round(y)) for x, y in points]

    def _pion_voleur(self, surface, position):
        x, y = int(round(position[0])), int(round(position[1]))
        r = int(round(self.rayon_pion))
        pygame.draw.circle(surface, (30, 20, 20), (x + 3, y + 4), r)
        pygame.draw.circle(surface, VOLEUR_COULEUR, (x, y), r)
        pygame.draw.ellipse(surface, VOLEUR_FONCE, (x - r // 2, y - r // 2, r, max(4, r // 2)))
        oeil_y = y - r // 4
        pygame.draw.circle(surface, (255, 255, 255), (x - r // 3, oeil_y), max(2, r // 8))
        pygame.draw.circle(surface, (255, 255, 255), (x + r // 3, oeil_y), max(2, r // 8))

    def _pion_police(self, surface, position):
        x, y = int(round(position[0])), int(round(position[1]))
        r = int(round(self.rayon_pion))
        pygame.draw.circle(surface, (20, 30, 70), (x + 3, y + 4), r)
        pygame.draw.circle(surface, POLICE_COULEUR, (x, y), r)
        pygame.draw.rect(surface, POLICE_CLAIR, (x - r // 2, y - r // 2, r, max(5, r // 3)),
                         border_radius=max(2, r // 5))
        pygame.draw.circle(surface, POLICE_CLAIR, (x, y + r // 5), max(3, r // 6))

    def dessiner(self, surface, etat, selection=None, cibles=(), dernier_coup=None,
                 survol=None, afficher_ids=False, mouvement=None):
        pygame.draw.rect(surface, FOND, self.zone)

        for points in self.courbes.values():
            pygame.draw.lines(surface, TRAIT, False, self._entiers(points), EPAISSEUR_ARETE)

        if dernier_coup is not None:
            points = self.courbes.get(frozenset(dernier_coup))
            if points is not None:
                pygame.draw.lines(surface, DERNIER_COUP, False, self._entiers(points),
                                  max(8, EPAISSEUR_ARETE + 4))

        rayon = int(round(self.rayon_noeud))
        for n in NODES:
            p = self.pos_int(n)
            pygame.draw.circle(surface, NOEUD_REMPLI, p, rayon)
            pygame.draw.circle(surface, NOEUD_CONTOUR, p, rayon, 4)
        pygame.draw.circle(surface, TRAIT, self.pos_int(0), max(7, rayon // 3), 3)

        for n in cibles:
            p = self.pos_int(n)
            pygame.draw.circle(surface, VERT_OK, p, int(rayon * 1.5), 5)
            pygame.draw.circle(surface, VERT_OK, p, 4)

        if survol is not None:
            pygame.draw.circle(surface, (180, 220, 255), self.pos_int(survol), rayon + 3, 3)

        dest_mvt, pos_mvt = (mouvement if mouvement is not None else (None, None))
        self._pion_voleur(surface, pos_mvt if dest_mvt == etat.voleur else self.pos(etat.voleur))
        for p in etat.policiers:
            self._pion_police(surface, pos_mvt if dest_mvt == p else self.pos(p))

        if selection is not None:
            pygame.draw.circle(surface, SELECTION, self.pos_int(selection),
                               int(round(self.rayon_pion)) + 5, 5)

        if afficher_ids:
            for n in NODES:
                px, py = self.pos(n)
                image = police(12).render(str(n), True, TEXTE_PALE)
                surface.blit(image, image.get_rect(center=(round(px), round(py + self.rayon_noeud + 10))))


class Panneau:
    def __init__(self, rect=ZONE_PANNEAU):
        self.rect = rect
        x, y, w, h = rect
        self.x, self.y, self.w, self.h = x, y, w, h
        self.regles_ouvertes = False
        self.bouton_regles = Bouton((x + 14, y + h - 196, w - 28, 34), "Règles", self._basculer_regles)
        largeur_b = (w - 42) // 2
        by = y + h - 52
        self.boutons = {
            "recommencer": Bouton((x + 14, by - 44, largeur_b, 36), "Recommencer"),
            "annuler": Bouton((x + 28 + largeur_b, by - 44, largeur_b, 36), "Annuler"),
            "indice": Bouton((x + 14, by, largeur_b, 36), "Indice"),
            "menu": Bouton((x + 28 + largeur_b, by, largeur_b, 36), "Menu"),
        }

    def _basculer_regles(self):
        self.regles_ouvertes = not self.regles_ouvertes
        self.bouton_regles.texte = "Masquer les règles" if self.regles_ouvertes else "Règles"

    def evenement(self, event):
        if self.bouton_regles.evenement(event):
            return True
        for b in self.boutons.values():
            if b.evenement(event):
                return True
        return False

    def _ecrire(self, surface, texte, pos, taille=15, couleur=TEXTE, largeur_max=None, gras=False):
        fonte = police(taille, gras)
        if largeur_max is None:
            image = fonte.render(texte, True, couleur)
            surface.blit(image, pos)
            return pos[1] + image.get_height() + 3
        y = pos[1]
        for ligne in decouper_texte(texte, fonte, largeur_max):
            image = fonte.render(ligne, True, couleur)
            surface.blit(image, (pos[0], y))
            y += image.get_height() + 2
        return y

    def dessiner(self, surface, partie, nom_voleur, nom_police, statut="", reflechit=False):
        pygame.draw.rect(surface, PANNEAU_FOND, self.rect, border_radius=14)
        x, y = self.x + 14, self.y + 14

        surface.blit(police(26, True).render("Police et Voleur", True, TEXTE), (x, y))
        y += 38

        pygame.draw.rect(surface, (235, 238, 243), (x, y, self.w - 28, 58), border_radius=8)
        if partie.etat.trait == VOLEUR:
            titre_tour, couleur_tour = "Tour du voleur", VOLEUR_COULEUR
        else:
            titre_tour, couleur_tour = "Tour des policiers", POLICE_COULEUR
        pygame.draw.circle(surface, couleur_tour, (x + 18, y + 20), 8)
        surface.blit(police(17, True).render(titre_tour, True, TEXTE), (x + 34, y + 7))
        surface.blit(police(13).render(
            f"Coup n° {partie.etat.ply // 2 + 1}  •  {partie.etat.ply} demi-coups",
            True, TEXTE_PALE), (x + 34, y + 31))
        y += 68

        surface.blit(police(17, True).render("Joueurs", True, TEXTE), (x, y))
        y += 22
        y = self._ecrire(surface, f"Voleur : {nom_voleur}", (x, y), 13)
        y = self._ecrire(surface, f"Policiers : {nom_police}", (x, y), 13)
        y += 4

        surface.blit(police(17, True).render("Statut", True, TEXTE), (x, y))
        y += 22
        texte_statut = statut
        if reflechit:
            points = "." * ((pygame.time.get_ticks() // 500) % 4)
            texte_statut = f"L'IA réfléchit{points}" if not texte_statut else f"{texte_statut} {points}"
        if partie.terminee:
            texte_statut = {"VOLEUR": "Victoire du voleur",
                            "POLICIERS": "Victoire des policiers"}.get(partie.resultat, "Match nul")
        y = self._ecrire(surface, texte_statut or "Choisissez un coup", (x, y), 13,
                         largeur_max=self.w - 28)
        y += 3

        surface.blit(police(17, True).render("Historique", True, TEXTE), (x, y))
        y += 21
        recents = partie.historique[-8:]
        if recents:
            debut = max(1, len(partie.historique) - 7)
            for numero, (coup, _etat) in enumerate(recents, start=debut):
                camp = "Voleur" if numero % 2 == 1 else "Police"
                y = self._ecrire(surface, f"{numero}. {camp} {coup[0]}→{coup[1]}", (x, y), 13, TEXTE_PALE)
        else:
            y = self._ecrire(surface, "Aucun coup", (x, y), 13, TEXTE_PALE)
        y += 4

        ly = min(y, self.y + self.h - 245)
        surface.blit(police(17, True).render("Légende", True, TEXTE), (x, ly))
        ly += 23
        pygame.draw.circle(surface, VOLEUR_COULEUR, (x + 8, ly + 7), 7)
        surface.blit(police(13).render("Voleur", True, TEXTE_PALE), (x + 20, ly))
        pygame.draw.circle(surface, POLICE_COULEUR, (x + 100, ly + 7), 7)
        surface.blit(police(13).render("Policier", True, TEXTE_PALE), (x + 112, ly))
        pygame.draw.circle(surface, VERT_OK, (x + 205, ly + 7), 7, 3)
        surface.blit(police(13).render("Déplacement", True, TEXTE_PALE), (x + 217, ly))

        ry = self.y + self.h - 196
        if self.regles_ouvertes:
            regles = ("• Un point adjacent par coup.\n• Pas de point occupé.\n"
                      "• Voleur : revenir au centre.\n• Police : bloquer le voleur.")
            hauteur_boite = 112
            pygame.draw.rect(surface, (245, 247, 250),
                             (x, ry - hauteur_boite - 38, self.w - 28, hauteur_boite), border_radius=8)
            ty = ry - hauteur_boite - 30
            for ligne in decouper_texte(regles, police(13), self.w - 56):
                surface.blit(police(13).render(ligne, True, TEXTE_PALE), (x + 8, ty))
                ty += 19
        else:
            pygame.draw.rect(surface, (245, 247, 250), (x, ry - 4, self.w - 28, 32), border_radius=8)

        self.bouton_regles.dessiner(surface)
        for b in self.boutons.values():
            b.dessiner(surface)


class Joueur:
    """Un camp de la partie : un humain, un bot, ou (en réseau) un adversaire distant."""

    def __init__(self, type_joueur, profondeur, nom=None):
        self.type = type_joueur
        self.profondeur = profondeur
        self.distant = type_joueur == "distant"
        self.module = None if type_joueur in ("humain", "distant") else BOTS[type_joueur]
        self.nom = nom or ("Adversaire" if self.distant else nom_bot(type_joueur, profondeur))

    @property
    def humain(self):
        return self.module is None and not self.distant


class EcranJeu:
    def __init__(self, app, config):
        self.app = app
        self.partie = Partie()
        self.plateau = Plateau()
        self.panneau = Panneau()
        self.joueurs = {
            VOLEUR: Joueur(config["voleur"], config["profondeur_voleur"]),
            POLICE: Joueur(config["police"], config["profondeur_police"]),
        }

        self.selection = None
        self.cibles = []
        self.dernier_coup = None
        self.survol = None
        self.afficher_ids = False
        self.indice = None
        self.statut = ""
        self.pause = False

        self.animation = None
        self.mouvement = None
        self.mode = "HUMAIN"
        self.file_ia = queue.Queue()
        self.jeton = 0
        self.annule_ia = threading.Event()
        self.ia_lancee = False
        self.attente_ms = 0
        self.coup_en_attente = None
        self.titre_fin = ""
        self.message_fin = ""
        self.boutons_fin = {
            "rejouer": Bouton((310, 460, 180, 48), "Rejouer"),
            "menu": Bouton((550, 460, 180, 48), "Menu"),
            "quitter": Bouton((790, 460, 180, 48), "Quitter", couleur=GRIS_NUL),
        }
        self.boutons_fin["rejouer"].action = self.recommencer
        self.boutons_fin["menu"].action = self.menu
        self.boutons_fin["quitter"].action = app.quitter

        b = self.panneau.boutons
        b["recommencer"].action = self.recommencer
        b["annuler"].action = self.annuler
        b["indice"].action = self.demander_indice
        b["menu"].action = self.menu

        self._mode_apres_coup()


    def joueur_courant(self):
        return self.joueurs[self.partie.etat.trait]

    def _mode_apres_coup(self):
        self.mode = "HUMAIN" if self.joueur_courant().humain else "IA_REFLECHIT"
        self.ia_lancee = False

    def _annuler_ia(self):
        self.annule_ia.set()
        self.jeton += 1
        self.ia_lancee = False

    def _reset_selection(self):
        self.selection = None
        self.cibles = []
        self.indice = None


    def recommencer(self):
        self._annuler_ia()
        self.partie.reinitialiser()
        self._reset_selection()
        self.dernier_coup = None
        self.statut = ""
        self.animation = None
        self.mouvement = None
        self.pause = False
        self._mode_apres_coup()

    def menu(self):
        self._annuler_ia()
        self.app.afficher_menu()

    def annuler(self):
        if not self.partie.historique or self.mode in ("IA_REFLECHIT", "IA_ATTENTE", "ANIMATION"):
            return
        self._annuler_ia()
        humains = [camp for camp, j in self.joueurs.items() if j.humain]
        if len(humains) == 1:

            fait = 0
            while self.partie.historique and fait < 2:
                self.partie.annuler()
                fait += 1
                if self.partie.etat.trait == humains[0]:
                    break
        else:
            self.partie.annuler()
        self._reset_selection()
        self.animation = None
        self.mouvement = None
        self.dernier_coup = self.partie.historique[-1][0] if self.partie.historique else None
        self.statut = "Coup annulé"
        self._mode_apres_coup()

    def demander_indice(self):
        if self.mode not in ("HUMAIN", "SELECTION") or self.partie.terminee:
            return
        role = "voleur" if self.partie.etat.trait == VOLEUR else "policiers"
        observation = creer_observation(self.partie, role, 4, None)
        self.statut = "Calcul de l'indice…"
        jeton = self.jeton

        def calcul():
            try:
                module = importlib.import_module("bots.bot_perfect")
            except Exception:
                module = BOTS.get("minimax")
            try:
                coup = tuple(module.decide(observation))
            except Exception:
                coup = None
            if jeton == self.jeton and coup is not None:
                self.indice = coup
                self.statut = f"Indice : {coup[0]} → {coup[1]}"

        threading.Thread(target=calcul, daemon=True).start()


    def _jouer(self, coup):
        depart, arrivee = coup
        p0, p1 = self.plateau.pos(depart), self.plateau.pos(arrivee)
        self.partie.jouer(coup)
        self.dernier_coup = coup
        self._reset_selection()
        self.animation = {"dest": arrivee, "p0": p0, "p1": p1, "ecoule": 0, "duree": 250}
        self.mouvement = (arrivee, p0)
        self.mode = "ANIMATION"

        if self.partie.terminee:
            raison = self.partie.raison
            if raison == "CENTRE":
                self.titre_fin = "Victoire du voleur"
                self.message_fin = "Victoire du voleur : il est revenu au centre."
            elif raison == "BLOQUE":
                self.titre_fin = "Victoire des policiers"
                self.message_fin = "Victoire des policiers : le voleur est bloqué."
            elif raison == "REPETITION":
                self.titre_fin = "Match nul"
                self.message_fin = "Match nul (même position répétée 3 fois)."
            else:
                self.titre_fin = "Match nul"
                self.message_fin = "Match nul (limite de coups atteinte)."
            self.statut = self.message_fin


    def _lancer_ia(self):
        if self.partie.terminee or self.joueur_courant().humain:
            return
        self.jeton += 1
        jeton = self.jeton
        self.annule_ia = threading.Event()
        joueur = self.joueur_courant()
        role = "voleur" if self.partie.etat.trait == VOLEUR else "policiers"
        observation = creer_observation(self.partie, role, joueur.profondeur, self.annule_ia)
        self.ia_lancee = True

        def calcul():
            try:
                coup = joueur.module.decide(observation)
                self.file_ia.put((jeton, coup, None))
            except BaseException as exc:
                self.file_ia.put((jeton, None, exc))

        threading.Thread(target=calcul, daemon=True).start()


    def evenement(self, event):
        if self.mode == "GAME_OVER":
            for b in self.boutons_fin.values():
                if b.evenement(event):
                    return
            return

        if self.panneau.evenement(event):
            return

        if event.type == pygame.MOUSEMOTION:
            self.survol = self.plateau.noeud_en(event.pos)

        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 3:
                self._reset_selection()
                if self.mode == "SELECTION":
                    self.mode = "HUMAIN"
                return
            if event.button != 1 or self.mode not in ("HUMAIN", "SELECTION"):
                return

            n = self.plateau.noeud_en(event.pos)
            if n is None:
                self._reset_selection()
                self.mode = "HUMAIN"
                return

            if self.selection is not None and n in self.cibles:
                self.statut = ""
                self._jouer((self.selection, n))
                return

            etat = self.partie.etat
            siens = (etat.voleur,) if etat.trait == VOLEUR else etat.policiers
            if n in siens:
                self.selection = n
                self.cibles = [d for s, d in self.partie.coups() if s == n]
                self.mode = "SELECTION"
                self.statut = "Ce pion est bloqué" if not self.cibles else ""
            else:
                occupe = n == etat.voleur or n in etat.policiers
                self._reset_selection()
                self.mode = "HUMAIN"
                if occupe:
                    self.statut = "Case occupée"

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._reset_selection()
                if self.mode == "SELECTION":
                    self.mode = "HUMAIN"
            elif event.key == pygame.K_r:
                self.recommencer()
            elif event.key == pygame.K_u:
                self.annuler()
            elif event.key == pygame.K_h:
                self.demander_indice()
            elif event.key == pygame.K_m:
                self.menu()
            elif event.key == pygame.K_SPACE:
                self.pause = not self.pause
            elif event.key == pygame.K_d:
                self.afficher_ids = not self.afficher_ids


    def mise_a_jour(self, dt_ms):
        if self.mode == "ANIMATION":
            a = self.animation
            a["ecoule"] = min(a["duree"], a["ecoule"] + dt_ms)
            t = a["ecoule"] / a["duree"]
            t = t * t * (3.0 - 2.0 * t)
            pos = (a["p0"][0] + (a["p1"][0] - a["p0"][0]) * t,
                   a["p0"][1] + (a["p1"][1] - a["p0"][1]) * t)
            self.mouvement = (a["dest"], pos)
            if a["ecoule"] >= a["duree"]:
                self.animation = None
                self.mouvement = None
                if self.partie.terminee:
                    self.mode = "GAME_OVER"
                else:
                    self._mode_apres_coup()
            return

        if self.mode == "GAME_OVER":
            return

        if self.mode == "HUMAIN" or self.mode == "SELECTION":
            if not self.joueur_courant().humain:
                self._mode_apres_coup()

        if self.mode == "IA_REFLECHIT":
            if not self.ia_lancee:
                self._lancer_ia()
            try:
                jeton, coup, erreur = self.file_ia.get_nowait()
            except queue.Empty:
                return
            if jeton != self.jeton:
                return
            self.ia_lancee = False
            self.coup_en_attente = (coup, erreur)
            self.attente_ms = DELAI_IA_MS
            self.mode = "IA_ATTENTE"

        elif self.mode == "IA_ATTENTE":
            if self.pause:
                return
            self.attente_ms -= dt_ms
            if self.attente_ms > 0:
                return
            coup, erreur = self.coup_en_attente
            try:
                coup = tuple(coup)
            except TypeError:
                coup = None
            if erreur is not None or coup not in self.partie.coups():
                if erreur is not None:
                    print(f"[bot] erreur : {erreur!r}")
                self.statut = "Réponse IA invalide : coup de repli joué"
                coup = self.partie.coups()[0]
            else:
                self.statut = ""
            self._jouer(coup)


    def dessiner(self, surface):
        surface.fill(FOND)
        cibles = set(self.cibles)
        if self.indice is not None:
            cibles.add(self.indice[1])
        self.plateau.dessiner(surface, self.partie.etat, self.selection, cibles,
                              self.dernier_coup, self.survol, self.afficher_ids, self.mouvement)
        self.panneau.dessiner(surface, self.partie, self.joueurs[VOLEUR].nom,
                              self.joueurs[POLICE].nom, self.statut,
                              reflechit=(self.mode == "IA_REFLECHIT"))

        if self.mode == "GAME_OVER":
            voile = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            voile.fill((20, 20, 30, 145))
            surface.blit(voile, (0, 0))
            boite = pygame.Rect(220, 250, 840, 300)
            pygame.draw.rect(surface, (250, 250, 252), boite, border_radius=14)
            couleur = {"CENTRE": VOLEUR_COULEUR, "BLOQUE": POLICE_COULEUR}.get(self.partie.raison, GRIS_NUL)
            titre = police(34, True).render(self.titre_fin, True, couleur)
            surface.blit(titre, titre.get_rect(center=(boite.centerx, boite.y + 55)))
            message = police(20).render(self.message_fin, True, TEXTE)
            surface.blit(message, message.get_rect(center=(boite.centerx, boite.y + 120)))
            for b in self.boutons_fin.values():
                b.dessiner(surface)


class EcranMenu:
    PAR_LIGNE = 5

    def __init__(self, app):
        self.app = app
        self.type_voleur = "humain"
        self.type_police = bot_par_defaut("minimax")
        self.profondeur_voleur = DEFAULT_DEPTH
        self.profondeur_police = DEFAULT_DEPTH
        self.prets = set()
        self.en_cours = set()

        self.types = ["humain"] + list(BOTS)
        lignes = (len(self.types) + self.PAR_LIGNE - 1) // self.PAR_LIGNE
        bloc = 34 + lignes * 50 + 50

        self.y_voleur = 160
        self.y_police = self.y_voleur + bloc + 14
        self.boutons_voleur = self._boutons_types(self.y_voleur + 34, "voleur")
        self.boutons_police = self._boutons_types(self.y_police + 34, "police")
        y_prof_v = self.y_voleur + 34 + lignes * 50 + 4
        y_prof_p = self.y_police + 34 + lignes * 50 + 4
        self.y_prof = {"voleur": y_prof_v, "police": y_prof_p}
        self.moins = {"voleur": Bouton((330, y_prof_v, 48, 36), "−", lambda: self._profondeur("voleur", -1)),
                      "police": Bouton((330, y_prof_p, 48, 36), "−", lambda: self._profondeur("police", -1))}
        self.plus = {"voleur": Bouton((430, y_prof_v, 48, 36), "+", lambda: self._profondeur("voleur", 1)),
                     "police": Bouton((430, y_prof_p, 48, 36), "+", lambda: self._profondeur("police", 1))}

        self.y_info = self.y_police + bloc + 6
        y_presets = self.y_info + 56
        self.presets = {
            "h_vs_ia": Bouton((170, y_presets, 220, 44), "Humain (voleur) vs IA",
                              lambda: self._preset("h_vs_ia")),
            "p_vs_ia": Bouton((410, y_presets, 220, 44), "Humain (police) vs IA",
                              lambda: self._preset("p_vs_ia")),
            "ia_vs_ia": Bouton((650, y_presets, 220, 44), "IA vs IA",
                               lambda: self._preset("ia_vs_ia")),
            "h_vs_h": Bouton((890, y_presets, 220, 44), "Humain vs Humain",
                             lambda: self._preset("h_vs_h")),
        }
        y_jouer = min(y_presets + 80, HAUTEUR - 70)
        self.y_attente = y_jouer - 26
        self.bouton_jouer = Bouton((430, y_jouer, 240, 52), "Jouer", self.jouer)
        self.bouton_quitter = Bouton((690, y_jouer, 160, 52), "Quitter", app.quitter)
        self.bouton_reseau = Bouton((870, y_jouer, 220, 52), "Jeu en réseau",
                                    app.afficher_reseau, couleur=VERT_OK)
        self._preparer(self.type_police)

    def _boutons_types(self, y, camp):
        boutons = {}
        for i, t in enumerate(self.types):
            rect = (170 + (i % self.PAR_LIGNE) * 190, y + (i // self.PAR_LIGNE) * 50, 175, 42)
            libelle = "Humain" if t == "humain" else getattr(BOTS[t], "NOM", t)
            boutons[t] = Bouton(rect, libelle, lambda t=t, c=camp: self._choisir(c, t))
        return boutons

    def _choisir(self, camp, type_joueur):
        if camp == "voleur":
            self.type_voleur = type_joueur
        else:
            self.type_police = type_joueur
        self._preparer(type_joueur)

    def _profondeur(self, camp, delta):
        if camp == "voleur":
            self.profondeur_voleur = max(PROFONDEUR_MIN, min(PROFONDEUR_MAX, self.profondeur_voleur + delta))
        else:
            self.profondeur_police = max(PROFONDEUR_MIN, min(PROFONDEUR_MAX, self.profondeur_police + delta))

    def _preparer(self, type_joueur):
        """Les bots qui ont une fonction preparer(regles) (ex. le solveur) sont prêts en tâche de fond."""
        if type_joueur == "humain" or type_joueur in self.prets or type_joueur in self.en_cours:
            return
        module = BOTS[type_joueur]
        if not callable(getattr(module, "preparer", None)):
            self.prets.add(type_joueur)
            return
        self.en_cours.add(type_joueur)

        def calcul():
            try:
                module.preparer(REGLES)
            finally:
                self.en_cours.discard(type_joueur)
                self.prets.add(type_joueur)

        threading.Thread(target=calcul, daemon=True).start()

    def _preset(self, nom):
        if nom == "h_vs_ia":
            self.type_voleur, self.type_police = "humain", bot_par_defaut("minimax")
        elif nom == "p_vs_ia":
            self.type_voleur, self.type_police = bot_par_defaut("minimax"), "humain"
        elif nom == "ia_vs_ia":
            self.type_voleur = self.type_police = bot_par_defaut("minimax", "random")
        else:
            self.type_voleur = self.type_police = "humain"
        self._preparer(self.type_voleur)
        self._preparer(self.type_police)

    def _attente(self):
        return any(t in self.en_cours for t in (self.type_voleur, self.type_police))

    def jouer(self):
        if self._attente():
            return
        self.app.demarrer_partie({
            "voleur": self.type_voleur,
            "police": self.type_police,
            "profondeur_voleur": self.profondeur_voleur,
            "profondeur_police": self.profondeur_police,
        })

    def evenement(self, event):
        boutons = [*self.boutons_voleur.values(), *self.boutons_police.values(),
                   *self.presets.values(), self.bouton_jouer, self.bouton_quitter,
                   self.bouton_reseau]
        if self._a_profondeur(self.type_voleur):
            boutons += [self.moins["voleur"], self.plus["voleur"]]
        if self._a_profondeur(self.type_police):
            boutons += [self.moins["police"], self.plus["police"]]
        for b in boutons:
            b.evenement(event)

        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                self.jouer()
            elif event.key == pygame.K_ESCAPE:
                self.app.quitter()

    @staticmethod
    def _a_profondeur(type_joueur):
        return type_joueur != "humain" and getattr(BOTS[type_joueur], "PROFONDEUR", False)

    def mise_a_jour(self, dt_ms):
        self.bouton_jouer.actif = not self._attente()

    def dessiner(self, surface):
        surface.fill(FOND)
        titre = police(48, True).render("Police et Voleur", True, TEXTE)
        surface.blit(titre, titre.get_rect(center=(640, 60)))
        sous = police(24).render("Configurer la partie", True, TEXTE_PALE)
        surface.blit(sous, sous.get_rect(center=(640, 108)))

        surface.blit(police(18).render("Voleur", True, VOLEUR_COULEUR), (170, self.y_voleur + 4))
        surface.blit(police(18).render("Policiers", True, POLICE_COULEUR), (170, self.y_police + 4))

        for t, b in self.boutons_voleur.items():
            if t == self.type_voleur:
                pygame.draw.rect(surface, VOLEUR_COULEUR, b.rect, 3, border_radius=10)
            b.dessiner(surface)
        for t, b in self.boutons_police.items():
            if t == self.type_police:
                pygame.draw.rect(surface, POLICE_COULEUR, b.rect, 3, border_radius=10)
            b.dessiner(surface)

        for camp, type_joueur, valeur in (("voleur", self.type_voleur, self.profondeur_voleur),
                                          ("police", self.type_police, self.profondeur_police)):
            if self._a_profondeur(type_joueur):
                y = self.y_prof[camp]
                surface.blit(police(18).render(f"Profondeur : {valeur}", True, TEXTE), (170, y + 6))
                self.moins[camp].dessiner(surface)
                self.plus[camp].dessiner(surface)

        def description(t):
            return "Vous choisissez les déplacements à la souris." if t == "humain" \
                else getattr(BOTS[t], "DESCRIPTION", "")

        surface.blit(police(15).render(f"Voleur — {description(self.type_voleur)}", True, TEXTE_PALE),
                     (170, self.y_info))
        surface.blit(police(15).render(f"Policiers — {description(self.type_police)}", True, TEXTE_PALE),
                     (170, self.y_info + 22))

        for b in self.presets.values():
            b.dessiner(surface)

        if self._attente():
            texte = police(15).render("Calcul du solveur…", True, TEXTE_PALE)
            surface.blit(texte, texte.get_rect(center=(640, self.y_attente)))

        self.bouton_jouer.dessiner(surface)
        self.bouton_quitter.dessiner(surface)
        self.bouton_reseau.dessiner(surface)



class ChampTexte:
    """Petite zone de saisie de texte."""

    def __init__(self, rect, texte="", max_len=24, autorises=None):
        self.rect = rect
        self.texte = texte
        self.max_len = max_len
        self.autorises = autorises
        self.focus = False

    def evenement(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.focus = dans_rect(self.rect, event.pos)
        elif event.type == pygame.KEYDOWN and self.focus:
            if event.key == pygame.K_BACKSPACE:
                self.texte = self.texte[:-1]
            elif event.unicode and event.unicode.isprintable() and len(self.texte) < self.max_len:
                if self.autorises is None or event.unicode in self.autorises:
                    self.texte += event.unicode

    def dessiner(self, surface):
        pygame.draw.rect(surface, (255, 255, 255), self.rect, border_radius=8)
        pygame.draw.rect(surface, POLICE_COULEUR if self.focus else (190, 195, 205),
                         self.rect, 3 if self.focus else 2, border_radius=8)
        curseur = "|" if self.focus and (pygame.time.get_ticks() // 500) % 2 == 0 else ""
        image = police(20).render(self.texte + curseur, True, TEXTE)
        surface.blit(image, (self.rect[0] + 12, self.rect[1] + (self.rect[3] - image.get_height()) // 2))


def libelle_joueur(type_joueur, profondeur):
    return nom_bot(type_joueur, profondeur)


class EcranReseau:
    """Configuration (héberger / rejoindre), puis salle d'attente."""

    def __init__(self, app, message=""):
        self.app = app
        self.phase = "config"          # config | connexion | lobby
        self.message = message
        self.hebergement = True
        self.type_joueur = "humain"
        self.profondeur = DEFAULT_DEPTH
        self.champ_nom = ChampTexte((330, 150, 300, 42), "Joueur", max_len=16)
        self.champ_ip = ChampTexte((330, 330, 300, 42), "192.168.", max_len=21,
                                   autorises="0123456789.:")

        self.types = ["humain"] + list(BOTS)
        self.boutons_types = {}
        for i, t in enumerate(self.types):
            rect = (170 + (i % 5) * 190, 232 + (i // 5) * 50, 175, 42)
            libelle = "Humain" if t == "humain" else getattr(BOTS[t], "NOM", t)
            self.boutons_types[t] = Bouton(rect, libelle, lambda t=t: self._choisir_type(t))
        lignes = (len(self.types) + 4) // 5
        self.y_prof = 232 + lignes * 50 + 4
        self.moins = Bouton((330, self.y_prof, 48, 36), "−", lambda: self._profondeur(-1))
        self.plus = Bouton((430, self.y_prof, 48, 36), "+", lambda: self._profondeur(1))
        self.y_mode = self.y_prof + 56

        self.bouton_heberger = Bouton((170, self.y_mode, 220, 46), "Héberger la partie",
                                      lambda: self._choisir_mode(True))
        self.bouton_rejoindre = Bouton((410, self.y_mode, 220, 46), "Rejoindre un ami",
                                       lambda: self._choisir_mode(False))
        self.y_ip = self.y_mode + 64
        self.champ_ip.rect = (330, self.y_ip, 300, 42)
        y_bas = HAUTEUR - 70
        self.bouton_continuer = Bouton((430, y_bas, 240, 52), "Continuer", self._continuer)
        self.bouton_retour = Bouton((690, y_bas, 160, 52), "Retour", self.retour, couleur=GRIS_NUL)

        self.bouton_lancer = Bouton((430, y_bas, 240, 52), "Lancer la partie", self._lancer)
        self.bouton_quitter_salle = Bouton((690, y_bas, 200, 52), "Quitter la salle",
                                           self.retour, couleur=GRIS_NUL)

        if app.session is not None and app.session.moi is not None:
            self.phase = "lobby"

    # ---- actions
    def _choisir_type(self, t):
        self.type_joueur = t

    def _profondeur(self, delta):
        self.profondeur = max(PROFONDEUR_MIN, min(PROFONDEUR_MAX, self.profondeur + delta))

    def _choisir_mode(self, hebergement):
        self.hebergement = hebergement

    def _a_profondeur(self):
        return self.type_joueur != "humain" and getattr(BOTS[self.type_joueur], "PROFONDEUR", False)

    def _continuer(self):
        nom = self.champ_nom.texte.strip() or "Joueur"
        adresse, port = "127.0.0.1", PORT_DEFAUT
        session = SessionReseau()
        if self.hebergement:
            try:
                session.heberger(port)
            except OSError as exc:
                self.message = f"Impossible de démarrer le serveur sur le port {port} : {exc}"
                return
        else:
            saisie = self.champ_ip.texte.strip()
            if not saisie:
                self.message = "Entrez l'adresse IP de l'ordinateur qui héberge."
                return
            adresse, _, p = saisie.partition(":")
            if p:
                try:
                    port = int(p)
                except ValueError:
                    self.message = "Port invalide."
                    return
        self.app.session = session
        self.app.config_reseau = {"type": self.type_joueur, "profondeur": self.profondeur}
        session.connecter(adresse, port, nom, libelle_joueur(self.type_joueur, self.profondeur))
        self.phase = "connexion"
        self.message = ""

    def _lancer(self):
        s = self.app.session
        if s is not None and s.hote and len(s.joueurs) == 2:
            s.lancer()

    def retour(self):
        self.app.quitter_reseau()
        self.app.afficher_menu()

    # ---- événements
    def evenement(self, event):
        if self.phase == "config":
            self.champ_nom.evenement(event)
            if not self.hebergement:
                self.champ_ip.evenement(event)
            boutons = [*self.boutons_types.values(), self.bouton_heberger, self.bouton_rejoindre,
                       self.bouton_continuer, self.bouton_retour]
            if self._a_profondeur():
                boutons += [self.moins, self.plus]
            for b in boutons:
                b.evenement(event)
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN:
                    self._continuer()
                elif event.key == pygame.K_ESCAPE:
                    self.retour()
        else:
            for b in (self.bouton_lancer, self.bouton_quitter_salle):
                b.evenement(event)
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.retour()

    def evenement_reseau(self, nom, data):
        if nom == "connecte":
            self.phase = "lobby"
            self.message = ""
        elif nom == "echec":
            self.app.quitter_reseau()
            self.phase = "config"
            self.message = data

    def mise_a_jour(self, dt_ms):
        s = self.app.session
        self.bouton_lancer.actif = bool(s and s.hote and len(s.joueurs) == 2)

    # ---- dessin
    def _etiquette(self, surface, texte, pos, couleur=TEXTE):
        surface.blit(police(18).render(texte, True, couleur), pos)

    def dessiner(self, surface):
        surface.fill(FOND)
        titre = police(48, True).render("Jeu en réseau", True, TEXTE)
        surface.blit(titre, titre.get_rect(center=(640, 60)))

        if self.phase == "config":
            self._dessiner_config(surface)
        elif self.phase == "connexion":
            texte = police(24).render("Connexion en cours…", True, TEXTE_PALE)
            surface.blit(texte, texte.get_rect(center=(640, 300)))
        else:
            self._dessiner_lobby(surface)

    def _dessiner_config(self, surface):
        sous = police(22).render("Chaque PC choisit son joueur, puis l'un héberge et l'autre rejoint.",
                                 True, TEXTE_PALE)
        surface.blit(sous, sous.get_rect(center=(640, 108)))

        self._etiquette(surface, "Votre pseudo", (170, 160))
        self.champ_nom.dessiner(surface)

        self._etiquette(surface, "Votre joueur (humain ou bot)", (170, 204))
        for t, b in self.boutons_types.items():
            b.dessiner(surface)
            if t == self.type_joueur:
                pygame.draw.rect(surface, VERT_OK, b.rect, 5, border_radius=10)
        if self._a_profondeur():
            self._etiquette(surface, f"Profondeur : {self.profondeur}", (170, self.y_prof + 6))
            self.moins.dessiner(surface)
            self.plus.dessiner(surface)

        for b, actif in ((self.bouton_heberger, self.hebergement),
                         (self.bouton_rejoindre, not self.hebergement)):
            b.dessiner(surface)
            if actif:
                pygame.draw.rect(surface, VERT_OK, b.rect, 5, border_radius=10)

        if self.hebergement:
            info = f"Votre adresse à donner à l'ami : {ip_locale()}:{PORT_DEFAUT}"
            surface.blit(police(18).render(info, True, TEXTE), (170, self.y_ip + 10))
        else:
            self._etiquette(surface, "IP de l'hôte", (170, self.y_ip + 10))
            self.champ_ip.dessiner(surface)

        if self.message:
            texte = police(17, True).render(self.message, True, VOLEUR_COULEUR)
            surface.blit(texte, texte.get_rect(center=(640, HAUTEUR - 100)))
        self.bouton_continuer.dessiner(surface)
        self.bouton_retour.dessiner(surface)

    def _dessiner_lobby(self, surface):
        s = self.app.session
        sous = "Salle d'attente"
        if s.hote:
            sous += f" — dites à votre ami de rejoindre {ip_locale()}:{s.port}"
        texte = police(22).render(sous, True, TEXTE_PALE)
        surface.blit(texte, texte.get_rect(center=(640, 108)))

        for slot in range(2):
            y = 200 + slot * 110
            pygame.draw.rect(surface, PANNEAU_FOND, (270, y, 740, 90), border_radius=12)
            if slot < len(s.joueurs):
                j = s.joueurs[slot]
                moi = " (vous)" if slot == s.moi else ""
                role = "Hôte" if slot == 0 else "Invité"
                surface.blit(police(24, True).render(f"{j['nom']}{moi}", True, TEXTE), (294, y + 12))
                surface.blit(police(17).render(f"{role} — {j['label']}", True, TEXTE_PALE), (294, y + 50))
            else:
                surface.blit(police(20).render("En attente d'un joueur…", True, TEXTE_PALE), (294, y + 32))

        regle = ("Au début de chaque manche, un joueur choisit son camp (police ou voleur). "
                 "L'hôte choisit la 1re manche, l'invité la 2e, etc.")
        for i, ligne in enumerate(decouper_texte(regle, police(16), 700)):
            surface.blit(police(16).render(ligne, True, TEXTE_PALE), (290, 450 + i * 22))

        if self.message:
            texte = police(17, True).render(self.message, True, VOLEUR_COULEUR)
            surface.blit(texte, texte.get_rect(center=(640, HAUTEUR - 100)))
        if s.hote:
            self.bouton_lancer.dessiner(surface)
        else:
            texte = police(18).render("L'hôte va lancer la partie…", True, TEXTE_PALE)
            surface.blit(texte, texte.get_rect(center=(550, HAUTEUR - 44)))
        self.bouton_quitter_salle.dessiner(surface)


class EcranChoixRole:
    """Au début de chaque manche : un seul joueur choisit son camp, l'autre prend l'autre."""

    def __init__(self, app):
        self.app = app
        s = app.session
        self.je_choisis = s.chooser == s.moi
        self.bouton_police = Bouton((300, 400, 320, 90), "Je joue les POLICIERS",
                                    lambda: s.choisir_role("police"), couleur=POLICE_COULEUR)
        self.bouton_voleur = Bouton((660, 400, 320, 90), "Je joue le VOLEUR",
                                    lambda: s.choisir_role("voleur"), couleur=VOLEUR_COULEUR)
        self.bouton_quitter = Bouton((540, HAUTEUR - 70, 200, 52), "Quitter", self.quitter,
                                     couleur=GRIS_NUL)

    def quitter(self):
        self.app.quitter_reseau()
        self.app.afficher_menu()

    def evenement(self, event):
        boutons = [self.bouton_quitter]
        if self.je_choisis:
            boutons += [self.bouton_police, self.bouton_voleur]
        for b in boutons:
            b.evenement(event)

    def evenement_reseau(self, nom, data):
        pass

    def mise_a_jour(self, dt_ms):
        pass

    def dessiner(self, surface):
        s = self.app.session
        surface.fill(FOND)
        titre = police(48, True).render(f"Manche {s.manche}", True, TEXTE)
        surface.blit(titre, titre.get_rect(center=(640, 90)))

        score = f"{s.nom_joueur(0)} {s.scores[0]}  —  {s.scores[1]} {s.nom_joueur(1)}"
        image = police(26, True).render(score, True, TEXTE_PALE)
        surface.blit(image, image.get_rect(center=(640, 170)))

        if self.je_choisis:
            message = "À vous de choisir votre camp :"
        else:
            message = f"{s.nom_joueur(s.chooser)} choisit son camp…"
        image = police(30).render(message, True, TEXTE)
        surface.blit(image, image.get_rect(center=(640, 300)))

        if self.je_choisis:
            self.bouton_police.dessiner(surface)
            self.bouton_voleur.dessiner(surface)
        else:
            points = "." * ((pygame.time.get_ticks() // 500) % 4)
            image = police(20).render(f"En attente{points}", True, TEXTE_PALE)
            surface.blit(image, image.get_rect(center=(640, 420)))
        self.bouton_quitter.dessiner(surface)


class EcranJeuReseau(EcranJeu):
    """Une manche en réseau : mon camp est joué ici (humain ou bot), l'autre est distant.

    Tous les coups passent par le serveur : un coup local est envoyé puis appliqué
    quand le serveur le renvoie (les deux PC voient donc exactement la même partie).
    """

    def __init__(self, app, donnees):
        s = app.session
        self.session = s
        self.mon_camp = VOLEUR if donnees["roles"][s.moi] == "voleur" else POLICE
        cfg = app.config_reseau
        config = {"profondeur_voleur": cfg["profondeur"], "profondeur_police": cfg["profondeur"]}
        config["voleur" if self.mon_camp == VOLEUR else "police"] = cfg["type"]
        config["police" if self.mon_camp == VOLEUR else "voleur"] = "distant"
        super().__init__(app, config)

        self.joueurs[self.mon_camp].nom = self._nom_complet(s.moi)
        self.joueurs[1 - self.mon_camp].nom = self._nom_complet(s.adversaire)
        self.statut = f"Vous jouez {'le voleur' if self.mon_camp == VOLEUR else 'les policiers'}"

        for cle in ("recommencer", "annuler", "indice"):
            self.panneau.boutons[cle].actif = False
        self.boutons_fin["rejouer"].texte = "Manche suivante"
        self.boutons_fin["rejouer"].action = self._manche_suivante
        self.boutons_fin["menu"].action = self.menu
        self.attente_manche = False

    def _nom_complet(self, slot):
        s = self.session
        j = s.joueurs[slot]
        return f"{j['nom']} ({j['label']})"

    # ---- les coups passent par le serveur
    def _mode_apres_coup(self):
        if self.joueur_courant().distant:
            self.mode = "DISTANT"
            self.ia_lancee = False
        else:
            super()._mode_apres_coup()

    def _jouer(self, coup):
        """Coup local (clic ou bot) : on l'envoie, il sera appliqué à son retour."""
        self._reset_selection()
        self.statut = ""
        self.mode = "DISTANT"
        self.session.jouer(coup)

    def _appliquer(self, coup):
        super()._jouer(coup)

    def evenement_reseau(self, nom, data):
        if nom == "coup":
            if data["n"] != len(self.partie.historique):
                self.session.demander_sync()
                return
            try:
                self._appliquer(tuple(data["coup"]))
            except ValueError:
                self.session.demander_sync()
        elif nom == "sync":
            self._reconstruire([tuple(c) for c in data["coups"]])
        elif nom == "erreur":
            self.statut = data.get("message", "Erreur")
            self.session.demander_sync()

    def _reconstruire(self, coups):
        self._annuler_ia()
        self.partie.reinitialiser()
        for coup in coups:
            self.partie.jouer(coup)
        self.animation = None
        self.mouvement = None
        self._reset_selection()
        self.dernier_coup = coups[-1] if coups else None
        if self.partie.terminee:
            self.mode = "GAME_OVER"
        else:
            self._mode_apres_coup()

    def evenement(self, event):
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_r, pygame.K_u, pygame.K_h):
            return
        super().evenement(event)

    def _manche_suivante(self):
        self.session.manche_suivante()
        self.attente_manche = True

    def menu(self):
        self._annuler_ia()
        self.app.quitter_reseau()
        self.app.afficher_menu()

    def mise_a_jour(self, dt_ms):
        super().mise_a_jour(dt_ms)
        if self.partie.terminee:
            return
        if self.mode == "DISTANT":
            if self.joueur_courant().distant:
                self.statut = f"En attente du coup de {self.session.nom_joueur(self.session.adversaire)}…"
            else:
                self.statut = "Envoi du coup…"
        elif self.mode in ("HUMAIN", "SELECTION") and self.statut.startswith(("En attente", "Envoi")):
            self.statut = "À vous de jouer"

    def dessiner(self, surface):
        super().dessiner(surface)
        s = self.session
        if self.mode == "GAME_OVER":
            gagnant = {"VOLEUR": VOLEUR, "POLICIERS": POLICE}.get(self.partie.resultat)
            if gagnant is None:
                phrase = "Personne ne marque de point."
            else:
                nom = s.nom_joueur(s.moi if gagnant == self.mon_camp else s.adversaire)
                phrase = f"Point pour {nom} !"
            image = police(22, True).render(phrase, True, TEXTE)
            surface.blit(image, image.get_rect(center=(640, 395)))
            score = f"{s.nom_joueur(0)} {s.scores[0]}  —  {s.scores[1]} {s.nom_joueur(1)}"
            image = police(20).render(score, True, TEXTE_PALE)
            surface.blit(image, image.get_rect(center=(640, 428)))
            if self.attente_manche:
                image = police(16).render("En attente du choix du camp…", True, TEXTE_PALE)
                surface.blit(image, image.get_rect(center=(640, 530)))


class Application:
    """
    Le jeu est dessiné sur une toile fixe de 1280x800 puis mise à l'échelle de la fenêtre.
    On ne rappelle JAMAIS set_mode lors d'un redimensionnement (cela provoquait un
    clignotement infini puis la disparition de la fenêtre).
    """

    def __init__(self, plein_ecran=False):
        pygame.init()
        pygame.display.set_caption("Police et Voleur")
        info = pygame.display.Info()
        echelle = min(1.0, (info.current_w - 80) / LARGEUR, (info.current_h - 120) / HAUTEUR)
        echelle = max(echelle, 0.4)
        self.taille_fenetre = (int(LARGEUR * echelle), int(HAUTEUR * echelle))
        self.plein_ecran = False
        self.fenetre = pygame.display.set_mode(self.taille_fenetre, pygame.RESIZABLE)
        self.toile = pygame.Surface((LARGEUR, HAUTEUR))
        self.horloge = pygame.time.Clock()
        self.actif = True
        self.session = None          # SessionReseau quand on joue en réseau
        self.config_reseau = {"type": "humain", "profondeur": DEFAULT_DEPTH}
        self.ecran = EcranMenu(self)
        if plein_ecran:
            self.basculer_plein_ecran()

    def quitter(self):
        self.quitter_reseau()
        self.actif = False

    def afficher_menu(self):
        self.ecran = EcranMenu(self)

    def demarrer_partie(self, config):
        self.ecran = EcranJeu(self, config)

    # ---- réseau
    def afficher_reseau(self, message=""):
        self.ecran = EcranReseau(self, message)

    def quitter_reseau(self):
        if self.session is not None:
            self.session.fermer()
            self.session = None

    def traiter_reseau(self):
        """Distribue les événements Socket.IO reçus (venant d'un autre thread)."""
        while self.session is not None:
            try:
                nom, data = self.session.file.get_nowait()
            except queue.Empty:
                return
            self._evenement_reseau(nom, data)

    def _evenement_reseau(self, nom, data):
        if nom == "choix_role":
            self.ecran = EcranChoixRole(self)
        elif nom == "manche":
            self.ecran = EcranJeuReseau(self, data)
        elif nom == "adversaire_parti":
            self.ecran = EcranReseau(self, "Votre adversaire a quitté la partie.")
        elif nom == "deconnecte":
            self.quitter_reseau()
            self.afficher_reseau("Connexion perdue.")
        elif nom == "echec":
            self.ecran.evenement_reseau(nom, data)
        elif hasattr(self.ecran, "evenement_reseau"):
            self.ecran.evenement_reseau(nom, data)

    def basculer_plein_ecran(self):
        self.plein_ecran = not self.plein_ecran
        if self.plein_ecran:
            self.fenetre = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.fenetre = pygame.display.set_mode(self.taille_fenetre, pygame.RESIZABLE)

    def _geometrie(self):
        w, h = self.fenetre.get_size()
        echelle = min(w / LARGEUR, h / HAUTEUR)
        ox = (w - LARGEUR * echelle) / 2
        oy = (h - HAUTEUR * echelle) / 2
        return echelle, ox, oy

    def _vers_toile(self, event):
        """Convertit les coordonnées souris de la fenêtre vers la toile 1280x800."""
        if event.type in (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
            echelle, ox, oy = self._geometrie()
            donnees = dict(event.dict)
            donnees["pos"] = (int((event.pos[0] - ox) / echelle), int((event.pos[1] - oy) / echelle))
            return pygame.event.Event(event.type, donnees)
        return event

    def _afficher(self):
        self.fenetre = pygame.display.get_surface()
        w, h = self.fenetre.get_size()
        if (w, h) == (LARGEUR, HAUTEUR):
            self.fenetre.blit(self.toile, (0, 0))
        else:
            echelle, ox, oy = self._geometrie()
            taille = (max(1, int(LARGEUR * echelle)), max(1, int(HAUTEUR * echelle)))
            self.fenetre.fill((0, 0, 0))
            self.fenetre.blit(pygame.transform.smoothscale(self.toile, taille), (int(ox), int(oy)))
        pygame.display.flip()

    def lancer(self):
        try:
            while self.actif:
                dt_ms = self.horloge.tick(FPS)
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.actif = False
                    elif event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                        self.basculer_plein_ecran()
                    elif event.type == pygame.VIDEORESIZE:
                        pass
                    else:
                        self.ecran.evenement(self._vers_toile(event))
                self.traiter_reseau()
                self.ecran.mise_a_jour(dt_ms)
                self.ecran.dessiner(self.toile)
                self._afficher()
        finally:
            pygame.quit()
        return 0


def main():
    BOTS.update(charger_bots())
    choix = ["humain"] + list(BOTS)

    parser = argparse.ArgumentParser(description="Police et Voleur")
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--thief", choices=choix, help="type du joueur voleur")
    parser.add_argument("--cops", choices=choix, help="type des policiers")
    parser.add_argument("--depth", type=int, default=DEFAULT_DEPTH)
    args = parser.parse_args()

    app = Application(plein_ecran=args.fullscreen)

    if args.thief is not None or args.cops is not None:
        profondeur = max(PROFONDEUR_MIN, min(PROFONDEUR_MAX, args.depth))
        app.demarrer_partie({
            "voleur": args.thief or "humain",
            "police": args.cops or bot_par_defaut("minimax"),
            "profondeur_voleur": profondeur,
            "profondeur_police": profondeur,
        })

    return app.lancer()


if __name__ == "__main__":
    raise SystemExit(main())
