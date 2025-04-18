import asyncio
import websockets
# Import the exception type
import websockets.exceptions

import json
import random
import itertools
from collections import defaultdict, Counter
from typing import List, Dict, Tuple, Set, Optional, Any
import logging
import time

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] (%(funcName)s) %(message)s')

# --- Constants ---
MAX_PLAYERS = 8
STARTING_STACK = 1000
SMALL_BLIND = 10
BIG_BLIND = 20
HAND_END_DELAY = 5
ACTION_TIMEOUT = 60.0


# --- Card Logic ---
SUITS = "♠♥♦♣"
RANKS = "23456789TJQKA"
RANK_VALUES = {rank: i for i, rank in enumerate(RANKS)}

def create_deck():
    """Creates a standard 52-card deck."""
    return [rank + suit for rank in RANKS for suit in SUITS]

# --- Hand Evaluation ---
def get_rank_value(rank_char: str) -> int: return RANK_VALUES.get(rank_char, -1)

def evaluate_hand(hand: List[str], community_cards: List[str]) -> Tuple[int, List[int], str, List[str]]:
    """Evaluates the best possible 5-card poker hand from 7 cards."""
    all_cards = hand + community_cards
    if not all_cards or len(all_cards) < 5: return (0, [], "Invalid Hand (<5 cards)", [])
    best_score = (-1, [], "Invalid Hand", [])
    for combo_tuple in itertools.combinations(all_cards, 5):
        combo = list(combo_tuple)
        valid_combo = [c for c in combo if isinstance(c, str) and len(c) >= 1]
        if len(valid_combo) != 5: continue
        ranks = sorted([get_rank_value(c[0]) for c in valid_combo], reverse=True)
        suits = [c[1] for c in valid_combo if len(c) >= 2]
        if len(suits) != 5: continue
        is_flush = len(set(suits)) == 1
        is_straight = all(ranks[i] == ranks[0] - i for i in range(5))
        if not is_straight and ranks == [12, 3, 2, 1, 0]: is_straight = True; ranks = [3, 2, 1, 0, -1]
        is_sf = is_straight and is_flush
        rank_counts = Counter(ranks); counts = sorted(rank_counts.values(), reverse=True)
        primary_kickers = sorted(rank_counts.keys(), key=lambda r: (rank_counts[r], r), reverse=True)
        current_score = (-1, [], "Unknown", [])
        if is_sf: current_score = (9, [ranks[0]], "Straight Flush", combo)
        elif counts[0] == 4: current_score = (8, primary_kickers, "Four of a Kind", combo)
        elif counts == [3, 2]: current_score = (7, primary_kickers, "Full House", combo)
        elif is_flush: current_score = (6, ranks, "Flush", combo)
        elif is_straight: current_score = (5, [ranks[0]], "Straight", combo)
        elif counts[0] == 3: current_score = (4, primary_kickers, "Three of a Kind", combo)
        elif counts == [2, 2, 1]: current_score = (3, primary_kickers, "Two Pair", combo)
        elif counts[0] == 2: current_score = (2, primary_kickers, "One Pair", combo)
        else: current_score = (1, ranks, "High Card", combo)
        if current_score[0] > best_score[0] or (current_score[0] == best_score[0] and current_score[1] > best_score[1]):
            best_5_display = sorted(combo, key=lambda c: get_rank_value(c[0]), reverse=True)
            best_score = (current_score[0], current_score[1], current_score[2], best_5_display)
    if best_score[0] == -1:
        valid_all = [c for c in all_cards if isinstance(c, str) and len(c) >= 1]
        if not valid_all: return (0, [], "Invalid Hand", [])
        ranks = sorted([get_rank_value(c[0]) for c in valid_all], reverse=True)
        best_5 = sorted(valid_all, key=lambda c: get_rank_value(c[0]), reverse=True)[:5]
        return (1, ranks[:5], "High Card (Fallback)", best_5)
    final_name = best_score[2]
    if best_score[0] == 1 and best_score[1]: final_name = f"High Card ({RANKS[best_score[1][0]]})"
    elif best_score[0] == 2 and best_score[1]: final_name = f"Pair of {RANKS[best_score[1][0]]}s"
    elif best_score[0] == 3 and len(best_score[1]) >= 2: final_name = f"Two Pair, {RANKS[best_score[1][0]]}s & {RANKS[best_score[1][1]]}s"
    elif best_score[0] == 7 and len(best_score[1]) >= 2: final_name = f"Full House, {RANKS[best_score[1][0]]}s full of {RANKS[best_score[1][1]]}s"
    return (best_score[0], best_score[1], final_name, best_score[3])

# --- Game State Classes ---
class Player:
    def __init__(self, player_id: int, websocket):
        self.id: int = player_id; self.name: Optional[str] = None; self.websocket = websocket
        self.stack: int = STARTING_STACK; self.hand: List[str] = []; self.current_bet: int = 0
        self.total_bet_this_hand: int = 0; self.status: str = "waiting"; self.is_dealer: bool = False
        self.last_action: Optional[str] = None; self.last_action_time: float = 0.0

    def to_dict(self, show_hand=False):
        display_name = self.name if self.name else f"Player {self.id}"
        hand_display = ['??', '??'] if self.hand else []
        if self.status == "folded": hand_display = []
        elif show_hand and self.hand: hand_display = self.hand
        return {"id": self.id, "name": display_name, "stack": self.stack, "hand": hand_display,
                "current_bet": self.current_bet, "status": self.status, "is_dealer": self.is_dealer,
                "last_action": self.last_action}

    def can_act(self) -> bool: return self.status == "active" and self.stack > 0

class PokerGame:
    def __init__(self):
        self.players: Dict[int, Player] = {}; self.connected_websockets_set: Set = set()
        self.next_player_id: int = 1; self.deck: List[str] = []; self.community_cards: List[str] = []
        self.pot: int = 0; self.current_bet: int = 0; self.last_raiser_id: Optional[int] = None
        self.current_player_id: Optional[int] = None; self.dealer_button_pos: int = -1
        self.small_blind_pos: int = 0; self.big_blind_pos: int = 0; self.game_stage: str = "idle"
        self.active_players_order: List[int] = []; self.game_loop_task: Optional[asyncio.Task] = None
        self._action_lock = asyncio.Lock()
        self._player_action_event: Optional[asyncio.Event] = None

    # --- Connection & Setup Methods ---
    async def register_player(self, websocket):
        """Adds a new player, sends ID, waits for name."""
        if len(self.players) >= MAX_PLAYERS:
            logging.warning(f"Connection rejected: Game full."); await self.send_error(websocket, "Game is full.")
            try: await websocket.close(code=1008, reason="Game full")
            except: pass; return
        player_id = self.next_player_id; player = Player(player_id, websocket)
        async with self._action_lock:
            self.players[player_id] = player; self.connected_websockets_set.add(websocket); self.next_player_id += 1
        logging.info(f"Player {player_id} connected. Requesting name.")
        await self.send_message(websocket, "assign_id", {"playerId": player_id})
        # ** REMOVED initial game_state broadcast here **
        # await self.send_message(websocket, "game_state", self.get_state_for_player(player_id))
        # Now we wait for the 'set_name' message before broadcasting state including this player


    async def set_player_name(self, player_id: int, name: str):
        """Sets player name, broadcasts state, and triggers game start check."""
        async with self._action_lock:
            player = self.players.get(player_id)
            if player:
                if player.name is None: # Only log/broadcast first time name is set
                     player.name = name.strip()[:15]; logging.info(f"P{player_id} set name: '{player.name}'.")
                     broadcast_needed = True
                else: # Name already set, ignore? Or allow change? For now, ignore change.
                     logging.warning(f"P{player_id} attempted to change name to '{name}', ignored.")
                     broadcast_needed = False # Don't broadcast if name didn't change
            else: logging.error(f"Cannot set name for unknown P{player_id}"); return

        # Broadcast state *after* name is set so others see the named player
        if broadcast_needed:
            await self.broadcast_game_state()
            await self.check_start_game()

    async def check_start_game(self):
        # Count players who have a name
        ready_players = [p for p in self.players.values() if p.name is not None]
        num_ready = len(ready_players)
        if num_ready >= 2 and self.game_stage == "idle":
            async with self._action_lock:
                # Double check condition inside lock to prevent race condition
                if self.game_stage == "idle" and (not self.game_loop_task or self.game_loop_task.done()):
                    logging.info(f"{num_ready} players ready. Starting game loop.")
                    self.game_loop_task = asyncio.create_task(self.game_loop())
        elif self.game_stage != "idle": logging.debug(f"Check start: Game in progress ({self.game_stage}).")
        else: logging.debug(f"Check start: Not enough players ({num_ready}). Waiting.")

    async def unregister_player(self, websocket):
        player_id_to_remove = None; player_name = "Unknown"; player_status = "unknown"; was_their_turn = False; player = None
        async with self._action_lock:
            for pid, p in self.players.items():
                if p.websocket == websocket:
                    player_id_to_remove = pid; player_name = p.name or f"P{pid}"; player_status = p.status
                    was_their_turn = (self.current_player_id == pid); player = p; break
            self.connected_websockets_set.discard(websocket)
            if player_id_to_remove is None: logging.debug(f"WS disconnected but no player found."); return
            logging.info(f"{player_name} (ID:{player_id_to_remove}) disconnected.")
            should_check_hand_end = False
            if player and player_status not in ["waiting", "folded"] and self.game_stage not in ["idle", "hand_over", "showdown", "starting"]:
                logging.info(f"Folding disconnected player {player_name}.")
                player.status = "folded"; player.hand = []; player.last_action = "fold"; should_check_hand_end = True
            if player_id_to_remove in self.players: del self.players[player_id_to_remove]
            active_game_players = [p for p in self.players.values() if p.name is not None]
            num_remaining = len(active_game_players)
            reset_game = False
            if num_remaining < 2 and self.game_stage != "idle":
                logging.warning(f"<{num_remaining} players. Resetting game."); self.game_stage = "idle"; self.pot = 0
                self.community_cards = []; self.current_player_id = None; self.current_bet = 0
                if self.game_loop_task and not self.game_loop_task.done(): self.game_loop_task.cancel(); self.game_loop_task = None
                if was_their_turn and self._player_action_event: self._player_action_event.set(); self._player_action_event = None
                reset_game = True
        # --- Outside Lock ---
        await self.broadcast_game_state() # Broadcast state showing player removed
        if reset_game:
            # Reset remaining players' status locally (no need for lock if game loop stopped)
            for p in self.players.values(): p.status = "waiting"; p.hand = []; p.current_bet = 0; p.total_bet_this_hand = 0; p.is_dealer = False; p.last_action = None
            await self.broadcast("game_message", {"message": "Not enough players. Waiting..."})
            await self.broadcast_game_state(); return # Broadcast reset state
        if should_check_hand_end:
            if was_their_turn:
                 async with self._action_lock:
                     if self._player_action_event: self._player_action_event.set(); self._player_action_event = None
                 logging.debug(f"Set action event due to P{player_id_to_remove} disconnect.")
            if not await self.check_hand_over_conditions() and was_their_turn:
                 logging.debug(f"Player disconnected on turn. Advancing."); await self.check_round_end()

    # --- Communication Methods ---
    async def send_message(self, websocket, msg_type: str, payload: Any):
        try: message = json.dumps({"type": msg_type, "payload": payload}); await websocket.send(message)
        except websockets.exceptions.ConnectionClosed: logging.warning(f"Send fail: Conn Closed ws={getattr(websocket, 'id', id(websocket))}")
        except Exception as e: logging.error(f"Send {msg_type} error ws={getattr(websocket, 'id', id(websocket))}: {e}", exc_info=False)
    async def send_error(self, websocket, error_message: str):
        ws_id = getattr(websocket, 'id', id(websocket)); logging.warning(f"SEND_ERR ws={ws_id}: {error_message}")
        await self.send_message(websocket, "error", {"message": error_message})
    async def broadcast(self, msg_type: str, payload: Any, exclude_websockets: Set = set()):
        if not self.connected_websockets_set: return
        tasks = [asyncio.create_task(self.send_message(ws, msg_type, payload)) for ws in list(self.connected_websockets_set) if ws not in exclude_websockets]
        if tasks: await asyncio.wait(tasks)
    async def broadcast_game_state(self):
        if not self.players: return
        # Create state for each player and send individually
        tasks = []
        # Iterate over a copy of player values in case dict changes
        current_players = list(self.players.values())
        for p in current_players:
             # Ensure player websocket is still valid before creating task
             if p.websocket in self.connected_websockets_set:
                 state_for_player = self.get_state_for_player(p.id)
                 tasks.append(asyncio.create_task(self.send_message(p.websocket, "game_state", state_for_player)))
        # tasks = [asyncio.create_task(self.send_message(p.websocket, "game_state", self.get_state_for_player(p.id))) for p in list(self.players.values()) if p.websocket in self.connected_websockets_set]
        if tasks: await asyncio.wait(tasks)

    def get_state_for_player(self, perspective_player_id: int) -> Dict[str, Any]:
        # Lock might be needed if players dict or player attributes change frequently during read
        # But reads are generally safer than writes. Let's assume it's okay for now.
        player_states = {}
        dealer_id = -1
        # Read shared state that might change
        current_stage = self.game_stage
        current_cc = self.community_cards
        current_pot = self.pot
        acting_player = self.current_player_id
        is_showdown = current_stage == "showdown"

        # Create player state dicts
        for pid, p in self.players.items():
            # Exclude players who haven't set a name yet? Only if game hasn't started?
            # Let's include them for now, client handles display name "Player X"
            # if p.name is None and current_stage != "idle": continue # Option to hide nameless players

            show_hand = (pid == perspective_player_id) or (is_showdown and p.status != "folded")
            player_states[pid] = p.to_dict(show_hand=show_hand);
            if p.is_dealer: dealer_id = pid

        return {"players": player_states, "community_cards": current_cc, "pot": current_pot, "current_player_id": acting_player,
                "dealer_id": dealer_id, "game_stage": current_stage, "bigBlind": BIG_BLIND}

    # --- Game Flow Methods ---
    async def game_loop(self):
        logging.info("GAME LOOP STARTED")
        try:
            while True:
                async with self._action_lock:
                    current_players_list = list(self.players.values())
                    named_players = [p for p in current_players_list if p.name is not None]
                    can_continue = len(named_players) >= 2
                if not can_continue:
                    logging.warning("Game loop: <2 players. Stopping.")
                    async with self._action_lock: self.game_stage = "idle"
                    await self.broadcast("game_message", {"message": "Game paused. Waiting..."})
                    await self.broadcast_game_state(); break
                logging.info("Starting new hand..."); await self.start_new_hand_setup()
                if self.game_stage != "hand_over": await self.run_betting_round() # Preflop
                if self.game_stage != "hand_over": await self.deal_community_cards("flop"); await self.run_betting_round() # Flop
                if self.game_stage != "hand_over": await self.deal_community_cards("turn"); await self.run_betting_round() # Turn
                if self.game_stage != "hand_over": await self.deal_community_cards("river"); await self.run_betting_round() # River
                if self.game_stage != "hand_over": await self.perform_showdown()
                logging.info(f"Hand concluded (Stage: {self.game_stage}). Wait {HAND_END_DELAY}s..."); await self.broadcast_game_state()
                await self.broadcast("game_message", {"message": f"--- Next hand in {HAND_END_DELAY}s ---"}); await asyncio.sleep(HAND_END_DELAY)
        except asyncio.CancelledError: logging.info("Game loop cancelled.")
        except Exception as e: logging.exception(f"!!! GAME LOOP ERROR: {e} !!!")
        finally:
             async with self._action_lock: self.game_stage = "idle"; self.game_loop_task = None
             logging.info("GAME LOOP EXITED")

    async def start_new_hand_setup(self):
        logging.info("Setting up new hand")
        dealer_id = -1; sb_id = -1; bb_id = -1; sb_amt = 0; bb_amt = 0; first_actor_id = None
        async with self._action_lock:
            self.game_stage = "starting"; self.deck = create_deck(); random.shuffle(self.deck); self.community_cards = []
            self.pot = 0; self.current_bet = 0; self.last_raiser_id = None; self.current_player_id = None; self._player_action_event = None
            eligible = {pid: p for pid, p in self.players.items() if p.stack > 0 and p.name is not None}
            if len(eligible) < 2: logging.warning("Setup fail: <2 eligible"); self.game_stage = "idle"; raise Exception("Setup fail")
            self.active_players_order = sorted(list(eligible.keys())); num_eligible = len(self.active_players_order)
            logging.debug(f"Active Order: {self.active_players_order}")
            for player in self.players.values():
                if player.id in eligible: player.hand=[]; player.current_bet=0; player.total_bet_this_hand=0; player.status="active"; player.is_dealer=False; player.last_action=None
                else: player.status="waiting"; player.hand=[]; player.current_bet=0; player.total_bet_this_hand=0; player.is_dealer=False; player.last_action=None
            if self.dealer_button_pos == -1: self.dealer_button_pos = random.randrange(num_eligible)
            else: self.dealer_button_pos = (self.dealer_button_pos + 1) % num_eligible
            dealer_found = False
            for i in range(num_eligible):
                 check_idx = (self.dealer_button_pos + i) % num_eligible
                 if self.active_players_order[check_idx] in eligible: self.dealer_button_pos = check_idx; dealer_found = True; break
            if not dealer_found: logging.error("Failed find valid dealer!"); self.dealer_button_pos=0
            dealer_id = self.active_players_order[self.dealer_button_pos]; self.players[dealer_id].is_dealer = True
            if num_eligible == 2: self.small_blind_pos=self.dealer_button_pos; self.big_blind_pos=(self.dealer_button_pos + 1) % num_eligible
            else: self.small_blind_pos=(self.dealer_button_pos + 1) % num_eligible; self.big_blind_pos=(self.dealer_button_pos + 2) % num_eligible
            sb_id=self.active_players_order[self.small_blind_pos]; bb_id=self.active_players_order[self.big_blind_pos]
            deal_start = (self.dealer_button_pos + 1) % num_eligible
            for _ in range(2):
                for i in range(num_eligible):
                    p_id = self.active_players_order[(deal_start + i) % num_eligible]
                    if self.players[p_id].status=="active":
                         if self.deck: self.players[p_id].hand.append(self.deck.pop())
                         else: logging.error("Deck empty deal!"); raise Exception("Deck Empty")
            sb_amt = self._post_blind_internal(sb_id, SMALL_BLIND); bb_amt = self._post_blind_internal(bb_id, BIG_BLIND)
            self.current_bet = bb_amt; self.last_raiser_id = bb_id
            start_action_pos = (self.big_blind_pos + 1) % num_eligible;
            for i in range(num_eligible):
                check_id = self.active_players_order[(start_action_pos + i) % num_eligible]
                if self.players[check_id].can_act(): first_actor_id = check_id; break
            if first_actor_id is None: logging.warning("No active player found start preflop."); self.current_player_id = None
            else: self.current_player_id = first_actor_id
            self.game_stage = "preflop";
        # --- Broadcast after lock ---
        dealer_player = self.players.get(dealer_id); sb_player = self.players.get(sb_id); bb_player = self.players.get(bb_id)
        dealer_name = dealer_player.name if dealer_player else f"P{dealer_id}"; sb_name = sb_player.name if sb_player else f"P{sb_id}"; bb_name = bb_player.name if bb_player else f"P{bb_id}"
        logging.info(f"Dealer:{dealer_name}, SB:{sb_name}, BB:{bb_name}")
        await self.broadcast("game_message", {"message": f"--- Start Hand --- Dealer: {dealer_name}"})
        await self.broadcast_game_state(); await asyncio.sleep(0.5)
        sb_status = self.players.get(sb_id, {}).status; bb_status = self.players.get(bb_id, {}).status
        await self.broadcast("game_message", {"message": f"{sb_name} posts SB ${sb_amt}" + (" (All-in)" if sb_status=='all-in' else "")})
        await self.broadcast_game_state(); await asyncio.sleep(0.2)
        await self.broadcast("game_message", {"message": f"{bb_name} posts BB ${bb_amt}" + (" (All-in)" if bb_status=='all-in' else "")})
        await self.broadcast_game_state(); await asyncio.sleep(0.2)
        if await self.check_hand_over_conditions(): return
        current_player = self.players.get(self.current_player_id)
        if current_player: logging.info(f"Preflop starts with {current_player.name or f'P{self.current_player_id}'}")
        else: logging.warning("Setup done but no first actor found. Hand should end.")

    def _post_blind_internal(self, player_id: int, amount: int) -> int:
        player = self.players[player_id]; blind_amount = min(amount, player.stack)
        player.stack -= blind_amount; player.current_bet = blind_amount; player.total_bet_this_hand += blind_amount
        self.pot += blind_amount; player.last_action = "blind"
        if player.stack == 0: player.status = "all-in"
        logging.info(f"{player.name or f'P{player.id}'} posts blind ${blind_amount}" + (" (All-in)" if player.status=='all-in' else ""))
        return blind_amount

    async def run_betting_round(self):
        async with self._action_lock:
             current_stage = self.game_stage; active_order = list(self.active_players_order)
             num_active = len(active_order); dealer_pos = self.dealer_button_pos
        if current_stage == "hand_over": return
        logging.info(f"--- Start Betting Round: {current_stage.upper()} ---")
        if num_active == 0: logging.warning(f"Round {current_stage}: No active players. Skip."); return
        if current_stage != "preflop":
             async with self._action_lock:
                self.current_bet = 0; self.last_raiser_id = None; self.current_player_id = None
                for pid in active_order:
                    if (p := self.players.get(pid)): p.current_bet = 0
                first_actor_pos = -1; start_idx = (dealer_pos + 1) % num_active
                for i in range(num_active):
                    check_pos = (start_idx + i) % num_active
                    p_id = active_order[check_pos]; player = self.players.get(p_id)
                    if player and player.can_act(): first_actor_pos = check_pos; break
                if first_actor_pos != -1: self.current_player_id = active_order[first_actor_pos]
                else: logging.warning(f"No active player found left of dealer {current_stage}."); self.current_player_id = None
             await self.broadcast_game_state()
             first_actor_player = self.players.get(self.current_player_id)
             first_actor_name = first_actor_player.name if first_actor_player else "None"
             logging.info(f"Post-flop round starts with {first_actor_name}")
             if self.current_player_id is None: await self.check_hand_over_conditions(); return
        # --- Betting Loop ---
        action_count = 0; max_actions = num_active * 3 + 5
        while action_count < max_actions:
            action_count += 1; logging.debug(f"Betting loop iter {action_count}/{max_actions}")
            async with self._action_lock: current_actor_id = self.current_player_id; last_aggro_id = self.last_raiser_id; current_stage_check = self.game_stage
            if current_stage_check == "hand_over": logging.info("Betting loop end: Stage is hand_over."); return
            if await self.check_hand_over_conditions(): logging.info(f"Betting loop end: Hand over condition met."); return
            async with self._action_lock: round_complete = self.is_betting_round_complete(last_aggro_id)
            if round_complete: logging.info(f"Betting round {self.game_stage} complete."); return
            if current_actor_id is None: logging.error("Betting loop ERROR: current_actor_id is None! Attempt advance."); await self.advance_to_next_player(); continue
            current_player = self.players.get(current_actor_id)
            if not current_player: logging.error(f"Betting loop ERROR: Player {current_actor_id} not found! Advancing."); await self.advance_to_next_player(); continue
            if not current_player.can_act(): logging.debug(f"Betting loop: Skip P{current_actor_id} (Status:{current_player.status}). Advance."); await self.advance_to_next_player(); await asyncio.sleep(0.01); continue
            await self.request_player_action()
            logging.debug(f"Betting loop: Waiting for action event from P{current_actor_id}")
            async with self._action_lock: current_event = self._player_action_event
            if current_event:
                try:
                    await asyncio.wait_for(current_event.wait(), timeout=ACTION_TIMEOUT)
                    logging.debug(f"Betting loop: Action event received/set for P{current_actor_id}")
                except asyncio.TimeoutError:
                    logging.warning(f"Player P{current_actor_id} timed out.")
                    await self.handle_player_action(current_actor_id, "fold", None) # Auto-fold on timeout
            else:
                 logging.warning(f"Betting loop: No action event found for P{current_actor_id} after request?")
                 await asyncio.sleep(0.1)
        logging.error(f"Betting round {self.game_stage} exceeded max actions!")

    async def request_player_action(self):
        player_id_to_request = None; player_websocket = None; payload = None; should_advance = False
        async with self._action_lock:
            if self.current_player_id is None: logging.error("REQ_ACTION ERR: No current player."); return
            player = self.players.get(self.current_player_id)
            if not player: logging.error(f"REQ_ACTION ERR: Player {self.current_player_id} not found."); return
            if not player.can_act():
                logging.warning(f"REQ_ACTION WARN: Player {player.name or f'P{player.id}'} cannot act. Advancing."); should_advance=True
            else:
                player_id = player.id; player_stack = player.stack; player_bet = player.current_bet; player_name = player.name or f"P{player.id}"
                round_bet = self.current_bet; stage = self.game_stage; last_raiser = self.last_raiser_id
                bb_id = self.active_players_order[self.big_blind_pos] if self.big_blind_pos < len(self.active_players_order) else None
                player_websocket = player.websocket; player_id_to_request = player_id
                allowed = ["fold"]; call_needed = max(0, round_bet - player_bet); eff_stack = player_stack
                if player_bet == round_bet: allowed.append("check"); call_amt = 0
                elif call_needed > 0 and eff_stack > 0: allowed.append("call"); call_amt = min(call_needed, eff_stack)
                else: call_amt = 0
                max_bet = player_bet + eff_stack; min_open = BIG_BLIND
                prev_bet = self.get_previous_bet_level(); last_raise = round_bet - prev_bet
                min_raise_delta = max(last_raise, BIG_BLIND); req_min_raise = round_bet + min_raise_delta; min_total = 0
                if round_bet == 0 and eff_stack > 0: allowed.append("bet"); min_total = min(min_open, max_bet)
                is_bb_opt = (stage == "preflop" and player_id == bb_id and player_bet == round_bet and last_raiser == player_id)
                if (round_bet > 0 or is_bb_opt) and eff_stack > 0:
                     if max_bet > round_bet: allowed.append("raise"); min_raise = min(req_min_raise, max_bet); min_total = max(min_total, min_raise)
                final_max = max(min_total, max_bet);
                logging.debug(f" P{player_id} Opts: {allowed}, Call:{call_amt}, Min:{min_total}, Max:{final_max}")
                payload = {"playerId": player_id, "actions": allowed, "callAmount": call_amt, "minRaise": min_total, "maxRaise": final_max,
                           "currentBet": round_bet, "stack": player_stack, "bigBlind": BIG_BLIND}
                self._player_action_event = asyncio.Event()
                logging.debug(f"Created action event for P{player_id}")
        # --- Actions After Lock Release ---
        if should_advance: await self.advance_to_next_player(); return
        if player_websocket and payload:
            logging.info(f"Requesting action from P{player_id_to_request}")
            await self.send_message(player_websocket, "player_turn", payload)
            await self.broadcast_game_state()

    def get_previous_bet_level(self) -> int: # Assumes lock held or state stable
        bets = sorted([p.current_bet for pid in self.active_players_order if (p := self.players.get(pid)) and p.status not in ['folded', 'waiting'] and p.current_bet < self.current_bet], reverse=True)
        return bets[0] if bets else 0

    async def handle_player_action(self, player_id: int, action: str, amount: Optional[int] = None):
        error_to_send = None; final_valid_action = False; final_broadcast_payload = None; player_websocket = None
        event_to_set = None
        async with self._action_lock:
            logging.debug(f"HANDLE_ACTION: P{player_id} tries '{action}' {amount if amount else ''}. Current Actor: P{self.current_player_id}, Stage: {self.game_stage}, RoundBet: {self.current_bet}")
            if self.game_stage in ["idle", "starting", "hand_over", "showdown"]: logging.warning(f"Action ignore: Stage {self.game_stage}"); return
            if player_id != self.current_player_id: logging.warning(f"Action ignore: P{player_id} OOT (Expected P{self.current_player_id})"); error_to_send = ("Not your turn.", player_id); valid_action = False
            else:
                player = self.players.get(player_id)
                if not player: logging.error(f"Action ERR: Player {player_id} not found!"); return
                player_name = player.name or f"P{player.id}"; player_websocket = player.websocket
                if not player.can_act(): logging.warning(f"Action ignore: {player_name} Cannot Act (Status: {player.status})"); error_to_send = (f"Cannot act (Status: {player.status}).", player_id); valid_action = False
                else:
                    logging.info(f"Processing action: {player_name} - {action.upper()} {f'${amount}' if amount is not None else ''}")
                    valid_action = False; error_msg = None; broadcast_payload = {"playerId": player_id, "action": action, "amount": None}
                    # --- Action Logic ---
                    if action == "fold": player.status = "folded"; player.hand = []; player.last_action = "fold"; valid_action = True
                    elif action == "check":
                        if player.current_bet == self.current_bet: player.last_action = "check"; valid_action = True
                        else: error_msg = f"Cannot check. Bet is ${self.current_bet}."
                    elif action == "call":
                        call_needed = max(0, self.current_bet - player.current_bet)
                        if call_needed > 0:
                            actual_call = min(call_needed, player.stack); player.stack -= actual_call; bet_inc = actual_call
                            player.current_bet += bet_inc; player.total_bet_this_hand += bet_inc; self.pot += bet_inc; player.last_action = "call"; valid_action = True
                            broadcast_payload["amount"] = player.current_bet
                            if player.stack == 0: player.status = "all-in"; logging.info(f"{player_name} All-in calling.")
                        else: error_msg = "Cannot call (already matched)."
                    elif action == "bet" or action == "raise":
                        if amount is None or not isinstance(amount, int) or amount <= 0: error_msg = "Invalid amount."
                        else:
                            total_bet = amount; bet_inc = total_bet - player.current_bet
                            if bet_inc > player.stack: error_msg = f"Insufficient stack."
                            else:
                                is_open = (self.current_bet == 0)
                                bb_id = self.active_players_order[self.big_blind_pos] if self.big_blind_pos < len(self.active_players_order) else None
                                if action == "bet" and not is_open: error_msg = f"Cannot 'bet', must 'call'/'raise'."
                                elif action == "raise" and is_open:
                                     is_bb_option = (self.game_stage == "preflop" and player.id == bb_id and player.current_bet == self.current_bet)
                                     if not is_bb_option: error_msg = f"Cannot 'raise', must 'bet'."
                                else: # Check min amount
                                    is_all_in = (bet_inc == player.stack); min_open = BIG_BLIND
                                    prev_bet = self.get_previous_bet_level(); last_raise = self.current_bet - prev_bet
                                    min_raise_delta = max(last_raise, BIG_BLIND); req_min_raise = self.current_bet + min_raise_delta
                                    min_legal = min_open if action == "bet" else req_min_raise
                                    if total_bet < min_legal and not is_all_in: error_msg = f"Min {action} is ${min_legal}."
                                    else: # VALID ACTION
                                        player.stack -= bet_inc; player.current_bet = total_bet; player.total_bet_this_hand += bet_inc
                                        self.pot += bet_inc; player.last_action = action; valid_action = True; broadcast_payload["amount"] = total_bet
                                        prev_round_bet = self.current_bet; self.current_bet = total_bet
                                        is_full_aggro = False
                                        if action == "bet" and (total_bet >= min_open or is_all_in): is_full_aggro = True
                                        elif action == "raise" and (total_bet >= req_min_raise): is_full_aggro = True
                                        if is_full_aggro: self.last_raiser_id = player_id; logging.debug(f" Action by {player_name} reopens betting.")
                                        else: logging.debug(f" Action by {player_name} does not fully reopen betting.")
                                        if player.stack == 0: player.status = "all-in"; logging.info(f"{player_name} All-in {action}ing.")
                    else: error_msg = f"Unknown action: {action}"
                    # --- Store results ---
                    final_valid_action = valid_action; final_error_msg = error_msg
                    final_broadcast_payload = broadcast_payload if final_valid_action else None
                    if error_to_send is None and final_error_msg: error_to_send = (final_error_msg, player_id)
                    # --- Signal Event ---
                    if self._player_action_event:
                        logging.debug(f"Action processed (Valid={final_valid_action}), attempting to set event for P{player_id}")
                        event_to_set = self._player_action_event
                        self._player_action_event = None # Clear ref immediately
                    else: logging.warning(f"No action event found for P{player_id} during handle_action.")
        # --- Actions After Lock Release ---
        if event_to_set: logging.debug(f"Setting action event for P{player_id} outside lock."); event_to_set.set()
        if final_valid_action:
            if final_broadcast_payload: await self.broadcast("player_action", final_broadcast_payload)
            await self.broadcast_game_state()
            if not await self.check_hand_over_conditions(): await self.check_round_end()
            else: logging.info("Hand ended immediately after valid action.")
        else: # Invalid action or OOT
             if error_to_send:
                  err_msg, err_pid = error_to_send; err_player = self.players.get(err_pid)
                  if err_player: await self.send_error(err_player.websocket, err_msg)
             # Only re-request if it was the correct player's turn but action was invalid
             if player_id == self.current_player_id: await self.request_player_action()

    async def check_hand_over_conditions(self) -> bool:
        is_over = False; winner_id = None; pot_to_award = 0
        async with self._action_lock:
            if self.game_stage == "hand_over": return True
            contenders = [p for pid in self.active_players_order if (p := self.players.get(pid)) and p.status in ["active", "all-in"]]
            num_contenders = len(contenders); winner_id = contenders[0].id if num_contenders == 1 else None
            is_over = num_contenders <= 1
            if is_over:
                 logging.info(f"CHECK_HAND_OVER: {num_contenders} contenders. Ending hand."); self.game_stage = "hand_over"; self.current_player_id = None
                 pot_to_award = self.pot; self.pot = 0
                 if self._player_action_event: self._player_action_event.set(); self._player_action_event = None; logging.debug("Cleared action event on hand over.")
        if is_over: await self.award_pot(winner_id, pot_to_award, is_uncontested=True)
        logging.debug(f"check_hand_over result: {is_over}")
        return is_over

    def is_betting_round_complete(self, last_aggressor_id: Optional[int]) -> bool:
        """Checks if betting round complete. Assumes lock is held."""
        actionable = [p for pid in self.active_players_order if (p := self.players.get(pid)) and p.can_act()]
        if not actionable: logging.debug("is_round_complete: No actionable players. TRUE"); return True
        all_matched = True
        for p in self.players.values():
            if p.id not in self.active_players_order or p.status in ["waiting", "folded"]: continue
            if p.status != "all-in" and p.current_bet < self.current_bet: logging.debug(f"is_round_complete: P{p.id} Bet:{p.current_bet}<RoundBet:{self.current_bet}. FALSE"); return False
        # Check BB Option
        is_bb_preflop_option = False
        if self.game_stage == "preflop" and self.current_player_id is not None and self.big_blind_pos < len(self.active_players_order):
            bb_player_id = self.active_players_order[self.big_blind_pos]
            if self.current_player_id == bb_player_id and last_aggressor_id == bb_player_id:
                 bb_player = self.players.get(bb_player_id)
                 if bb_player and bb_player.last_action == "blind": is_bb_preflop_option = True
        if is_bb_preflop_option: logging.debug(f"is_round_complete: BB Preflop Option. FALSE"); return False
        # Check if action back on aggressor
        if self.current_player_id == last_aggressor_id: logging.debug(f"is_round_complete: Action back on aggressor P{last_aggressor_id}. TRUE"); return True
        logging.debug(f"is_round_complete: All matched, action P{self.current_player_id} != Aggressor P{last_aggressor_id}. FALSE")
        return False

    async def check_round_end(self):
        logging.debug("Entering check_round_end")
        round_complete = False; current_actor_before_check = self.current_player_id
        async with self._action_lock:
            if self.game_stage == "hand_over": logging.debug("check_round_end: Hand over, exit."); return
            round_complete = self.is_betting_round_complete(self.last_raiser_id)
            logging.debug(f"check_round_end: is_betting_round_complete = {round_complete}")
        if round_complete: logging.info(f"Confirmed: Betting round {self.game_stage} ended.")
        else:
            await self.advance_to_next_player()
            async with self._action_lock:
                 current_actor_after_advance = self.current_player_id; player_can_still_act = False
                 if current_actor_after_advance is not None:
                      player = self.players.get(current_actor_after_advance)
                      if player: player_can_still_act = player.can_act()
                 if current_actor_after_advance == current_actor_before_check and player_can_still_act:
                      logging.warning(f"check_round_end: Advancing turn failed to change player from P{current_actor_before_check}! Still their turn.")
                      if self._player_action_event: logging.warning("Force setting action event as turn didn't advance."); self._player_action_event.set(); self._player_action_event = None

    async def advance_to_next_player(self):
        async with self._action_lock:
            original_player_id = self.current_player_id
            logging.debug(f"Attempting advance from P{original_player_id}")
            num_players = len(self.active_players_order)
            if num_players == 0: logging.warning("Advance FAIL: No active players."); self.current_player_id = None; return
            try: start_idx = self.active_players_order.index(original_player_id) if original_player_id is not None else -1
            except ValueError: logging.error(f"Advance ERR: P{original_player_id} not in order {self.active_players_order}. Find first."); start_idx = -1
            next_player_found = False
            for i in range(1, num_players + 1):
                next_idx = (start_idx + i) % num_players if start_idx != -1 else (i-1) % num_players
                next_pid = self.active_players_order[next_idx]; player = self.players.get(next_pid)
                logging.debug(f"Advance check: Idx {next_idx} -> P{next_pid} (Status:{player.status if player else 'N/A'}, Stack:{player.stack if player else 'N/A'})")
                if player and player.can_act():
                    self.current_player_id = next_pid; player_name = player.name or f"P{next_pid}"
                    logging.info(f"Advanced turn from P{original_player_id} -> P{self.current_player_id} ('{player_name}')")
                    if self._player_action_event: self._player_action_event.set(); self._player_action_event = None; logging.debug("Cleared action event on advance.")
                    next_player_found = True; break
            if not next_player_found:
                 logging.warning("Advance WARN: No actionable players found in loop.")
                 self.current_player_id = None
                 if self._player_action_event: self._player_action_event.set(); self._player_action_event = None; logging.debug("Cleared action event on loop end.")

    async def deal_community_cards(self, stage: str):
        logging.info(f"Dealing {stage.upper()}")
        error_msg = None; new_cards = []
        async with self._action_lock:
            if self.game_stage == "hand_over": return
            if not self.deck: logging.error(f"Deck empty before {stage}!"); return
            card_count=0; next_stage=""
            if stage=="flop": card_count=3; next_stage="flop"
            elif stage=="turn": card_count=1; next_stage="turn"
            elif stage=="river": card_count=1; next_stage="river"
            else: logging.error(f"Invalid stage '{stage}'"); return
            try:
                if self.deck: burned = self.deck.pop(); logging.debug(f"Burn {burned}")
                else: raise IndexError("Deck empty burn")
                for _ in range(card_count):
                    if self.deck: new_cards.append(self.deck.pop())
                    else: raise IndexError(f"Deck empty deal {stage}")
                self.community_cards.extend(new_cards); self.game_stage = next_stage; logging.info(f"Community ({stage}): {self.community_cards}")
                self.current_bet = 0
                for pid in self.active_players_order:
                     if (p := self.players.get(pid)): p.current_bet = 0
                if self._player_action_event: self._player_action_event.set(); self._player_action_event = None; logging.debug("Cleared action event on new round.")
            except IndexError as e: logging.error(f"Deck ran out: {e}"); self.game_stage = "hand_over"; error_msg = "Error: Deck ran out!"
        # --- Broadcast outside lock ---
        await self.broadcast_game_state(); await asyncio.sleep(0.5)
        if error_msg: await self.broadcast("game_message", {"message": error_msg}); return
        players_can_act = [p for pid in self.active_players_order if (p:=self.players.get(pid)) and p.can_act()]
        if len(players_can_act) <= 1: logging.info(f"<=1 player can act after {stage}. Skip bet round."); await self.check_hand_over_conditions()
        else: logging.debug(f"{len(players_can_act)} players can act post-{stage}.")

    async def perform_showdown(self):
        """Calculates pot distribution including side pots and updates stacks."""
        logging.info("--- Performing Showdown ---")
        all_hands_data = {}
        final_winners_summary = []

        async with self._action_lock:
            if self.game_stage == "hand_over": return
            self.game_stage = "showdown"; self.current_player_id = None
            if self._player_action_event: self._player_action_event.set(); self._player_action_event = None

            # 1. Identify contenders and their total bets
            contenders = sorted(
                [p for pid in self.active_players_order if (p := self.players.get(pid)) and p.status in ["active", "all-in"]],
                key=lambda p: p.total_bet_this_hand
            )
            all_hands_data = {p.id: p.hand for p in contenders if p.hand}

            if not contenders: logging.error("Showdown ERR: No contenders!"); self.game_stage = "hand_over"; return
            if len(contenders) == 1:
                logging.info("Showdown skip: Only 1 contender."); pot_to_award = self.pot; self.pot = 0; winner_id = contenders[0].id; is_over = True
            else: # Multiple contenders, calculate side pots
                logging.info(f"Showdown Contenders (sorted by bet): {[f'P{p.id}(Bet:{p.total_bet_this_hand})' for p in contenders]}")
                pots = [] # List of {"eligible_players": [ids], "amount": int}
                last_bet_level = 0
                player_winnings = defaultdict(int)

                # 2. Calculate pot structure
                for i, contender in enumerate(contenders):
                    current_bet_level = contender.total_bet_this_hand
                    bet_diff = current_bet_level - last_bet_level

                    if bet_diff > 0:
                        # Players contributing to this slice are those from index i onwards
                        contributors_count = len(contenders) - i
                        pot_slice_amount = bet_diff * contributors_count
                        # Players eligible to WIN this pot slice are those who bet AT LEAST this much
                        eligible_for_this_pot = [p.id for p in contenders if p.total_bet_this_hand >= current_bet_level]

                        if pot_slice_amount > 0 and eligible_for_this_pot:
                            pots.append({"eligible_players": eligible_for_this_pot, "amount": pot_slice_amount})
                            logging.info(f"Calculated Pot: Level ${current_bet_level}, Amount ${pot_slice_amount}, Eligible: {eligible_for_this_pot}")
                        last_bet_level = current_bet_level

                # 3. Award each pot
                total_pot_calculated = sum(p['amount'] for p in pots)
                logging.info(f"Total pot calculated from contributions: ${total_pot_calculated} (Original self.pot: ${self.pot})")
                self.pot = 0 # Pot is now represented in the 'pots' structure

                for i, pot_info in enumerate(pots):
                    eligible_ids = pot_info["eligible_players"]
                    pot_amount = pot_info["amount"]
                    pot_name = f"Main Pot" if i == 0 else f"Side Pot {i}"
                    logging.info(f"Awarding {pot_name} (${pot_amount}) among eligible: {eligible_ids}")

                    if not eligible_ids: logging.warning(f"{pot_name} has no eligible players?"); continue
                    if pot_amount == 0: logging.debug(f"{pot_name} is empty, skipping."); continue

                    eligible_evaluated = []
                    for p_id in eligible_ids:
                        player = self.players.get(p_id)
                        if not player or not player.hand: continue
                        try:
                            score, kicks, name, best5 = evaluate_hand(player.hand, self.community_cards)
                            eligible_evaluated.append({"id": p_id, "score": score, "kicks": kicks, "name": name, "best5": best5})
                        except Exception as e: logging.error(f"Hand eval error P{p_id} for {pot_name}: {e}")

                    if not eligible_evaluated: logging.warning(f"{pot_name} has no evaluated hands?"); continue

                    eligible_evaluated.sort(key=lambda x: (x["score"], x["kicks"]), reverse=True)
                    best_score = eligible_evaluated[0]["score"]; best_kicks = eligible_evaluated[0]["kicks"]
                    pot_winners = [h for h in eligible_evaluated if h["score"] == best_score and h["kicks"] == best_kicks]
                    num_winners = len(pot_winners)

                    if num_winners > 0:
                        win_each = pot_amount // num_winners; remainder = pot_amount % num_winners
                        if remainder > 0: logging.warning(f"{pot_name} remainder ${remainder} ignored.")
                        logging.info(f"{pot_name} Winners ({num_winners}): {[f'P{w["id"]}({w["name"]})' for w in pot_winners]}. Each wins ${win_each}")
                        for winner in pot_winners:
                            player_winnings[winner["id"]] += win_each # Accumulate total winnings
                            # Store details for final broadcast
                            # Find existing entry or add new one
                            summary_entry = next((item for item in final_winners_summary if item["playerId"] == winner["id"]), None)
                            if summary_entry:
                                summary_entry["amount"] += win_each # Add to existing amount
                                # Optionally update hand rank if this pot's rank is better? For now, keep first one found.
                            else:
                                final_winners_summary.append({
                                    "playerId": winner["id"],
                                    "playerName": self.players.get(winner["id"]).name or f"P{winner['id']}",
                                    "amount": win_each,
                                    "handRank": winner["name"], # Use rank name from this pot
                                    "winningHand": winner["best5"] # Use best 5 from this pot
                                })
                    else: logging.error(f"{pot_name} logic error: No winners found.")

                # 4. Update player stacks with total winnings
                logging.info("Updating stacks with total winnings:")
                for p_id, total_won in player_winnings.items():
                    if total_won > 0:
                        player = self.players.get(p_id)
                        if player: player.stack += total_won; logging.info(f" P{p_id} ({player.name}) wins total ${total_won}. New Stack: ${player.stack}")
                        else: logging.error(f"Player P{p_id} not found to award winnings ${total_won}")

                is_over = True; winner_id = None # Indicate showdown happened

            self.game_stage = "hand_over" # Set stage inside lock
        # --- Broadcast outside lock ---
        await self.broadcast("showdown", {"allHands": all_hands_data}) # Reveal hands
        await self.broadcast_game_state(); await asyncio.sleep(1) # Show revealed hands/stacks
        if is_over: # Award pot based on outcome
            if winner_id is not None: # Uncontested case
                 await self.award_pot(winner_id, pot_to_award, is_uncontested=True)
            else: # Showdown results (use consolidated summary)
                 await self.award_pot(None, 0, is_uncontested=False, winners_data=final_winners_summary)


    async def award_pot(self, winner_id: Optional[int], pot_amount: int, is_uncontested: bool, winners_data: Optional[List[Dict]] = None):
         """Awards pot (updates stack for uncontested) and sends pot_awarded message."""
         logging.info(f"Awarding Pot: Amt=${pot_amount}, Uncontested={is_uncontested}, WinnerID={winner_id}, WinnersData={winners_data}")
         final_payload = []
         winner_name_log = "N/A"

         if is_uncontested:
             async with self._action_lock: # Lock to update stack safely
                 if winner_id is not None and winner_id in self.players:
                     winner = self.players.get(winner_id)
                     if winner:
                         logging.info(f"Updating stack for uncontested winner P{winner_id} ('{winner.name}') Current: {winner.stack}, Adding: {pot_amount}")
                         winner.stack += pot_amount # Add pot amount to winner's stack
                         winner_name_log = winner.name or f"P{winner_id}"
                         final_payload.append({"playerId": winner_id, "playerName": winner_name_log, "amount": pot_amount})
                         logging.info(f"Stack updated for P{winner_id}. New stack: {winner.stack}")
                     else: logging.warning(f"Uncontested winner P{winner_id} not found during stack update."); final_payload.append({"playerName": "N/A", "amount": pot_amount})
                 else: logging.warning(f"Uncontested pot ${pot_amount} awarded, but winner ID {winner_id} invalid."); final_payload.append({"playerName": "N/A", "amount": pot_amount})
         else: # Showdown - stack update happened in perform_showdown
             if winners_data: final_payload = winners_data
             else: logging.error("Pot award called for showdown but no winner data provided.")

         logging.info(f"Final pot awarded payload: {final_payload}")
         await self.broadcast("pot_awarded", {"winners": final_payload, "isUncontested": is_uncontested})
         await self.broadcast_game_state() # Broadcast final state AFTER pot award

# --- WebSocket Server Logic ---
game = PokerGame()

async def handler(websocket):
    player = None; ws_id_str = f"{websocket.remote_address}" if hasattr(websocket, 'remote_address') else f"UnknownWS({id(websocket)})"
    logging.info(f"Connect attempt from {ws_id_str}")
    try:
        await game.register_player(websocket)
        async with game._action_lock:
             for p_obj in game.players.values():
                 if p_obj.websocket == websocket: player = p_obj; break
        if not player: logging.warning(f"Registration fail for {ws_id_str}. Close handler."); return
        p_id_str = f"P{player.id}"
        logging.info(f"Connect {ws_id_str} reg as {p_id_str}")
        async for message in websocket:
            current_pid = player.id if player else None; p_id_str = f"P{current_pid}" if current_pid else ws_id_str
            async with game._action_lock: player_exists = current_pid in game.players
            if not player_exists: logging.warning(f"WS {ws_id_str} msg but P{current_pid} gone. Break."); break
            logging.debug(f"Raw msg from {p_id_str}: {message}")
            try:
                data = json.loads(message); msg_type = data.get("type"); payload = data.get("payload")
                if not msg_type or payload is None: logging.warning(f"Invalid msg format from {p_id_str}"); await game.send_error(websocket, "Invalid msg format."); continue
                if msg_type == "set_name" and isinstance(payload.get("name"), str): await game.set_player_name(player.id, payload["name"])
                elif msg_type == "player_action" and isinstance(payload.get("action"), str):
                    amount = payload.get("amount"); parsed_amount = None
                    if amount is not None:
                        try: parsed_amount = int(amount)
                        except (ValueError, TypeError): await game.send_error(websocket, "Invalid amount."); continue
                    await game.handle_player_action(player.id, payload["action"].lower(), parsed_amount)
                else: logging.warning(f"Unknown msg type '{msg_type}' from {p_id_str}")
            except json.JSONDecodeError: logging.warning(f"Invalid JSON from {p_id_str}"); await game.send_error(websocket, "Invalid JSON.")
            except websockets.exceptions.ConnectionClosed: logging.info(f"Conn closed mid-process {p_id_str}."); break
            except Exception as e: logging.exception(f"!!! Handler Process ERR for {p_id_str}: {e} !!!"); await game.send_error(websocket, f"Internal server error.")
    except websockets.exceptions.ConnectionClosedOK: logging.info(f"Conn closed OK {p_id_str if player else ws_id_str}")
    except websockets.exceptions.ConnectionClosedError as e: logging.info(f"Conn closed ERR {p_id_str if player else ws_id_str}: {e}")
    except Exception as e: logging.exception(f"!!! Handler Unhandled ERR {p_id_str if player else ws_id_str}: {e} !!!")
    finally:
        ws_id = getattr(websocket, 'id', id(websocket)); p_id_final = player.id if player else 'N/A'
        logging.info(f"Handler finally ws={ws_id} (Player:{p_id_final})")
        await game.unregister_player(websocket)
        logging.info(f"Unregister done ws={ws_id}")

async def main():
    loop = asyncio.get_running_loop(); stop = loop.create_future()
    if game.game_loop_task and not game.game_loop_task.done():
        logging.info("Cancel existing game loop..."); game.game_loop_task.cancel()
        try: await game.game_loop_task
        except asyncio.CancelledError: pass; logging.info("Prev loop cancelled.")
    host = "0.0.0.0"; port = 8765; logging.info(f"--- Starting Server ws://{host}:{port} ---")
    try:
        async with websockets.serve(handler, host, port) as server:
             logging.info(f"Server listening on {host}:{port}"); await stop
    except asyncio.CancelledError: logging.info("Main server task cancelled.")
    finally:
         if game.game_loop_task and not game.game_loop_task.done():
              game.game_loop_task.cancel();
              try: await game.game_loop_task
              except asyncio.CancelledError: pass
         logging.info("Server shutdown complete.")

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("\n--- Server stopped (Ctrl+C) ---")
    except Exception as e: logging.exception(f"--- Server stopped on error: {e} ---")

# This version includes the side pot logic in `perform_showdown`. It calculates the different pot levels based on player contributions and awards each pot slice only to the eligible players who contributed that mu