
import argparse
import threading

import socketio
from werkzeug.serving import WSGIRequestHandler, make_server

from regles import POLICE, VOLEUR, Partie

PORT_DEFAUT = 5000
NOM_CAMP = {VOLEUR: "voleur", POLICE: "police"}
CAMP_DE_NOM = {"voleur": VOLEUR, "police": POLICE}


class Salle:
    """Tout l'état d'une salle de jeu. Toutes les méthodes sont appelées sous verrou."""

    def __init__(self, sio):
        self.sio = sio
        self.verrou = threading.RLock()
        self.joueurs = []
        self.reinitialiser()

    def reinitialiser(self):
        self.phase = "lobby"
        self.manche = 0
        self.scores = [0, 0]
        self.chooser = None
        self.roles = [None, None]
        self.partie = None


    def slot_de(self, sid):
        for i, j in enumerate(self.joueurs):
            if j["sid"] == sid:
                return i
        return None

    def infos(self):
        return [{"nom": j["nom"], "label": j["label"]} for j in self.joueurs]

    def diffuser(self, evenement, data=None):
        for j in self.joueurs:
            self.sio.emit(evenement, data, to=j["sid"])

    def envoyer_lobby(self):
        for i, j in enumerate(self.joueurs):
            self.sio.emit("lobby", {
                "moi": i,
                "joueurs": self.infos(),
                "scores": self.scores,
                "manche": self.manche,
            }, to=j["sid"])

    def rejoindre(self, sid, nom, label):
        if len(self.joueurs) >= 2:
            return {"ok": False, "raison": "La salle est déjà pleine (2 joueurs)."}
        if self.phase != "lobby":
            return {"ok": False, "raison": "Une partie est déjà en cours."}
        self.joueurs.append({"sid": sid, "nom": (nom or "Joueur")[:20], "label": label or ""})
        self.envoyer_lobby()
        return {"ok": True, "slot": len(self.joueurs) - 1}

    def quitter(self, sid):
        slot = self.slot_de(sid)
        if slot is None:
            return
        self.joueurs.pop(slot)
        self.reinitialiser()
        if self.joueurs:
            self.sio.emit("adversaire_parti", to=self.joueurs[0]["sid"])
            self.envoyer_lobby()

    def lancer(self, sid):
        if self.slot_de(sid) != 0 or len(self.joueurs) != 2 or self.phase != "lobby":
            return
        self.demarrer_choix()

    def demarrer_choix(self):
        """Nouvelle manche : le joueur qui choisit alterne (hôte, puis invité, puis hôte...)."""
        self.manche += 1
        self.chooser = (self.manche - 1) % 2
        self.phase = "role"
        self.partie = None
        self.diffuser("choix_role", {
            "manche": self.manche,
            "chooser": self.chooser,
            "joueurs": self.infos(),
            "scores": self.scores,
        })

    def choisir_role(self, sid, role):
        if self.phase != "role" or self.slot_de(sid) != self.chooser or role not in CAMP_DE_NOM:
            return
        camp = CAMP_DE_NOM[role]
        self.roles[self.chooser] = camp
        self.roles[1 - self.chooser] = POLICE if camp == VOLEUR else VOLEUR
        self.partie = Partie()
        self.phase = "jeu"
        self.diffuser("manche", {
            "manche": self.manche,
            "roles": [NOM_CAMP[c] for c in self.roles],
            "joueurs": self.infos(),
            "scores": self.scores,
        })

    def jouer(self, sid, depart, arrivee):
        slot = self.slot_de(sid)
        if self.phase != "jeu" or slot is None:
            return
        coup = (depart, arrivee)
        if self.roles[slot] != self.partie.etat.trait:
            return self.sio.emit("erreur", {"message": "Ce n'est pas votre tour."}, to=sid)
        if coup not in self.partie.coups():
            return self.sio.emit("erreur", {"message": "Coup illégal."}, to=sid)

        self.partie.jouer(coup)
        self.diffuser("coup", {"n": len(self.partie.historique) - 1, "coup": list(coup)})

        if self.partie.terminee:
            gagnant = None
            if self.partie.resultat == "VOLEUR":
                gagnant = self.roles.index(VOLEUR)
            elif self.partie.resultat == "POLICIERS":
                gagnant = self.roles.index(POLICE)
            if gagnant is not None:
                self.scores[gagnant] += 1
            self.phase = "fin"
            self.diffuser("fin", {
                "resultat": self.partie.resultat,
                "raison": self.partie.raison,
                "gagnant": gagnant,
                "scores": self.scores,
            })

    def manche_suivante(self, sid):
        if self.phase == "fin" and self.slot_de(sid) is not None:
            self.demarrer_choix()

    def sync(self, sid):
        if self.partie is not None and self.phase in ("jeu", "fin"):
            self.sio.emit("sync", {"coups": [list(c) for c, _ in self.partie.historique]}, to=sid)


def creer_application():
    """Retourne (application WSGI, objet Salle)."""
    sio = socketio.Server(async_mode="threading", cors_allowed_origins="*")
    salle = Salle(sio)

    @sio.event
    def rejoindre(sid, data):
        data = data or {}
        with salle.verrou:
            return salle.rejoindre(sid, str(data.get("nom", "")), str(data.get("label", "")))

    @sio.event
    def lancer(sid, data=None):
        with salle.verrou:
            salle.lancer(sid)

    @sio.event
    def choisir_role(sid, data):
        with salle.verrou:
            salle.choisir_role(sid, (data or {}).get("role"))

    @sio.event
    def coup(sid, data):
        try:
            depart, arrivee = int(data["depart"]), int(data["arrivee"])
        except (TypeError, KeyError, ValueError):
            return
        with salle.verrou:
            salle.jouer(sid, depart, arrivee)

    @sio.event
    def manche_suivante(sid, data=None):
        with salle.verrou:
            salle.manche_suivante(sid)

    @sio.event
    def sync(sid, data=None):
        with salle.verrou:
            salle.sync(sid)

    @sio.event
    def disconnect(sid, *args):
        with salle.verrou:
            salle.quitter(sid)

    return socketio.WSGIApp(sio), salle


class _GestionnaireSilencieux(WSGIRequestHandler):
    """Pas de journal HTTP dans la console (les fermetures WebSocket y font du bruit)."""

    def log(self, type, message, *args):
        pass


class Serveur:
    """Serveur HTTP/Socket.IO qui tourne dans un thread (utilisable depuis le jeu)."""

    def __init__(self, port=PORT_DEFAUT, hote="0.0.0.0"):
        application, self.salle = creer_application()
        self.port = port
        self._http = make_server(hote, port, application, threaded=True,
                                 request_handler=_GestionnaireSilencieux)
        self._thread = threading.Thread(target=self._http.serve_forever, daemon=True)

    def demarrer(self):
        self._thread.start()
        return self

    def arreter(self):
        self._http.shutdown()
        self._http.server_close()


def main():
    parser = argparse.ArgumentParser(description="Serveur Police et Voleur")
    parser.add_argument("--port", type=int, default=PORT_DEFAUT)
    args = parser.parse_args()
    from reseau import ip_locale
    serveur = Serveur(args.port).demarrer()
    print(f"Serveur lancé. Les joueurs se connectent à {ip_locale()}:{args.port}  (Ctrl+C pour arrêter)")
    try:
        serveur._thread.join()
    except KeyboardInterrupt:
        serveur.arreter()


if __name__ == "__main__":
    main()
