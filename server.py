import pickle
import random
import socket
import threading
import time
from typing import Dict, List, Any

from game_hooks import hooks

BUFFERSIZE = 8192
PORT = 4321

print("Server Address: " + socket.gethostbyname(socket.gethostname()))

clients: List[socket.socket] = []
clients_lock = threading.Lock()


class Minion:
    def __init__(self, player_id):
        self.x = 50
        self.y = 50
        self.sync_img = None
        self.sync_img_index = None
        self.left_img_index = 0
        self.right_img_index = 0
        self.up_img_index = 0
        self.down_img_index = 0
        self.alive_status = True
        self.player_id = player_id
        self.player_colour = f"Player_{player_id}"
        self.tasks_completed = 0
        self.sabotagelights_sync = 0
        self.sabotagereactor_sync = 0
        self.victim_id = 0
        self.imposter = False
        self.emergency_sync = 0
        self.voted = None
        self.got_votes = 0
        self.emergency_meeting_img_sync = None
        self.emergency_meeting_img_sync_report = None
        self.victim_id_report = 0
        self.got_reported = False
        self.eject_sync = False
        self.eject_img = None


minionmap: Dict[int, Minion] = {}
registered_kills = set()
registered_meetings = set()
match_started = False


def check_and_start_match():
    global match_started
    if len(minionmap) >= 2 and not match_started:
        match_started = True
        players = [m.player_colour for m in minionmap.values()]
        impostors = [players[0]]
        for m in minionmap.values():
            if m.player_colour in impostors:
                m.imposter = True
        hooks.deal_roles(players, impostors)
        print(f"[Clanfall Multiuser] Match started with players: {players}, impostor: {impostors}")


def update_world(message: bytes):
    global match_started
    try:
        arr = pickle.loads(message)
    except Exception:
        return

    if not isinstance(arr, list) or len(arr) < 26:
        return

    player_id = arr[1]
    if player_id == 0 or player_id not in minionmap:
        return

    m = minionmap[player_id]
    m.x = arr[2]
    m.y = arr[3]
    m.alive_status = arr[4]
    m.sync_img = arr[5]
    m.sync_img_index = arr[6]
    m.left_img_index = arr[7]
    m.right_img_index = arr[8]
    m.up_img_index = arr[9]
    m.down_img_index = arr[10]
    if arr[11]:
        m.player_colour = arr[11]
    m.tasks_completed = arr[12]
    m.sabotagelights_sync = arr[13]
    m.sabotagereactor_sync = arr[14]

    # Hook 1: Kill Event
    victim_id = arr[15]
    if victim_id != 0 and (player_id, victim_id) not in registered_kills:
        registered_kills.add((player_id, victim_id))
        vic_name = minionmap[victim_id].player_colour if victim_id in minionmap else f"Player_{victim_id}"
        hooks.report_kill(m.player_colour, vic_name, "Electrical")
        print(f"[Clanfall Multiuser] Kill ingested: {m.player_colour} -> {vic_name}")

    m.victim_id = victim_id
    m.imposter = arr[16]

    # Hook 2: Emergency Meeting / Report Event
    emergency_sync = arr[17]
    if emergency_sync != 0 and emergency_sync not in registered_meetings:
        registered_meetings.add(emergency_sync)
        evidence = hooks.report_body(m.player_colour)
        print(f"[Clanfall Multiuser] Meeting triggered by {m.player_colour}. Evidence surfaced: {evidence}")

    m.emergency_sync = emergency_sync
    
    # Hook 3: Vote Event
    voted = arr[18]
    if voted is not None and m.voted != voted:
        target_name = minionmap[voted].player_colour if (isinstance(voted, int) and voted in minionmap) else str(voted)
        hooks.report_vote(m.player_colour, target_name)
        print(f"[Clanfall Multiuser] Vote ingested: {m.player_colour} -> {target_name}")

    m.voted = voted
    m.got_votes = arr[19]
    m.emergency_meeting_img_sync = arr[20]
    m.emergency_meeting_img_sync_report = arr[21]
    m.victim_id_report = arr[22]
    m.got_reported = arr[23]

    # Hook 4: Ejection Event
    eject_sync = arr[24]
    if eject_sync and not m.eject_sync:
        hooks.report_eject(m.player_colour)
        print(f"[Clanfall Multiuser] Ejection ingested: {m.player_colour}")

    m.eject_sync = eject_sync
    m.eject_img = arr[25]

    check_and_start_match()

    # Build update payload
    update = ['player locations']
    for val in minionmap.values():
        update.append([
            val.player_id, val.x, val.y, val.alive_status, val.sync_img,
            val.sync_img_index, val.left_img_index, val.right_img_index,
            val.up_img_index, val.down_img_index, val.player_colour,
            val.tasks_completed, val.sabotagelights_sync, val.sabotagereactor_sync,
            val.victim_id, val.imposter, val.emergency_sync, val.voted,
            val.got_votes, val.emergency_meeting_img_sync,
            val.emergency_meeting_img_sync_report, val.victim_id_report,
            val.got_reported, val.eject_sync, val.eject_img
        ])

    data = pickle.dumps(update)

    with clients_lock:
        to_remove = []
        for client_socket in clients:
            try:
                client_socket.sendall(data)
            except Exception:
                to_remove.append(client_socket)
        for dead in to_remove:
            if dead in clients:
                clients.remove(dead)


def handle_client(conn: socket.socket, addr):
    print(f"Connection address: {addr[0]} {addr[1]}")
    with clients_lock:
        clients.append(conn)

    player_id = random.randint(1000, 1000000)
    minionmap[player_id] = Minion(player_id)

    try:
        conn.sendall(pickle.dumps(['id update', player_id]))
        while True:
            data = conn.recv(BUFFERSIZE)
            if not data:
                break
            update_world(data)
    except Exception:
        pass
    finally:
        with clients_lock:
            if conn in clients:
                clients.remove(conn)
        if player_id in minionmap:
            del minionmap[player_id]
        conn.close()


def run_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('', PORT))
    server_socket.listen(10)
    print(f"2D Multiuser LAN Server listening on port {PORT} with Clanfall integration active...")
    while True:
        conn, addr = server_socket.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        t.start()


if __name__ == "__main__":
    run_server()