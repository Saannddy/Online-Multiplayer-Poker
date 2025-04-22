import asyncio
import websockets
import websockets.exceptions
import json
import random
import itertools
from collections import defaultdict, Counter
from typing import List, Dict, Tuple, Set, Optional, Any
import logging
import time
import ssl

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] (%(funcName)s) %(message)s')

MAX_PLAYERS = 8
STARTING_STACK = 1000
SMALL_BLIND = 10
BIG_BLIND = 20
HAND_END_DELAY = 5
ACTION_TIMEOUT = 60.0

SUITS = "♠♥♦♣"
RANKS = "23456789TJQKA"
RANK_VALUES = {rank: i for i, rank in enumerate(RANKS)}

def create_deck():
    return [rank + suit for rank in RANKS for suit in SUITS]

def get_rank_value(rank_char: str) -> int:
    return RANK_VALUES.get(rank_char, -1)

def evaluate_hand(hand: List[str], community_cards: List[str]) -> Tuple[int, List[int], str, List[str]]:
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
        ace_low_ranks = [12, 3, 2, 1, 0]
        is_ace_low_straight = (ranks == ace_low_ranks)
        if not is_straight and is_ace_low_straight:
            is_straight = True
            ranks_for_kicker = [3, 2, 1, 0, -1]
        else:
            ranks_for_kicker = ranks
        is_sf = is_straight and is_flush
        rank_counts = Counter(ranks); counts = sorted(rank_counts.values(), reverse=True)
        primary_kickers = sorted(rank_counts.keys(), key=lambda r: (rank_counts[r], r), reverse=True)
        current_score = (-1, [], "Unknown", [])
        if is_sf: current_score = (9, [ranks_for_kicker[0]], "Straight Flush", combo)
        elif counts[0] == 4: current_score = (8, primary_kickers, "Four of a Kind", combo)
        elif counts == [3, 2]: current_score = (7, primary_kickers, "Full House", combo)
        elif is_flush: current_score = (6, ranks, "Flush", combo)
        elif is_straight: current_score = (5, [ranks_for_kicker[0]], "Straight", combo)
        elif counts[0] == 3: current_score = (4, primary_kickers, "Three of a Kind", combo)
        elif counts == [2, 2, 1]: current_score = (3, primary_kickers, "Two Pair", combo)
        elif counts[0] == 2: current_score = (2, primary_kickers, "One Pair", combo)
        else: current_score = (1, ranks, "High Card", combo)
        if current_score[0] > best_score[0] or \
           (current_score[0] == best_score[0] and current_score[1] > best_score[1]):
            best_5_display = sorted(combo, key=lambda c: get_rank_value(c[0]), reverse=True)
            best_score = (current_score[0], current_score[1], current_score[2], best_5_display)
    if best_score[0] == -1:
        valid_all = [c for c in all_cards if isinstance(c, str) and len(c) >= 1]
        if not valid_all: return (0, [], "Invalid Hand", [])
        ranks = sorted([get_rank_value(c[0]) for c in valid_all], reverse=True)
        best_5 = sorted(valid_all, key=lambda c: get_rank_value(c[0]), reverse=True)[:5]
        return (1, ranks[:5], "High Card (Fallback)", best_5)
    final_name = best_score[2]
    try:
        score_val = best_score[0]; kickers = best_score[1]; best_5_cards = best_score[3]
        if score_val == 9 and kickers:
            high_rank_idx = kickers[0]; high_rank_char = RANKS[high_rank_idx] if high_rank_idx >= 0 else '5'
            final_name = f"{high_rank_char}-high Straight Flush"
            sf_ranks_set = {get_rank_value(c[0]) for c in best_5_cards}
            if sf_ranks_set == {12, 11, 10, 9, 8}: final_name = "Royal Flush"
        elif score_val == 8 and len(kickers) >= 1: final_name = f"Four of a Kind, {RANKS[kickers[0]]}s"
        elif score_val == 7 and len(kickers) >= 2: final_name = f"Full House, {RANKS[kickers[0]]}s full of {RANKS[kickers[1]]}s"
        elif score_val == 6 and kickers: final_name = f"{RANKS[kickers[0]]}-high Flush"
        elif score_val == 5 and kickers:
            high_rank_idx = kickers[0]; high_rank_char = RANKS[high_rank_idx] if high_rank_idx >= 0 else '5'
            final_name = f"{high_rank_char}-high Straight"
        elif score_val == 4 and len(kickers) >= 1: final_name = f"Three of a Kind, {RANKS[kickers[0]]}s"
        elif score_val == 3 and len(kickers) >= 2: final_name = f"Two Pair, {RANKS[kickers[0]]}s & {RANKS[kickers[1]]}s"
        elif score_val == 2 and len(kickers) >= 1: final_name = f"Pair of {RANKS[kickers[0]]}s"
        elif score_val == 1 and kickers: final_name = f"{RANKS[kickers[0]]}-High"
    except IndexError:
        logging.error(f"Error refining hand name for score: {best_score}", exc_info=True)
        final_name = best_score[2]
    return (best_score[0], best_score[1], final_name, best_score[3])

class Player:
    def __init__(self, player_id: int, websocket):
        self.id: int = player_id
        self.name: Optional[str] = None
        self.websocket = websocket
        self.stack: int = STARTING_STACK
        self.hand: List[str] = []
        self.current_bet: int = 0
        self.total_bet_this_hand: int = 0
        self.status: str = "waiting"
        self.is_dealer: bool = False
        self.last_action: Optional[str] = None
        self.last_action_time: float = 0.0
        self.last_hand_rank: Optional[str] = None

    def to_dict(self, show_hand=False) -> Dict[str, Any]:
        display_name = self.name if self.name else f"Player {self.id}"
        hand_display = ['??', '??'] if self.hand else []
        if self.status == "folded": hand_display = []
        elif show_hand and self.hand: hand_display = self.hand
        return {
            "id": self.id, "name": display_name, "stack": self.stack, "hand": hand_display,
            "current_bet": self.current_bet, "status": self.status, "is_dealer": self.is_dealer,
            "last_action": self.last_action, "last_hand_rank": self.last_hand_rank,
            "total_bet_this_hand": self.total_bet_this_hand
        }

    def can_act(self) -> bool:
        return self.status == "active" and self.stack > 0

class PokerGame:
    def __init__(self):
        self.players: Dict[int, Player] = {}
        self.connected_websockets_set: Set = set()
        self.next_player_id: int = 1
        self.deck: List[str] = []
        self.community_cards: List[str] = []
        self.pot: int = 0
        self.current_bet: int = 0
        self.last_raiser_id: Optional[int] = None
        self.current_player_id: Optional[int] = None
        self.dealer_button_pos: int = -1
        self.small_blind_pos: int = 0
        self.big_blind_pos: int = 0
        self.game_stage: str = "idle"
        self.active_players_order: List[int] = []
        self.game_loop_task: Optional[asyncio.Task] = None
        self._action_lock = asyncio.Lock()
        self._player_action_event: Optional[asyncio.Event] = None
        self.actions_this_round: Set[int] = set()

    async def register_player(self, websocket):
        if len(self.players) >= MAX_PLAYERS:
            logging.warning(f"Connection rejected: Game full ({len(self.players)} players).")
            await self.send_error(websocket, "Game is full.")
            try: await websocket.close(code=1008, reason="Game full")
            except websockets.exceptions.ConnectionClosed: pass
            return
        player_id = self.next_player_id
        player = Player(player_id, websocket)
        async with self._action_lock:
            self.players[player_id] = player
            self.connected_websockets_set.add(websocket)
            self.next_player_id += 1
        logging.info(f"Player {player_id} connected ({websocket.remote_address}). Requesting name.")
        await self.send_message(websocket, "assign_id", {"playerId": player_id})

    async def set_player_name(self, player_id: int, name: str):
        broadcast_needed = False
        async with self._action_lock:
            player = self.players.get(player_id)
            if player:
                if player.name is None:
                     player.name = name.strip()[:15]
                     logging.info(f"Player {player_id} set name to: '{player.name}'.")
                     broadcast_needed = True
                else:
                     logging.warning(f"Player {player_id} attempted to change name to '{name}', ignored.")
                     broadcast_needed = False
            else:
                logging.error(f"Cannot set name for unknown Player ID: {player_id}")
                return
        if broadcast_needed:
            await self.broadcast_game_state()
            await self.check_start_game()

    async def check_start_game(self):
        ready_players = [p for p in self.players.values() if p.name is not None]
        num_ready = len(ready_players)
        if num_ready >= 2 and self.game_stage == "idle":
            async with self._action_lock:
                if self.game_stage == "idle" and (not self.game_loop_task or self.game_loop_task.done()):
                    logging.info(f"{num_ready} players ready. Starting game loop.")
                    self.game_loop_task = asyncio.create_task(self.game_loop())
        elif self.game_stage != "idle":
            logging.debug(f"Check start: Game already in progress ({self.game_stage}).")
        else:
            logging.debug(f"Check start: Not enough players ({num_ready}). Waiting for more.")

    async def unregister_player(self, websocket):
        player_id_to_remove = None; player_name = "Unknown"; player_status = "unknown"; was_their_turn = False; player_to_remove = None
        async with self._action_lock:
            for pid, p in self.players.items():
                if p.websocket == websocket:
                    player_id_to_remove = pid; player_name = p.name or f"Player {pid}"; player_status = p.status
                    was_their_turn = (self.current_player_id == pid); player_to_remove = p
                    break
            self.connected_websockets_set.discard(websocket)
            if player_id_to_remove is None:
                logging.debug(f"Websocket disconnected but no associated player found.")
                return
            logging.info(f"{player_name} (ID:{player_id_to_remove}) disconnected.")
            should_check_hand_end = False
            if player_to_remove and player_status not in ["waiting", "folded"] and self.game_stage not in ["idle", "hand_over", "showdown", "starting"]:
                logging.info(f"Folding disconnected player {player_name}.")
                player_to_remove.status = "folded"; player_to_remove.hand = []; player_to_remove.last_action = "fold"; player_to_remove.last_hand_rank = None
                should_check_hand_end = True
            if player_id_to_remove in self.players:
                del self.players[player_id_to_remove]
            active_game_players = [p for p in self.players.values() if p.name is not None]
            num_remaining = len(active_game_players)
            reset_game = False
            if num_remaining < 2 and self.game_stage != "idle":
                logging.warning(f"Only {num_remaining} player(s) remaining. Resetting game to idle state.")
                self.game_stage = "idle"; self.pot = 0; self.community_cards = []; self.current_player_id = None; self.current_bet = 0
                if self.game_loop_task and not self.game_loop_task.done(): self.game_loop_task.cancel(); self.game_loop_task = None
                if was_their_turn and self._player_action_event: self._player_action_event.set(); self._player_action_event = None
                reset_game = True
        await self.broadcast_game_state()
        if reset_game:
            for p in self.players.values():
                p.status = "waiting"; p.hand = []; p.current_bet = 0; p.total_bet_this_hand = 0
                p.is_dealer = False; p.last_action = None; p.last_hand_rank = None
            await self.broadcast("game_message", {"message": "Not enough players to continue. Waiting..."})
            await self.broadcast_game_state()
            return
        if should_check_hand_end:
            if was_their_turn:
                 async with self._action_lock:
                     if self._player_action_event: self._player_action_event.set(); self._player_action_event = None
                 logging.debug(f"Set action event due to Player {player_id_to_remove} disconnect-fold.")
            if not await self.check_hand_over_conditions() and was_their_turn:
                 logging.debug(f"Player disconnected on turn. Checking round end / advancing.")
                 await self.check_round_end()

    async def send_message(self, websocket, msg_type: str, payload: Any):
        if websocket not in self.connected_websockets_set: return
        try:
            message = json.dumps({"type": msg_type, "payload": payload})
            await websocket.send(message)
        except websockets.exceptions.ConnectionClosed: logging.warning(f"Send failed: Conn Closed OK ws={getattr(websocket, 'id', id(websocket))}")
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
        tasks = []
        current_players = list(self.players.values())
        for p in current_players:
             if p.websocket in self.connected_websockets_set:
                 state_for_player = self.get_state_for_player(p.id)
                 tasks.append(asyncio.create_task(self.send_message(p.websocket, "game_state", state_for_player)))
        if tasks: await asyncio.wait(tasks)

    def get_state_for_player(self, perspective_player_id: int) -> Dict[str, Any]:
        player_states = {}
        dealer_id = -1
        current_stage = self.game_stage; current_cc = self.community_cards; current_pot = self.pot
        acting_player = self.current_player_id; is_reveal_stage = current_stage in ["showdown", "hand_over"]
        for pid, p in self.players.items():
            show_hand = (pid == perspective_player_id) or (is_reveal_stage and p.status != "folded")
            player_states[pid] = p.to_dict(show_hand=show_hand);
            if p.is_dealer: dealer_id = pid
        return {
            "players": player_states, "community_cards": current_cc, "pot": current_pot,
            "current_player_id": acting_player, "dealer_id": dealer_id, "game_stage": current_stage,
            "bigBlind": BIG_BLIND
        }

    async def game_loop(self):
        logging.info("GAME LOOP STARTED")
        try:
            while True:
                async with self._action_lock:
                    current_players_list = list(self.players.values())
                    named_players = [p for p in current_players_list if p.name is not None]
                    can_continue = len(named_players) >= 2
                if not can_continue:
                    logging.warning("Game loop: Less than 2 named players. Stopping.")
                    async with self._action_lock: self.game_stage = "idle"
                    await self.broadcast("game_message", {"message": "Game paused. Waiting for players..."})
                    await self.broadcast_game_state(); break
                logging.info("-" * 20 + " Starting New Hand " + "-" * 20)
                await self.start_new_hand_setup()
                if self.game_stage != "hand_over": await self.run_betting_round()
                if self.game_stage != "hand_over": await self.deal_community_cards("flop"); await self.run_betting_round()
                if self.game_stage != "hand_over": await self.deal_community_cards("turn"); await self.run_betting_round()
                if self.game_stage != "hand_over": await self.deal_community_cards("river"); await self.run_betting_round()
                if self.game_stage != "hand_over": await self.perform_showdown()
                logging.info(f"Hand concluded (Stage: {self.game_stage}). Waiting {HAND_END_DELAY}s...")
                await self.broadcast("game_message", {"message": f"--- Next hand starting in {HAND_END_DELAY}s ---"})
                await asyncio.sleep(HAND_END_DELAY)
        except asyncio.CancelledError: logging.info("Game loop was cancelled.")
        except Exception as e: logging.exception(f"!!! UNEXPECTED ERROR IN GAME LOOP: {e} !!!")
        finally:
             async with self._action_lock: self.game_stage = "idle"; self.game_loop_task = None
             logging.info("GAME LOOP EXITED")

    async def start_new_hand_setup(self):
        logging.info("Setting up new hand...")
        dealer_id = -1; sb_id = -1; bb_id = -1; sb_amt = 0; bb_amt = 0; first_actor_id = None
        async with self._action_lock:
            self.game_stage = "starting"; self.deck = create_deck(); random.shuffle(self.deck)
            self.community_cards = []; self.pot = 0; self.current_bet = 0; self.last_raiser_id = None
            self.current_player_id = None; self._player_action_event = None
            self.actions_this_round = set()
            eligible_players = {pid: p for pid, p in self.players.items() if p.stack > 0 and p.name is not None}
            if len(eligible_players) < 2:
                logging.warning("New hand setup failed: Less than 2 eligible players.")
                self.game_stage = "idle"; raise Exception("Setup failed: Not enough eligible players.")
            self.active_players_order = sorted(list(eligible_players.keys()))
            num_eligible = len(self.active_players_order)
            logging.debug(f"Eligible Player Order for Hand: {self.active_players_order}")
            for player in self.players.values():
                player.last_hand_rank = None
                if player.id in eligible_players:
                    player.hand = []; player.current_bet = 0; player.total_bet_this_hand = 0
                    player.status = "active"; player.is_dealer = False; player.last_action = None
                else:
                    player.status = "waiting"; player.hand = []; player.current_bet = 0
                    player.total_bet_this_hand = 0; player.is_dealer = False; player.last_action = None
            if self.dealer_button_pos == -1: self.dealer_button_pos = random.randrange(num_eligible)
            else: self.dealer_button_pos = (self.dealer_button_pos + 1) % num_eligible
            dealer_found = False
            for i in range(num_eligible):
                 check_idx = (self.dealer_button_pos + i) % num_eligible
                 if self.active_players_order[check_idx] in eligible_players:
                     self.dealer_button_pos = check_idx; dealer_found = True; break
            if not dealer_found: logging.error("Failed to find a valid dealer position!"); self.dealer_button_pos = 0
            dealer_id = self.active_players_order[self.dealer_button_pos]; self.players[dealer_id].is_dealer = True
            if num_eligible == 2:
                 self.small_blind_pos = self.dealer_button_pos; self.big_blind_pos = (self.dealer_button_pos + 1) % num_eligible
            else:
                 self.small_blind_pos = (self.dealer_button_pos + 1) % num_eligible; self.big_blind_pos = (self.dealer_button_pos + 2) % num_eligible
            sb_id = self.active_players_order[self.small_blind_pos]; bb_id = self.active_players_order[self.big_blind_pos]
            deal_start_pos = (self.dealer_button_pos + 1) % num_eligible
            for _ in range(2):
                for i in range(num_eligible):
                    p_idx = (deal_start_pos + i) % num_eligible; p_id = self.active_players_order[p_idx]
                    if self.players[p_id].status == "active":
                         if self.deck: self.players[p_id].hand.append(self.deck.pop())
                         else: logging.error("Deck ran out during initial deal!"); raise Exception("Deck Empty during deal")
            sb_amt = self._post_blind_internal(sb_id, SMALL_BLIND); bb_amt = self._post_blind_internal(bb_id, BIG_BLIND)
            self.current_bet = bb_amt; self.last_raiser_id = bb_id
            start_action_pos = (self.big_blind_pos + 1) % num_eligible;
            for i in range(num_eligible):
                check_idx = (start_action_pos + i) % num_eligible; check_id = self.active_players_order[check_idx]
                if self.players[check_id].can_act(): first_actor_id = check_id; break
            if first_actor_id is None: logging.warning("No active player found to start preflop betting (all-in?)."); self.current_player_id = None
            else: self.current_player_id = first_actor_id
            self.game_stage = "preflop";
        dealer_player = self.players.get(dealer_id); sb_player = self.players.get(sb_id); bb_player = self.players.get(bb_id)
        dealer_name = dealer_player.name if dealer_player else f"P{dealer_id}"; sb_name = sb_player.name if sb_player else f"P{sb_id}"; bb_name = bb_player.name if bb_player else f"P{bb_id}"
        logging.info(f"Hand Setup Complete: Dealer: {dealer_name}({dealer_id}), SB: {sb_name}({sb_id}), BB: {bb_name}({bb_id})")
        await self.broadcast("game_message", {"message": f"--- Starting New Hand --- Dealer: {dealer_name}"})
        await self.broadcast_game_state(); await asyncio.sleep(0.5)
        sb_status = self.players.get(sb_id).status if self.players.get(sb_id) else 'unknown'
        bb_status = self.players.get(bb_id).status if self.players.get(bb_id) else 'unknown'
        await self.broadcast("game_message", {"message": f"{sb_name} posts Small Blind ${sb_amt}" + (" (All-in)" if sb_status=='all-in' else "")})
        await self.broadcast_game_state(); await asyncio.sleep(0.2)
        await self.broadcast("game_message", {"message": f"{bb_name} posts Big Blind ${bb_amt}" + (" (All-in)" if bb_status=='all-in' else "")})
        await self.broadcast_game_state(); await asyncio.sleep(0.2)
        if await self.check_hand_over_conditions(): return
        current_player = self.players.get(self.current_player_id)
        if current_player: logging.info(f"Preflop betting starts with {current_player.name or f'P{self.current_player_id}'}")
        else: logging.warning("Setup done but no first actor found for preflop. Hand should end.")

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
             num_active_players = len(active_order); dealer_pos = self.dealer_button_pos
        if current_stage == "hand_over": return
        logging.info(f"--- Starting Betting Round: {current_stage.upper()} ---")
        if num_active_players == 0: return
        if current_stage != "preflop":
             async with self._action_lock:
                self.current_bet = 0; self.last_raiser_id = None; self.current_player_id = None
                self.actions_this_round = set()
                for pid in active_order:
                    if (p := self.players.get(pid)) and p.status != "folded":
                        p.current_bet = 0
                        p.last_action = None
                first_actor_pos = -1; num_in_order = len(self.active_players_order)
                if num_in_order == 0: logging.error("RunBettingRound: active_players_order empty post-flop!"); return
                start_idx = (dealer_pos + 1) % num_in_order
                for i in range(num_in_order):
                    check_pos = (start_idx + i) % num_in_order; p_id = self.active_players_order[check_pos]
                    player = self.players.get(p_id)
                    if player and player.can_act(): first_actor_pos = check_pos; break
                if first_actor_pos != -1: self.current_player_id = self.active_players_order[first_actor_pos]
                else: logging.warning(f"No active player found to start betting round {current_stage}."); self.current_player_id = None
             await self.broadcast_game_state()
             first_actor_player = self.players.get(self.current_player_id)
             first_actor_name = first_actor_player.name if first_actor_player else "None"
             logging.info(f"Post-flop round ({current_stage}) starts with {first_actor_name}")
             if self.current_player_id is None: await self.check_hand_over_conditions(); return
        action_count = 0; max_actions = num_active_players * 3 + 5
        while action_count < max_actions:
            action_count += 1; logging.debug(f"Betting loop iter {action_count}/{max_actions} Stage: {current_stage}")
            async with self._action_lock:
                current_actor_id = self.current_player_id; last_aggro_id = self.last_raiser_id
                current_stage_check = self.game_stage
            if current_stage_check == "hand_over": logging.info("Betting loop ending: Stage changed to hand_over."); return
            if await self.check_hand_over_conditions(): logging.info(f"Betting loop ending: Hand over condition met."); return
            async with self._action_lock: round_complete = self.is_betting_round_complete(last_aggro_id)
            if round_complete: logging.info(f"Betting round {current_stage} complete."); return
            if current_actor_id is None: logging.error("Betting loop ERROR: current_player_id is None! Advancing."); await self.advance_to_next_player(); continue
            current_player = self.players.get(current_actor_id)
            if not current_player: logging.error(f"Betting loop ERROR: Player {current_actor_id} not found! Advancing."); await self.advance_to_next_player(); continue
            if not current_player.can_act(): logging.debug(f"Betting loop: Skipping P{current_actor_id} (Status: {current_player.status}). Advancing."); await self.advance_to_next_player(); await asyncio.sleep(0.01); continue
            await self.request_player_action()
            logging.debug(f"Betting loop: Waiting for action event from P{current_actor_id}")
            async with self._action_lock: current_event = self._player_action_event
            if current_event:
                try:
                    await asyncio.wait_for(current_event.wait(), timeout=ACTION_TIMEOUT)
                    logging.debug(f"Betting loop: Action event received/set for P{current_actor_id}")
                except asyncio.TimeoutError:
                    logging.warning(f"Player P{current_actor_id} timed out on stage {current_stage}.")
                    await self.handle_player_action(current_actor_id, "fold", None)
            else:
                 logging.warning(f"Betting loop: No action event found for P{current_actor_id} after request?")
                 await asyncio.sleep(0.1)
        logging.error(f"Betting round {current_stage} exceeded max actions ({max_actions})! Ending round prematurely.")

    async def request_player_action(self):
        player_id_to_request = None; player_websocket = None; payload = None; should_advance = False
        async with self._action_lock:
            if self.current_player_id is None: logging.error("Request Action Error: No current player ID set."); return
            player = self.players.get(self.current_player_id)
            if not player: logging.error(f"Request Action Error: Player {self.current_player_id} not found."); return
            if not player.can_act(): logging.warning(f"Request Action: Player {player.name or f'P{player.id}'} cannot act (Status: {player.status}). Advancing turn."); should_advance = True
            else:
                player_id = player.id; player_stack = player.stack; player_bet = player.current_bet
                player_name = player.name or f"P{player.id}"; round_bet = self.current_bet; stage = self.game_stage
                last_raiser = self.last_raiser_id; bb_id = self.active_players_order[self.big_blind_pos] if self.big_blind_pos < len(self.active_players_order) else None
                player_websocket = player.websocket; player_id_to_request = player_id
                allowed = ["fold"]; call_needed = max(0, round_bet - player_bet); eff_stack = player_stack
                if player_bet == round_bet: allowed.append("check"); call_amt = 0
                elif call_needed > 0 and eff_stack > 0: allowed.append("call"); call_amt = min(call_needed, eff_stack)
                else: call_amt = 0
                max_possible_total_bet = player_bet + eff_stack; min_open_bet = BIG_BLIND
                prev_bet_level = self.get_previous_bet_level(); last_raise_size = round_bet - prev_bet_level
                min_raise_delta = max(last_raise_size, BIG_BLIND); req_min_raise_total = round_bet + min_raise_delta
                min_slider_value = 0; can_increase_bet = eff_stack > 0
                if round_bet == 0 and can_increase_bet:
                     allowed.append("bet"); min_slider_value = min(min_open_bet, max_possible_total_bet)
                is_bb_preflop_option = (stage == "preflop" and player_id == bb_id and player_bet == round_bet and last_raiser == bb_id)
                if (round_bet > 0 or is_bb_preflop_option) and can_increase_bet:
                     if max_possible_total_bet > round_bet:
                         allowed.append("raise")
                         min_raise_total_this_player = min(req_min_raise_total, max_possible_total_bet)
                         min_slider_value = max(min_slider_value, min_raise_total_this_player)
                final_min_slider = min_slider_value; final_max_slider = max_possible_total_bet
                if final_min_slider > final_max_slider: final_min_slider = final_max_slider
                if final_min_slider == final_max_slider and final_min_slider <= round_bet:
                     if 'bet' in allowed: allowed.remove('bet')
                     if 'raise' in allowed: allowed.remove('raise')
                     final_min_slider = 0; final_max_slider = 0
                logging.debug(f" P{player_id} Requesting Action. Opts: {allowed}, CallAmt:{call_amt}, MinSlider:{final_min_slider}, MaxSlider:{final_max_slider}")
                payload = {
                    "playerId": player_id, "actions": allowed, "callAmount": call_amt, "minRaise": final_min_slider,
                    "maxRaise": final_max_slider, "currentBet": round_bet, "stack": player_stack, "bigBlind": BIG_BLIND
                }
                self._player_action_event = asyncio.Event(); logging.debug(f"Created action event for P{player_id}")
        if should_advance: await self.advance_to_next_player(); return
        if player_websocket and payload:
            logging.info(f"Requesting action from {player_name} (ID: {player_id_to_request})")
            await self.send_message(player_websocket, "player_turn", payload)
            await self.broadcast_game_state()

    def get_previous_bet_level(self) -> int:
        bets = sorted([p.current_bet for pid in self.active_players_order if (p := self.players.get(pid)) and p.status not in ['folded', 'waiting'] and p.current_bet < self.current_bet], reverse=True)
        return bets[0] if bets else 0

    async def handle_player_action(self, player_id: int, action: str, amount: Optional[int] = None):
        error_to_send = None; final_valid_action = False; final_broadcast_payload = None; player_websocket = None; event_to_set = None
        async with self._action_lock:
            logging.debug(f"HANDLE_ACTION: P{player_id} attempts '{action}' {f'(${amount})' if amount else ''}. Current Actor: P{self.current_player_id}, Stage: {self.game_stage}, RoundBet: ${self.current_bet}")
            if self.game_stage in ["idle", "starting", "hand_over", "showdown"]: logging.warning(f"Action '{action}' ignored: Invalid game stage ({self.game_stage})."); return
            if player_id != self.current_player_id: logging.warning(f"Action '{action}' ignored: Player {player_id} acted out of turn (Expected P{self.current_player_id})."); error_to_send = ("Not your turn.", player_id); valid_action = False
            else:
                player = self.players.get(player_id)
                if not player: logging.error(f"Action '{action}' Error: Player {player_id} (current player) not found!"); return
                player_name = player.name or f"P{player.id}"; player_websocket = player.websocket
                if not player.can_act(): logging.warning(f"Action '{action}' ignored: {player_name} cannot act (Status: {player.status}, Stack: {player.stack})."); error_to_send = (f"Cannot act (Status: {player.status}).", player_id); valid_action = False; event_to_set = self._player_action_event
                else:
                    logging.info(f"Processing action: {player_name} - {action.upper()} {f'${amount}' if amount is not None else ''}")
                    valid_action = False; error_msg = None; broadcast_payload = {"playerId": player_id, "action": action, "amount": None}
                    if action == "fold": player.status = "folded"; player.hand = []; player.last_action = "fold"; player.last_hand_rank = None; valid_action = True
                    elif action == "check":
                        if player.current_bet == self.current_bet: player.last_action = "check"; valid_action = True
                        else: error_msg = f"Cannot check. Current bet to match is ${self.current_bet}."
                    elif action == "call":
                        call_needed = max(0, self.current_bet - player.current_bet)
                        if call_needed > 0:
                            actual_call = min(call_needed, player.stack); player.stack -= actual_call; bet_inc = actual_call
                            player.current_bet += bet_inc; player.total_bet_this_hand += bet_inc; self.pot += bet_inc
                            player.last_action = "call"; valid_action = True; broadcast_payload["amount"] = player.current_bet
                            if player.stack == 0: player.status = "all-in"; logging.info(f"{player_name} is All-in calling.")
                        else: error_msg = "Cannot call (already matched bet or nothing to call)."
                    elif action == "bet" or action == "raise":
                        if amount is None or not isinstance(amount, int) or amount <= 0: error_msg = "Invalid bet/raise amount provided."
                        else:
                            total_bet_intended = amount; bet_increase = total_bet_intended - player.current_bet
                            if bet_increase <= 0: error_msg = f"Bet/Raise amount (${total_bet_intended}) must be greater than your current bet (${player.current_bet})."
                            elif bet_increase > player.stack: error_msg = f"Insufficient stack ({player.stack}) for bet increase of ${bet_increase} (Total: ${total_bet_intended})."
                            else:
                                is_opening_action = (self.current_bet == 0); bb_id = self.active_players_order[self.big_blind_pos] if self.big_blind_pos < len(self.active_players_order) else None
                                is_bb_preflop_option = (self.game_stage == "preflop" and player.id == bb_id and player.current_bet == self.current_bet)
                                if action == "bet" and not is_opening_action: error_msg = f"Invalid action: Cannot 'bet' when facing a bet (${self.current_bet}). Use 'call' or 'raise'."
                                elif action == "raise" and is_opening_action and not is_bb_preflop_option: error_msg = f"Invalid action: Cannot 'raise' when there is no bet to raise. Use 'bet'."
                                else:
                                    is_all_in = (bet_increase == player.stack); min_open = BIG_BLIND; prev_bet = self.get_previous_bet_level()
                                    last_raise_size = self.current_bet - prev_bet; min_raise_delta = max(last_raise_size, BIG_BLIND)
                                    req_min_raise_total = self.current_bet + min_raise_delta; min_legal_total = min_open if action == "bet" else req_min_raise_total
                                    if total_bet_intended < min_legal_total and not is_all_in: error_msg = f"Amount too small. Minimum {action} total is ${min_legal_total}."
                                    else:
                                        player.stack -= bet_increase; player.current_bet = total_bet_intended; player.total_bet_this_hand += bet_increase
                                        self.pot += bet_increase; player.last_action = action; valid_action = True; broadcast_payload["amount"] = total_bet_intended
                                        prev_round_bet = self.current_bet; self.current_bet = total_bet_intended
                                        is_full_aggro = False
                                        if action == "bet" and total_bet_intended > 0: is_full_aggro = True
                                        elif action == "raise" and total_bet_intended >= req_min_raise_total: is_full_aggro = True
                                        if is_all_in and total_bet_intended < req_min_raise_total and action == "raise": is_full_aggro = False
                                        if is_full_aggro: self.last_raiser_id = player_id; self.actions_this_round = {player_id}; logging.debug(f" Action by {player_name} (${total_bet_intended}) reopens betting. Reset actions_this_round.")
                                        else: logging.debug(f" Action by {player_name} (${total_bet_intended}) does not fully reopen betting (MinReq: ${req_min_raise_total}).")
                                        if player.stack == 0: player.status = "all-in"; logging.info(f"{player_name} is All-in {action}ing ${total_bet_intended}.")
                    else: error_msg = f"Unknown action type received: {action}"
                    if valid_action: self.actions_this_round.add(player_id)
                    final_valid_action = valid_action; final_broadcast_payload = broadcast_payload if final_valid_action else None
                    if error_to_send is None and error_msg: error_to_send = (error_msg, player_id)
                    if self._player_action_event: event_to_set = self._player_action_event; self._player_action_event = None
                    else: logging.warning(f"No action event found for P{player_id} during handle_action post-processing.")
        if event_to_set: logging.debug(f"Setting action event for P{player_id} outside lock."); event_to_set.set()
        if final_valid_action:
            if final_broadcast_payload: await self.broadcast("player_action", final_broadcast_payload)
            await self.broadcast_game_state()
            if not await self.check_hand_over_conditions(): await self.check_round_end()
            else: logging.info("Hand ended immediately after valid action.")
        else:
             if error_to_send:
                  err_msg, err_pid = error_to_send; err_player = self.players.get(err_pid)
                  if err_player and err_player.websocket in self.connected_websockets_set: await self.send_error(err_player.websocket, err_msg)
             if player_id == self.current_player_id: logging.info(f"Invalid action by P{player_id}. Re-requesting action."); await self.request_player_action()

    async def check_hand_over_conditions(self) -> bool:
        is_over = False; winner_id = None; pot_to_award = 0
        async with self._action_lock:
            if self.game_stage == "hand_over": return True
            contenders = [p for pid in self.active_players_order if (p := self.players.get(pid)) and p.status in ["active", "all-in"]]
            num_contenders = len(contenders); is_over = num_contenders <= 1
            if is_over:
                 logging.info(f"CHECK_HAND_OVER: Only {num_contenders} contender(s) remain. Ending hand."); self.game_stage = "hand_over"; self.current_player_id = None
                 pot_to_award = self.pot; self.pot = 0; winner_id = contenders[0].id if num_contenders == 1 else None
                 if winner_id is None and num_contenders == 0: logging.warning("Hand ended with 0 contenders? Pot disappears?")
                 if self._player_action_event: self._player_action_event.set(); self._player_action_event = None; logging.debug("Cleared action event on hand over check.")
        if is_over: await self.award_pot(winner_id, pot_to_award, is_uncontested=True)
        logging.debug(f"check_hand_over result: {is_over}")
        return is_over

    def is_betting_round_complete(self, last_aggressor_id: Optional[int]) -> bool:
        actionable = [p for pid in self.active_players_order if (p := self.players.get(pid)) and p.can_act()]
        if not actionable: logging.debug("is_round_complete: No actionable players left. TRUE"); return True
        all_matched_or_allin = True
        for p in self.players.values():
            if p.id not in self.active_players_order or p.status in ["waiting", "folded"]: continue
            if p.status != "all-in" and p.current_bet < self.current_bet:
                logging.debug(f"is_round_complete: P{p.id} Bet:${p.current_bet} < RoundBet:${self.current_bet}. FALSE"); all_matched_or_allin = False; break
        if not all_matched_or_allin: return False
        is_bb_preflop_option = False
        if self.game_stage == "preflop" and self.current_player_id is not None and self.big_blind_pos < len(self.active_players_order):
            bb_player_id = self.active_players_order[self.big_blind_pos]
            if self.current_player_id == bb_player_id and last_aggressor_id == bb_player_id:
                 bb_player = self.players.get(bb_player_id)
                 if bb_player and bb_player.last_action == "blind": is_bb_preflop_option = True
        if is_bb_preflop_option: logging.debug(f"is_round_complete: BB Preflop Option pending for P{self.current_player_id}. FALSE"); return False
        if last_aggressor_id is not None:
            if self.current_player_id == last_aggressor_id: logging.debug(f"is_round_complete: Action back on aggressor P{last_aggressor_id}. TRUE"); return True
            else: logging.debug(f"is_round_complete: Aggressor P{last_aggressor_id} exists, action not back on them yet (Current: P{self.current_player_id}). FALSE"); return False
        else:
            eligible_actor_ids = {pid for pid in self.active_players_order if (p := self.players.get(pid)) and p.status not in ['folded', 'waiting', 'all-in']}
            if not eligible_actor_ids: logging.debug("is_round_complete: No aggressor and no eligible actors left. TRUE"); return True
            round_complete_check = self.actions_this_round.issuperset(eligible_actor_ids)
            logging.debug(f"is_round_complete: No aggressor. Actions taken: {self.actions_this_round}. Eligible actors: {eligible_actor_ids}. Complete? {round_complete_check}")
            return round_complete_check

    async def check_round_end(self):
        logging.debug("Entering check_round_end")
        round_complete = False; current_actor_before_check = self.current_player_id
        async with self._action_lock:
            if self.game_stage == "hand_over": logging.debug("check_round_end: Hand is over, exiting."); return
            round_complete = self.is_betting_round_complete(self.last_raiser_id)
            logging.debug(f"check_round_end: is_betting_round_complete result = {round_complete}")
        if round_complete: logging.info(f"Confirmed: Betting round {self.game_stage} ended.")
        else:
            logging.debug("check_round_end: Round not complete, advancing turn.")
            await self.advance_to_next_player()
            async with self._action_lock:
                 current_actor_after_advance = self.current_player_id; player_can_still_act = False
                 if current_actor_after_advance is not None:
                      player = self.players.get(current_actor_after_advance)
                      if player: player_can_still_act = player.can_act()
                 if current_actor_after_advance == current_actor_before_check and player_can_still_act:
                      logging.warning(f"check_round_end: Advancing turn failed to change player from P{current_actor_before_check}! Still their turn. Possible loop?")
                      if self._player_action_event: logging.warning("Force setting action event as turn didn't advance."); self._player_action_event.set(); self._player_action_event = None

    async def advance_to_next_player(self):
        async with self._action_lock:
            original_player_id = self.current_player_id; logging.debug(f"Attempting to advance turn from P{original_player_id}")
            num_players_in_order = len(self.active_players_order)
            if num_players_in_order == 0: logging.warning("Advance Turn Failed: No players in active order."); self.current_player_id = None; return
            try: start_idx = self.active_players_order.index(original_player_id) if original_player_id is not None else -1
            except ValueError: logging.error(f"Advance Turn Error: P{original_player_id} not in active order {self.active_players_order}. Starting search."); start_idx = -1
            next_player_found = False
            for i in range(1, num_players_in_order + 1):
                next_idx = (start_idx + i) % num_players_in_order; next_pid = self.active_players_order[next_idx]; player = self.players.get(next_pid)
                logging.debug(f"Advance check: Pos {next_idx} -> P{next_pid} (Status: {player.status if player else 'N/A'}, Stack: {player.stack if player else 'N/A'})")
                if player and player.can_act():
                    self.current_player_id = next_pid; player_name = player.name or f"P{next_pid}"
                    logging.info(f"Advanced turn from P{original_player_id} -> P{self.current_player_id} ('{player_name}')")
                    if self._player_action_event: self._player_action_event.set(); self._player_action_event = None; logging.debug("Cleared action event on turn advance.")
                    next_player_found = True; break
            if not next_player_found:
                 logging.warning("Advance Turn Warning: No actionable players found in the loop.")
                 self.current_player_id = None
                 if self._player_action_event: self._player_action_event.set(); self._player_action_event = None; logging.debug("Cleared action event on loop end (no next player found).")

    async def deal_community_cards(self, stage: str):
        logging.info(f"Dealing {stage.upper()}...")
        error_msg = None; new_cards = []
        async with self._action_lock:
            if self.game_stage == "hand_over": logging.debug(f"Skipping deal '{stage}': Hand already over."); return
            if not self.deck: logging.error(f"Deck empty before dealing {stage}!"); self.game_stage = "hand_over"; error_msg = "Error: Deck ran out of cards!"; return
            card_count = 0; next_stage = ""
            if stage == "flop": card_count = 3; next_stage = "flop"
            elif stage == "turn": card_count = 1; next_stage = "turn"
            elif stage == "river": card_count = 1; next_stage = "river"
            else: logging.error(f"Invalid stage '{stage}' provided to deal_community_cards."); return
            try:
                if self.deck: burned = self.deck.pop(); logging.debug(f"Burned card: {burned}")
                else: raise IndexError("Deck empty before burning card")
                for _ in range(card_count):
                    if self.deck: new_cards.append(self.deck.pop())
                    else: raise IndexError(f"Deck empty while dealing {stage}")
                self.community_cards.extend(new_cards); self.game_stage = next_stage; logging.info(f"Community Cards ({stage}): {self.community_cards}")
                if self._player_action_event: self._player_action_event.set(); self._player_action_event = None; logging.debug("Cleared action event on new community card deal.")
            except IndexError as e: logging.error(f"Deck ran out during {stage} deal: {e}"); self.game_stage = "hand_over"; error_msg = "Error: Deck ran out!"; event_to_set = self._player_action_event
        await self.broadcast_game_state(); await asyncio.sleep(0.5)
        if error_msg: await self.broadcast("game_message", {"message": error_msg}); return
        players_can_act = [p for pid in self.active_players_order if (p:=self.players.get(pid)) and p.can_act()]
        if len(players_can_act) <= 1: logging.info(f"Only {len(players_can_act)} player(s) can act after {stage}. Skipping betting round."); await self.check_hand_over_conditions()
        else: logging.debug(f"{len(players_can_act)} players can act after {stage}. Proceeding to betting round.")

    async def perform_showdown(self):
        logging.info("-" * 20 + " Performing Showdown " + "-" * 20)
        all_hands_data = {}; hand_ranks_data = {}; final_winners_summary = []
        async with self._action_lock:
            if self.game_stage == "hand_over": logging.debug("Skipping showdown: Hand already marked as over."); return
            self.game_stage = "showdown"; self.current_player_id = None
            if self._player_action_event: self._player_action_event.set(); self._player_action_event = None
            contenders = sorted([p for pid in self.active_players_order if (p := self.players.get(pid)) and p.status in ["active", "all-in"]], key=lambda p: p.total_bet_this_hand)
            all_hands_data = {p.id: p.hand for p in contenders if p.hand}
            if not contenders: logging.error("Showdown Error: No contenders found!"); self.game_stage = "hand_over"; return
            if len(contenders) == 1: logging.warning("Showdown Warning: Only 1 contender remains (should have ended earlier)."); pass
            pots = []; player_winnings = defaultdict(int); player_contributions = {p.id: p.total_bet_this_hand for p in self.players.values() if p.total_bet_this_hand > 0}
            sorted_contributions = sorted([(p.id, p.total_bet_this_hand) for p in contenders], key=lambda item: item[1]); last_contribution_level = 0; all_player_ids_in_pot = list(player_contributions.keys())
            for p_id, contribution in sorted_contributions:
                level_contribution = contribution - last_contribution_level
                if level_contribution <= 0: continue
                contributors_at_this_level_or_more = [player_id for player_id in all_player_ids_in_pot if player_contributions.get(player_id, 0) >= contribution]
                pot_amount_at_level = level_contribution * len(contributors_at_this_level_or_more)
                eligible_winners_for_this_pot = [c.id for c in contenders if c.total_bet_this_hand >= contribution]
                if pot_amount_at_level > 0 and eligible_winners_for_this_pot:
                    pots.append({"eligible_players": eligible_winners_for_this_pot, "amount": pot_amount_at_level})
                    logging.info(f"Pot Slice Calc: Level ${contribution}, Amt ${pot_amount_at_level}, Eligible: {eligible_winners_for_this_pot}")
                last_contribution_level = contribution
            total_pot_calculated = sum(p['amount'] for p in pots); logging.info(f"Total pot calculated: ${total_pot_calculated} (Tracked self.pot: ${self.pot})")
            if total_pot_calculated != self.pot:
                logging.warning(f"Pot mismatch! Calculated ${total_pot_calculated}, Tracked ${self.pot}. Adjusting last pot slice.");
                if pots: diff = self.pot - total_pot_calculated; pots[-1]["amount"] += diff; logging.info(f"Adjusted last pot amount by ${diff}. New amount: ${pots[-1]['amount']}")
                elif self.pot > 0: logging.error("Pot mismatch but no pot slices calculated!"); pots.append({"eligible_players": [c.id for c in contenders], "amount": self.pot})
            self.pot = 0
            evaluated_hands = {}
            for p in contenders:
                if p.hand:
                    try:
                        eval_result = evaluate_hand(p.hand, self.community_cards); evaluated_hands[p.id] = eval_result
                        hand_ranks_data[p.id] = eval_result[2]; p.last_hand_rank = eval_result[2]
                    except Exception as e: logging.error(f"Hand evaluation error for Player {p.id}: {e}", exc_info=True); hand_ranks_data[p.id] = "Eval Error"; p.last_hand_rank = "Eval Error"
            for i, pot_info in enumerate(pots):
                eligible_ids = pot_info["eligible_players"]; pot_amount = pot_info["amount"]; pot_name = f"Main Pot" if i == 0 else f"Side Pot {i}"
                logging.info(f"Awarding {pot_name} (${pot_amount}) among eligible players: {eligible_ids}")
                if not eligible_ids: logging.warning(f"{pot_name} has no eligible players? Skipping."); continue
                if pot_amount <= 0: logging.debug(f"{pot_name} is empty or negative (${pot_amount}), skipping award."); continue
                eligible_evaluated_for_pot = []
                for p_id in eligible_ids:
                    if p_id in evaluated_hands: score, kicks, name, best5 = evaluated_hands[p_id]; eligible_evaluated_for_pot.append({"id": p_id, "score": score, "kicks": kicks, "name": name, "best5": best5})
                if not eligible_evaluated_for_pot: logging.warning(f"{pot_name}: No evaluated hands found among eligible players {eligible_ids}? Skipping."); continue
                eligible_evaluated_for_pot.sort(key=lambda x: (x["score"], x["kicks"]), reverse=True)
                best_score_in_pot = eligible_evaluated_for_pot[0]["score"]; best_kickers_in_pot = eligible_evaluated_for_pot[0]["kicks"]
                pot_winners = [h for h in eligible_evaluated_for_pot if h["score"] == best_score_in_pot and h["kicks"] == best_kickers_in_pot]
                num_winners = len(pot_winners)
                if num_winners > 0:
                    win_each = pot_amount // num_winners; remainder = pot_amount % num_winners
                    if remainder > 0: logging.warning(f"{pot_name} split resulted in ${remainder} remainder, which is ignored.")
                    for winner in pot_winners:
                        player_winnings[winner["id"]] += win_each
                        summary_entry = next((item for item in final_winners_summary if item["playerId"] == winner["id"]), None)
                        if summary_entry: summary_entry["amount"] += win_each
                        else: final_winners_summary.append({"playerId": winner["id"], "playerName": self.players.get(winner["id"]).name or f"P{winner['id']}", "amount": win_each, "handRank": winner["name"], "winningHand": winner["best5"]})
                else: logging.error(f"{pot_name} logic error: No winners found among evaluated hands.")
            logging.info("Updating player stacks with total winnings from all pots:")
            for p_id, total_won in player_winnings.items():
                if total_won > 0:
                    player = self.players.get(p_id)
                    if player: player.stack += total_won; logging.info(f" Player P{p_id} ({player.name}) wins total ${total_won}. New Stack: ${player.stack}")
                    else: logging.error(f"Player P{p_id} not found when trying to award winnings ${total_won}")
            self.game_stage = "hand_over"
        await self.broadcast("showdown", {"allHands": all_hands_data, "handRanks": hand_ranks_data})
        await self.broadcast_game_state(); await asyncio.sleep(1)
        await self.award_pot(None, 0, is_uncontested=False, winners_data=final_winners_summary)

    async def award_pot(self, winner_id: Optional[int], pot_amount: int, is_uncontested: bool, winners_data: Optional[List[Dict]] = None):
         logging.info(f"Awarding Pot: Amount=${pot_amount}, Uncontested={is_uncontested}, WinnerID={winner_id}, WinnersData={winners_data}")
         final_payload = []
         if is_uncontested:
             async with self._action_lock:
                 if winner_id is not None and winner_id in self.players:
                     winner = self.players.get(winner_id)
                     if winner:
                         logging.info(f"Updating stack for uncontested winner P{winner_id} ('{winner.name}') Current: ${winner.stack}, Adding: ${pot_amount}")
                         winner.stack += pot_amount; winner_name = winner.name or f"P{winner_id}"
                         final_payload.append({"playerId": winner_id, "playerName": winner_name, "amount": pot_amount})
                         logging.info(f"Stack updated for P{winner_id}. New stack: ${winner.stack}")
                     else: logging.warning(f"Uncontested winner P{winner_id} not found during stack update."); final_payload.append({"playerName": f"P{winner_id} (Not Found)", "amount": pot_amount})
                 else: logging.warning(f"Uncontested pot ${pot_amount} awarded, but winner ID {winner_id} is invalid or player not found."); final_payload.append({"playerName": "Unknown Winner", "amount": pot_amount})
                 self.game_stage = "hand_over"
         else:
             if winners_data: final_payload = winners_data
             else: logging.error("Pot award called for showdown but no winner data was provided.")
             async with self._action_lock: self.game_stage = "hand_over"
         logging.info(f"Broadcasting pot_awarded: {final_payload}")
         await self.broadcast("pot_awarded", {"winners": final_payload, "isUncontested": is_uncontested})
         await self.broadcast_game_state()

game = PokerGame()

async def handler(websocket):
    player = None; ws_id_str = f"{websocket.remote_address}" if hasattr(websocket, 'remote_address') else f"UnknownWS({id(websocket)})"
    logging.info(f"Incoming connection attempt from {ws_id_str}")
    try:
        await game.register_player(websocket)
        async with game._action_lock:
             for p_obj in game.players.values():
                 if p_obj.websocket == websocket: player = p_obj; break
        if not player: logging.warning(f"Registration failed for {ws_id_str}. Closing handler."); return
        p_id_str = f"P{player.id}"; logging.info(f"Connection {ws_id_str} successfully registered as {p_id_str}")
        async for message in websocket:
            current_pid = player.id if player else None; p_id_log_str = f"P{current_pid}" if current_pid else ws_id_str
            async with game._action_lock: player_exists = current_pid in game.players
            if not player_exists: logging.warning(f"WS {ws_id_str} msg but P{current_pid} no longer exists. Breaking loop."); break
            logging.debug(f"Raw message received from {p_id_log_str}: {message}")
            try:
                data = json.loads(message); msg_type = data.get("type"); payload = data.get("payload")
                if not msg_type or payload is None: logging.warning(f"Invalid msg format from {p_id_log_str}: {message}"); await game.send_error(websocket, "Invalid message format (missing type or payload)."); continue
                if msg_type == "set_name" and isinstance(payload.get("name"), str): await game.set_player_name(player.id, payload["name"])
                elif msg_type == "player_action" and isinstance(payload.get("action"), str):
                    action = payload["action"].lower(); amount = payload.get("amount"); parsed_amount = None
                    if amount is not None:
                        try: parsed_amount = int(amount); assert parsed_amount >= 0
                        except (ValueError, TypeError, AssertionError): logging.warning(f"Invalid amount '{amount}' from {p_id_log_str} for '{action}'."); await game.send_error(websocket, "Invalid action amount provided."); continue
                    await game.handle_player_action(player.id, action, parsed_amount)
                else: logging.warning(f"Unknown msg type '{msg_type}' from {p_id_log_str}"); await game.send_error(websocket, f"Unknown message type received: {msg_type}")
            except json.JSONDecodeError: logging.warning(f"Invalid JSON from {p_id_log_str}: {message}"); await game.send_error(websocket, "Invalid JSON format.")
            except websockets.exceptions.ConnectionClosed: logging.info(f"Connection closed for {p_id_log_str} while processing message."); break
            except Exception as e: logging.exception(f"!!! Error processing message from {p_id_log_str}: {e} !!!"); await game.send_error(websocket, f"An internal server error occurred.")
    except websockets.exceptions.ConnectionClosedOK: logging.info(f"Connection closed normally for {p_id_str if player else ws_id_str}")
    except websockets.exceptions.ConnectionClosedError as e: logging.info(f"Connection closed with error for {p_id_str if player else ws_id_str}: {e}")
    except Exception as e: logging.exception(f"!!! Unhandled Error in WebSocket handler for {p_id_str if player else ws_id_str}: {e} !!!")
    finally:
        ws_id = id(websocket); p_id_final = player.id if player else 'N/A'
        logging.info(f"WebSocket handler finally block executing for ws={ws_id} (Player ID: {p_id_final})")
        await game.unregister_player(websocket)
        logging.info(f"Unregister player completed for ws={ws_id}")

async def main():
    loop = asyncio.get_running_loop(); stop_server = loop.create_future()
    if game.game_loop_task and not game.game_loop_task.done():
        logging.info("Attempting to cancel existing game loop task..."); game.game_loop_task.cancel()
        try: await game.game_loop_task
        except asyncio.CancelledError: pass
        logging.info("Previous game loop task cancelled."); game.game_loop_task = None
        
    CERT_PATH = "cert.pem" 
    KEY_PATH = "key.pem"   

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ssl_context.load_cert_chain(certfile=CERT_PATH, keyfile=KEY_PATH)
        logging.info(f"SSL certificate loaded successfully from {CERT_PATH} and {KEY_PATH}")
        use_ssl = True
    except FileNotFoundError:
        logging.error(f"!!! SSL Error: Certificate ({CERT_PATH}) or Key ({KEY_PATH}) not found.")
        logging.error("!!! Server will start WITHOUT SSL (ws://). Communication will NOT be secure.")
        ssl_context = None
        use_ssl = False
    except ssl.SSLError as e:
        logging.error(f"!!! SSL Error loading certificate/key: {e}")
        logging.error("!!! Server will start WITHOUT SSL (ws://). Communication will NOT be secure.")
        ssl_context = None
        use_ssl = False
        
    host = "0.0.0.0"; port = 8765; logging.info(f"--- Starting Poker WebSocket Server on wss://{host}:{port} ---")
    protocol = "wss" if use_ssl else "ws"
    logging.info(f"--- Starting Poker WebSocket Server on {protocol}://{host}:{port} ---")
    
    try:
        async with websockets.serve(handler, host, port, ssl=ssl_context if use_ssl else None) as server:
             logging.info(f"Server listening on {server.sockets[0].getsockname()}")
             await stop_server
    except asyncio.CancelledError: logging.info("Main server task was cancelled.")
    except OSError as e: logging.error(f"Could not start server on {host}:{port}. Error: {e}. Is the port already in use?")
    except Exception as e: logging.exception(f"An unexpected error occurred in main(): {e}")
    finally:
         logging.info("--- Shutting down server ---")
         if game.game_loop_task and not game.game_loop_task.done():
              logging.info("Cancelling active game loop task during shutdown..."); game.game_loop_task.cancel()
              try: await game.game_loop_task
              except asyncio.CancelledError: pass
              logging.info("Game loop task cancelled.")
         logging.info("Server shutdown complete.")

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("\n--- Server stopped by KeyboardInterrupt (Ctrl+C) ---")
    except Exception as e: logging.exception(f"--- Server stopped due to unexpected error: {e} ---")

