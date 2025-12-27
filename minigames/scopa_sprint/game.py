# minigames/scopa_sprint/game.py
# Scopa Sprint — simplified, best-of-3
# Assets: background.png + spritesheet.png (same folder) with graceful fallbacks.
# Scoring (per round): Most Cards (1) + Scopa (+1 each). Ties: Sette Bello (7♦) > Last Capture.
# Visuals: opponent side deck & counter (deck size), backs centered at top, bigger panel fonts.
# Match end: shows "You Win!" / "You Lose!" banner, then fires callbacks so the minigame ends.

import os
import itertools
import random
import time
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple, Dict, Any

import pygame

from game_context import GameContext
from scene_manager import Scene
from minigames.shared.end_banner import EndBanner

TITLE = "Scopa Sprint"
MINIGAME_ID = "scopa_sprint"
MULTIPLAYER_ENABLED = True

# ---------------------------
# Tuning / constants
# ---------------------------
SEED_BASE = 4242  # deterministic per-round seed base
ROUNDS_BEST_OF = 1  # single round win
BANNER_MS = 1700
SCOPA_FLASH_MS = 900

RIGHT_PANEL_W = 260
TABLE_MARGIN = 20
CARD_W, CARD_H = 78, 112
CARD_GAP = 8
CARD_RADIUS = 10
HAND_Y_OFFSET = 20

SUITS = ["coins", "cups", "swords", "clubs"]
SUIT_ICON = {"coins": "♦", "cups": "♥", "swords": "♠", "clubs": "♣"}
SUIT_COLOR = {
    "coins": (220, 60, 60),
    "cups": (220, 60, 60),
    "swords": (30, 30, 30),
    "clubs": (30, 30, 30),
}

# Demo fallback art (only if local files missing)
DEMO_BG = "/mnt/data/background (1).png"
DEMO_SHEET = "/mnt/data/spritesheet (4).png"


def try_load(path):
    try:
        return pygame.image.load(path).convert_alpha()
    except Exception:
        return None


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------------------
# Card
# ---------------------------
@dataclass(frozen=True)
class Card:
    rank: int  # 1..10
    suit: str  # coins/cups/swords/clubs


# ---------------------------
# Renderer (spritesheet like Sevens Blitz)
# ---------------------------
class CardRenderer:
    """
    spritesheet.png 13x5 (A..K x 4 suits + backs):
      ranks 1..10 -> cols 0..9  (A..10)
      rows: swords->0 (spades), cups->1 (hearts), coins->2 (diamonds), clubs->3 (clubs)
      back: row 4, col 0
    """

    def __init__(self, screen, font, small):
        self.screen = screen
        self.font = font
        self.small = small

        here = os.path.dirname(__file__)
        self.bg = try_load(os.path.join(here, "background.png")) or try_load(DEMO_BG)
        self.sheet = try_load(os.path.join(here, "spritesheet.png")) or try_load(
            DEMO_SHEET
        )

        self.cols, self.rows = 13, 5
        if self.sheet:
            self.tw = self.sheet.get_width() // self.cols
            self.th = self.sheet.get_height() // self.rows
        else:
            self.tw = self.th = 1

        self.row_for_suit = {"swords": 0, "cups": 1, "coins": 2, "clubs": 3}
        self.col_for_rank = {r: (r - 1) for r in range(1, 11)}

    def draw_bg(self, surf, W, H):
        if self.bg:
            surf.blit(pygame.transform.smoothscale(self.bg, (W, H)), (0, 0))
        else:
            # felt fallback
            surf.fill((132, 92, 52))
            felt = pygame.Rect(
                TABLE_MARGIN,
                TABLE_MARGIN,
                W - RIGHT_PANEL_W - TABLE_MARGIN * 2,
                H - TABLE_MARGIN * 2,
            )
            pygame.draw.rect(surf, (22, 96, 82), felt, border_radius=22)
            pygame.draw.rect(surf, (10, 63, 55), felt, 3, border_radius=22)

    def _tile(self, col, row):
        if not self.sheet:
            return None
        r = pygame.Rect(col * self.tw, row * self.th, self.tw, self.th)
        img = self.sheet.subsurface(r).copy()
        return pygame.transform.smoothscale(img, (CARD_W, CARD_H))

    def face(self, card: Card):
        if not self.sheet:
            return None
        return self._tile(self.col_for_rank[card.rank], self.row_for_suit[card.suit])

    def back(self):
        return self._tile(0, 4)  # first back

    def draw_card(
        self, card: Card, rect: pygame.Rect, is_selected=False, can_select=False
    ):
        face = self.face(card)
        if face:
            self.screen.blit(face, rect.topleft)
        else:
            pygame.draw.rect(
                self.screen, (250, 250, 250), rect, border_radius=CARD_RADIUS
            )
            pygame.draw.rect(
                self.screen, (30, 30, 30), rect, 2, border_radius=CARD_RADIUS
            )
            t = self.font.render(
                f"{('A' if card.rank==1 else str(card.rank))}{SUIT_ICON[card.suit]}",
                True,
                SUIT_COLOR[card.suit],
            )
            self.screen.blit(t, (rect.x + 8, rect.y + 6))
        if can_select:
            pygame.draw.rect(
                self.screen, (40, 150, 255), rect, 3, border_radius=CARD_RADIUS
            )
        if is_selected:
            pygame.draw.rect(
                self.screen, (255, 215, 60), rect, 4, border_radius=CARD_RADIUS
            )

    def draw_back(self, rect: pygame.Rect):
        img = self.back()
        if img:
            self.screen.blit(img, rect.topleft)
        else:
            pygame.draw.rect(
                self.screen, (40, 90, 140), rect, border_radius=CARD_RADIUS
            )
            pygame.draw.rect(
                self.screen, (10, 20, 30), rect, 2, border_radius=CARD_RADIUS
            )


# ---------------------------
# Scoring (simplified)
# ---------------------------
class Scoring:
    @staticmethod
    def most_cards_point(p1_caps: List[Card], p2_caps: List[Card]) -> Tuple[int, int]:
        a, b = len(p1_caps), len(p2_caps)
        return (1, 0) if a > b else (0, 1) if b > a else (0, 0)

    @staticmethod
    def sette_bello(caps: List[Card]) -> bool:
        return any(c.rank == 7 and c.suit == "coins" for c in caps)


# ---------------------------
# Move gen / AI
# ---------------------------
def all_capture_sets_for(table: List[Card], want_sum: int) -> List[Set[int]]:
    sets = []
    idxs = list(range(len(table)))
    # singletons
    for i in idxs:
        if table[i].rank == want_sum:
            sets.append(frozenset((i,)))
    # combos
    for r in range(2, len(table) + 1):
        for comb in itertools.combinations(idxs, r):
            if sum(table[i].rank for i in comb) == want_sum:
                sets.append(frozenset(comb))
    # dedupe
    out, seen = [], set()
    for s in sets:
        if s not in seen:
            out.append(set(s))
            seen.add(s)
    return out


@dataclass
class Move:
    hand_index: int
    capture_indices: Optional[Set[int]]  # None => drop


class AIPlayer:
    def __init__(self, rng: random.Random):
        self.rng = rng

    def choose(self, hand: List[Card], table: List[Card]) -> Move:
        legal: List[Move] = []
        for hi, c in enumerate(hand):
            caps = all_capture_sets_for(table, c.rank)
            if caps:
                for cap in caps:
                    legal.append(Move(hi, set(cap)))
            else:
                legal.append(Move(hi, None))

        # Score with simple heuristic
        def score(m: Move) -> float:
            c = hand[m.hand_index]
            s = 0.0
            if m.capture_indices is None:
                s -= 2.0
                # avoid enabling easy sweep
                ssum = sum(x.rank for x in (table + [c]))
                if 1 <= ssum <= 10:
                    s -= 10.0
            else:
                grabbed = [table[i] for i in m.capture_indices] + [c]
                s += 5.0  # prefer capture
                s += 0.6 * len(grabbed)  # more cards -> helps "most cards"
                if len(m.capture_indices) == len(table):
                    s += 20.0  # scopa
                s += 0.4 * sum(1 for g in grabbed if g.suit == "coins")
            return s + self.rng.uniform(-0.6, 0.6)

        if self.rng.random() < 0.05:
            return self.rng.choice(legal)
        return max(legal, key=score)


# ---------------------------
# Scene
# ---------------------------
def safe_fonts():
    # Bigger fonts for the right panel
    return (
        pygame.font.SysFont(None, 42),  # big header
        pygame.font.SysFont(None, 28),  # panel text
        pygame.font.SysFont(None, 22),
    )  # small


class ScopaSprintGame:
    def __init__(self, manager, result_callback, **kwargs):
        self.manager = manager
        self.result_callback = result_callback
        self.screen = manager.screen
        self.W, self.H = manager.size

        self.big, self.panel_font, self.small = safe_fonts()
        self.count_font = pygame.font.SysFont(None, 32)  # big numbers over side decks
        self.renderer = CardRenderer(self.screen, self.panel_font, self.small)

        self.play_w = self.W - RIGHT_PANEL_W
        self.panel_rect = pygame.Rect(self.play_w, 0, RIGHT_PANEL_W, self.H)
        self.confirm_rect = pygame.Rect(0, 0, 0, 0)  # safe default

        # Match state
        self.round_index = 0
        self.round_wins_p = 0
        self.round_wins_ai = 0
        self.start_player = 0  # 0 = local host side, 1 = remote
        self.match_over = False
        self.turn_idx = 0

        # Multiplayer plumbing
        ctx_flags = getattr(kwargs.get("context"), "flags", {}) or {}
        self.duel_id = kwargs.get("duel_id") or ctx_flags.get("duel_id")
        self.participants = kwargs.get("participants") or ctx_flags.get("participants") or []
        self.net_client = kwargs.get("multiplayer_client") or ctx_flags.get("multiplayer_client")
        self.local_id = kwargs.get("local_player_id") or ctx_flags.get("local_player_id")
        self.local_idx = 0
        if self.participants and self.local_id in self.participants:
            try:
                self.local_idx = self.participants.index(self.local_id)
            except ValueError:
                self.local_idx = 0
        self.remote_idx = 1 if self.local_idx == 0 else 0
        self.remote_id = self.participants[self.remote_idx] if len(self.participants) > self.remote_idx else None
        self.net_enabled = bool(self.duel_id and self.participants and self.net_client and self.local_id)
        self.is_authority = not self.net_enabled or self.local_idx == 0
        self.awaiting_ack = False
        self.net_interval = 1.0 / 15.0
        self.net_last = 0.0
        self.pending_payload: Dict[str, Any] = {}

        self.reset_round()

        # banners / result
        self.flash_until = 0
        self.flash_text = ""
        self.banner_until = 0
        self.banner_text = ""
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # ----- round setup -----
    def reset_round(self):
        seed_src = self.duel_id or "scopa"
        seed = SEED_BASE + self.round_index * 997 + (hash(seed_src) & 0xFFFF)
        self.rng = random.Random(seed)

        # deck
        self.deck: List[Card] = [Card(r, s) for s in SUITS for r in range(1, 11)]
        self.rng.shuffle(self.deck)
        self.deck_count = len(self.deck)

        # initial deal
        self.table: List[Card] = [self.deck.pop() for _ in range(4)]
        self.hand_p: List[Card] = [self.deck.pop() for _ in range(3)]
        self.hand_ai: List[Card] = [self.deck.pop() for _ in range(3)]
        self.deck_count = len(self.deck)
        self.caps_p: List[Card] = []
        self.caps_ai: List[Card] = []
        self.scopa_p = 0
        self.scopa_ai = 0
        self.last_taker: Optional[int] = None  # 0/1

        # player to move
        self.turn_idx = self.start_player

        # selection state
        self.selected_hand_idx: Optional[int] = None
        self.selected_table: Set[int] = set()
        self.cached_legal: List[Set[int]] = []

        self.ai = AIPlayer(self.rng)

    # ----- events -----
    def handle_event(self, e):
        if e.type != pygame.MOUSEBUTTONDOWN or e.button != 1:
            return
        mx, my = e.pos
        # Only the active player can act in turn-based mode
        if self.net_enabled and self.turn_idx != self.local_idx:
            return

        # confirm
        if self.confirm_rect.collidepoint(mx, my):
            self.player_confirm()
            return

        # select hand card
        hand_y = self.H - CARD_H - HAND_Y_OFFSET
        hi = self._hit_index((mx, my), self.hand_p, hand_y)
        if hi is not None and hi < len(self.hand_p):
            if self.selected_hand_idx == hi:
                self.selected_hand_idx = None
                self.selected_table.clear()
                self.cached_legal = []
            else:
                self.selected_hand_idx = hi
                self.selected_table.clear()
                self.cached_legal = self.legal_sets_for_selected()
            return

        # toggle table card in selection
        ti = self._hit_index((mx, my), self.table, self.table_y)
        if (
            self.selected_hand_idx is not None
            and ti is not None
            and ti < len(self.table)
        ):
            legal = self.cached_legal or self.legal_sets_for_selected()
            if any(ti in s for s in legal):
                if ti in self.selected_table:
                    self.selected_table.remove(ti)
                else:
                    self.selected_table.add(ti)

    # ----- logic -----
    def legal_sets_for_selected(self) -> List[Set[int]]:
        if self.selected_hand_idx is None or self.selected_hand_idx >= len(self.hand_p):
            return []
        card = self.hand_p[self.selected_hand_idx]
        return all_capture_sets_for(self.table, card.rank)

    def _hit_index(self, pos, cards, row_y) -> Optional[int]:
        x, y = pos
        if not (row_y <= y <= row_y + CARD_H):
            return None
        n = len(cards)
        if n == 0:
            return None
        total_w_cards = n * CARD_W + (n - 1) * CARD_GAP
        base_x = max(TABLE_MARGIN, (self.play_w - total_w_cards) // 2)
        for i in range(n):
            r = pygame.Rect(base_x + i * (CARD_W + CARD_GAP), row_y, CARD_W, CARD_H)
            if r.collidepoint(x, y):
                return i
        return None

    def try_draw(self, hand: List[Card]):
        while len(hand) < 3 and self.deck:
            hand.append(self.deck.pop())
        self.deck_count = len(self.deck)

    def do_capture(self, who_idx: int, move_card: Card, capture_indices: Set[int]):
        grabbed = [self.table[i] for i in sorted(capture_indices)]
        clears = len(capture_indices) == len(self.table)
        for i in sorted(capture_indices, reverse=True):
            del self.table[i]
        target = self.caps_p if who_idx == 0 else self.caps_ai
        target.extend(grabbed)
        target.append(move_card)
        self.last_taker = who_idx
        if clears:
            if who_idx == 0:
                self.scopa_p += 1
            else:
                self.scopa_ai += 1
            self.flash("Scopa!", SCOPA_FLASH_MS)

    def apply_move(self, side_idx: int, hand_idx: int, capture_indices: Set[int]) -> bool:
        """Apply a move for the given side. Returns True if applied."""
        hand = self.hand_p if side_idx == 0 else self.hand_ai
        if hand_idx < 0 or hand_idx >= len(hand):
            return False
        card = hand[hand_idx]
        legal_sets = all_capture_sets_for(self.table, card.rank)
        valid_capture = any(capture_indices == s for s in legal_sets)
        has_capture = bool(legal_sets)

        if valid_capture:
            hand.pop(hand_idx)
            self.do_capture(side_idx, card, set(capture_indices))
            self.try_draw(hand)
        elif not has_capture and len(capture_indices) == 0:
            hand.pop(hand_idx)
            self.table.append(card)
            self.try_draw(hand)
        else:
            return False
        # next turn
        self.turn_idx = 1 - side_idx
        # clear selection for local view
        self.selected_hand_idx = None
        self.selected_table.clear()
        self.cached_legal = []
        return True

    def player_confirm(self):
        if self.selected_hand_idx is None:
            return
        if self.net_enabled and self.turn_idx != self.local_idx:
            return
        hand_idx = self.selected_hand_idx
        capture_set = set(self.selected_table)
        if self.net_enabled and not self.is_authority:
            self._net_send_action(
                {
                    "kind": "play",
                    "hand": hand_idx,
                    "capture": sorted(capture_set),
                }
            )
            # clear local selection after sending to avoid duplicate submits
            self.selected_hand_idx = None
            self.selected_table.clear()
            self.cached_legal = []
            return
        # authority or offline path
        if not self.apply_move(self.turn_idx, hand_idx, capture_set):
            self.flash("Must capture", 650)
            return
        self.end_if_round_over()
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    def ai_play(self):
        # Not used in multiplayer; kept for solo (net disabled)
        if self.net_enabled:
            return
        if not self.hand_ai:
            self.try_draw(self.hand_ai)
            self.turn_idx = 0
            return

        mv = self.ai.choose(self.hand_ai, self.table)
        card = self.hand_ai.pop(mv.hand_index)
        caps_for = all_capture_sets_for(self.table, card.rank)

        if mv.capture_indices is None:
            if not caps_for:
                self.table.append(card)
                self.turn_idx = 0
            else:
                self.do_capture(1, card, caps_for[0])
                self.try_draw(self.hand_ai)
                self.turn_idx = 0
        else:
            self.do_capture(1, card, mv.capture_indices)
            self.try_draw(self.hand_ai)
            self.turn_idx = 0

    def end_if_round_over(self) -> bool:
        if self.deck or self.hand_p or self.hand_ai:
            return False

        # Leftovers go to last taker (no Scopa)
        if self.table and self.last_taker:
            target = self.caps_p if self.last_taker == 0 else self.caps_ai
            target.extend(self.table)
            self.table.clear()

        # Simplified scoring for the round
        p_m_cards, a_m_cards = Scoring.most_cards_point(self.caps_p, self.caps_ai)
        p_sc, a_sc = self.scopa_p, self.scopa_ai
        p_total = p_m_cards + p_sc
        a_total = a_m_cards + a_sc

        # Determine round winner
        winner = None
        if p_total > a_total:
            winner = 0
        elif a_total > p_total:
            winner = 1
        else:
            # tiebreakers: Sette Bello, then Last Capture
            sb_p = Scoring.sette_bello(self.caps_p)
            sb_a = Scoring.sette_bello(self.caps_ai)
            if sb_p and not sb_a:
                winner = 0
            elif sb_a and not sb_p:
                winner = 1
            elif self.last_taker in (0, 1):
                winner = self.last_taker
            else:
                winner = None  # true tie round

        # Banner
        ptxt = f"You +{p_m_cards} Cards, +{p_sc} Scopa = {p_total}"
        atxt = f"Opp +{a_m_cards} Cards, +{a_sc} Scopa = {a_total}"
        if winner == 0:
            self.round_wins_p += 1
            self.banner(f"Round to You  |  {ptxt}  vs  {atxt}")
        elif winner == 1:
            self.round_wins_ai += 1
            self.banner(f"Round to Opponent  |  {ptxt}  vs  {atxt}")
        else:
            self.banner(f"Round Tied  |  {ptxt}  vs  {atxt}")

        self.round_index += 1

        # Match end? (best of 3)
        need = ROUNDS_BEST_OF // 2 + 1  # 2
        if (
            self.round_wins_p >= need
            or self.round_wins_ai >= need
            or self.round_index >= ROUNDS_BEST_OF
        ):
            # If tied at the end (possible due to a tied round), favor the player
            if self.round_wins_p > self.round_wins_ai:
                self.finish(True)
                return True
            if self.round_wins_ai > self.round_wins_p:
                self.finish(False)
                return True
            self.finish(True)
            return True
        else:
            # next round: alternate starting player
            self.start_player = 1 - self.start_player
            if self.is_authority:
                self.reset_round()
                self._net_send_state(force=True)
        return True

    # --- banners / result ---
    def finish(self, player_won: bool):
        if self.match_over:
            return
        self.match_over = True
        self.turn = None
        self.banner("You Win!" if player_won else "You Lose!", BANNER_MS)
        if callable(self.result_callback):
            payload = self._match_result_payload()
            try:
                self.result_callback(player_won, self.banner_text, payload)
            except Exception:
                pass
        if self.net_enabled and self.is_authority:
            winner_id = self.local_id if player_won else self.remote_id
            loser_id = self.remote_id if player_won else self.local_id
            self.pending_payload = {"winner": winner_id, "loser": loser_id}
            self._net_send_action(
                {"kind": "finish", "winner": winner_id, "loser": loser_id, "outcome": "win" if player_won else "lose"}
            )

    def flash(self, text: str, ms: int):
        self.flash_until = pygame.time.get_ticks() + ms
        self.flash_text = text

    def banner(self, text: str, ms: int = BANNER_MS):
        self.banner_until = pygame.time.get_ticks() + ms
        self.banner_text = text

    def _match_result_payload(self):
        payload = {
            "rounds_won": {
                "player": self.round_wins_p,
                "opponent": self.round_wins_ai,
            },
            "rounds_played": self.round_index,
            "last_round_scopa": {
                "player": self.scopa_p,
                "opponent": self.scopa_ai,
            },
            "sette_bello": {
                "player": Scoring.sette_bello(self.caps_p),
                "opponent": Scoring.sette_bello(self.caps_ai),
            },
        }
        if self.net_enabled:
            winner_id = self.local_id if self.round_wins_p >= self.round_wins_ai else self.remote_id
            loser_id = self.remote_id if winner_id == self.local_id else self.local_id
            payload["winner"] = winner_id
            payload["loser"] = loser_id
        return payload

    # ----- update/draw -----
    def update(self, dt):
        self._net_poll_actions(float(dt))
        now = pygame.time.get_ticks()

        if self.net_enabled:
            # authority sends periodic state
            if self.is_authority and not self.match_over:
                self._net_send_state()

        if not self.net_enabled and self.turn_idx == 1:
            if not hasattr(self, "_ai_wait"):
                self._ai_wait = now + 300
            if now >= self._ai_wait:
                delattr(self, "_ai_wait")
                self.ai_play()
                self.end_if_round_over()

    def draw(self):
        self.renderer.draw_bg(self.screen, self.W, self.H)

        # right panel frame
        panel = self.panel_rect
        pygame.draw.rect(self.screen, (18, 20, 28), panel)
        pygame.draw.rect(self.screen, (70, 74, 90), panel, 2)

        # round pips (top center) – show player's wins
        cx = self.play_w // 2
        pip_r = 7
        for i in range(ROUNDS_BEST_OF):
            x = cx - (ROUNDS_BEST_OF * 24) // 2 + i * 24
            pygame.draw.circle(self.screen, (230, 230, 230), (x, 16), pip_r, 1)
            if i < self.round_wins_p:
                pygame.draw.circle(self.screen, (40, 180, 80), (x, 16), pip_r - 1)

        # Opponent side deck (top-left) with **deck size** counter and their tally
        ai_side_y = TABLE_MARGIN + 16
        side_rect = self.draw_ai_side_deck(ai_side_y)

        # Row of opponent backs **centered** at top
        ai_row_y = ai_side_y
        self.draw_ai_row_center(ai_row_y)

        # Table below the opponent row
        self.table_y = ai_row_y + CARD_H + 18
        self.draw_row(self.table, self.table_y, highlight_legal=True)

        # Player hand and bottom side deck
        hand_y = self.H - CARD_H - HAND_Y_OFFSET
        self.draw_hand(hand_y)

        # Confirm button
        self.confirm_rect = pygame.Rect(self.play_w // 2 - 70, hand_y - 46, 140, 32)
        self.draw_confirm()

        # Side panel HUD (bigger fonts)
        self.draw_panel()

        # Banners
        now = pygame.time.get_ticks()
        if now < self.flash_until:
            self.draw_center_banner(
                self.flash_text, (255, 230, 90), 30, y=self.table_y - 36
            )
        if now < self.banner_until:
            self.draw_center_banner(self.banner_text, (250, 250, 255), 44)

    # ---- draw helpers ----
    def draw_big_number(self, num: int, center_x: int, top_y: int):
        s = str(num)
        shadow = self.count_font.render(s, True, (0, 0, 0))
        self.screen.blit(shadow, (center_x - shadow.get_width() // 2 + 2, top_y + 2))
        fg = self.count_font.render(s, True, (255, 255, 255))
        self.screen.blit(fg, (center_x - fg.get_width() // 2, top_y))

    def draw_tally_pips(self, center_x: int, top_y: int, wins: int, total: int = 2):
        size, gap = 12, 6
        row_w = total * size + (total - 1) * gap
        x0 = center_x - row_w // 2
        for i in range(total):
            r = pygame.Rect(x0 + i * (size + gap), top_y, size, size)
            pygame.draw.rect(self.screen, (235, 235, 235), r, 1, border_radius=3)
            if i < wins:
                pygame.draw.rect(
                    self.screen, (40, 180, 80), r.inflate(-2, -2), border_radius=3
                )

    def draw_ai_side_deck(self, y: int) -> pygame.Rect:
        side = pygame.Rect(TABLE_MARGIN, y, CARD_W, CARD_H)
        self.renderer.draw_back(side)
        # Counter now mirrors player's: **shared deck size**
        self.draw_big_number(self.deck_count, side.centerx, side.y - 28)
        self.draw_tally_pips(side.centerx, side.bottom + 6, self.round_wins_ai, total=2)
        return side

    def draw_ai_row_center(self, y: int):
        n = len(self.hand_ai)
        if n <= 0:
            return
        total_w_cards = n * CARD_W + (n - 1) * CARD_GAP
        start_x = max(TABLE_MARGIN, (self.play_w - total_w_cards) // 2)
        for i in range(n):
            rect = pygame.Rect(start_x + i * (CARD_W + CARD_GAP), y, CARD_W, CARD_H)
            self.renderer.draw_back(rect)

    def draw_row(self, cards: List[Card], y: int, highlight_legal=False):
        n = len(cards)
        total_w_cards = n * CARD_W + (n - 1) * CARD_GAP
        base_x = max(TABLE_MARGIN, (self.play_w - total_w_cards) // 2)
        for i, c in enumerate(cards):
            rect = pygame.Rect(base_x + i * (CARD_W + CARD_GAP), y, CARD_W, CARD_H)
            can = sel = False
            if (
                highlight_legal
                and self.turn_idx == self.local_idx
                and self.selected_hand_idx is not None
            ):
                legal = self.cached_legal or self.legal_sets_for_selected()
                can = any(i in s for s in legal)
                sel = i in self.selected_table
            self.renderer.draw_card(c, rect, is_selected=sel, can_select=can)

    def draw_hand(self, y: int):
        n = len(self.hand_p)
        total_w_cards = n * CARD_W + (n - 1) * CARD_GAP
        base_x = max(TABLE_MARGIN, (self.play_w - total_w_cards) // 2)
        for i, c in enumerate(self.hand_p):
            rect = pygame.Rect(base_x + i * (CARD_W + CARD_GAP), y, CARD_W, CARD_H)
            sel = (i == self.selected_hand_idx) and (self.turn_idx == self.local_idx)
            self.renderer.draw_card(c, rect, is_selected=sel, can_select=False)

        # Player side deck at bottom-left + count + tally
        back = pygame.Rect(TABLE_MARGIN, y, CARD_W, CARD_H)
        self.renderer.draw_back(back)
        self.draw_big_number(
            self.deck_count, back.centerx, back.y - 28
        )  # shared deck count
        self.draw_tally_pips(back.centerx, back.bottom + 6, self.round_wins_p, total=2)

    def draw_confirm(self):
        label = "Play"
        enabled = False
        if self.turn_idx == self.local_idx and self.selected_hand_idx is not None:
            legal = self.cached_legal or self.legal_sets_for_selected()
            has_capture = bool(legal)
            valid = any(self.selected_table == s for s in legal)
            if valid:
                label, enabled = "Capture", True
            elif not has_capture and len(self.selected_table) == 0:
                label, enabled = "Play", True
        col = (60, 160, 80) if enabled else (90, 95, 110)
        pygame.draw.rect(self.screen, col, self.confirm_rect, border_radius=12)
        pygame.draw.rect(
            self.screen, (18, 20, 28), self.confirm_rect, 2, border_radius=12
        )
        t = self.panel_font.render(label, True, (255, 255, 255))
        self.screen.blit(t, t.get_rect(center=self.confirm_rect.center))

    def draw_panel(self):
        x0 = self.panel_rect.x + 12
        y = 18
        hdr = self.big.render("Scopa Sprint", True, (245, 245, 255))
        self.screen.blit(hdr, (x0, y))
        y += 46

        # bigger panel text
        rounds = self.panel_font.render(
            f"Rounds:  {self.round_wins_p} - {self.round_wins_ai}",
            True,
            (230, 230, 235),
        )
        self.screen.blit(rounds, (x0, y))
        y += 28

        def line(txt):
            t = self.panel_font.render(txt, True, (220, 220, 230))
            self.screen.blit(t, (x0, y))

        line(f"Your captures: {len(self.caps_p)}")
        y += 26
        line(f"Opp captures:  {len(self.caps_ai)}")
        y += 26
        line(f"Your Scopa: {self.scopa_p}   |   Opp: {self.scopa_ai}")
        y += 26

        hint = self.small.render(
            "Ties: Sette Bello > Last Capture", True, (170, 176, 190)
        )
        self.screen.blit(hint, (x0, y))

    def draw_center_banner(self, text: str, color=(255, 255, 255), size=44, y=None):
        f = pygame.font.SysFont(None, clamp(int(size), 22, 72))
        t = f.render(text, True, color)
        if y is None:
            y = self.H // 2 - t.get_height() // 2
        x = (self.play_w - t.get_width()) // 2
        pad = 10
        back = pygame.Rect(
            x - pad, y - pad, t.get_width() + pad * 2, t.get_height() + pad * 2
        )
        s = pygame.Surface(back.size, pygame.SRCALPHA)
        s.fill((0, 0, 0, 160))
        self.screen.blit(s, back.topleft)
        self.screen.blit(t, (x, y))

    # ---------- networking ----------
    def _pack_state(self):
        return {
            "round_index": self.round_index,
            "round_wins": [self.round_wins_p, self.round_wins_ai],
            "start_player": self.start_player,
            "turn": self.turn_idx,
            "deck_len": len(self.deck),
            "table": [(c.rank, c.suit) for c in self.table],
            "hand_p": [(c.rank, c.suit) for c in self.hand_p],
            "hand_ai": [(c.rank, c.suit) for c in self.hand_ai],
            "caps_p": [(c.rank, c.suit) for c in self.caps_p],
            "caps_ai": [(c.rank, c.suit) for c in self.caps_ai],
            "scopa": [self.scopa_p, self.scopa_ai],
            "last_taker": self.last_taker,
            "deck_len": self.deck_count,
        }

    def _apply_state(self, st: dict):
        if not st:
            return

        def make_cards(arr):
            result = []
            for item in arr or []:
                if isinstance(item, Card):
                    result.append(item)
                else:
                    try:
                        r, s = item
                        result.append(Card(int(r), s))
                    except Exception:
                        pass
            return result

        local_bottom = self.local_idx == 0
        hand_bottom = "hand_p" if local_bottom else "hand_ai"
        hand_top = "hand_ai" if local_bottom else "hand_p"
        caps_bottom = "caps_p" if local_bottom else "caps_ai"
        caps_top = "caps_ai" if local_bottom else "caps_p"

        self.round_index = int(st.get("round_index", self.round_index))
        rw = st.get("round_wins") or [self.round_wins_p, self.round_wins_ai]
        if len(rw) == 2:
            if local_bottom:
                self.round_wins_p, self.round_wins_ai = int(rw[0]), int(rw[1])
            else:
                self.round_wins_p, self.round_wins_ai = int(rw[1]), int(rw[0])
        self.start_player = int(st.get("start_player", self.start_player))
        turn = st.get("turn", self.turn_idx)
        self.turn_idx = int(turn)
        if "table" in st:
            self.table = make_cards(st.get("table"))
        if hand_bottom in st:
            self.hand_p = make_cards(st.get(hand_bottom))
        if hand_top in st:
            self.hand_ai = make_cards(st.get(hand_top))
        if caps_bottom in st:
            self.caps_p = make_cards(st.get(caps_bottom))
        if caps_top in st:
            self.caps_ai = make_cards(st.get(caps_top))
        if "scopa" in st:
            sc = st.get("scopa") or [self.scopa_p, self.scopa_ai]
            if len(sc) == 2:
                if local_bottom:
                    self.scopa_p, self.scopa_ai = int(sc[0]), int(sc[1])
                else:
                    self.scopa_p, self.scopa_ai = int(sc[1]), int(sc[0])
        if "last_taker" in st:
            lt = st.get("last_taker")
            self.last_taker = None if lt is None else int(lt)
        if "deck_len" in st:
            try:
                self.deck_count = int(st.get("deck_len", self.deck_count))
            except Exception:
                pass
        # Keep selection only if still valid and still our turn.
        if self.selected_hand_idx is not None:
            if self.turn_idx != self.local_idx or self.selected_hand_idx >= len(self.hand_p):
                self.selected_hand_idx = None
                self.selected_table.clear()
                self.cached_legal = []

    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[ScopaSprint] send failed: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        now = time.perf_counter()
        if not force and (now - self.net_last) < self.net_interval:
            return
        self.net_last = now
        payload = {"kind": kind, "state": self._pack_state()}
        payload.update(extra or {})
        self._net_send_action(payload)

    def _net_poll_actions(self, dt: float):
        if not self.net_enabled or not self.net_client:
            return
        while True:
            msg = self.net_client.pop_duel_action(self.duel_id)
            if not msg:
                break
            if msg.get("from") == self.local_id:
                continue
            self._apply_remote_action(msg.get("action") or {})

    def _apply_remote_action(self, action: dict):
        if not action:
            return
        kind = action.get("kind")
        if kind == "state":
            if not self.is_authority:
                self._apply_state(action.get("state") or {})
            return
        if kind == "play" and self.is_authority:
            hand_idx = int(action.get("hand", -1))
            capture = set(action.get("capture") or [])
            side = self.remote_idx
            if self.apply_move(side, hand_idx, capture):
                self.end_if_round_over()
            self._net_send_state(force=True)
            return
        if kind == "finish":
            win_id = action.get("winner")
            lose_id = action.get("loser")
            outcome = action.get("outcome")
            mapped = outcome
            if win_id or lose_id:
                if win_id == self.local_id:
                    mapped = "win"
                elif lose_id == self.local_id:
                    mapped = "lose"
            self.pending_payload = {"winner": win_id, "loser": lose_id}
            self.banner(mapped, BANNER_MS)
            self.match_over = True
            if callable(self.result_callback):
                try:
                    self.result_callback(mapped == "win", self.banner_text, self.pending_payload)
                except Exception:
                    pass


# ---------------------------
# Scene wrapper (Arena integration)
# ---------------------------
class ScopaSprintScene(Scene):
    def __init__(self, manager, context, callback, **kwargs):
        super().__init__(manager)
        self.manager = manager
        self.context = context or GameContext()
        self.callback = callback
        self.screen = manager.screen
        self.w, self.h = manager.size
        self.minigame_id = "scopa_sprint"
        self.pending_outcome = None
        self.pending_payload = {}
        self._completed = False
        self.banner = EndBanner(
            duration=float(kwargs.get("banner_duration", 2.5)),
            titles={
                "win": "Scopa Sprint Cleared!",
                "lose": "Scopa Sprint Failed",
                "forfeit": "Scopa Sprint Forfeit",
            },
        )
        self.game = ScopaSprintGame(manager, self._on_game_result, context=self.context, **kwargs)

    def _on_game_result(self, player_won: bool, subtitle: str, payload: Optional[dict]):
        if self.pending_outcome:
            return
        self.pending_outcome = "win" if player_won else "lose"
        self.pending_payload = payload or {}
        self.banner.show(self.pending_outcome, subtitle=subtitle or "")

    def handle_event(self, event):
        if self.pending_outcome:
            if event.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self.banner.skip()
                self._finalize(self.pending_outcome)
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._pause_game()
            return
        self.game.handle_event(event)

    def update(self, dt):
        if self.pending_outcome:
            if self.banner.update(dt):
                self._finalize(self.pending_outcome)
            return
        self.game.update(dt)

    def draw(self):
        self.game.draw()
        if self.pending_outcome:
            self.banner.draw(self.screen, self.game.big, self.game.small, (self.w, self.h))

    def _pause_game(self):
        try:
            from pause_menu import PauseMenuScene
        except Exception as exc:
            print(f"[ScopaSprint] Pause menu unavailable: {exc}")
            return
        if self.context is None:
            self.context = GameContext()
        self.manager.push(PauseMenuScene(self.manager, self.context, self))

    def _finalize(self, outcome):
        if self._completed:
            return
        self._completed = True
        self.pending_outcome = None
        if self.context is None:
            self.context = GameContext()
        self.context.last_result = {
            "minigame": self.minigame_id,
            "outcome": outcome,
            "details": self.pending_payload,
        }
        if hasattr(self.manager, "pop"):
            try:
                self.manager.pop()
            except Exception as exc:
                print(f"[ScopaSprint] Unable to pop scene: {exc}")
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception as exc:
                print(f"[ScopaSprint] Callback error: {exc}")

    def forfeit_from_pause(self):
        if self.pending_outcome:
            self._finalize(self.pending_outcome)
        else:
            self.pending_payload = {"reason": "forfeit"}
            self.pending_outcome = "forfeit"
            self.banner.show("forfeit", subtitle="Forfeit")


def launch(manager, context, callback, **kwargs):
    return ScopaSprintScene(manager, context, callback, **kwargs)
