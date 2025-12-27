# game.py — Sevens Blitz (half-deck duel, 3-file build)
# Requires in SAME folder:
#   - background.png
#   - spritesheet.png  (13x5 frames, each 80x120; rows: ♠,♥,♦,♣, extras[backs,blank])
#
# Exports:
#   TITLE = "Sevens Blitz"
#   launch(manager, on_win=None, on_lose=None, **kwargs) -> Scene

import os
import random
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import pygame

TITLE = "Sevens Blitz"
MINIGAME_ID = "sevens_blitz"
MULTIPLAYER_ENABLED = True

# --------------------------- Spritesheet geometry ----------------------------
SPR_W, SPR_H = 80, 120
SUITS = ["♠", "♥", "♦", "♣"]
SUIT_NAMES = {"♠": "Spades", "♥": "Hearts", "♦": "Diamonds", "♣": "Clubs"}
SUIT_ROW = {"♠": 0, "♥": 1, "♦": 2, "♣": 3}
RANKS = list(range(1, 14))
RANK_COL = {r: i for i, r in enumerate(RANKS)}
EXTRA_ROW = 4
BACK_COLORS = {"red": 0, "blue": 1, "green": 2}
DEFAULT_BACK = "blue"

# ------------------------------ Look & Flow ----------------------------------
HAND_SCALE = 0.60  # player's hand size
OPP_SCALE = 0.54  # opponent's back cards
AI_THINK_MS = 450  # short delay so you can see flow
LAST_HILITE_MS = 500  # highlight last played card on board
TOAST_MS = 700  # quick popup when someone is skipped/passes
ROUND_BANNER_MS = 1100  # round result banner
MATCH_BANNER_MS = 1500  # match result banner


# ------------------------------ Model ----------------------------------------
@dataclass(frozen=True)
class Card:
    suit: str
    rank: int  # 1..13

    @property
    def pip(self) -> int:
        return self.rank

    @property
    def text(self) -> str:
        name = {1: "Ace", 11: "Jack", 12: "Queen", 13: "King"}.get(self.rank, str(self.rank))
        suit = SUIT_NAMES.get(self.suit, self.suit)
        return f"{name} of {suit}"


class Deck:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.cards = [Card(s, r) for s in SUITS for r in RANKS]
        self.rng.shuffle(self.cards)

    def deal_half(self) -> tuple[list[Card], list[Card]]:
        p = self.cards[:26]
        a = self.cards[26:52]
        p.sort(key=lambda c: (SUIT_ROW[c.suit], c.rank))
        a.sort(key=lambda c: (SUIT_ROW[c.suit], c.rank))
        return p, a


class Board:
    def __init__(self):
        self.played = {s: set() for s in SUITS}

    def started(self, suit: str) -> bool:
        return 7 in self.played[suit]

    def _range(self, suit: str) -> tuple[int, int]:
        s = self.played[suit]
        return (min(s), max(s)) if s else (0, 0)

    def can_play(self, c: Card) -> bool:
        if c.rank == 7:
            return True
        if not self.started(c.suit):
            return False
        lo, hi = self._range(c.suit)
        return c.rank == lo - 1 or c.rank == hi + 1

    def legal_from(self, hand: list[Card]) -> list[Card]:
        return [c for c in hand if self.can_play(c)]

    def play(self, c: Card) -> None:
        assert self.can_play(c), "Illegal play"
        self.played[c.suit].add(c.rank)


class Hand:
    def __init__(self, cards: list[Card]):
        self.cards = list(cards)

    def remove(self, c: Card) -> None:
        self.cards.remove(c)

    def pip_total(self) -> int:
        return sum(c.pip for c in self.cards)

    def __len__(self) -> int:
        return len(self.cards)


class AI:
    def __init__(self, rng: random.Random, randomness: float = 0.05):
        self.rng = rng
        self.randomness = randomness

    def choose(self, board: Board, hand: Hand) -> Optional[Card]:
        legal = board.legal_from(hand.cards)
        if not legal:
            return None
        if self.rng.random() < self.randomness:
            return self.rng.choice(legal)

        def score(c: Card) -> float:
            if c.rank == 7:
                return 100.0
            return (
                abs(c.rank - 7) + SUIT_ROW[c.suit] * 0.05
            )  # dump farther from 7; deterministic tie-break

        return max(legal, key=score)


# ------------------------------ Scene glue -----------------------------------
try:
    from scene_manager import Scene
except Exception:

    class Scene:
        def __init__(self, manager):
            self.manager = manager

        def handle_event(self, e):
            pass

        def update(self, dt):
            pass

        def draw(self):
            pass


class SevensBlitzScene(Scene):
    def __init__(self, manager, context, callback, **kwargs):
        super().__init__(manager)
        self.context = context
        self.callback = callback
        self.screen = manager.screen
        self.w, self.h = manager.size

        pygame.font.init()
        self.font = pygame.font.SysFont(None, 22)
        self.font_big = pygame.font.SysFont(None, 32)

        here = os.path.dirname(__file__)
        self.sheet = pygame.image.load(
            os.path.join(here, "spritesheet.png")
        ).convert_alpha()
        self.bg = pygame.image.load(os.path.join(here, "background.png")).convert()

        # layout
        self.margin = 20
        self.table_top = 110
        self.row_gap = 16
        avail = self.w - self.margin * 2 - SPR_W
        self.spacing = max(28, min(44, (avail - SPR_W) / 12))

        # RNG base + match
        self.base_seed = getattr(manager, "seed", 424242)
        self.rng = random.Random(self.base_seed)
        self.round_idx = 0
        self.player_wins = 0
        self.ai_wins = 0
        self.need = 2  # best-of-3

        # Multiplayer plumbing
        flags = getattr(context, "flags", {}) if context else {}
        self.duel_id = kwargs.get("duel_id") or (flags or {}).get("duel_id")
        self.participants = kwargs.get("participants") or (flags or {}).get("participants") or []
        self.net_client = kwargs.get("multiplayer_client") or (flags or {}).get("multiplayer_client")
        self.local_id = kwargs.get("local_player_id") or (flags or {}).get("local_player_id")
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
        self.net_timer = 0.0
        self.net_interval = 1.0 / 15.0
        self.pending_payload: Dict[str, Any] = {}

        # precompute suit row y positions
        rows_h = self.h - self.table_top - 220
        row_h = (rows_h - self.row_gap * 3) / 4
        self.row_y = {
            s: self.table_top + i * (row_h + self.row_gap) + row_h / 2
            for i, s in enumerate(SUITS)
        }

        # pass button
        self.pass_rect = pygame.Rect(0, 0, 140, 44)
        self.pass_rect.bottomright = (self.w - self.margin, self.h - self.margin)

        # state machine
        self.state = "PLAY"  # PLAY → ROUND_BANNER → (repeat) → MATCH_BANNER
        self.timer_ms = 0
        self.last_msg = ""
        self.last_play: Optional[Card] = None
        self.last_hilite_ms = 0
        self.toast_text = ""
        self.toast_ms = 0
        self._completed = False
        self.match_outcome: Optional[str] = None
        # Single-player AI helper
        self.ai_player = AI(random.Random(self.base_seed))
        self.ai_timer = 0

        # round vars (set by _start_round)
        self.board = Board()
        self.p_hand = Hand([])
        self.a_hand = Hand([])
        self.human_turn = True
        self.passes = 0
        self.turn_idx = 0
        self._start_round()  # initializes a fresh round
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    # -------------------------- helpers --------------------------
    def _sheet_frame(
        self,
        suit: Optional[str] = None,
        rank: Optional[int] = None,
        back: Optional[str] = None,
    ) -> pygame.Rect:
        if back is not None:
            return pygame.Rect(
                BACK_COLORS[back] * SPR_W, EXTRA_ROW * SPR_H, SPR_W, SPR_H
            )
        assert suit is not None and rank is not None
        return pygame.Rect(RANK_COL[rank] * SPR_W, SUIT_ROW[suit] * SPR_H, SPR_W, SPR_H)

    def _blit_card(
        self, rect: pygame.Rect, suit=None, rank=None, back=None, highlight=False
    ):
        src = self._sheet_frame(suit, rank, back)
        surf = self.sheet.subsurface(src)
        if rect.size != (SPR_W, SPR_H):
            surf = pygame.transform.smoothscale(surf, rect.size)
        self.screen.blit(surf, rect.topleft)
        if highlight:
            pygame.draw.rect(self.screen, (255, 230, 120), rect, 3, border_radius=10)

    def _hand_rects_player(self) -> list[pygame.Rect]:
        w = int(SPR_W * HAND_SCALE)
        h = int(SPR_H * HAND_SCALE)
        n = max(1, len(self.p_hand))
        total = min(self.w - self.margin * 2, n * (w + 8))
        step = total / n
        x0 = (self.w - total) / 2
        y = self.h - (h + 24)
        return [pygame.Rect(int(x0 + i * step), int(y), w, h) for i in range(n)]

    def _hand_rects_opponent(self) -> list[pygame.Rect]:
        w = int(SPR_W * OPP_SCALE)
        h = int(SPR_H * OPP_SCALE)
        n = max(1, len(self.a_hand))
        total = min(self.w - self.margin * 2, n * (w + 6))
        step = total / n
        x0 = (self.w - total) / 2
        y = 64
        return [pygame.Rect(int(x0 + i * step), int(y), w, h) for i in range(n)]

    def _board_rect(self, suit: str, rank: int) -> pygame.Rect:
        cx = self.w // 2
        x = int(cx - SPR_W // 2 + (rank - 7) * self.spacing)
        y = int(self.row_y[suit] - SPR_H // 2)
        return pygame.Rect(x, y, SPR_W, SPR_H)

    def _draw_row_label(self, suit: str):
        """Use a tiny 7-card from the spritesheet instead of Unicode suits (prevents missing-glyph boxes)."""
        mini = pygame.Rect(
            self.margin + 6,
            int(self.row_y[suit] - (SPR_H * 0.18)),
            int(SPR_W * 0.22),
            int(SPR_H * 0.22),
        )
        self._blit_card(mini, suit, 7)

    # ----- round lifecycle -----
    def _round_seed(self) -> int:
        base = self.duel_id or self.base_seed
        return (hash(base) & 0xFFFF_FFFF) + self.round_idx * 7919

    def _start_round(self):
        self.round_idx += 1
        seed = self._round_seed()
        self.ai_player = AI(random.Random(seed))
        self.ai_timer = 0

        # one deck per round (fix jitter/re-deal)
        deck = Deck(seed)
        p, a = deck.deal_half()
        self.board = Board()
        # perspective: p_hand is local player's hand, a_hand is opponent's
        if self.local_idx == 0:
            self.p_hand = Hand(p)
            self.a_hand = Hand(a)
        else:
            self.p_hand = Hand(a)
            self.a_hand = Hand(p)

        self.passes = 0
        self.last_msg = f"Round {self.round_idx}"
        self.last_play = None
        self.last_hilite_ms = 0
        self.match_outcome = None

        # forced opening & who goes next
        self.turn_idx = self._forced_open(random.Random(seed))
        self.human_turn = self.turn_idx == self.local_idx

        # IMPORTANT: actually enter play state (fix banner loop)
        self.state = "PLAY"
        self.timer_ms = 0
        self.toast_ms = 0
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    def _forced_open(self, rng: random.Random) -> int:
        p7 = [c for c in self.p_hand.cards if c.rank == 7]
        a7 = [c for c in self.a_hand.cards if c.rank == 7]
        if any(c.suit == "♦" for c in p7) or any(c.suit == "♦" for c in a7):
            if any(c.suit == "♦" for c in p7):
                c = next(c for c in self.p_hand.cards if c.suit == "♦" and c.rank == 7)
                self.board.play(c)
                self.p_hand.remove(c)
                self.last_msg = f"You opened {c.text}"
                self.last_play = c
                self.last_hilite_ms = LAST_HILITE_MS
                return 1
            else:
                c = next(c for c in self.a_hand.cards if c.suit == "♦" and c.rank == 7)
                self.board.play(c)
                self.a_hand.remove(c)
                self.last_msg = f"Opponent opened {c.text}"
                self.last_play = c
                self.last_hilite_ms = LAST_HILITE_MS
                return 0
        for s in ["♦", "♣", "♥", "♠"]:
            m = [c for c in p7 if c.suit == s]
            if m:
                c = m[0]
                self.board.play(c)
                self.p_hand.remove(c)
                self.last_msg = f"You opened {c.text}"
                self.last_play = c
                self.last_hilite_ms = LAST_HILITE_MS
                return 1
            m = [c for c in a7 if c.suit == s]
            if m:
                c = m[0]
                self.board.play(c)
                self.a_hand.remove(c)
                self.last_msg = f"Opponent opened {c.text}"
                self.last_play = c
                self.last_hilite_ms = LAST_HILITE_MS
                return 0
        s = rng.choice(SUITS)
        self.board.play(Card(s, 7))
        self.last_msg = f"Neutral start: 7{s}"
        self.last_play = Card(s, 7)
        self.last_hilite_ms = LAST_HILITE_MS
        return rng.choice([0, 1])

    # -------------------------- Scene API --------------------------
    def handle_event(self, e):
        if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            try:
                from pause_menu import PauseMenuScene
            except Exception as exc:
                print(f"[SevensBlitz] Pause menu unavailable: {exc}")
            else:
                ctx = self.context
                if ctx is None:
                    from game_context import GameContext

                    ctx = GameContext()
                self.manager.push(PauseMenuScene(self.manager, ctx, self))
            return

        if self.state == "MATCH_BANNER":
            if e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                self._finalize(self.match_outcome or ("win" if self.player_wins > self.ai_wins else "lose"))
            return

        if self.state != "PLAY":
            return

        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.turn_idx == self.local_idx:
            mx, my = e.pos
            playable = self.board.legal_from(self.p_hand.cards)
            # click a card
            for card, rect in zip(self.p_hand.cards[:], self._hand_rects_player()):
                if rect.collidepoint(mx, my) and card in playable:
                    if self.net_enabled and not self.is_authority:
                        self._net_send_action({"kind": "play", "card": (card.suit, card.rank)})
                    else:
                        self._apply_play(self.local_idx, card)
                    return
            # pass (only if no legal)
            if not playable and self.pass_rect.collidepoint(mx, my):
                if self.net_enabled and not self.is_authority:
                    self._net_send_action({"kind": "pass"})
                else:
                    self._apply_pass(self.local_idx)

        if e.type == pygame.KEYDOWN and self.turn_idx == self.local_idx:
            if e.key == pygame.K_SPACE and not self.board.legal_from(self.p_hand.cards):
                if self.net_enabled and not self.is_authority:
                    self._net_send_action({"kind": "pass"})
                else:
                    self._apply_pass(self.local_idx)

    def update(self, dt):
        self._net_poll_actions(float(dt))
        ms = int(dt * 1000)

        # toasts
        if self.toast_ms > 0:
            self.toast_ms = max(0, self.toast_ms - ms)

        # last play highlight
        if self.last_hilite_ms > 0:
            self.last_hilite_ms = max(0, self.last_hilite_ms - ms)

        if self.state == "PLAY":
            if not self.net_enabled and self.turn_idx != self.local_idx:
                self.ai_timer += ms
                if self.ai_timer >= AI_THINK_MS:
                    self.ai_timer = 0
                    ai_side = 1 - self.local_idx
                    ai_hand = self.a_hand if ai_side == 1 else self.p_hand
                    move = self.ai_player.choose(self.board, ai_hand)
                    if move:
                        self._apply_play(ai_side, move)
                    else:
                        self._apply_pass(ai_side)
            # blocked?
            if self.state == "PLAY" and self.passes >= 2:
                p_pips = self.p_hand.pip_total()
                a_pips = self.a_hand.pip_total()
                if p_pips < a_pips:
                    self._end_round(
                        player_won=True,
                        reason=f"Blocked. Pip totals — You {p_pips} vs Opp {a_pips}",
                    )
                elif a_pips < p_pips:
                    self._end_round(
                        player_won=False,
                        reason=f"Blocked. Pip totals — You {p_pips} vs Opp {a_pips}",
                    )
                else:
                    # tie → replay
                    self.state = "ROUND_BANNER"
                    self.banner_title = "Tie — replay"
                    self.banner_sub = f"Pips {p_pips}–{a_pips}"
                    self.timer_ms = ROUND_BANNER_MS

        elif self.state == "ROUND_BANNER":
            self.timer_ms -= ms
            if self.timer_ms <= 0:
                # if match over → MATCH_BANNER, else start next round
                if self.player_wins >= self.need or self.ai_wins >= self.need:
                    self._enter_match_banner()
                else:
                    if self.is_authority:
                        self._start_round()  # sets state="PLAY"
                        self._net_send_state(force=True)

        elif self.state == "MATCH_BANNER":
            self.timer_ms -= ms
            if self.timer_ms <= 0:
                self._finalize(self.match_outcome or ("win" if self.player_wins > self.ai_wins else "lose"))

        if self.state == "MATCH_BANNER" and self.match_outcome and self.net_enabled and self.is_authority:
            winner_id = self.local_id if self.match_outcome == "win" else self.remote_id
            loser_id = self.remote_id if winner_id == self.local_id else self.local_id
            if not self.pending_payload:
                self.pending_payload = {"winner": winner_id, "loser": loser_id}
            self._net_send_action({"kind": "finish", "winner": winner_id, "loser": loser_id, "outcome": self.match_outcome})

        # keep remote in sync during play
        if self.net_enabled and self.is_authority and self.state == "PLAY":
            self._net_send_state()

    def draw(self):
        # background
        self.screen.blit(
            pygame.transform.smoothscale(self.bg, (self.w, self.h)), (0, 0)
        )

        # opponent back cards (top)
        for r in self._hand_rects_opponent():
            self._blit_card(r, back=DEFAULT_BACK)

        # row labels (tiny 7-cards, avoids missing-font boxes)
        for s in SUITS:
            self._draw_row_label(s)

        # placed cards
        for s in SUITS:
            for r in sorted(self.board.played[s]):
                rect = self._board_rect(s, r)
                hi = (
                    self.last_hilite_ms > 0
                    and self.last_play
                    and self.last_play.suit == s
                    and self.last_play.rank == r
                )
                self._blit_card(rect, s, r, highlight=hi)

        # HUD (turn + counts + match pips)
        hud_y = 16
        turn_txt = (
            "Your turn"
            if (self.state == "PLAY" and self.human_turn)
            else "Opponent turn"
        )
        t1 = self.font.render(turn_txt, True, (245, 245, 255))
        self.screen.blit(t1, (self.margin, hud_y))
        t2 = self.font.render(f"Opponent cards: {len(self.a_hand)}", True, (245, 245, 255))
        self.screen.blit(t2, (self.margin, hud_y + 22))

        # match dots
        x0 = self.w // 2 - 80
        for i in range(3):
            pygame.draw.circle(
                self.screen,
                (255, 230, 120) if i < self.player_wins else (110, 120, 120),
                (x0 + i * 28, 20),
                8,
            )
        x1 = self.w // 2 + 80
        for i in range(3):
            pygame.draw.circle(
                self.screen,
                (255, 230, 120) if i < self.ai_wins else (110, 120, 120),
                (x1 + i * 28, 20),
                8,
            )

        if self.last_msg:
            msg = self.font.render(self.last_msg, True, (245, 245, 255))
            self.screen.blit(msg, (self.w // 2 - msg.get_width() // 2, self.h - 26))

        # player hand (bottom)
        playable = (
            set(self.board.legal_from(self.p_hand.cards))
            if (self.state == "PLAY" and self.human_turn)
            else set()
        )
        for card, rect in zip(self.p_hand.cards, self._hand_rects_player()):
            self._blit_card(rect, card.suit, card.rank, highlight=(card in playable))

        # pass button when stuck
        if self.state == "PLAY" and self.human_turn and not playable:
            pygame.draw.rect(
                self.screen, (220, 70, 70), self.pass_rect, border_radius=8
            )
            pygame.draw.rect(
                self.screen, (255, 240, 240), self.pass_rect, 2, border_radius=8
            )
            t = self.font.render("PASS", True, (255, 245, 245))
            self.screen.blit(t, t.get_rect(center=self.pass_rect.center))

        # toast (quick pass/skip popup)
        if self.toast_ms > 0 and self.toast_text:
            box = pygame.Rect(0, 0, 380, 48)
            box.midtop = (self.w // 2, 60)
            srf = pygame.Surface(box.size, pygame.SRCALPHA)
            srf.fill((0, 0, 0, 160))
            self.screen.blit(srf, box.topleft)
            pygame.draw.rect(self.screen, (230, 230, 250), box, 2, border_radius=10)
            t = self.font.render(self.toast_text, True, (255, 255, 255))
            self.screen.blit(t, t.get_rect(center=box.center))

        # round/match banners
        if self.state in ("ROUND_BANNER", "MATCH_BANNER"):
            dim = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            dim.fill((0, 0, 0, 160))
            self.screen.blit(dim, (0, 0))
            if self.state == "ROUND_BANNER":
                T = self.font_big.render(self.banner_title, True, (255, 235, 140))
                S = self.font.render(self.banner_sub, True, (235, 240, 245))
            else:
                won = self.player_wins > self.ai_wins
                T = self.font_big.render(
                    "Match Win!" if won else "Match Lost", True, (255, 235, 140)
                )
                S = self.font.render(
                    f"{self.player_wins}–{self.ai_wins}", True, (235, 240, 245)
                )
            self.screen.blit(T, T.get_rect(center=(self.w // 2, self.h // 2 - 18)))
            self.screen.blit(S, S.get_rect(center=(self.w // 2, self.h // 2 + 18)))
        # Debug turn
        if self.net_enabled:
            t = self.font.render(
                f"Turn: {'You' if self.turn_idx==self.local_idx else 'Opp'}", True, (230,230,230)
            )
            self.screen.blit(t, (self.margin, self.h - 30))

    # ------------------------- end helpers -------------------------
    def _toast(self, text: str):
        self.toast_text = text
        self.toast_ms = TOAST_MS

    def _apply_play(self, side_idx: int, card: Card):
        self.board.play(card)
        (self.p_hand if side_idx == 0 else self.a_hand).remove(card)
        who = "You" if side_idx == self.local_idx else "Opponent"
        self.last_msg = f"{who} played {card.text}"
        self.last_play = card
        self.last_hilite_ms = LAST_HILITE_MS
        self.passes = 0
        if len(self.p_hand if side_idx == 0 else self.a_hand) == 0:
            self._end_round(side_idx == 0, f"{who} emptied their hand")
        else:
            self.turn_idx = 1 - side_idx
            self.human_turn = self.turn_idx == self.local_idx
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    def _apply_pass(self, side_idx: int):
        who = "You" if side_idx == self.local_idx else "Opponent"
        self._toast(f"{who} skipped")
        self.last_msg = f"{who} passed"
        self.passes += 1
        self.turn_idx = 1 - side_idx
        self.human_turn = self.turn_idx == self.local_idx
        if self.net_enabled and self.is_authority:
            self._net_send_state(force=True)

    def _end_round(self, player_won: bool, reason: str):
        if player_won:
            self.player_wins += 1
            self.banner_title = "Round won!"
        else:
            self.ai_wins += 1
            self.banner_title = "Round lost"
        self.banner_sub = reason
        self.state = "ROUND_BANNER"
        self.timer_ms = ROUND_BANNER_MS
        if self.player_wins >= self.need or self.ai_wins >= self.need:
            self.match_outcome = "win" if self.player_wins > self.ai_wins else "lose"

    def _enter_match_banner(self):
        if not self.match_outcome:
            self.match_outcome = "win" if self.player_wins > self.ai_wins else "lose"
        self.state = "MATCH_BANNER"
        self.timer_ms = MATCH_BANNER_MS

    # ----------------------- networking helpers ----------------------
    def _pack_state(self):
        return {
            "round": self.round_idx,
            "turn": self.turn_idx,
            "board": {s: list(r) for s, r in self.board.played.items()},
            "hand_p": [(c.suit, c.rank) for c in self.p_hand.cards],
            "hand_a": [(c.suit, c.rank) for c in self.a_hand.cards],
            "passes": self.passes,
            "player_wins": self.player_wins,
            "ai_wins": self.ai_wins,
            "last": (self.last_play.suit, self.last_play.rank) if self.last_play else None,
            "state": self.state,
            "banner": [getattr(self, "banner_title", ""), getattr(self, "banner_sub", ""), self.timer_ms],
        }

    def _apply_state(self, st: dict):
        if not st:
            return
        def make_cards(arr): return [Card(s, int(r)) for s, r in arr] if arr else []
        # perspective: host sends p as bottom (host). If local_idx==1 swap.
        if self.local_idx == 0:
            self.p_hand = Hand(make_cards(st.get("hand_p")))
            self.a_hand = Hand(make_cards(st.get("hand_a")))
        else:
            self.p_hand = Hand(make_cards(st.get("hand_a")))
            self.a_hand = Hand(make_cards(st.get("hand_p")))
        self.turn_idx = int(st.get("turn", self.turn_idx))
        self.board.played = {s: set(st.get("board", {}).get(s, [])) for s in SUITS}
        self.passes = int(st.get("passes", self.passes))
        if self.local_idx == 0:
            self.player_wins = int(st.get("player_wins", self.player_wins))
            self.ai_wins = int(st.get("ai_wins", self.ai_wins))
        else:
            self.player_wins = int(st.get("ai_wins", self.player_wins))
            self.ai_wins = int(st.get("player_wins", self.ai_wins))
        last = st.get("last")
        self.last_play = Card(last[0], int(last[1])) if last else None
        self.state = st.get("state", self.state)
        if self.state == "ROUND_BANNER":
            bt, bs, tm = st.get("banner", ["", "", ROUND_BANNER_MS])
            self.banner_title, self.banner_sub = bt, bs
            self.timer_ms = int(tm)
        self.human_turn = self.turn_idx == self.local_idx

    def _net_send_action(self, payload: dict):
        if not self.net_enabled or not self.net_client or not payload:
            return
        try:
            self.net_client.send_duel_action({"duel_id": self.duel_id, "action": payload})
        except Exception as exc:
            print(f"[SevensBlitz] send failed: {exc}")

    def _net_send_state(self, kind="state", force=False, **extra):
        if not self.net_enabled:
            return
        now = time.perf_counter()
        if not force and (now - self.net_timer) < self.net_interval:
            return
        self.net_timer = now
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
            card = action.get("card")
            if card and len(card) == 2:
                self._apply_play(self.remote_idx, Card(card[0], int(card[1])))
            return
        if kind == "pass" and self.is_authority:
            self._apply_pass(self.remote_idx)
            return
        if kind == "finish":
            win_id = action.get("winner")
            lose_id = action.get("loser")
            outcome = action.get("outcome")
            mapped = outcome
            if win_id == self.local_id:
                mapped = "win"
            elif lose_id == self.local_id:
                mapped = "lose"
            self.pending_payload = {"winner": win_id, "loser": lose_id}
            self.match_outcome = mapped
            self._enter_match_banner()
            return

    def _finalize(self, outcome: str):
        if self._completed:
            return
        self._completed = True
        if self.context is not None:
            payload = {"minigame": MINIGAME_ID, "outcome": outcome}
            payload.update(self.pending_payload)
            self.context.last_result = payload
        if hasattr(self.manager, "pop"):
            self.manager.pop()
        if callable(self.callback):
            try:
                self.callback(self.context)
            except Exception:
                pass

    def forfeit_from_pause(self):
        if self._completed:
            return
        self.match_outcome = "lose"
        self._finalize("forfeit")


# --------------------------- module entry ------------------------------------
def launch(manager, context, callback, **kwargs):
    print("[SevensBlitz] Launching card duel.")
    return SevensBlitzScene(manager, context, callback, **kwargs)
