import queue
import socket
import threading

import socketio

from serveur import PORT_DEFAUT, Serveur

EVENEMENTS = ("lobby", "choix_role", "manche", "coup", "fin", "sync",
              "adversaire_parti", "erreur")


def ip_locale():
    """Adresse IP de ce PC sur le réseau local (celle à donner à l'ami)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class SessionReseau:
    """Connexion au serveur + dernières infos reçues.

    Les événements reçus (thread réseau) sont mis dans `file` sous forme
    (nom, données) ; l'interface les lit dans sa boucle (voir Application).
    """

    def __init__(self):
        self.file = queue.Queue()
        self.sio = None
        self.serveur = None        # Serveur embarqué si on héberge
        self.port = PORT_DEFAUT
        self.moi = None            # slot : 0 = hôte, 1 = invité
        self.joueurs = []          # [{"nom", "label"}]
        self.scores = [0, 0]
        self.manche = 0
        self.chooser = None
        self.roles = [None, None]
        self.ferme = False

    @property
    def hote(self):
        return self.moi == 0

    @property
    def adversaire(self):
        return 1 - self.moi

    def nom_joueur(self, slot):
        if 0 <= slot < len(self.joueurs):
            return self.joueurs[slot]["nom"]
        return "?"

    def heberger(self, port=PORT_DEFAUT):
        """Démarre le serveur dans ce processus (lève OSError si le port est pris)."""
        self.port = port
        self.serveur = Serveur(port).demarrer()

    def connecter(self, adresse, port, nom, label):
        """Connexion en tâche de fond. Résultat : événement 'connecte' ou 'echec'."""
        self.port = port
        threading.Thread(target=self._connecter, args=(adresse, port, nom, label),
                         daemon=True).start()

    def _connecter(self, adresse, port, nom, label):
        sio = socketio.Client(reconnection=False)
        for evenement in EVENEMENTS:
            sio.on(evenement, lambda data=None, e=evenement: self._recu(e, data))
        try:
            sio.connect(f"http://{adresse}:{port}", wait_timeout=5)
            reponse = sio.call("rejoindre", {"nom": nom, "label": label}, timeout=5)
        except Exception as exc:
            try:
                sio.disconnect()
            except Exception:
                pass
            self._recu("echec", f"Connexion impossible à {adresse}:{port} ({exc.__class__.__name__}).")
            return
        if not reponse or not reponse.get("ok"):
            try:
                sio.disconnect()
            except Exception:
                pass
            self._recu("echec", (reponse or {}).get("raison", "Réponse invalide du serveur."))
            return
        sio.on("disconnect", lambda *a: self._recu("deconnecte", None))
        self.sio = sio
        self.moi = reponse["slot"]
        self._recu("connecte", None)

    def _recu(self, nom, data):
        if self.ferme:
            return
        if nom == "lobby":
            self.moi = data["moi"]
            self.joueurs = data["joueurs"]
            self.scores = data["scores"]
            self.manche = data["manche"]
        elif nom == "choix_role":
            self.joueurs = data["joueurs"]
            self.scores = data["scores"]
            self.manche = data["manche"]
            self.chooser = data["chooser"]
        elif nom == "manche":
            self.joueurs = data["joueurs"]
            self.scores = data["scores"]
            self.manche = data["manche"]
            self.roles = data["roles"]
        elif nom == "fin":
            self.scores = data["scores"]
        self.file.put((nom, data))

    def emettre(self, evenement, data=None):
        if self.sio is not None and self.sio.connected:
            try:
                self.sio.emit(evenement, data)
            except Exception:
                pass

    def lancer(self):
        self.emettre("lancer")

    def choisir_role(self, role):
        self.emettre("choisir_role", {"role": role})

    def jouer(self, coup):
        self.emettre("coup", {"depart": coup[0], "arrivee": coup[1]})

    def manche_suivante(self):
        self.emettre("manche_suivante")

    def demander_sync(self):
        self.emettre("sync")

    def fermer(self):
        self.ferme = True
        if self.sio is not None:
            try:
                self.sio.disconnect()
            except Exception:
                pass
        if self.serveur is not None:
            try:
                self.serveur.arreter()
            except Exception:
                pass
