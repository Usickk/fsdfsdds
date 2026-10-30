#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🏴‍☠️ ONE PIECE RPG — текстовая RPG для Telegram (python-telegram-bot v20+)
"""
import asyncio
import html
import hashlib
import json
import logging
import math
import os
import random
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, Defaults, TypeHandler, filters
)

# ═══════════════════════════ НАСТРОЙКИ ══════════════════════════
TOKEN = os.environ.get("BOT_TOKEN")

# ИСПРАВЛЕНИЕ ОТСТУПОВ ЗДЕСЬ
try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

DB_PATH = os.environ.get("DB_PATH", "one_piece_game.db")
NOTIFY_ON_START = os.environ.get("NOTIFY_ON_START", "0") == "1"
BOSS_CHAR_DROP = 0.12
GACHA_COOLDOWN = 2 * 60 * 60
ADVENTURE_COOLDOWN = 30
BOSS_COOLDOWN = 30 * 60

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("onepiece")
CONN: sqlite3.Connection = None
BATTLES: dict = {}

# ═══════════════════════════ УТИЛИТЫ ═══════════════════════════
def n(v) -> str:
    return f"{int(v):,}".replace(",", " ")

def bel(v) -> str:
    return f"{n(v)} ฿"

def esc(s) -> str:
    return html.escape(str(s))

def fmt_duration(sec) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h} ч {m:02d} мин {s:02d} сек" if h else f"{m} мин {s:02d} сек"

def fmt_ts(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts else "—"

def fmt_age(ts) -> str:
    if not ts:
        return "—"
    sec = int(time.time() - ts)
    if sec < 60:
        return "только что"
    if sec < 3600:
        return f"{sec // 60} мин"
    if sec < 86400:
        return f"{sec // 3600} ч {(sec % 3600) // 60} мин"
    days = sec // 86400
    if days < 30:
        return f"{days} дн."
    if days < 365:
        return f"{days // 30} мес."
    return f"{days // 365} г. {days % 365 // 30} мес."

def bar(cur, mx, length=10) -> str:
    cur = max(0, cur)
    filled = 0 if mx <= 0 else int(round(length * cur / mx))
    if cur > 0 and filled == 0:
        filled = 1
    return "█" * filled + "░" * (length - filled)

def btn(text, data) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)

MENU_ROW = [btn(" Меню", "menu")]

# ═══════════════════════════ ДАННЫЕ ИГРЫ ═══════════════════════════
ROLES = {
    "pirate": {"title": "‍☠️ Пират", "desc": "Урон и криты: 30% шанс критического удара (×2).",
               "hp": 100, "atk": 14, "def": 5, "haki": 10, "crit": 0.30, "dodge": 0.05, "gold": 1.0, "taken": 1.0, "growth": (14, 3, 1, 1)},
    "marine": {"title": "⚓ Дозорный", "desc": "Здоровье и броня: больше HP/DEF и на 10% меньше получаемого урона.",
               "hp": 140, "atk": 10, "def": 10, "haki": 8, "crit": 0.08, "dodge": 0.05, "gold": 1.0, "taken": 0.90, "growth": (16, 2, 1, 1)},
    "hunter": {"title": "🎯 Охотник за головами", "desc": "Золото и уклонение: +25% Белли со всех наград и 20% шанс уклониться.",
               "hp": 110, "atk": 11, "def": 6, "haki": 9, "crit": 0.10, "dodge": 0.20, "gold": 1.25, "taken": 1.0, "growth": (14, 2, 1, 2)},
}
RARITY = {"common": "⚪ Обычный", "rare": "🔵 Редкий", "epic": "🟣 Эпический", "legendary": "🟡 Легендарный"}
RARITY_ORDER = ["common", "rare", "epic", "legendary"]
DUPE_VALUE = {"common": 100, "rare": 300, "epic": 800, "legendary": 2500}
ITEMS = {
    "meat": {"name": "🍖 Мясо на косточке", "kind": "food", "rarity": "common", "heal": 0.25},
    "onigiri": {"name": " Онигири", "kind": "food", "rarity": "common", "heal": 0.15},
    "sanji_meal": {"name": "🍱 Обед от Санджи", "kind": "food", "rarity": "rare", "heal": 0.50},
    "feast": {"name": "🍗 Пир Гранд Лайн", "kind": "food", "rarity": "epic", "heal": 1.00},
    "cutlass": {"name": "🗡️ Абордажная сабля", "kind": "weapon", "rarity": "common", "atk": 3},
    "knife": {"name": "🔪 Охотничий нож", "kind": "weapon", "rarity": "common", "atk": 4},
    "slingshot": {"name": "🏹 Рогатка Капута", "kind": "weapon", "rarity": "rare", "atk": 6, "haki": 1},
    "marine_shield": {"name": "🛡️ Щит Дозорного", "kind": "weapon", "rarity": "rare", "atk": 2, "def": 6},
    "sandai": {"name": "⚔️ Сандай Китетсу", "kind": "weapon", "rarity": "epic", "atk": 12, "def": 1},
    "wado": {"name": "🌸 Вадо Итимондзи", "kind": "weapon", "rarity": "epic", "atk": 14, "haki": 2},
    "shusui": {"name": "🗡️ Сюсуй", "kind": "weapon", "rarity": "legendary", "atk": 20, "haki": 3},
    "yoru": {"name": "🖤 Ёру — Чёрный Клинок", "kind": "weapon", "rarity": "legendary", "atk": 24, "haki": 4},
    "enma": {"name": "🔥 Энма", "kind": "weapon", "rarity": "legendary", "atk": 28, "def": 2, "haki": 6},
    "mera": {"name": " Мера-Мера но Ми", "short": "Мера-Мера", "kind": "fruit", "rarity": "epic", "mult": 1.8, "effect": "burn", "info": "поджигает врага (урон 2 хода)"},
    "hie": {"name": "🧊 Хие-Хие но Ми", "short": "Хие-Хие", "kind": "fruit", "rarity": "epic", "mult": 1.6, "effect": "freeze", "info": "замораживает врага — он пропускает ход"},
    "suna": {"name": "️ Суна-Суна но Ми", "short": "Суна-Суна", "kind": "fruit", "rarity": "epic", "mult": 1.7, "effect": "drain", "info": "вы лечитесь на 35% от нанесённого урона"},
    "neko": {"name": "🐆 Неко-Неко но Ми: Леопард", "short": "Леопард", "kind": "fruit", "rarity": "epic", "mult": 1.7, "pierce": 0.5, "info": "игнорирует 50% брони врага"},
    "goro": {"name": " Горо-Горо но Ми", "short": "Горо-Горо", "kind": "fruit", "rarity": "legendary", "mult": 2.0, "effect": "shock", "info": "мощный удар + паралич врага на ход"},
    "gomu": {"name": "🎈 Гому-Гому но Ми", "short": "Гому-Гому", "kind": "fruit", "rarity": "legendary", "mult": 2.3, "info": "огромный чистый урон"},
    "yami": {"name": "🌌 Ями-Ями но Ми", "short": "Ями-Ями", "kind": "fruit", "rarity": "legendary", "mult": 2.0, "pierce": 1.0, "info": "полностью игнорирует броню врага"},
    "pearl": {"name": "🦪 Жемчужина", "kind": "treasure", "rarity": "common", "sell": 250},
    "gold_bar": {"name": " Золотой слиток", "kind": "treasure", "rarity": "rare", "sell": 600},
    "t_buggy": {"name": "⭐ Звёздный алмаз Багги", "kind": "treasure", "rarity": "rare", "sell": 1500},
    "t_croc": {"name": "🏺 Золото Бароку Воркс", "kind": "treasure", "rarity": "epic", "sell": 4000},
    "t_enel": {"name": "⚡ Золотой шар Скайпии", "kind": "treasure", "rarity": "epic", "sell": 8000},
    "t_lucci": {"name": " Чёрная жемчужина Эниес-Лобби", "kind": "treasure", "rarity": "epic", "sell": 15000},
    "t_flamingo": {"name": "🦩 Розовое сокровище Дресс-Розы", "kind": "treasure", "rarity": "legendary", "sell": 30000},
    "t_kaido": {"name": "🐉 Сокровище Онигасимы", "kind": "treasure", "rarity": "legendary", "sell": 80000},
    "t_shanks": {"name": "🍷 Пробка от вина Шанкса", "kind": "treasure", "rarity": "legendary", "sell": 150000},
    "t_blackbeard": {"name": "💀 Череп Тича", "kind": "treasure", "rarity": "legendary", "sell": 200000},
}
FOOD_WEIGHTS = {"meat": 50, "onigiri": 35, "sanji_meal": 12, "feast": 3}

def keys_of(kind, rarity=None):
    return [k for k, v in ITEMS.items() if v["kind"] == kind and (rarity is None or v["rarity"] == rarity)]

def inventory_value(inv: dict) -> int:
    total = 0
    for k, qty in inv.items():
        it = ITEMS.get(k)
        if not it: continue
        if it["kind"] == "treasure":
            total += it["sell"] * qty
        else:
            total += {"common": 100, "rare": 500, "epic": 2000, "legendary": 10000}.get(it["rarity"], 100) * qty
    return total

def rarest_item(inv: dict):
    if not inv: return None
    best_key, best_rank = None, -1
    for k in inv:
        it = ITEMS.get(k)
        if not it: continue
        rank = RARITY_ORDER.index(it["rarity"])
        if rank > best_rank:
            best_rank, best_key = rank, k
    return best_key

# ═══════════════════════════ БОССЫ (МАСШТАБИРУЕМЫЕ) ═══════════════════════════
BOSSES = {
    # Вместо hp/atk/def используем множители относительно игрока
    "buggy": dict(name="Багги Клоун", emoji="🤡", level=1, 
                  hp_mult=2.5, atk_mult=1.3, def_mult=1.2, 
                  special=("Бака-Бака: Град ножей", 1.6), 
                  belly=800, exp=120, bounty=300_000, treasure="t_buggy", 
                  bonus=[("slingshot", 0.25)], lore="Капитан Пиратов Багги."),
    
    "croc": dict(name="Сэр Крокодайл", emoji="🐊", level=3, 
                 hp_mult=3.0, atk_mult=1.5, def_mult=1.4, 
                 special=("Суна-Суна: Пустынный Спада", 1.7), 
                 belly=2500, exp=300, bounty=1_500_000, treasure="t_croc", 
                 bonus=[("suna", 0.25)], lore="Бывший Шичибукай."),
    
    "enel": dict(name="Бог Энель", emoji="", level=5, 
                 hp_mult=3.5, atk_mult=1.7, def_mult=1.6, 
                 special=("Эль-Тор: Небесный Гром", 1.8), 
                 belly=5000, exp=600, bounty=2_500_000, treasure="t_enel", 
                 bonus=[("goro", 0.15)], lore="Бог Скайпии."),
    
    "lucci": dict(name="Роб Луччи", emoji="🐆", level=8, 
                  hp_mult=4.0, atk_mult=1.9, def_mult=1.8, 
                  special=("Рокуоган: Удар шести королей", 1.8), 
                  belly=9000, exp=1200, bounty=4_000_000, treasure="t_lucci", 
                  bonus=[("neko", 0.25)], lore="Лучший агент CP9."),
    
    "doflamingo": dict(name="Донкихот Дофламинго", emoji="🦩", level=12, 
                       hp_mult=4.5, atk_mult=2.1, def_mult=2.0, 
                       special=("Бизоу Нитей: Птичья Клетка", 1.9), 
                       belly=18000, exp=2500, bounty=8_000_000, treasure="t_flamingo", 
                       bonus=[("wado", 0.30), ("shusui", 0.10)], lore="Король Дресс-Розы."),
    
    "kaido": dict(name="Кайдо — Король Зверей", emoji="🐉", level=16, 
                  hp_mult=5.5, atk_mult=2.4, def_mult=2.3, 
                  special=("Хорюмэй: Громовой Багуа", 2.0), 
                  belly=40000, exp=6000, bounty=20_000_000, treasure="t_kaido", 
                  bonus=[("enma", 0.25), ("yoru", 0.15), ("gomu", 0.10)], lore="Император Йонко."),
    
    "akainu": dict(name="Акаину (Сакадзуки)", emoji="🌋", level=20, 
                   hp_mult=6.0, atk_mult=2.6, def_mult=2.5, 
                   special=("Мега-Магма: Метеоритный Кулак", 2.1), 
                   belly=50000, exp=8000, bounty=30_000_000, treasure="t_kaido", 
                   bonus=[("enma", 0.30)], lore="Флот-адмирал."),
    
    "aokiji": dict(name="Аокидзи (Кузан)", emoji="❄️", level=20, 
                   hp_mult=5.8, atk_mult=2.5, def_mult=2.7, 
                   special=("Ледяной Век: Ледяное Время", 2.0), 
                   belly=48000, exp=7500, bounty=28_000_000, treasure="t_kaido", 
                   bonus=[("hie", 0.30)], lore="Бывший адмирал льда."),
    
    "kizaru": dict(name="Кидзару (Борсалино)", emoji="✨", level=20, 
                   hp_mult=5.5, atk_mult=2.8, def_mult=2.2, 
                   special=("Световой Скоростной Пинок", 2.2), 
                   belly=49000, exp=7800, bounty=29_000_000, treasure="t_kaido", 
                   bonus=[("goro", 0.30)], lore="Адмирал света."),
    
    "shanks": dict(name="Шанкс — Рыжеволосый", emoji="", level=25, 
                   hp_mult=7.0, atk_mult=3.2, def_mult=2.8, 
                   special=("Хаки Королей: Разрез Реальности", 2.5), 
                   belly=100000, exp=15000, bounty=45_000_000, treasure="t_shanks", 
                   bonus=[("yoru", 0.40), ("gomu", 0.20)], lore="Император."),
    
    "blackbeard": dict(name="Маршалл Д. Тич", emoji="💀", level=25, 
                       hp_mult=7.2, atk_mult=3.0, def_mult=3.0, 
                       special=("Кураи-Кураи: Черная Дыра", 2.3), 
                       belly=110000, exp=16000, bounty=48_000_000, treasure="t_blackbeard", 
                       bonus=[("yami", 0.40), ("goro", 0.20)], lore="Новый Император."),
    
    "big_mom": dict(name="Шарлотта Линлин", emoji="🍰", level=24, 
                    hp_mult=6.8, atk_mult=2.9, def_mult=3.2, 
                    special=("Сорь Сорь но Ми: Душа Наполеона", 2.2), 
                    belly=95000, exp=14000, bounty=42_000_000, treasure="t_flamingo", 
                    bonus=[("feast", 0.50)], lore="Императрица Тотто Ленда."),
    
    "mihawk": dict(name="Дракуль Михок", emoji="", level=22, 
                   hp_mult=6.0, atk_mult=3.5, def_mult=2.0, 
                   special=("Кокуйо Оогами: Всемирный Разрез", 2.4), 
                   belly=60000, exp=10000, bounty=35_000_000, treasure="t_shanks", 
                   bonus=[("yoru", 0.50)], lore="Сильнейший фехтовальщик."),
    
    "zoro_future": dict(name="Ророноа Зоро (Из будущего?)", emoji="", level=18, 
                        hp_mult=5.0, atk_mult=2.3, def_mult=1.9, 
                        special=("Санторю Оги: Ичидзен Санторю", 2.1), 
                        belly=30000, exp=5000, bounty=15_000_000, treasure="t_enel", 
                        bonus=[("sandai", 0.40), ("shusui", 0.30)], lore="Загадочный мечник."),
    
    "law": dict(name="Трафальгар Ло", emoji="🎩", level=19, 
                hp_mult=5.2, atk_mult=2.2, def_mult=1.7, 
                special=("Операция: Стерильная Комната", 2.0), 
                belly=35000, exp=5500, bounty=18_000_000, treasure="t_lucci", 
                bonus=[("slingshot", 0.30)], lore="Хирург Смерти."),
    
    "kid": dict(name="Юстасс Кид", emoji="", level=19, 
                hp_mult=5.3, atk_mult=2.4, def_mult=2.0, 
                special=("Парк Пушка: Магнитный Удар", 2.1), 
                belly=36000, exp=5600, bounty=19_000_000, treasure="t_croc", 
                bonus=[("marine_shield", 0.40)], lore="Капитан Пиратов Кида."),
}

ENEMIES = [("Пират-новичок", "🏴☠️", 1.0, 1.0, 1.0, 1.0), ("Бандит с побережья", "️", 0.9, 1.1, 0.8, 1.1), ("Рядовой Дозорный", "⚓", 1.1, 0.9, 1.2, 0.9), ("Охотник за головами", "🎯", 1.0, 1.0, 1.0, 1.2), ("Рыболюд-разбойник", "🐟", 1.2, 1.0, 0.9, 1.1)]
MONSTERS = [("Морской Король", "🐉", 1.6, 0.95, 1.1, 2.2), ("Гигантский Кракен", "🐙", 1.5, 1.0, 1.0, 2.2)]
UPGRADES = {
    "hp": ("❤️ Здоровье", "max_hp", 10, lambda v: (int(v * 1.5), max(1, int(v * 0.2)))),
    "atk": ("⚔️ Атака", "atk", 2, lambda v: (v * 30, max(1, v * 2))),
    "def": ("🛡️ Защита", "defense", 2, lambda v: (v * 40, max(1, v * 2))),
    "haki": ("🌀 Воля (Хаки)", "haki", 2, lambda v: (v * 35, max(1, v * 2))),
}

def exp_need(level: int) -> int:
    return 80 + level * 40

# ═══════════════════════════ ПЕРСОНАЖИ (ПРИЗЫВ) ══════════════════════════
SUMMON_COST, SUMMON_COST_10 = 1_500, 13_500
SUMMON_FREE_COOLDOWN = 6 * 60 * 60
SUMMON_RATES = [70, 22, 7, 1]
PITY_RARE, PITY_EPIC, PITY_LEG = 10, 40, 120
SQUAD_SIZE, SQUAD_IN_ADVENTURE, MAX_STARS = 3, False, 5
DUPE_CHAR_VALUE = {"common": 300, "rare": 800, "epic": 2000, "legendary": 6000}
CHARACTERS = {
    "usopp": dict(name="Усопп", emoji="🎯", rarity="common", atk=11, style="bandana", skill=("Огненная Звезда", "burn", 1.3, 2)),
    "nami": dict(name="Нами", emoji="🌊", rarity="common", atk=9, style="long", skill=("Тандер Тэмпо", "stun", 1.2, 3)),
    "chopper": dict(name="Тони Тони Чоппер", emoji="🦌", rarity="common", atk=6, style="hat", skill=("Ранбл: Лечение", "heal", 0.30, 3)),
    "koby": dict(name="Коби", emoji="", rarity="common", atk=10, style="short", skill=("Железный Кулак Дозора", "shield", 1.1, 3)),
    "vivi": dict(name="Нефертари Виви", emoji="🏜️", rarity="common", atk=10, style="long", skill=("Пикок Слэшер", "dmg", 1.5, 2)),
    "carrot": dict(name="Кэрротт", emoji="🐰", rarity="common", atk=12, style="ears", skill=("Сулонг: Лунный Удар", "dmg", 1.6, 3)),
    "robin": dict(name="Нико Робин", emoji="🌸", rarity="rare", atk=18, style="long", skill=("Клутч: Гигантская Рука", "stun", 1.4, 3)),
    "franky": dict(name="Фрэнки", emoji="🤖", rarity="rare", atk=22, style="spiky", skill=("Радикал Бим", "pierce", 1.7, 3)),
    "brook": dict(name="Брук", emoji="", rarity="rare", atk=19, style="afro", skill=("Соул Парад", "drain", 1.5, 3)),
    "smoker": dict(name="Смокер", emoji="💨", rarity="rare", atk=21, style="short", skill=("Белый Захват", "stun", 1.5, 3)),
    "jinbe": dict(name="Дзимбэй", emoji="🌊", rarity="rare", atk=24, style="bald", skill=("Рыболюдское Каратэ", "dmg", 1.9, 3)),
    "marco": dict(name="Марко Феникс", emoji="🦅", rarity="rare", atk=20, style="spiky", skill=("Пламя Феникса", "heal", 0.40, 4)),
    "sanji": dict(name="Санджи", emoji="👨‍🍳", rarity="epic", atk=32, style="long", skill=("Дьябль Жамбе", "burn", 1.9, 3)),
    "zoro": dict(name="Ророноа Зоро", emoji="️", rarity="epic", atk=36, style="bandana", skill=("Санторю: Они Гири", "pierce", 2.0, 3)),
    "ace": dict(name="Портгас Д. Эйс", emoji="🔥", rarity="epic", atk=34, style="hat", skill=("Хикен", "burn", 2.1, 3)),
    "sabo": dict(name="Сабо", emoji="🎩", rarity="epic", atk=33, style="hat", skill=("Рюкэн: Драконий Кулак", "pierce", 1.9, 3)),
    "hancock": dict(name="Боа Хэнкок", emoji="💘", rarity="epic", atk=30, style="long", skill=("Мэро Мэро: Окаменение", "stun", 2.0, 4)),
    "luffy": dict(name="Монки Д. Луффи", emoji="", rarity="legendary", atk=52, style="hat", skill=("Гомо Гомо: Гатлинг", "dmg", 2.5, 4)),
    "whitebeard": dict(name="Эдвард Ньюгейт", emoji="🌋", rarity="legendary", atk=55, style="stache", skill=("Гура Гура: Землетрясение", "pierce", 2.4, 4)),
    "roger": dict(name="Гол Д. Роджер", emoji="🏴☠️", rarity="legendary", atk=50, style="stache", skill=("Хаки Королей: Разрыв Небес", "stun", 2.3, 4)),
}
CHAR_ORDER = {r: i for i, r in enumerate(RARITY_ORDER)}

def char_keys_sorted(keys):
    return sorted(keys, key=lambda k: (-CHAR_ORDER[CHARACTERS[k]["rarity"]], CHARACTERS[k]["name"]))

def stars_str(stars):
    stars = max(1, min(MAX_STARS, int(stars)))
    return "★" * stars + "☆" * (MAX_STARS - stars)

def star_mult(stars):
    return 1.0 + 0.2 * (max(1, stars) - 1)

def skill_info(key, stars=1):
    name, kind, mult, cd = CHARACTERS[key]["skill"]
    if kind == "heal":
        eff = f"лечит {int(min(0.9, mult * star_mult(stars)) * 100)}% HP отряда"
    else:
        eff = {"dmg": f"урон ×{mult}", "pierce": f"урон ×{mult}, игнорирует 60% брони", "burn": f"урон ×{mult} + ожог на 2 хода", "stun": f"урон ×{mult} + враг пропускает ход", "shield": f"урон ×{mult} + входящий урон −60% на ход", "drain": f"урон ×{mult}, лечит 40% от урона"}[kind]
    return f"<b>{name}</b> — {eff} (перезарядка {cd} р.)"

def char_power(key, stars, player_atk):
    return int(CHARACTERS[key]["atk"] * star_mult(stars) + player_atk * 0.5)

# ═════════════════════════ БАЗА ДАННЫХ ═════════════════════════
def init_db():
    global CONN
    CONN = sqlite3.connect(DB_PATH)
    CONN.row_factory = sqlite3.Row
    CONN.executescript("""
CREATE TABLE IF NOT EXISTS players(user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, role TEXT, level INTEGER DEFAULT 1, exp INTEGER DEFAULT 0, hp INTEGER, max_hp INTEGER, atk INTEGER, defense INTEGER, haki INTEGER, belly INTEGER DEFAULT 300, bounty INTEGER DEFAULT 0, weapon TEXT, fruit TEXT, last_gacha REAL DEFAULT 0, last_adventure REAL DEFAULT 0, hp_ts REAL DEFAULT 0, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, boss_kills INTEGER DEFAULT 0, pulls INTEGER DEFAULT 0, created REAL, last_seen REAL, no_cooldown INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS inventory(user_id INTEGER, item TEXT, qty INTEGER DEFAULT 0, PRIMARY KEY(user_id, item));
CREATE TABLE IF NOT EXISTS boss_kills(user_id INTEGER, boss TEXT, kills INTEGER DEFAULT 0, last_kill REAL DEFAULT 0, PRIMARY KEY(user_id, boss));
CREATE TABLE IF NOT EXISTS characters(user_id INTEGER, char TEXT, stars INTEGER DEFAULT 1, got REAL DEFAULT 0, PRIMARY KEY(user_id, char));
CREATE TABLE IF NOT EXISTS summon_state(user_id INTEGER PRIMARY KEY, since_rare INTEGER DEFAULT 0, since_epic INTEGER DEFAULT 0, since_leg INTEGER DEFAULT 0, total INTEGER DEFAULT 0, last_free REAL DEFAULT 0, squad TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS backups(user_id INTEGER PRIMARY KEY, data_json TEXT, backup_time REAL);
""")
    try:
        CONN.execute("ALTER TABLE players ADD COLUMN no_cooldown INTEGER DEFAULT 0")
        CONN.commit()
    except sqlite3.OperationalError:
        pass

def create_player(user, role):
    r = ROLES[role]
    now = time.time()
    CONN.execute("INSERT INTO players(user_id,name,username,role,level,exp,hp,max_hp,atk,defense,haki,belly,bounty,weapon,fruit,last_gacha,last_adventure,hp_ts,created,last_seen) VALUES (?,?,?,?,1,0,?,?,?,?,?,300,0,NULL,NULL,0,0,?,?,?)", (user.id, user.full_name, user.username, role, r["hp"], r["hp"], r["atk"], r["def"], r["haki"], now, now, now))
    CONN.commit()

def get_player(uid):
    row = CONN.execute("SELECT * FROM players WHERE user_id=?", (uid,)).fetchone()
    if not row: return None
    p = dict(row)
    if p["hp"] < p["max_hp"]:
        now = time.time()
        regen = int((now - p["hp_ts"]) / 60 * 0.02 * p["max_hp"])
        if regen > 0:
            p["hp"] = min(p["max_hp"], p["hp"] + regen)
            CONN.execute("UPDATE players SET hp=?, hp_ts=? WHERE user_id=?", (p["hp"], now, uid))
            CONN.commit()
    return p

def update_player(uid, **fields):
    if not fields: return
    if "hp" in fields and "hp_ts" not in fields:
        fields["hp_ts"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    CONN.execute(f"UPDATE players SET {cols} WHERE user_id=?", (*fields.values(), uid))
    CONN.commit()

def add_belly(uid, amount):
    CONN.execute("UPDATE players SET belly = MAX(0, belly + ?) WHERE user_id=?", (int(amount), uid))
    CONN.commit()

def inv_get(uid):
    rows = CONN.execute("SELECT item, qty FROM inventory WHERE user_id=? AND qty>0", (uid,)).fetchall()
    return {r["item"]: r["qty"] for r in rows}

def inv_add(uid, key, qty=1):
    CONN.execute("INSERT INTO inventory(user_id,item,qty) VALUES(?,?,?) ON CONFLICT(user_id,item) DO UPDATE SET qty = qty + excluded.qty", (uid, key, qty))
    CONN.commit()

def inv_remove(uid, key, qty=1):
    CONN.execute("UPDATE inventory SET qty = qty - ? WHERE user_id=? AND item=?", (qty, uid, key))
    CONN.execute("DELETE FROM inventory WHERE qty<=0")
    CONN.commit()

def boss_record(uid, key):
    row = CONN.execute("SELECT kills, last_kill FROM boss_kills WHERE user_id=? AND boss=?", (uid, key)).fetchone()
    return (row["kills"], row["last_kill"]) if row else (0, 0.0)

def boss_mark(uid, key):
    CONN.execute("INSERT INTO boss_kills(user_id,boss,kills,last_kill) VALUES(?,?,1,?) ON CONFLICT(user_id,boss) DO UPDATE SET kills = kills + 1, last_kill = excluded.last_kill", (uid, key, time.time()))
    CONN.commit()

def chars_get(uid):
    rows = CONN.execute("SELECT char, stars FROM characters WHERE user_id=?", (uid,)).fetchall()
    return {r["char"]: r["stars"] for r in rows if r["char"] in CHARACTERS}

def summon_state(uid):
    row = CONN.execute("SELECT * FROM summon_state WHERE user_id=?", (uid,)).fetchone()
    if not row:
        CONN.execute("INSERT INTO summon_state(user_id) VALUES(?)", (uid,))
        CONN.commit()
        row = CONN.execute("SELECT * FROM summon_state WHERE user_id=?", (uid,)).fetchone()
    return dict(row)

def summon_update(uid, **fields):
    summon_state(uid)
    cols = ", ".join(f"{k}=?" for k in fields)
    CONN.execute(f"UPDATE summon_state SET {cols} WHERE user_id=?", (*fields.values(), uid))
    CONN.commit()

def squad_get(uid):
    owned = chars_get(uid)
    raw = summon_state(uid)["squad"] or ""
    out = []
    for k in raw.split(","):
        if k in owned and k not in out:
            out.append(k)
    return out[:SQUAD_SIZE]

def squad_set(uid, keys):
    summon_update(uid, squad=",".join(keys[:SQUAD_SIZE]))

def squad_auto(uid):
    owned = chars_get(uid)
    best = sorted(owned, key=lambda k: -(CHARACTERS[k]["atk"] * star_mult(owned[k])))
    squad_set(uid, best[:SQUAD_SIZE])
    return best[:SQUAD_SIZE]

def char_add(uid, key):
    owned = chars_get(uid)
    rar = CHARACTERS[key]["rarity"]
    res = dict(key=key, rarity=rar, new=False, stars=1, upgraded=False, maxed=False, comp=0)
    if key not in owned:
        CONN.execute("INSERT INTO characters(user_id,char,stars,got) VALUES(?,?,1,?)", (uid, key, time.time()))
        CONN.commit()
        res["new"] = True
        return res
    stars = owned[key]
    if stars < MAX_STARS:
        stars += 1
        CONN.execute("UPDATE characters SET stars=? WHERE user_id=? AND char=?", (stars, uid, key))
        CONN.commit()
        res.update(stars=stars, upgraded=True)
    else:
        res.update(stars=stars, maxed=True, comp=DUPE_CHAR_VALUE[rar])
        add_belly(uid, DUPE_CHAR_VALUE[rar])
    return res

def pick_rarity(st):
    if st["since_leg"] + 1 >= PITY_LEG: return "legendary"
    if st["since_epic"] + 1 >= PITY_EPIC: return random.choices(["epic", "legendary"], weights=[SUMMON_RATES[2], SUMMON_RATES[3]])[0]
    if st["since_rare"] + 1 >= PITY_RARE: return random.choices(["rare", "epic", "legendary"], weights=SUMMON_RATES[1:])[0]
    return random.choices(RARITY_ORDER, weights=SUMMON_RATES)[0]

def summon_pull(uid):
    st = summon_state(uid)
    rar = pick_rarity(st)
    key = random.choice([k for k, c in CHARACTERS.items() if c["rarity"] == rar])
    res = char_add(uid, key)
    summon_update(uid, since_rare=0 if rar != "common" else st["since_rare"] + 1, since_epic=0 if rar in ("epic", "legendary") else st["since_epic"] + 1, since_leg=0 if rar == "legendary" else st["since_leg"] + 1, total=st["total"] + 1)
    if res["new"]:
        sq = squad_get(uid)
        if len(sq) < SQUAD_SIZE:
            squad_set(uid, sq + [key])
        res["to_squad"] = True
    return res

# ═══════════════════════════ МЕДИА ══════════════════════════
MEDIA_DIR = Path(__file__).resolve().parent / "media"
CARD_DIR = MEDIA_DIR / "characters"
RARITY_COLORS = {"common": (170, 185, 205), "rare": (70, 140, 255), "epic": (175, 85, 245), "legendary": (255, 195, 50)}
ANIM_SECONDS = {"common": 3.0, "rare": 3.2, "epic": 3.6, "legendary": 4.2}
ANIM_WAIT = {r: s + 0.6 for r, s in ANIM_SECONDS.items()}
ANIM_SIZE, ANIM_FPS = 480, 20
UPLOAD_KW = dict(read_timeout=90, write_timeout=90, connect_timeout=30, pool_timeout=30)
FILE_IDS, SUMMONING = {}, set()
FONT_NAMES = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf", "NotoSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf", "/Library/Fonts/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]

def _pil():
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        return Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        return None

def _font(size):
    _, _, _, ImageFont = _pil()
    for name in FONT_NAMES:
        try: return ImageFont.truetype(name, size)
        except OSError: continue
    return ImageFont.load_default()

def _mix(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

def _hsv(h, s, v):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, s, v)
    return int(r*255), int(g*255), int(b*255)

def _gradient(w, h, top, bottom):
    Image = _pil()[0]
    base = Image.new("RGB", (1, h))
    base.putdata([_mix(top, bottom, y / max(1, h - 1)) for y in range(h)])
    return base.resize((w, h))

def _star_poly(cx, cy, r, points=5, inner=0.45, rot=-math.pi / 2):
    pts = []
    for i in range(points * 2):
        rad = r if i % 2 == 0 else r * inner
        a = rot + i * math.pi / points
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts

def _draw_figure(d, cx, key, accent, S):
    dark, rim = (16, 18, 30), accent
    st = CHARACTERS[key]["style"]
    top = 700 * S // 2
    d.ellipse((cx - 340*S//2, top + 300*S//2, cx + 340*S//2, top + 900*S//2), fill=dark, outline=rim, width=6)
    d.rectangle((cx - 55*S//2, top + 240*S//2, cx + 55*S//2, top + 380*S//2), fill=dark)
    hx, hy, hr = cx, top + 130*S//2, 150*S//2
    if st == "long":
        d.ellipse((hx - hr - 40, hy - hr - 20, hx + hr + 40, hy + hr + 230), fill=accent)
    if st == "afro":
        d.ellipse((hx - hr - 90, hy - hr - 90, hx + hr + 90, hy + hr + 10), fill=accent)
    if st == "ears":
        for sx in (-1, 1):
            d.ellipse((hx + sx*95 - 35, hy - hr - 190, hx + sx*95 + 35, hy - hr + 30), fill=dark, outline=accent, width=6)
    d.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=dark, outline=rim, width=6)
    if st in ("spiky", "bandana", "short"):
        for i in range(9 if st == "spiky" else 6):
            a = math.pi * (1.05 + 0.9 * i / (8 if st == "spiky" else 5))
            r0, r1 = hr * 0.8, hr * (1.45 if st == "spiky" else 1.2)
            d.polygon([(hx + r0*math.cos(a-0.2), hy + r0*math.sin(a-0.2)), (hx + r1*math.cos(a), hy + r1*math.sin(a)), (hx + r0*math.cos(a+0.2), hy + r0*math.sin(a+0.2))], fill=accent)
    if st == "bandana":
        d.rectangle((hx - hr, hy - hr*0.55, hx + hr, hy - hr*0.25), fill=accent)
    if st == "hat":
        d.ellipse((hx - hr - 130, hy - hr*0.62, hx + hr + 130, hy - hr*0.12), fill=accent)
        d.pieslice((hx - hr*0.82, hy - hr*1.22, hx + hr*0.82, hy - hr*0.1), 180, 360, fill=accent)
        d.rectangle((hx - hr*0.82, hy - hr*0.5, hx + hr*0.82, hy - hr*0.34), fill=(150, 30, 30))
    if st == "stache":
        d.pieslice((hx - hr*0.95, hy + hr*0.05, hx + hr*0.95, hy + hr*0.95), 0, 180, fill=(235, 235, 245))
        for sx in (-1, 1):
            d.ellipse((hx + sx*hr*0.42 - 16, hy - 10, hx + sx*hr*0.42 + 16, hy + 14), fill=_mix(accent, (255, 255, 255), 0.7))

def make_card(key, path):
    Image, ImageDraw, ImageFilter, _ = _pil()
    c = CHARACTERS[key]
    rar = c["rarity"]
    rc = RARITY_COLORS[rar]
    W, H, S = 1200, 1600, 2
    accent = _hsv(sum(map(ord, key)) * 37, 0.55, 0.95)
    img = _gradient(W, H, _mix((8, 10, 24), rc, 0.42), (6, 8, 18)).convert("RGBA")
    rays = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rays)
    n = {"common": 8, "rare": 12, "epic": 16, "legendary": 24}[rar]
    for i in range(n):
        a = 2*math.pi*i/n
        w = math.pi/n*0.55
        rd.polygon([(600, 900), (600+2000*math.cos(a-w), 900+2000*math.sin(a-w)), (600+2000*math.cos(a+w), 900+2000*math.sin(a+w))], fill=rc+(46,))
    rays = rays.filter(ImageFilter.GaussianBlur(6))
    img.alpha_composite(rays)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((150, 380, 1050, 1280), fill=rc+(120,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(120)))
    d = ImageDraw.Draw(img)
    _draw_figure(d, 600, key, accent, S)
    rng = random.Random(key)
    for _ in range({"common": 10, "rare": 18, "epic": 28, "legendary": 44}[rar]):
        x, y, r = rng.randint(60, W-60), rng.randint(120, 1150), rng.randint(8, 22)
        d.polygon(_star_poly(x, y, r, 4, 0.25), fill=_mix(rc, (255, 255, 255), 0.6)+(230,))
    band = Image.new("RGBA", (W, 420), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bd.rectangle((0, 0, W, 420), fill=(4, 6, 14, 215))
    img.alpha_composite(band, (0, H - 420))
    d = ImageDraw.Draw(img)
    size = 130
    f = _font(size)
    while d.textlength(c["name"], font=f) > W - 160 and size > 50:
        size -= 6
        f = _font(size)
    d.text((W/2, H-290), c["name"], font=f, fill=(255, 255, 255), anchor="mm")
    d.text((W/2, H-175), RARITY[rar].split(" ", 1)[1].upper(), font=_font(70), fill=rc, anchor="mm")
    d.text((W/2, H-85), f"ATK {c['atk']}  •  {c['skill'][0]}", font=_font(50), fill=(190, 195, 215), anchor="mm")
    for i in range(CHAR_ORDER[rar] + 1):
        x = W/2 + (i - CHAR_ORDER[rar]/2)*120
        d.polygon(_star_poly(x, 110, 52), fill=rc, outline=(255, 255, 255))
    d.rounded_rectangle((14, 14, W-14, H-14), radius=70, outline=rc, width=24)
    d.rounded_rectangle((52, 52, W-52, H-52), radius=46, outline=_mix(rc, (0, 0, 0), 0.45), width=6)
    if rar == "legendary":
        d.rounded_rectangle((84, 84, W-84, H-84), radius=30, outline=(255, 240, 170), width=5)
    img.convert("RGB").resize((W//2, H//2), Image.LANCZOS).save(path, "JPEG", quality=92)

_BG_CACHE = {}
def _anim_frame(rar, t):
    Image, ImageDraw, ImageFilter, _ = _pil()
    S = ANIM_SIZE
    rc = RARITY_COLORS[rar]
    power = {"common": 0.55, "rare": 0.75, "epic": 0.9, "legendary": 1.0}[rar]
    burst_t = 0.5
    if "bg" not in _BG_CACHE:
        bg = _gradient(S, S, (4, 8, 22), (14, 36, 78)).convert("RGBA")
        rng = random.Random(7)
        bd = ImageDraw.Draw(bg)
        for _ in range(60):
            x, y = rng.randint(0, S), rng.randint(0, int(S*0.65))
            bd.ellipse((x, y, x+2, y+2), fill=(200, 215, 255, rng.randint(80, 200)))
        _BG_CACHE["bg"] = bg
    img = _BG_CACHE["bg"].copy()
    d = ImageDraw.Draw(img)
    for k in range(5):
        y0 = S*0.72 + k*18
        pts = [(x, y0 + math.sin(x/38 + t*14 + k)*5) for x in range(0, S+12, 12)]
        d.polygon(pts + [(S, S), (0, S)], fill=(10+k*4, 40+k*8, 90+k*10, 255))
    cx, cy = S//2, int(S*0.58)
    shake = 0.0
    if t < burst_t:
        shake = math.sin(t*150)*9*(t/burst_t)**2 * power
    elif t < burst_t + 0.12:
        shake = math.sin(t*190)*6*(1-(t-burst_t)/0.12) * power
    cxs = cx + shake
    charge = min(1.0, t/burst_t)
    opened = max(0.0, min(1.0, (t-burst_t)/0.1))
    after = max(0.0, (t-burst_t)/(1-burst_t))
    fx = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fx)
    hy = cy - 10
    if t < burst_t:
        r = 20 + 70*charge**2
        fd.ellipse((cxs-r, hy-r, cxs+r, hy+r), fill=rc+(int(40+150*charge**2),))
        for i in range(36):
            a = i*2.399+1.0
            dist = (1-charge)*(S*0.46)+14
            px, py = cxs+math.cos(a)*dist, hy+math.sin(a)*dist*0.8
            fd.ellipse((px-3, py-3, px+3, py+3), fill=_mix(rc, (255, 255, 255), 0.5)+(int(230*charge+20),))
    else:
        nb = {"common": 7, "rare": 10, "epic": 14, "legendary": 20}[rar]
        grow = min(1.0, after*4)
        for i in range(nb):
            a = -math.pi/2 + (i-(nb-1)/2)*(math.pi*1.05/nb) + math.sin(t*6+i)*0.04
            w = 0.07*(0.6+0.4*math.sin(i*1.7+t*9))
            col = rc if (i%3 or rar!="legendary") else (255, 255, 255)
            fd.polygon([(cxs, hy), (cxs+S*1.2*grow*math.cos(a-w), hy+S*1.2*grow*math.sin(a-w)), (cxs+S*1.2*grow*math.cos(a+w), hy+S*1.2*grow*math.sin(a+w))], fill=col+(int(150*power),))
        r = 90 + 60*math.sin(after*math.pi) + 30*after
        fd.ellipse((cxs-r, hy-r, cxs+r, hy+r), fill=rc+(int(170*power),))
        ring = after*S*0.75
        fd.ellipse((cxs-ring, hy-ring, cxs+ring, hy+ring), outline=rc+(int(255*max(0, 1-after*1.4)),), width=6)
        rng = random.Random(3)
        for i in range(int(28*power)+8):
            a = rng.uniform(-math.pi*0.95, -math.pi*0.05)
            sp = rng.uniform(0.25, 0.9)*S
            px, py = cxs+math.cos(a)*sp*after, hy+math.sin(a)*sp*after+80*after**2
            fd.polygon(_star_poly(px, py, rng.uniform(4, 10), 4, 0.25), fill=_mix(rc, (255, 255, 255), 0.6)+(int(255*(1-after*0.6)),))
    img.alpha_composite(fx.filter(ImageFilter.GaussianBlur(4)))
    d = ImageDraw.Draw(img)
    bw, bh = 170, 82
    L, R, top = cxs-bw/2, cxs+bw/2, cy+6
    d.rounded_rectangle((L, top, R, top+bh), radius=10, fill=(112, 66, 28), outline=(50, 28, 10), width=3)
    for i in range(1, 4):
        d.line((L+i*bw/4, top+4, L+i*bw/4, top+bh-4), fill=(80, 46, 18), width=2)
    d.rectangle((L, top+30, R, top+40), fill=(224, 176, 52))
    gap = 26*opened
    if opened > 0:
        d.rectangle((L+6, top-gap, R-6, top+4), fill=_mix(rc, (255, 255, 255), 0.7))
    lid_top = top - 48 - gap*1.4
    skew = 20*opened
    d.polygon([(L, top-gap), (R, top-gap), (R-8-skew, lid_top), (L+8+skew, lid_top)], fill=(130, 76, 32), outline=(50, 28, 10))
    d.rectangle((L, top-gap-8, R, top-gap+2), fill=(224, 176, 52))
    d.rounded_rectangle((cxs-12, top-4-gap*0.3, cxs+12, top+22), radius=4, fill=(245, 205, 70), outline=(90, 60, 10), width=2)
    fl = 0.0
    if burst_t-0.05 < t < burst_t+0.2:
        fl = max(0.0, 1-abs(t-(burst_t+0.02))/0.12) * power
    if t > 0.92 and rar == "legendary":
        fl = max(fl, (t-0.92)/0.08*0.35)
    if fl > 0:
        img.alpha_composite(Image.new("RGBA", (S, S), (255, 255, 255, int(255*min(1, fl)))))
    return img.convert("RGB")

def _ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe: return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None

def make_anim(rar, out_base):
    secs = ANIM_SECONDS[rar]
    nframes = int(secs * ANIM_FPS)
    exe = _ffmpeg_exe()
    if exe:
        out = out_base.with_suffix(".mp4")
        proc = subprocess.Popen([exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ANIM_SIZE}x{ANIM_SIZE}", "-r", str(ANIM_FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24", "-movflags", "+faststart", "-an", str(out)], stdin=subprocess.PIPE)
        try:
            for i in range(nframes):
                proc.stdin.write(_anim_frame(rar, i/(nframes-1)).tobytes())
            proc.stdin.close()
            if proc.wait() == 0 and out.exists():
                return out
        except (BrokenPipeError, OSError):
            pass
        log.warning("ffmpeg не справился, делаю GIF")
    out = out_base.with_suffix(".gif")
    frames = [_anim_frame(rar, i/(nframes-1)).resize((360, 360)).convert("P", palette=_pil()[0].ADAPTIVE) for i in range(0, nframes)]
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=int(1000/ANIM_FPS), loop=0)
    return out

def anim_path(rar):
    for ext in (".mp4", ".gif", ".webm", ".mov"):
        p = MEDIA_DIR / f"anim_{rar}{ext}"
        if p.exists(): return p
    return None

def card_path(key):
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = CARD_DIR / f"{key}{ext}"
        if p.exists(): return p
    return None

def ensure_media():
    if not _pil():
        log.warning("Pillow не установлен (pip install pillow) — призыв будет без картинок и видео.")
        return
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    for key in CHARACTERS:
        if not card_path(key):
            make_card(key, CARD_DIR / f"{key}.jpg")
    for rar in RARITY_ORDER:
        if not anim_path(rar):
            log.info("Генерирую видео-анимацию призыва: %s", rar)
            try:
                make_anim(rar, MEDIA_DIR / f"anim_{rar}")
            except Exception:
                log.exception("Не удалось создать анимацию %s", rar)

def make_collage(keys):
    if not _pil(): return None
    Image = _pil()[0]
    cw, ch, cols = 240, 320, 5
    rows = (len(keys) + cols - 1) // cols
    sheet = Image.new("RGB", (cols*cw, rows*ch), (8, 10, 20))
    for i, k in enumerate(keys):
        p = card_path(k)
        if not p: continue
        with Image.open(p) as im:
            sheet.paste(im.convert("RGB").resize((cw, ch)), ((i%cols)*cw, (i//cols)*ch))
    bio = BytesIO()
    sheet.save(bio, "JPEG", quality=88)
    bio.name = "summon10.jpg"
    bio.seek(0)
    return bio

# ═══════════════════════════ ИГРОВАЯ ЛОГИКА ═══════════════════════════
def combat_stats(p):
    w = ITEMS.get(p["weapon"]) if p["weapon"] else None
    return {"atk": p["atk"] + (w.get("atk", 0) if w else 0), "defense": p["defense"] + (w.get("def", 0) if w else 0), "haki": p["haki"] + (w.get("haki", 0) if w else 0)}

def grant_exp(uid, amount):
    p = get_player(uid)
    hp_g, atk_g, def_g, haki_g = ROLES[p["role"]]["growth"]
    exp = p["exp"] + int(amount)
    lvl = start = p["level"]
    max_hp, atk, df, haki = p["max_hp"], p["atk"], p["defense"], p["haki"]
    while exp >= exp_need(lvl):
        exp -= exp_need(lvl)
        lvl += 1
        max_hp += hp_g
        atk += atk_g
        df += def_g
        haki += haki_g
    fields = {"exp": exp}
    notes = []
    if lvl > start:
        g = lvl - start
        fields.update(level=lvl, max_hp=max_hp, atk=atk, defense=df, haki=haki, hp=max_hp)
        notes.append(f" <b>НОВЫЙ УРОВЕНЬ: {lvl}!</b> (+{g*hp_g} HP, +{g*atk_g} ATK, +{g*def_g} DEF, +{g*haki_g} Haki) — здоровье восстановлено!")
    update_player(uid, **fields)
    return notes

def give_item(uid, key):
    it = ITEMS[key]
    inv = inv_get(uid)
    if it["kind"] in ("weapon", "fruit") and key in inv:
        comp = DUPE_VALUE[it["rarity"]]
        add_belly(uid, comp)
        return f"{it['name']} — уже есть в коллекции! Обмен на {bel(comp)}"
    inv_add(uid, key)
    extra = ""
    p = get_player(uid)
    if it["kind"] == "weapon" and not p["weapon"]:
        update_player(uid, weapon=key)
        extra = " — экипировано ✅"
    elif it["kind"] == "fruit" and not p["fruit"]:
        update_player(uid, fruit=key)
        extra = " — съеден, сила получена ✅"
    return f"{it['name']} ({RARITY[it['rarity']]}){extra}"

def roll_chest(level, gold, allow_rare=True):
    r = random.random()
    if not allow_rare: r *= 0.82
    if r < 0.35: return "belly", int(random.randint(150, 450) * (1 + 0.15*level) * gold)
    if r < 0.60: return "exp", int(random.randint(40, 120) * (1 + 0.10*level))
    if r < 0.82: return "item", random.choices(list(FOOD_WEIGHTS), weights=list(FOOD_WEIGHTS.values()))[0]
    if r < 0.92: return "item", random.choice(keys_of("weapon", random.choices(["common", "rare", "epic"], weights=[55, 35, 10])[0]))
    if r < 0.98: return "item", random.choice(keys_of("fruit", "epic"))
    if r < 0.995: return "item", random.choice(keys_of("weapon", "legendary"))
    return "item", random.choice(keys_of("fruit", "legendary"))

def apply_loot(uid, loot):
    kind, val = loot
    if kind == "belly":
        add_belly(uid, val)
        return f"💰 Белли: <b>+{bel(val)}</b>"
    if kind == "exp":
        notes = grant_exp(uid, val)
        return "\n".join([f"✨ Опыт: <b>+{n(val)}</b>"] + notes)
    it = ITEMS[val]
    msg = give_item(uid, val)
    if it["kind"] == "food":
        return f"🍽️ Еда: <b>{it['name']}</b> (лечит {int(it['heal']*100)}% HP) — в инвентаре"
    if it["rarity"] in ("epic", "legendary"):
        return f"\n🌟 <b>РЕДКАЯ НАХОДКА!</b>\n🎁 {msg}"
    return f"🎁 Предмет: {msg}"

def make_enemy(level, monster=False):
    name, emoji, hk, ak, dk, rk = random.choice(MONSTERS if monster else ENEMIES)
    var = random.uniform(0.92, 1.08)
    return dict(name=name, emoji=emoji, hp=int((50+16*level)*hk*var), atk=(7+2.4*level)*ak*var, defense=(2+0.8*level)*dk, special=None, reward=dict(belly=int((40+22*level)*rk*random.uniform(0.8, 1.2)), exp=int((18+6*level)*rk), bounty=int(150*level*rk), treasure=None, bonus=[]))

# ИЗМЕНЕНИЕ: Функция создания босса теперь принимает статы игрока
def boss_enemy(key, player_stats):
    b = BOSSES[key]
    
    # Получаем статы игрока (уже с учетом оружия)
    p_atk = player_stats["atk"]
    p_def = player_stats["defense"]
    p_max_hp = player_stats["max_hp"]
    
    # Рассчитываем статы босса на основе множителей и статов игрока
    # Добавляем небольшой рандомный разброс (+-5%), чтобы бои не были одинаковыми
    variance = random.uniform(0.95, 1.05)
    
    boss_hp = int(p_max_hp * b["hp_mult"] * variance)
    boss_atk = p_atk * b["atk_mult"] * variance
    boss_def = p_def * b["def_mult"] * variance
    
    # Гарантируем минимальные значения, чтобы босс не был слишком слабым на низких уровнях
    boss_hp = max(boss_hp, 100) 
    boss_atk = max(boss_atk, 10)
    boss_def = max(boss_def, 2)

    return dict(
        name=b["name"], 
        emoji=b["emoji"], 
        hp=boss_hp, 
        atk=boss_atk, 
        defense=boss_def, 
        special=b["special"], 
        reward=dict(
            belly=b["belly"], 
            exp=b["exp"], 
            bounty=b["bounty"], 
            treasure=b["treasure"], 
            bonus=b["bonus"]
        )
    )

def start_battle(uid, p, kind, key, enemy):
    st = combat_stats(p)
    role = ROLES[p["role"]]
    b = dict(kind=kind, key=key, name=enemy["name"], emoji=enemy["emoji"], e_hp=enemy["hp"], e_max=enemy["hp"], e_atk=enemy["atk"], e_def=enemy["defense"], special=enemy["special"], reward=enemy["reward"], p_hp=max(1, p["hp"]), p_max=p["max_hp"], p_atk=st["atk"], p_def=st["defense"], p_haki=st["haki"], crit=role["crit"], dodge=role["dodge"], taken=role["taken"], role=p["role"], fruit=p["fruit"], guard=False, skill_cd=0, turn=1, burn=0, stun=0, log=[], squad=build_squad(uid, st["atk"]) if (kind=="boss" or SQUAD_IN_ADVENTURE) else [])
    BATTLES[uid] = b
    return b

def calc_damage(atk, df, mult=1.0, pierce=0.0, flat=0.0):
    return max(1, int(atk*mult*random.uniform(0.85, 1.15) + flat - df*0.6*(1-pierce)))

def use_skill(b):
    fr = ITEMS.get(b["fruit"]) if b["fruit"] else None
    if fr:
        dmg = calc_damage(b["p_atk"], b["e_def"], fr["mult"], fr.get("pierce", 0.0), b["p_haki"])
        b["e_hp"] -= dmg
        b["skill_cd"] = 4
        lines = [f"🍈 <b>{fr['short']}</b>: {dmg} урона!"]
        eff = fr.get("effect")
        if eff == "burn":
            b["burn"] = 2
            lines.append(" Враг охвачен пламенем!")
        elif eff == "freeze":
            b["stun"] = 1
            lines.append(" Враг заморожен и пропустит ход!")
        elif eff == "drain":
            heal = int(dmg*0.35)
            b["p_hp"] = min(b["p_max"], b["p_hp"]+heal)
            lines.append(f"️ Вы высасываете влагу: +{heal} HP")
        elif eff == "shock":
            b["stun"] = 1
            lines.append("⚡ Враг парализован разрядом!")
        return lines
    dmg = calc_damage(b["p_atk"], b["e_def"], 1.3, 0.5, b["p_haki"]*1.5)
    b["e_hp"] -= dmg
    b["skill_cd"] = 3
    return [f"🌀 <b>Хаки Вооружения</b>: {dmg} урона (часть брони игнорируется)!"]

def build_squad(uid, player_atk):
    owned = chars_get(uid)
    out = []
    for k in squad_get(uid):
        c = CHARACTERS[k]
        name, kind, mult, cd = c["skill"]
        out.append(dict(key=k, name=c["name"], emoji=c["emoji"], skill=name, kind=kind, mult=mult, cd_max=cd, cd=0, stars=owned[k], atk=char_power(k, owned[k], player_atk)))
    return out

def use_char_skill(b, idx):
    c = b["squad"][idx]
    kind, mult = c["kind"], c["mult"]
    c["cd"] = c["cd_max"] + 1
    head = f"{c['emoji']} <b>{esc(c['name'])}</b> — {esc(c['skill'])}!"
    if kind == "heal":
        heal = max(1, int(b["p_max"]*min(0.9, mult*star_mult(c["stars"]))))
        before = b["p_hp"]
        b["p_hp"] = min(b["p_max"], b["p_hp"]+heal)
        return [head, f"💚 Вы восстановили {b['p_hp']-before} HP"]
    dmg = calc_damage(c["atk"], b["e_def"], mult, 0.6 if kind=="pierce" else 0.0)
    b["e_hp"] -= dmg
    lines = [f"{head} {dmg} урона!"]
    if kind == "burn":
        b["burn"] = 2
        lines.append(" Враг охвачен пламенем!")
    elif kind == "stun":
        b["stun"] = 1
        lines.append(" Враг оглушён и пропустит ход!")
    elif kind == "shield":
        b["guard"] = True
        lines.append("️ Входящий урон на этот ход снижен на 60%")
    elif kind == "drain":
        heal = int(dmg*0.4)
        b["p_hp"] = min(b["p_max"], b["p_hp"]+heal)
        lines.append(f"💚 Вы восстановили {heal} HP")
    elif kind == "pierce":
        lines.append(" Броня врага частично проигнорирована")
    return lines

def enemy_turn(b):
    lines = []
    if b["burn"] > 0:
        bd = max(1, int(b["p_atk"]*0.4))
        b["e_hp"] -= bd
        b["burn"] -= 1
        lines.append(f"🔥 Ожог: {bd} урона")
    if b["e_hp"] <= 0: return lines
    if b["stun"] > 0:
        b["stun"] -= 1
        lines.append(f"💫 {esc(b['name'])} не может двигаться и пропускает ход!")
        return lines
    special = bool(b["special"]) and b["turn"] % 3 == 0
    mult = b["special"][1] if special else 1.0
    if random.random() < b["dodge"]:
        lines.append("💨 Вы уклонились от удара!")
        return lines
    dmg = b["e_atk"]*mult*random.uniform(0.85, 1.15) - b["p_def"]*0.6
    dmg *= b["taken"]
    if b["guard"]: dmg *= 0.4
    dmg = max(1, int(dmg))
    b["p_hp"] -= dmg
    if special:
        lines.append(f"💥 <b>{esc(b['special'][0])}</b>! Вы теряете {dmg} HP")
    else:
        lines.append(f"👊 {esc(b['name'])} атакует: -{dmg} HP")
    return lines

def do_round(b, action):
    lines = []
    if action == "flee":
        chance = 0.5 + (0.15 if b["role"]=="hunter" else 0.0) - (0.25 if b["kind"]=="boss" else 0.0)
        if random.random() < chance:
            b["log"] = [" Вы сбежали с поля боя!"]
            return "fled"
        lines.append(" Побег не удался!")
    elif action == "def":
        b["guard"] = True
        heal = max(1, int(b["p_max"]*0.05))
        b["p_hp"] = min(b["p_max"], b["p_hp"]+heal)
        lines.append(f"🛡️ Вы в защитной стойке: урон снижен на 60%, +{heal} HP")
    elif action == "atk":
        crit = random.random() < b["crit"]
        dmg = calc_damage(b["p_atk"], b["e_def"])
        if crit: dmg *= 2
        b["e_hp"] -= dmg
        lines.append(f"️ {'💢 КРИТ! ' if crit else ''}Вы наносите {dmg} урона")
    elif action == "skill":
        lines += use_skill(b)
    elif action[:1] == "c" and action[1:].isdigit():
        lines += use_char_skill(b, int(action[1:]))
    
    if b["e_hp"] <= 0:
        b["log"] = lines
        return "win"
    
    lines += enemy_turn(b)
    b["guard"] = False
    b["log"] = lines
    if b["e_hp"] <= 0: return "win"
    if b["p_hp"] <= 0: return "lose"
    
    b["turn"] += 1
    if b["skill_cd"] > 0: b["skill_cd"] -= 1
    for c in b.get("squad", []):
        if c["cd"] > 0: c["cd"] -= 1
    return "continue"

def battle_text(b):
    title = "БОСС" if b["kind"]=="boss" else "БОЙ"
    status = []
    if b["burn"] > 0: status.append(" Враг горит")
    if b["stun"] > 0: status.append(" Враг оглушён")
    skill_line = f"🌀 Навык:  через {b['skill_cd']} р." if b["skill_cd"] > 0 else " Навык: ✅ готов"
    lines = [f"⚔️ <b>{title}: {esc(b['name'])}</b> {b['emoji']}  (раунд {b['turn']})", "", f"{b['emoji']} <b>{esc(b['name'])}</b>", f"❤️ {bar(b['e_hp'], b['e_max'])} {max(0, int(b['e_hp']))}/{int(b['e_max'])}", "", "🧑 <b>Вы</b>", f"❤️ {bar(b['p_hp'], b['p_max'])} {max(0, int(b['p_hp']))}/{b['p_max']}", skill_line]
    if b.get("squad"):
        lines.append(" Отряд: " + " • ".join([f"{c['emoji']} {esc(c['name'].split()[-1])} " + (f"{c['cd']}" if c["cd"]>0 else "✅") for c in b["squad"]]))
    if status: lines.append(" • ".join(status))
    if b["log"]: lines += ["", "📜 <b>Журнал:</b>"] + b["log"]
    return "\n".join(lines)

def battle_markup(b):
    fr = ITEMS.get(b["fruit"]) if b["fruit"] else None
    skill = f"🍈 {fr['short']}" if fr else " Хаки"
    if b["skill_cd"] > 0: skill += f" {b['skill_cd']}"
    rows = [[btn("⚔️ Атаковать", "b:atk"), btn(skill, "b:skill")], [btn("🛡️ Защита", "b:def"), btn("🏃 Сбежать", "b:flee")]]
    crew = [btn(f"{c['emoji']} {c['name'].split()[-1]}" + (f" ⏳{c['cd']}" if c["cd"]>0 else ""), f"b:c{i}") for i, c in enumerate(b.get("squad", []))]
    if crew: rows.insert(1, crew)
    return InlineKeyboardMarkup(rows)

AFTER_BATTLE_KB = InlineKeyboardMarkup([[btn(" Приключение", "adv"), btn("👑 Боссы", "boss")], MENU_ROW])

def settle_battle(uid, b, outcome):
    BATTLES.pop(uid, None)
    p = get_player(uid)
    head = "\n".join(b["log"])
    if outcome == "fled":
        update_player(uid, hp=max(1, b["p_hp"]))
        return f"{head}\n🏃 Вы вовремя ушли от боя. Здоровье: {max(1, b['p_hp'])}/{p['max_hp']}"
    if outcome == "lose":
        loss = int(p["belly"]*0.1)
        update_player(uid, hp=max(1, int(p["max_hp"]*0.2)), belly=p["belly"]-loss, losses=p["losses"]+1)
        return f"{head}\n💀 <b>ПОРАЖЕНИЕ!</b> {b['emoji']} {esc(b['name'])} оказался сильнее.\nВас подобрали дозорные и вылечили до 20% HP.\n Потеряно Белли: {bel(loss)}\n💡 Прокачайтесь (/upgrade), экипируйте оружие или поешьте перед боем."
    
    r = b["reward"]
    gold = ROLES[p["role"]]["gold"]
    gain = int(r["belly"]*gold)
    is_boss = b["kind"]=="boss"
    fields = dict(hp=max(1, b["p_hp"]), wins=p["wins"]+1, bounty=p["bounty"]+r["bounty"], belly=p["belly"]+gain)
    if is_boss: fields["boss_kills"] = p["boss_kills"]+1
    update_player(uid, **fields)
    
    out = [head, "", f"🏆 <b>ПОБЕДА{' НАД БОССОМ' if is_boss else ''}: {esc(b['name'])}!</b>", "", f"💰 Белли: <b>+{bel(gain)}</b>" + (" (бонус охотника)" if gold>1 else ""), f"👑 Награда за голову: <b>+{bel(r['bounty'])}</b>"]
    if is_boss:
        kills, _ = boss_record(uid, b["key"])
        if r["treasure"] and (kills==0 or random.random()<0.35):
            inv_add(uid, r["treasure"])
            t = ITEMS[r["treasure"]]
            out.append(f" Сокровище: <b>{t['name']}</b> (стоит {bel(t['sell'])})")
        for key, chance in r["bonus"]:
            if random.random() < chance:
                out.append(f"\n🌟 <b>Особый трофей:</b> {give_item(uid, key)}")
        boss_mark(uid, b["key"])
        if random.random() < BOSS_CHAR_DROP:
            rar = random.choices(RARITY_ORDER, weights=[62, 26, 10, 2])[0]
            ck = random.choice([k for k, c in CHARACTERS.items() if c["rarity"]==rar])
            r_res = char_add(uid, ck)
            c = CHARACTERS[ck]
            note = " новый персонаж!" if r_res["new"] else (f"⭐ звёзды: {stars_str(r_res['stars'])}" if r_res["upgraded"] else f"💰 +{bel(r_res['comp'])}")
            out.append(f"🎴 <b>Босс обронил карточку:</b> {c['emoji']} {c['name']} ({RARITY[rar]}) — {note}")
    out.append(f"✨ Опыт: <b>+{n(r['exp'])}</b>")
    out += grant_exp(uid, r["exp"])
    return "\n".join(out)

# ══════════════════════════ ОТВЕТЫ И ОБЩИЕ ЭКРАНЫ ═══════════════════════════
async def respond(update, text, markup=None):
    q = update.callback_query
    if q:
        m = q.message
        if m and (getattr(m, "photo", None) or getattr(m, "video", None) or getattr(m, "animation", None)):
            try: await m.delete()
            except TelegramError: pass
            try:
                await m.chat.send_message(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            except Exception as e:
                log.error(f"Ошибка отправки нового сообщения после удаления медиа: {e}")
            return
        try:
            await q.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except BadRequest as e:
            error_msg = str(e).lower()
            if "not modified" in error_msg: pass
            else:
                try: await q.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
                except Exception as e: log.error(f"Ошибка отправки нового сообщения: {e}")
        except Exception as e:
            log.error(f"Неожиданная ошибка при редактировании: {e}")
            try: await q.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
            except Exception: pass
    else:
        try: await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except Exception as e: log.error(f"Ошибка отправки сообщения: {e}")

async def require_player(update):
    p = get_player(update.effective_user.id)
    if not p:
        await respond(update, "🏴‍☠️ Ты ещё не вышел в море! Нажми /start и выбери свою роль.")
    return p

def main_kb():
    return InlineKeyboardMarkup([[btn("👤 Профиль", "profile"), btn("🎁 Сундук", "gacha")], [btn(" Приключение", "adv"), btn(" Боссы", "boss")], [btn("🎴 Призыв", "summon"), btn("👥 Отряд", "squad")], [btn("📈 Улучшения", "upgrade"), btn("🎒 Инвентарь", "inv")]])

async def show_menu(update, context):
    p = await require_player(update)
    if not p: return
    text = f"‍☠️ <b>Главное меню</b>\n{ROLES[p['role']]['title']} <b>{esc(p['name'])}</b> • ⭐ ур. {p['level']}\n❤️ {p['hp']}/{p['max_hp']}  •   {bel(p['belly'])}\nНаграда: {bel(p['bounty'])}\nКуда отправимся, капитан? "
    await respond(update, text, main_kb())

async def cmd_start(update, context):
    if get_player(update.effective_user.id): return await show_menu(update, context)
    lines = ["🏴‍☠️ <b>ONE PIECE RPG</b> 🌊", "", "Эра пиратов началась! Выбери свой путь по Гранд Лайн, собирай сокровища, ешь Дьявольские фрукты и побеждай легендарных боссов.", ""]
    for r in ROLES.values():
        lines.append(f"{r['title']}\nHP {r['hp']} • ATK {r['atk']} • DEF {r['def']} • Haki {r['haki']}\n{r['desc']}\n")
    lines.append("👇 <b>Выбери роль:</b>")
    kb = InlineKeyboardMarkup([[btn(r["title"], f"role:{k}")] for k, r in ROLES.items()])
    await respond(update, "\n".join(lines), kb)

async def choose_role(update, context, role):
    user = update.effective_user
    if role not in ROLES or get_player(user.id): return await show_menu(update, context)
    create_player(user, role)
    r = ROLES[role]
    await respond(update, f"🎉 <b>Добро пожаловать в Новый Мир, {esc(user.full_name)}!</b>\nТы выбрал путь: {r['title']}\n💰 Стартовые Белли: {bel(300)}\nОткрывай сундук, плыви навстречу приключениям и готовься к битве с боссами!", main_kb())
    if ADMIN_ID and user.id != ADMIN_ID:
        uname = f" (@{user.username})" if user.username else ""
        try: await context.bot.send_message(ADMIN_ID, f"🆕 Новый игрок: {esc(user.full_name)}{uname}\nID: {user.id} • {r['title']}")
        except TelegramError: pass

async def show_help(update, context):
    text = "📖 <b>Команды</b>\n/start — меню / начать игру\n/profile — профиль и характеристики\n/gacha — сундук с сокровищами (раз в 2 часа)\n/adventure — приключение в открытом море\n/boss — битвы с боссами\n/summon — призыв персонажей (видео-анимация + карточка)\n/characters — коллекция персонажей\n/squad — отряд для боёв с боссами\n/upgrade — улучшение характеристик\n/inventory — инвентарь и экипировка\n/myid — узнать свой Telegram ID"
    if ADMIN_ID and update.effective_user.id == ADMIN_ID:
        text += "\n🛠 <b>Админ:</b>\n/players — список игроков\n/player <ID или @username> — ПОЛНАЯ карточка игрока\n/top <категория> — рейтинг игроков\n/stats — общая статистика бота\n/give_gold <кому> <кол-во> — выдать Белли\n/give_item <кому> <ключ> [кол-во] — выдать предмет (ключи: /items)\n/give_char <кому> <ключ> — выдать персонажа (ключи: /items)\n/give_exp <кому> <кол-во> — выдать опыт\n/heal <кому> — полностью вылечить\n/reset_gacha <кому> [count] — сбросить таймер сундука\n/reset_boss <кому> — сбросить таймеры боссов\n/nocooldown <кому> <1|0> — снять/вернуть все кулдауны\n/reset_player <кому> — ПОЛНЫЙ сброс аккаунта\n/restore_player <ID> — восстановить из бекапа\n<i>«кому» = ID, @username или all</i>\n/msg <кому> <текст> — личное сообщение от админа (или ответом на сообщение)\n/broadcast <текст> — рассылка всем"
    await respond(update, text, InlineKeyboardMarkup([MENU_ROW]))

async def cmd_myid(update, context):
    await update.message.reply_text(f" Твой Telegram ID: <code>{update.effective_user.id}</code>")

async def show_profile(update, context):
    p = await require_player(update)
    if not p: return
    role = ROLES[p["role"]]
    st = combat_stats(p)
    w = ITEMS.get(p["weapon"]) if p["weapon"] else None
    f = ITEMS.get(p["fruit"]) if p["fruit"] else None
    def stat(icon, label, base, total):
        extra = f" (+{total-base})" if total>base else ""
        return f"{icon} {label}: <b>{base}</b>{extra}"
    text = "\n".join([f"🏴‍☠️ <b>{esc(p['name'])}</b> — {role['title']}", "", f"⭐ Уровень: <b>{p['level']}</b>  (опыт {p['exp']}/{exp_need(p['level'])})", f"❤️ HP: <b>{p['hp']}/{p['max_hp']}</b>  {bar(p['hp'], p['max_hp'])}", stat("⚔️", "Атака", p["atk"], st["atk"]), stat("️", "Защита", p["defense"], st["defense"]), stat("🌀", "Воля (Haki)", p["haki"], st["haki"]), "", f"💰 Белли: <b>{bel(p['belly'])}</b>", f"👑 Награда за голову: <b>{bel(p['bounty'])}</b>", "", f"️ Оружие: {w['name'] if w else '— нет —'}", f" Дьявольский фрукт: {f['name'] if f else '— нет —'}", f"🎴 Персонажи: {len(chars_get(p['user_id']))}/{len(CHARACTERS)} • Отряд: " + (", ".join(CHARACTERS[k]["emoji"]+CHARACTERS[k]["name"].split()[-1] for k in squad_get(p["user_id"])) or "— пуст —"), "", f"🏆 Побед: {p['wins']} • 💀 Поражений: {p['losses']} • 👑 Боссов: {p['boss_kills']}", "<i>❤️ Здоровье восстанавливается на 2% в минуту.</i>"])
    await respond(update, text, InlineKeyboardMarkup([[btn("🎒 Инвентарь", "inv"), btn("📈 Улучшения", "upgrade")], MENU_ROW]))

async def show_gacha(update, context):
    p = await require_player(update)
    if not p: return
    now = time.time()
    if not p.get("no_cooldown"):
        left = p["last_gacha"] + GACHA_COOLDOWN - now
        if left > 0:
            return await respond(update, f"🎁 <b>Сундук с сокровищами</b>\nТы уже открывал сундук недавно.\nСледующий откроется через: <b>{fmt_duration(left)}</b>", InlineKeyboardMarkup([[btn("🔄 Проверить таймер", "gacha")], MENU_ROW]))
    uid = p["user_id"]
    update_player(uid, last_gacha=now, pulls=p["pulls"]+1)
    loot = roll_chest(p["level"], ROLES[p["role"]]["gold"], allow_rare=True)
    result = apply_loot(uid, loot)
    await respond(update, f"📦 <b>Сундук с сокровищами открыт!</b>\n{result}\n⏳ Следующий сундук — через {fmt_duration(GACHA_COOLDOWN)}", InlineKeyboardMarkup([[btn(" Инвентарь", "inv"), btn("👤 Профиль", "profile")], MENU_ROW]))

async def show_upgrade(update, context, note=""):
    p = await require_player(update)
    if not p: return
    lines = ["📈 <b>Улучшения</b>", f"💰 Белли: <b>{bel(p['belly'])}</b>  •  ✨ Опыт: <b>{p['exp']}</b>", "<i>Улучшение тратит Белли и очки опыта.</i>", ""]
    if note: lines += [note, ""]
    rows = []
    for key, (label, field, inc, cost_fn) in UPGRADES.items():
        cb, ce = cost_fn(p[field])
        mark = "✅" if p["belly"]>=cb and p["exp"]>=ce else ""
        lines.append(f"{label}: {p[field]} → {p[field]+inc}  ({bel(cb)} + {ce} оп.)")
        rows.append([btn(f"{mark} {label} +{inc}  •  {n(cb)} ฿ / {ce} оп.", f"up:{key}")])
    rows.append(MENU_ROW)
    await respond(update, "\n".join(lines), InlineKeyboardMarkup(rows))

async def do_upgrade(update, context, key):
    p = await require_player(update)
    if not p or key not in UPGRADES: return
    label, field, inc, cost_fn = UPGRADES[key]
    cb, ce = cost_fn(p[field])
    if p["belly"]<cb or p["exp"]<ce: return await show_upgrade(update, context, "❌ <b>Не хватает ресурсов!</b>")
    fields = {field: p[field]+inc, "belly": p["belly"]-cb, "exp": p["exp"]-ce}
    if key == "hp": fields["hp"] = min(p["max_hp"]+inc, p["hp"]+inc)
    update_player(p["user_id"], **fields)
    await show_upgrade(update, context, f"✅ <b>{label} улучшено!</b> +{inc}")

async def show_inventory(update, context, note=""):
    p = await require_player(update)
    if not p: return
    inv = inv_get(p["user_id"])
    lines = [" <b>Инвентарь</b>", ""]
    if note: lines += [note, ""]
    rows = []
    def section(title, kind):
        keys = [k for k in inv if ITEMS[k]["kind"]==kind]
        keys.sort(key=lambda k: list(RARITY).index(ITEMS[k]["rarity"]))
        if keys:
            lines.append(title)
            return keys
        return []
    for k in section("🍽️ <b>Еда:</b>", "food"):
        it = ITEMS[k]
        lines.append(f"  {it['name']} ×{inv[k]} — лечит {int(it['heal']*100)}% HP")
        rows.append([btn(f"🍴 Съесть {it['name']} (×{inv[k]})", f"use:{k}")])
    for k in section("️ <b>Оружие и экипировка:</b>", "weapon"):
        it = ITEMS[k]
        bonus = ", ".join(f"+{it[s]} {lab}" for s, lab in (("atk", "ATK"), ("def", "DEF"), ("haki", "Haki")) if it.get(s))
        on = p["weapon"]==k
        lines.append(f"  {'✅ ' if on else ''}{it['name']} — {bonus} ({RARITY[it['rarity']]})")
        if not on: rows.append([btn(f"️ Экипировать {it['name']}", f"use:{k}")])
    for k in section("🍈 <b>Дьявольские фрукты:</b>", "fruit"):
        it = ITEMS[k]
        on = p["fruit"]==k
        lines.append(f"  {'✅ ' if on else ''}{it['name']} — {it['info']} ({RARITY[it['rarity']]})")
        if not on: rows.append([btn(f"🍈 Использовать {it['short']}", f"use:{k}")])
    total = 0
    tkeys = section("💎 <b>Сокровища:</b>", "treasure")
    for k in tkeys:
        it = ITEMS[k]
        lines.append(f"  {it['name']} ×{inv[k]} — {bel(it['sell'])} за шт.")
        total += it["sell"]*inv[k]
    if tkeys: rows.append([btn(f"💰 Продать все сокровища (+{n(total)} ฿)", "sell_all")])
    if not inv: lines.append("Пока пусто. Открой сундук (/gacha) или отправься в приключение (/adventure)!")
    rows.append(MENU_ROW)
    await respond(update, "\n".join(lines), InlineKeyboardMarkup(rows))

async def use_item(update, context, key):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    if uid in BATTLES: return await show_battle(update, "⚔️ Во время боя нельзя менять экипировку и есть!")
    if key not in ITEMS or key not in inv_get(uid): return await show_inventory(update, context, "❌ Такого предмета у вас нет.")
    it = ITEMS[key]
    if it["kind"] == "food":
        if p["hp"] >= p["max_hp"]: return await show_inventory(update, context, "😋 Вы и так полны сил!")
        heal = int(p["max_hp"]*it["heal"])
        new_hp = min(p["max_hp"], p["hp"]+heal)
        update_player(uid, hp=new_hp)
        inv_remove(uid, key)
        note = f" Вы съели {it['name']}: +{new_hp-p['hp']} HP ({new_hp}/{p['max_hp']})"
    elif it["kind"] == "weapon":
        update_player(uid, weapon=key)
        note = f"️ Экипировано: {it['name']}"
    elif it["kind"] == "fruit":
        update_player(uid, fruit=key)
        note = f"🍈 Сила фрукта получена: {it['name']}"
    else: note = ""
    await show_inventory(update, context, note)

async def sell_all(update, context):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    inv = inv_get(uid)
    total = 0
    for k, qty in inv.items():
        if ITEMS[k]["kind"] == "treasure":
            total += ITEMS[k]["sell"]*qty
            inv_remove(uid, k, qty)
    if total:
        add_belly(uid, total)
        note = f"💰 Сокровища проданы: <b>+{bel(total)}</b>"
    else: note = "У вас нет сокровищ для продажи."
    await show_inventory(update, context, note)

async def show_battle(update, note=""):
    b = BATTLES.get(update.effective_user.id)
    if not b: return await respond(update, "⚔️ Активного боя нет.", InlineKeyboardMarkup([MENU_ROW]))
    text = battle_text(b)
    if note: text = f"{note}\n{text}"
    await respond(update, text, battle_markup(b))

async def show_bosses(update, context):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    if uid in BATTLES: return await show_battle(update, "⚠️ Сначала закончите текущий бой!")
    now = time.time()
    lines = ["👑 <b>Боссы Гранд Лайн</b>", "", "Победи легендарных злодеев и забери их сокровища!", ""]
    rows = []
    for key, bs in BOSSES.items():
        kills, last = boss_record(uid, key)
        left = 0 if p.get("no_cooldown") else (last+BOSS_COOLDOWN-now)
        if p["level"] < bs["level"]: mark, cb = "🔒", f"lock:{key}"
        elif left > 0: mark, cb = "⏳", f"cd:{key}"
        else: mark, cb = "⚔️", f"boss:{key}"
        lines.append(f"{mark} {bs['emoji']} <b>{bs['name']}</b> — ур. {bs['level']}+ • побед: {kills}")
        rows.append([btn(f"{mark} {bs['emoji']} {bs['name']}", cb)])
    rows.append(MENU_ROW)
    await respond(update, "\n".join(lines), InlineKeyboardMarkup(rows))

async def show_boss_card(update, context, key):
    p = await require_player(update)
    if not p or key not in BOSSES: return
    bs = BOSSES[key]
    t = ITEMS[bs["treasure"]]
    lines = [f"{bs['emoji']} <b>{bs['name']}</b>", f"<i>{bs['lore']}</i>", "", f"❤️ HP: ~{int(bs['hp_mult']*100)}% от вашего макс.  ️ ATK: ~{int(bs['atk_mult']*100)}% от вашей атаки  🛡️ DEF: ~{int(bs['def_mult']*100)}% от вашей защиты", f"💥 Спецприём (каждый 3-й раунд): <b>{bs['special'][0]}</b> (×{bs['special'][1]})", "", " <b>Награда за победу:</b>", f"  💰 {bel(bs['belly'])} •  {n(bs['exp'])} опыта • 👑 +{bel(bs['bounty'])}", f"  💎 {t['name']}"]
    for ik, ch in bs["bonus"]:
        lines.append(f"   {ITEMS[ik]['name']} — шанс {int(ch*100)}%")
    if p["hp"] < p["max_hp"]*0.5:
        lines += ["", f"⚠️ У вас мало здоровья ({p['hp']}/{p['max_hp']}). Лучше поесть или отдохнуть!"]
    await respond(update, "\n".join(lines), InlineKeyboardMarkup([[btn("⚔️ В бой!", f"fight:{key}")], [btn("⬅️ К списку боссов", "boss")]]))

async def fight_boss(update, context, key):
    p = await require_player(update)
    if not p or key not in BOSSES: return
    uid = p["user_id"]
    if uid in BATTLES: return await show_battle(update, "️ Сначала закончите текущий бой!")
    bs = BOSSES[key]
    _, last = boss_record(uid, key)
    if p["level"] < bs["level"] or (not p.get("no_cooldown") and last+BOSS_COOLDOWN > time.time()):
        return await show_bosses(update, context)
    
    # ИЗМЕНЕНИЕ: Передаем статы игрока в функцию создания босса
    st = combat_stats(p)
    enemy_data = boss_enemy(key, st)
    
    b = start_battle(uid, p, "boss", key, enemy_data)
    b["log"] = [f"{bs['emoji']} <b>{bs['name']}</b> преграждает тебе путь!"]
    await respond(update, battle_text(b), battle_markup(b))

async def on_battle_action(update, context, action):
    uid = update.effective_user.id
    b = BATTLES.get(uid)
    if not b: return await respond(update, "⚔️ Этот бой уже завершён.", InlineKeyboardMarkup([MENU_ROW]))
    if action[:1]=="c" and action[1:].isdigit():
        i = int(action[1:])
        sq = b.get("squad", [])
        if i >= len(sq): return await show_battle(update)
        if sq[i]["cd"] > 0: return await show_battle(update, f"⏳ {esc(sq[i]['name'])} перезаряжается: ещё {sq[i]['cd']} р.")
    outcome = do_round(b, action)
    if outcome == "continue": return await respond(update, battle_text(b), battle_markup(b))
    await respond(update, settle_battle(uid, b, outcome), AFTER_BATTLE_KB)

async def show_adventure(update, context):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    if uid in BATTLES:
        log.info(f"Пользователь {uid} уже в бою, показываем текущий")
        return await show_battle(update, "⚠️ У вас уже идёт бой!")
    now = time.time()
    if not p.get("no_cooldown"):
        left = p["last_adventure"] + ADVENTURE_COOLDOWN - now
        if left > 0:
            return await respond(update, f"⛵ Корабль ещё готовится к отплытию. Подожди <b>{int(left)+1} сек.</b>", InlineKeyboardMarkup([[btn("🔄 Проверить", "adv")], MENU_ROW]))
    update_player(uid, last_adventure=now)
    L = p["level"]
    gold = ROLES[p["role"]]["gold"]
    kind = random.choices(["battle", "monster", "treasure", "feast", "storm", "sage", "chest", "calm"], weights=[34, 10, 16, 10, 10, 8, 7, 5])[0]
    if kind in ("battle", "monster"):
        enemy = make_enemy(L, monster=(kind=="monster"))
        log.info(f"Создаём бой приключения для пользователя {uid}, тип={kind}")
        b = start_battle(uid, p, "enemy", None, enemy)
        if kind == "monster": b["log"] = [f" Море содрогнулось — из глубины поднимается {enemy['emoji']} <b>{esc(enemy['name'])}</b>!"]
        else: b["log"] = [f" На горизонте показался {enemy['emoji']} <b>{esc(enemy['name'])}</b>. Бой неизбежен!"]
        try:
            await respond(update, battle_text(b), battle_markup(b))
            log.info(f"Бой приключения успешно показан пользователю {uid}")
        except Exception as e:
            log.error(f"Ошибка при показе боя приключения: {e}")
            BATTLES.pop(uid, None)
            await respond(update, "❌ Не удалось начать бой. Попробуйте ещё раз.", InlineKeyboardMarkup([MENU_ROW]))
        return
    if kind == "treasure":
        gain = int(random.randint(80, 250)*(1+0.12*L)*gold)
        add_belly(uid, gain)
        text = f"🏝️ <b>Необитаемый остров</b>\nТы нашёл закопанный клад по старой карте!\nБелли: <b>+{bel(gain)}</b>"
        if random.random() < 0.3:
            inv_add(uid, "pearl")
            text += f"\n🎁 А ещё — {ITEMS['pearl']['name']}!"
    elif kind == "feast":
        heal = int(p["max_hp"]*0.3)
        new_hp = min(p["max_hp"], p["hp"]+heal)
        update_player(uid, hp=new_hp)
        text = f" <b>Пир на пляже</b>\nМестные жители накормили тебя до отвала!\n❤️ +{new_hp-p['hp']} HP ({new_hp}/{p['max_hp']})"
        if random.random() < 0.5:
            fk = random.choice(["meat", "onigiri"])
            inv_add(uid, fk)
            text += f"\n🎁 Дали с собой: {ITEMS[fk]['name']}"
    elif kind == "storm":
        loss = int(p["max_hp"]*random.uniform(0.08, 0.15))
        new_hp = max(1, p["hp"]-loss)
        update_player(uid, hp=new_hp)
        xp = 10+L*2
        text = f"⛈️ <b>Шторм на Гранд Лайн!</b>\nВолны чуть не перевернули корабль.\n❤️ -{p['hp']-new_hp} HP ({new_hp}/{p['max_hp']})\n✨ Закалка: +{xp} опыта"
        notes = grant_exp(uid, xp)
        if notes: text += "\n" + "\n".join(notes)
    elif kind == "sage":
        xp = random.randint(30, 70)+L*5
        text = f"🧙 <b>Мудрый странник</b>\nСтарый морской волк поделился секретами боя.\n✨ Опыт: <b>+{xp}</b>"
        notes = grant_exp(uid, xp)
        if notes: text += "\n" + "\n".join(notes)
    elif kind == "chest":
        loot = roll_chest(L, gold, allow_rare=False)
        text = f"📦 <b>Дрейфующий сундук!</b>\nВолны вынесли к кораблю бочку.\n{apply_loot(uid, loot)}"
    else:
        xp = 10+L
        text = f"🌅 <b>Штиль</b>\nСпокойное море. Ты потренировался на палубе.\n✨ Опыт: <b>+{xp}</b>"
        notes = grant_exp(uid, xp)
        if notes: text += "\n" + "\n".join(notes)
    await respond(update, text, InlineKeyboardMarkup([[btn("🌊 Плыть дальше", "adv")], MENU_ROW]))

# ═══════════════════════════ ЭКРАНЫ ПЕРСОНАЖЕЙ ═══════════════════════════
RARITY_ICON = {k: v.split()[0] for k, v in RARITY.items()}
def _remember(path, msg):
    media_obj = msg.animation or msg.video or (msg.photo[-1] if msg.photo else None) or msg.document
    if media_obj: FILE_IDS[str(path)] = media_obj.file_id

async def send_photo_screen(update, context, photo, caption, markup):
    q = update.callback_query
    chat_id = update.effective_chat.id
    is_path = isinstance(photo, Path)
    handles = []
    def media():
        if is_path:
            fid = FILE_IDS.get(str(photo))
            if fid: return fid
            handles.append(open(photo, "rb"))
            return handles[-1]
        photo.seek(0)
        return photo
    try:
        if q and q.message and getattr(q.message, "photo", None):
            try:
                m = await q.edit_message_media(InputMediaPhoto(media(), caption=caption, parse_mode=ParseMode.HTML), reply_markup=markup, **UPLOAD_KW)
                if is_path and hasattr(m, "photo"): _remember(photo, m)
                return
            except BadRequest as e:
                if "not modified" in str(e).lower(): return
                m = await context.bot.send_photo(chat_id, media(), caption=caption, reply_markup=markup, parse_mode=ParseMode.HTML, **UPLOAD_KW)
                if is_path: _remember(photo, m)
                if q:
                    try: await q.message.delete()
                    except TelegramError: pass
    finally:
        for h in handles: h.close()

def summon_kb():
    return InlineKeyboardMarkup([[btn(f"🎴 Ещё ×1 • {n(SUMMON_COST)} ฿", "sm:1"), btn(f"🎴 ×10 • {n(SUMMON_COST_10)} ฿", "sm:10")], [btn("👥 Отряд", "squad"), btn("📚 Коллекция", "chars")], MENU_ROW])

async def show_summon(update, context, note=""):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    st = summon_state(uid)
    owned = chars_get(uid)
    free_left = 0 if p.get("no_cooldown") else st["last_free"]+SUMMON_FREE_COOLDOWN-time.time()
    rates = " • ".join(f"{RARITY_ICON[r]} {s}%" for r, s in zip(RARITY_ORDER, SUMMON_RATES))
    lines = ["🎴 <b>Призыв персонажей</b>", "<i>Зови союзников из мира One Piece — в боях с боссами они применяют свои навыки!</i>", ""]
    if note: lines += [note, ""]
    lines += [f"💰 Белли: <b>{bel(p['belly'])}</b>", f" Коллекция: <b>{len(owned)}/{len(CHARACTERS)}</b> • призывов: {st['total']}", "", f" Шансы: {rates}", f" Гарантии: 🔵+ через {PITY_RARE-st['since_rare']} • + через {PITY_EPIC-st['since_epic']} •  через {PITY_LEG-st['since_leg']} призывов", "⭐ Дубликат повышает звёзды персонажа (до ★5) — он становится сильнее."]
    rows = []
    if free_left <= 0: rows.append([btn(" Бесплатный призыв!", "sm:free")])
    else:
        lines.append(f"\nБесплатный призыв через <b>{fmt_duration(free_left)}</b>")
        rows += [[btn(f"🎴 Призвать ×1 • {n(SUMMON_COST)} ฿", "sm:1")], [btn(f"🎴 Призвать ×10 • {n(SUMMON_COST_10)} ฿", "sm:10")], [btn("👥 Отряд", "squad"), btn("📚 Коллекция", "chars")]]
        if free_left > 0: rows.append([btn("🔄 Обновить таймер", "summon")])
    rows.append(MENU_ROW)
    await respond(update, "\n".join(lines), InlineKeyboardMarkup(rows))

def _result_line(r):
    c = CHARACTERS[r["key"]]
    mark = "🆕" if r["new"] else (f"⭐{stars_str(r['stars']).count('★')}" if r["upgraded"] else "💰")
    return f"{RARITY_ICON[r['rarity']]} {c['emoji']} {c['name']} {mark}"

def summon_caption(results, belly):
    if len(results) == 1:
        r = results[0]
        c = CHARACTERS[r["key"]]
        head = {"legendary": "🌟 <b>ЛЕГЕНДАРНЫЙ ПРИЗЫВ!</b> 🌟", "epic": " <b>Эпический призыв!</b>", "rare": "🔵 <b>Редкий призыв!</b>", "common": " <b>Призыв</b>"}[r["rarity"]]
        lines = [head, "", f"{c['emoji']} <b>{c['name']}</b>", f"{RARITY[r['rarity']]}  {stars_str(r['stars'])}", f"⚔️ ATK: {c['atk']}", f"🌀 {skill_info(r['key'], r['stars'])}", ""]
        if r["new"]: lines.append("🆕 <b>Новый персонаж в коллекции!</b>" + (" Добавлен в отряд ✅" if r.get("to_squad") else ""))
        elif r["upgraded"]: lines.append(f"⭐ Дубликат! Звёзды повышены: {stars_str(r['stars'])}")
        else: lines.append(f"💰 Звёзды на максимуме — компенсация {bel(r['comp'])}")
        lines.append(f"\nБелли: {bel(belly)}")
        return "\n".join(lines)
    lines = ["🎴 <b>Призыв ×10</b>", ""] + [f"{i}. {_result_line(r)}" for i, r in enumerate(results, 1)]
    new = sum(1 for r in results if r["new"])
    comp = sum(r["comp"] for r in results)
    lines += ["", f"🆕 Новых: {new} •  Усилено: {sum(1 for r in results if r['upgraded'])}" + (f" • 💰 +{bel(comp)}" if comp else ""), f"💰 Белли: {bel(belly)}"]
    return "\n".join(lines)

async def run_summon(update, context, mode):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    if uid in SUMMONING: return
    if uid in BATTLES: return await show_battle(update, "⚠️ Сначала закончите текущий бой!")
    count = 10 if mode=="10" else 1
    now = time.time()
    st = summon_state(uid)
    if mode == "free":
        left = st["last_free"]+SUMMON_FREE_COOLDOWN-now
        if left > 0 and not p.get("no_cooldown"): return await show_summon(update, context, f"🕐 Бесплатный призыв будет через {fmt_duration(left)}")
        cost = 0
    else:
        cost = SUMMON_COST_10 if count==10 else SUMMON_COST
        if p["belly"] < cost: return await show_summon(update, context, f"❌ <b>Не хватает Белли!</b> Нужно {bel(cost)}.")
    SUMMONING.add(uid)
    try:
        if mode == "free": summon_update(uid, last_free=now)
        else: add_belly(uid, -cost)
        results = [summon_pull(uid) for _ in range(count)]
        await deliver_summon(update, context, results, get_player(uid)["belly"])
    except Exception: log.exception("Ошибка при призыве")
    finally: SUMMONING.discard(uid)

async def deliver_summon(update, context, results, belly):
    q = update.callback_query
    chat_id = update.effective_chat.id
    best = max(results, key=lambda r: CHAR_ORDER[r["rarity"]])["rarity"]
    caption = summon_caption(results, belly)
    markup = summon_kb()
    vid = None
    ap = anim_path(best)
    if ap:
        handle = None
        try:
            fid = FILE_IDS.get(str(ap))
            if not fid: handle = open(ap, "rb")
            vid = await context.bot.send_animation(chat_id, fid or handle, caption="🎴 <b>Призыв…</b>", parse_mode=ParseMode.HTML, **UPLOAD_KW)
            _remember(ap, vid)
        except TelegramError: log.exception("Не удалось отправить анимацию")
        finally:
            if handle: handle.close()
    if vid:
        if q:
            try: await q.message.delete()
            except TelegramError: pass
        await asyncio.sleep(ANIM_WAIT[best])
        photo = card_path(results[0]["key"]) if len(results)==1 else make_collage([r["key"] for r in results])
        if photo is None:
            if vid:
                try: await vid.delete()
                except TelegramError: pass
            return await context.bot.send_message(chat_id, caption, reply_markup=markup)
        if vid:
            handle = None
            try:
                if isinstance(photo, Path):
                    fid = FILE_IDS.get(str(photo))
                    if not fid: handle = open(photo, "rb")
                    media = fid or handle
                else:
                    photo.seek(0)
                    media = photo
                m = await context.bot.edit_message_media(chat_id=chat_id, message_id=vid.message_id, media=InputMediaPhoto(media, caption=caption, parse_mode=ParseMode.HTML), reply_markup=markup, **UPLOAD_KW)
                if isinstance(photo, Path) and hasattr(m, "photo"): _remember(photo, m)
                return
            except TelegramError:
                log.warning("edit_message_media не удался, отправляю картинку отдельно")
                try: await vid.delete()
                except TelegramError: pass
            finally:
                if handle: handle.close()
        if not isinstance(photo, Path): photo.seek(0)
        await send_photo_screen(update, context, photo, caption, markup)

async def show_collection(update, context):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    owned = chars_get(uid)
    squad = squad_get(uid)
    lines = [f"📚 <b>Коллекция персонажей</b>  {len(owned)}/{len(CHARACTERS)}", ""]
    for rar in reversed(RARITY_ORDER):
        lines.append(RARITY[rar])
        for k, c in CHARACTERS.items():
            if c["rarity"] != rar: continue
            if k in owned: lines.append(f"  {c['emoji']} {c['name']} {stars_str(owned[k])}" + (" 👥" if k in squad else ""))
            else: lines.append("  ▫️ ???")
    if not owned: lines += ["", "Пока никого нет. Призови первого персонажа — первый призыв бесплатный! 🎴"]
    rows = []
    if owned: rows.append([btn("🖼 Смотреть карточки", f"cv:{char_keys_sorted(owned)[0]}")])
    rows += [[btn(" Отряд", "squad"), btn(" Призыв", "summon")], MENU_ROW]
    await respond(update, "\n".join(lines), InlineKeyboardMarkup(rows))

async def show_char_card(update, context, key, note=""):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    owned = chars_get(uid)
    if key not in owned: return await show_collection(update, context)
    keys = char_keys_sorted(owned)
    i = keys.index(key)
    c = CHARACTERS[key]
    stars = owned[key]
    in_squad = key in squad_get(uid)
    lines = []
    if note: lines += [note, ""]
    lines += [f"{c['emoji']} <b>{c['name']}</b>", f"{RARITY[c['rarity']]}  {stars_str(stars)}", f"⚔️ ATK в бою: <b>{char_power(key, stars, p['atk'])}</b>  (база {c['atk']}, +20% за каждую ★)", f"🌀 {skill_info(key, stars)}", "👥 <b>В отряде</b>" if in_squad else ""]
    caption = "\n".join(l for l in lines if l is not None).strip()
    toggle = btn("➖ Убрать из отряда", f"sq:t:{key}") if in_squad else btn("➕ В отряд", f"sq:t:{key}")
    markup = InlineKeyboardMarkup([[btn("◀", f"cv:{keys[i-1]}"), btn(f"{i+1}/{len(keys)}", "noop"), btn("▶", f"cv:{keys[(i+1)%len(keys)]}")], [toggle], [btn("📚 Список", "chars"), btn("👥 Отряд", "squad")], MENU_ROW])
    photo = card_path(key)
    if photo: return await send_photo_screen(update, context, photo, caption, markup)
    await respond(update, caption, markup)

async def show_squad(update, context, note=""):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    owned = chars_get(uid)
    squad = squad_get(uid)
    lines = [f" <b>Отряд</b>  {len(squad)}/{SQUAD_SIZE}", "<i>В боях с боссами под полем боя появятся кнопки навыков отряда. Навык тратит ваш ход.</i>", ""]
    if note: lines += [note, ""]
    for i in range(SQUAD_SIZE):
        if i < len(squad):
            k = squad[i]
            c = CHARACTERS[k]
            lines.append(f"{i+1}. {c['emoji']} <b>{c['name']}</b> {stars_str(owned[k])} • ⚔️ {char_power(k, owned[k], p['atk'])}")
            lines.append(f"    🌀 {skill_info(k, owned[k])}")
        else: lines.append(f"{i+1}. ▫️ <i>свободный слот</i>")
    if not owned: lines += ["", "У вас пока нет персонажей — призовите первого! 🎴"]
    rows = [[btn(f"➖ {CHARACTERS[k]['name']}", f"sq:r:{k}")] for k in squad]
    if owned: rows.append([btn("⭐ Авто-подбор сильнейших", "sq:auto")])
    rows.append([btn("➕ Выбрать из коллекции", f"cv:{char_keys_sorted(owned)[0]}")])
    rows += [[btn(" Призыв", "summon"), btn(" Коллекция", "chars")], MENU_ROW]
    await respond(update, "\n".join(lines), InlineKeyboardMarkup(rows))

async def squad_action(update, context, data):
    p = await require_player(update)
    if not p: return
    uid = p["user_id"]
    if uid in BATTLES: return await show_battle(update, "️ Во время боя нельзя менять отряд!")
    parts = data.split(":")
    owned = chars_get(uid)
    squad = squad_get(uid)
    if parts[1] == "auto":
        squad_auto(uid)
        return await show_squad(update, context, "⭐ Отряд собран из сильнейших персонажей!")
    key = parts[2] if len(parts)>2 else ""
    if key not in owned: return await show_squad(update, context)
    if parts[1] == "r":
        squad_set(uid, [k for k in squad if k!=key])
        return await show_squad(update, context, f"👋 {CHARACTERS[key]['name']} покинул отряд")
    if parts[1] == "t":
        if key in squad:
            squad_set(uid, [k for k in squad if k!=key])
            return await show_char_card(update, context, key, "➖ Убран из отряда")
        if len(squad) >= SQUAD_SIZE:
            return await show_char_card(update, context, key, f"️ Отряд заполнен ({SQUAD_SIZE}/{SQUAD_SIZE}). Сначала уберите кого-нибудь в «👥 Отряд».")
        squad_set(uid, squad+[key])
        return await show_char_card(update, context, key, "✅ Добавлен в отряд")

# ═══════════════════════════ АДМИН-ПАНЕЛЬ ═══════════════════════════
async def admin_gate(update):
    if not ADMIN_ID:
        await update.message.reply_text("Админ не задан. Узнайте свой ID через /myid и запустите бота с ADMIN_ID.")
        return False
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(" Эта команда только для администратора.")
        return False
    return True

async def send_chunks(update, lines, limit=3500):
    chunk = ""
    for line in lines:
        if len(chunk)+len(line)+1 > limit:
            await update.message.reply_text(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk: await update.message.reply_text(chunk)

async def cmd_players(update, context):
    if not await admin_gate(update): return
    rows = CONN.execute("SELECT * FROM players ORDER BY bounty DESC, level DESC").fetchall()
    if not rows: return await update.message.reply_text("Пока нет зарегистрированных игроков.")
    lines = [f"👥 <b>Игроков: {len(rows)}</b>\n"]
    for i, r in enumerate(rows, 1):
        uname = f" @{esc(r['username'])}" if r["username"] else ""
        lines.append(f"{i}. <b>{esc(r['name'])}</b>{uname}\n<code>{r['user_id']}</code> • ⭐ ур. {r['level']} • {ROLES[r['role']]['title']}\n👑 Награда: {bel(r['bounty'])}")
    lines.append("\nПодробно: /player <ID или @username>")
    await send_chunks(update, lines)

async def cmd_player(update, context):
    if not await admin_gate(update): return
    if not context.args: return await update.message.reply_text("Использование: /player <ID или @username>")
    arg = context.args[0].lstrip("@")
    if arg.isdigit(): row = CONN.execute("SELECT * FROM players WHERE user_id=?", (int(arg),)).fetchone()
    else: row = CONN.execute("SELECT * FROM players WHERE LOWER(username)=LOWER(?)", (arg,)).fetchone()
    if not row: return await update.message.reply_text("Игрок не найден.")
    p = dict(row)
    st = combat_stats(p)
    w = ITEMS.get(p["weapon"]) if p["weapon"] else None
    f = ITEMS.get(p["fruit"]) if p["fruit"] else None
    inv = inv_get(p["user_id"])
    total_battles = p["wins"]+p["losses"]
    winrate = (p["wins"]/total_battles*100) if total_battles>0 else 0
    inv_value = inventory_value(inv)
    rarest = rarest_item(inv)
    rarest_name = ITEMS[rarest]["name"] if rarest else "—"
    age = fmt_age(p["created"])
    last_active = fmt_age(p["last_seen"])
    exp_need_val = exp_need(p["level"])
    exp_percent = int(p["exp"]/exp_need_val*100) if exp_need_val>0 else 0
    nc_status = " ДА" if p.get("no_cooldown") else "нет"
    boss_rows = CONN.execute("SELECT boss, kills FROM boss_kills WHERE user_id=? AND kills>0", (p["user_id"],)).fetchall()
    boss_lines = []
    total_boss_kills = 0
    for r in boss_rows:
        if r["boss"] in BOSSES:
            bs = BOSSES[r["boss"]]
            boss_lines.append(f"  {bs['emoji']} {bs['name']}: <b>{r['kills']}</b> раз(а)")
            total_boss_kills += r["kills"]
    inv_sorted = sorted(inv.items(), key=lambda x: RARITY_ORDER.index(ITEMS.get(x[0], {}).get("rarity", "common")), reverse=True)
    inv_lines = [f"  {ITEMS[k]['name']} ×{q}" for k, q in inv_sorted[:15]] or ["  — пусто —"]
    if len(inv_sorted) > 15: inv_lines.append(f"  <i>...и ещё {len(inv_sorted)-15} предметов</i>")
    uname = f"@{esc(p['username'])}" if p["username"] else "—"
    in_battle = "да ️" if p["user_id"] in BATTLES else "нет"
    text = "\n".join([f"📋 <b>ПОЛНАЯ КАРТОЧКА ИГРОКА</b>", f"━━━━━━━━━━━━━━━━━━━━━━", f" <b>{esc(p['name'])}</b> — {ROLES[p['role']]['title']}", f"🆔 <code>{p['user_id']}</code> • {uname}", "", f"📅 <b>Возраст аккаунта:</b> {age}", f"🕒 <b>Последняя активность:</b> {last_active} назад", f"🌟 <b>Безлимит:</b> {nc_status}", "", f"━━━ <b>📈 ПРОГРЕСС</b> ━━━", f"⭐ Уровень: <b>{p['level']}</b> ({p['exp']}/{exp_need_val} оп. • {exp_percent}%)", f"❤️ HP: <b>{p['hp']}/{p['max_hp']}</b> {bar(p['hp'], p['max_hp'])}", "", f"━━━ <b>️ ХАРАКТЕРИСТИКИ</b> ━━━", f"⚔️ Атака: <b>{p['atk']}</b> (итого {st['atk']})", f"🛡️ Защита: <b>{p['defense']}</b> (итого {st['defense']})", f" Хаки: <b>{p['haki']}</b> (итого {st['haki']})", "", f"━━━ <b>💰 ЭКОНОМИКА</b> ━━━", f"💰 Белли: <b>{bel(p['belly'])}</b>", f"👑 Награда за голову: <b>{bel(p['bounty'])}</b>", f" Стоимость инвентаря: <b>{bel(inv_value)}</b>", f"🏆 Редчайший предмет: {rarest_name}", "", f"━━━ <b>🎒 ЭКИПИРОВКА</b> ━━", f"️ Оружие: {w['name'] if w else '—'}", f"🍈 Фрукт: {f['name'] if f else '—'}", "", f"━━━ <b>️ БОЕВАЯ СТАТИСТИКА</b> ━━━", f"🏆 Побед: <b>{p['wins']}</b>", f"💀 Поражений: <b>{p['losses']}</b>", f"📊 Winrate: <b>{winrate:.1f}%</b> {bar(int(winrate), 100, 10)}", f"👑 Всего боссов убито: <b>{total_boss_kills}</b>", f"📦 Открыто сундуков: <b>{p['pulls']}</b>", f"⚔️ Сейчас в бою: {in_battle}", "", f"━━━ <b>👑 УБИТЫЕ БОССЫ</b> ━━━", *(boss_lines if boss_lines else ["  <i>пока ни одного</i>"]), "", f"━━━ <b> ИНВЕНТАРЬ</b> ({len(inv)} предметов) ━━━", *inv_lines])
    await send_chunks(update, text.split("\n"))

async def cmd_top(update, context):
    if not await admin_gate(update): return
    categories = {"bounty": ("👑 Награда за голову", "bounty", lambda r: bel(r["bounty"])), "level": ("⭐ Уровень", "level", lambda r: f"ур. {r['level']}"), "belly": ("💰 Белли", "belly", lambda r: bel(r["belly"])), "wins": ("🏆 Побед", "wins", lambda r: f"{r['wins']} побед"), "bosses": (" Боссов убито", "boss_kills", lambda r: f"{r['boss_kills']} боссов"), "pulls": ("📦 Сундуков открыто", "pulls", lambda r: f"{r['pulls']}"), "losses": ("💀 Поражений", "losses", lambda r: f"{r['losses']}")}
    if not context.args or context.args[0].lower() not in categories:
        lines = [" <b>Рейтинг игроков</b>", "", "<b>Доступные категории:</b>"]
        for key, (title, _, _) in categories.items():
            lines.append(f"  • <code>/top {key}</code> — {title}")
        return await update.message.reply_text("\n".join(lines))
    cat = context.args[0].lower()
    title, field, formatter = categories[cat]
    rows = CONN.execute(f"SELECT * FROM players ORDER BY {field} DESC LIMIT 15").fetchall()
    if not rows: return await update.message.reply_text("Пока нет игроков.")
    lines = [f"🏆 <b>{title} — ТОП-15</b>", ""]
    medals = {1: "", 2: "🥈", 3: "🥉"}
    for i, r in enumerate(rows, 1):
        medal = medals.get(i, f"{i}.")
        uname = f" @{esc(r['username'])}" if r["username"] else ""
        lines.append(f"{medal} <b>{esc(r['name'])}</b>{uname}")
        lines.append(f"   {ROLES[r['role']]['title']} •  ур. {r['level']} • {formatter(r)}")
    await send_chunks(update, lines)

async def cmd_stats(update, context):
    if not await admin_gate(update): return
    total_players = CONN.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    if total_players == 0: return await update.message.reply_text("Пока нет зарегистрированных игроков.")
    sums = CONN.execute("SELECT SUM(belly), SUM(bounty), SUM(wins), SUM(losses), SUM(boss_kills), SUM(pulls) FROM players").fetchone()
    total_belly, total_bounty, total_wins, total_losses, total_boss_kills, total_pulls = [s or 0 for s in sums]
    richest = CONN.execute("SELECT * FROM players ORDER BY belly DESC LIMIT 1").fetchone()
    strongest = CONN.execute("SELECT * FROM players ORDER BY level DESC LIMIT 1").fetchone()
    most_bountiful = CONN.execute("SELECT * FROM players ORDER BY bounty DESC LIMIT 1").fetchone()
    most_boss_kills = CONN.execute("SELECT * FROM players ORDER BY boss_kills DESC LIMIT 1").fetchone()
    active_battles = len(BATTLES)
    role_stats = CONN.execute("SELECT role, COUNT(*) FROM players GROUP BY role").fetchall()
    role_lines = [f"  {ROLES[r['role']]['title']}: <b>{r[1]}</b>" for r in role_stats]
    avg_level = CONN.execute("SELECT AVG(level) FROM players").fetchone()[0] or 0
    text = "\n".join([" <b>ОБЩАЯ СТАТИСТИКА БОТА</b>", "━━━━━━━━━━━━━━━━━━━━━━", "", f"👥 <b>Всего игроков:</b> {total_players}", f"⚔️ <b>Активных боёв:</b> {active_battles}", f"⭐ <b>Средний уровень:</b> {avg_level:.1f}", "", "━━━ <b> ЭКОНОМИКА</b> ━━━", f"💰 Всего Белли в обороте: <b>{bel(total_belly)}</b>", f" Суммарная награда: <b>{bel(total_bounty)}</b>", f" Всего открыто сундуков: <b>{n(total_pulls)}</b>", "", "━━━ <b>⚔️ БОИ</b> ━━", f"🏆 Всего побед: <b>{n(total_wins)}</b>", f"💀 Всего поражений: <b>{n(total_losses)}</b>", f" Всего убито боссов: <b>{n(total_boss_kills)}</b>", "", "━━━ <b>🏆 ЧЕМПИОНЫ</b> ━━━", f"💰 <b>Самый богатый:</b> {esc(richest['name'])} ({bel(richest['belly'])})", f"⭐ <b>Сильнейший:</b> {esc(strongest['name'])} (ур. {strongest['level']})", f" <b>Самая большая награда:</b> {esc(most_bountiful['name'])} ({bel(most_bountiful['bounty'])})", f"🐉 <b>Охотник на боссов:</b> {esc(most_boss_kills['name'])} ({most_boss_kills['boss_kills']} боссов)", "", "━━━ <b>👥 РАСПРЕДЕЛЕНИЕ ПО РОЛЯМ</b> ━━", *role_lines])
    await update.message.reply_text(text)

def resolve_targets(arg):
    a = arg.lstrip("@")
    if a.lower() in ("all", "все"): rows = CONN.execute("SELECT user_id, name FROM players").fetchall()
    elif a.isdigit(): rows = CONN.execute("SELECT user_id, name FROM players WHERE user_id=?", (int(a),)).fetchall()
    else: rows = CONN.execute("SELECT user_id, name FROM players WHERE LOWER(username)=LOWER(?)", (a,)).fetchall()
    return [dict(r) for r in rows]

def who(targets):
    return esc(targets[0]["name"]) if len(targets)==1 else f"всем игрокам ({len(targets)})"

async def notify_player(context, uid, text):
    try: await context.bot.send_message(uid, text)
    except TelegramError: pass

async def cmd_give_gold(update, context):
    if not await admin_gate(update): return
    if len(context.args) != 2: return await update.message.reply_text("Использование: /give_gold <ID|@username|all> <кол-во>")
    try: amount = int(context.args[1])
    except ValueError: return await update.message.reply_text("Количество должно быть числом.")
    targets = resolve_targets(context.args[0])
    if not targets: return await update.message.reply_text("Игрок не найден.")
    for t in targets: add_belly(t["user_id"], amount)
    text = f"✅ {who(targets)}: {'+' if amount>=0 else ''}{bel(amount)}"
    if len(targets)==1: text += f". Теперь у него {bel(get_player(targets[0]['user_id'])['belly'])}."
    await update.message.reply_text(text)
    if amount > 0 and len(targets)==1: await notify_player(context, targets[0]["user_id"], f"🎁 Администратор подарил вам <b>{bel(amount)}</b>! 💰")

async def cmd_items(update, context):
    if not await admin_gate(update): return
    titles = {"food": "🍽️ Еда", "weapon": "🗡️ Оружие", "fruit": "🍈 Фрукты", "treasure": " Сокровища"}
    lines = ["📋 <b>Ключи предметов</b> (для /give_item)\n"]
    for kind, title in titles.items():
        lines.append(f"<b>{title}</b>")
        for k in keys_of(kind): lines.append(f"  <code>{k}</code> — {ITEMS[k]['name']} ({RARITY[ITEMS[k]['rarity']]})")
    lines.append("")
    lines.append("<b> Персонажи</b> (для /give_char)")
    for k in char_keys_sorted(CHARACTERS): lines.append(f"  <code>{k}</code> — {CHARACTERS[k]['emoji']} {CHARACTERS[k]['name']} ({RARITY[CHARACTERS[k]['rarity']]})")
    await send_chunks(update, lines)

async def cmd_give_item(update, context):
    if not await admin_gate(update): return
    if len(context.args) < 2: return await update.message.reply_text("Использование: /give_item <ID|@username|all> <ключ> [кол-во]\nСписок ключей: /items")
    key = context.args[1].lower()
    if key not in ITEMS: return await update.message.reply_text("Нет такого предмета. Список ключей: /items")
    qty = 1
    if len(context.args) > 2:
        try: qty = int(context.args[2])
        except ValueError: return await update.message.reply_text("Количество должно быть числом.")
        if not 1<=qty<=999: return await update.message.reply_text("Количество: от 1 до 999.")
    targets = resolve_targets(context.args[0])
    if not targets: return await update.message.reply_text("Игрок не найден.")
    it = ITEMS[key]
    stackable = it["kind"] in ("food", "treasure")
    detail = ""
    for t in targets:
        if stackable: inv_add(t["user_id"], key, qty)
        else: detail = give_item(t["user_id"], key)
    label = f"{it['name']}" + (f" ×{qty}" if stackable and qty>1 else "")
    text = f"✅ Выдано {who(targets)}: {label}"
    if len(targets)==1 and not stackable: text += f"\n{detail}"
    await update.message.reply_text(text)
    if len(targets)==1: await notify_player(context, targets[0]["user_id"], f" Администратор подарил вам: <b>{label}</b>!")

async def cmd_give_char(update, context):
    if not await admin_gate(update): return
    if len(context.args) < 2: return await update.message.reply_text("Использование: /give_char <ID|@username|all> <ключ>\nСписок ключей: /items")
    key = context.args[1].lower()
    if key not in CHARACTERS: return await update.message.reply_text("Нет такого персонажа. Список ключей: /items")
    targets = resolve_targets(context.args[0])
    if not targets: return await update.message.reply_text("Игрок не найден.")
    c = CHARACTERS[key]
    for t in targets:
        r = char_add(t["user_id"], key)
        sq = squad_get(t["user_id"])
        if r["new"] and len(sq)<SQUAD_SIZE: squad_set(t["user_id"], sq+[key])
    await update.message.reply_text(f"✅ Выдан персонаж {who(targets)}: {c['emoji']} {c['name']} ({RARITY[c['rarity']]})")
    if len(targets)==1: await notify_player(context, targets[0]["user_id"], f"🎴 Администратор подарил вам персонажа: <b>{c['emoji']} {c['name']}</b>!")

async def cmd_give_exp(update, context):
    if not await admin_gate(update): return
    if len(context.args) != 2: return await update.message.reply_text("Использование: /give_exp <ID|@username|all> <кол-во>")
    try: amount = int(context.args[1])
    except ValueError: return await update.message.reply_text("Количество должно быть числом.")
    if amount <= 0: return await update.message.reply_text("Количество должно быть больше нуля.")
    targets = resolve_targets(context.args[0])
    if not targets: return await update.message.reply_text("Игрок не найден.")
    notes = []
    for t in targets: notes = grant_exp(t["user_id"], amount)
    text = f"✅ {who(targets)}: ✨ +{n(amount)} опыта"
    if len(targets)==1 and notes: text += "\n" + "\n".join(notes)
    await update.message.reply_text(text)
    if len(targets)==1: await notify_player(context, targets[0]["user_id"], f"🎁 Администратор начислил вам <b>{n(amount)}</b> опыта! ✨")

async def cmd_heal(update, context):
    if not await admin_gate(update): return
    if not context.args: return await update.message.reply_text("Использование: /heal <ID|@username|all>")
    targets = resolve_targets(context.args[0])
    if not targets: return await update.message.reply_text("Игрок не найден.")
    for t in targets:
        p = get_player(t["user_id"])
        update_player(t["user_id"], hp=p["max_hp"])
        if t["user_id"] in BATTLES: BATTLES[t["user_id"]]["p_hp"] = BATTLES[t["user_id"]]["p_max"]
    await update.message.reply_text(f"✅ {who(targets)}: ❤️ здоровье восстановлено до максимума")
    if len(targets)==1: await notify_player(context, targets[0]["user_id"], "💖 Администратор полностью вылечил вас!")

async def cmd_reset_gacha(update, context):
    if not await admin_gate(update): return
    if not context.args: return await update.message.reply_text("Использование: /reset_gacha <ID|@username|all> [count]\nБез count — сбрасывается только таймер.\nС count — дополнительно обнуляется счётчик открытий.")
    targets = resolve_targets(context.args[0])
    if not targets: return await update.message.reply_text("Игрок не найден.")
    with_count = len(context.args)>1 and context.args[1].lower() in ("count", "счетчик", "счётчик", "pulls")
    sql = "UPDATE players SET last_gacha=0, pulls=0 WHERE user_id=?" if with_count else "UPDATE players SET last_gacha=0 WHERE user_id=?"
    CONN.executemany(sql, [(t["user_id"],) for t in targets])
    CONN.executemany("UPDATE summon_state SET last_free=0 WHERE user_id=?", [(t["user_id"],) for t in targets])
    CONN.commit()
    text = f"✅ {who(targets)}:  таймер сундука сброшен"
    if with_count: text += ", счётчик открытий обнулён"
    await update.message.reply_text(text)
    if len(targets)==1: await notify_player(context, targets[0]["user_id"], "🎁 Таймер сундука сброшен — открывайте его: /gacha")

async def cmd_reset_boss(update, context):
    if not await admin_gate(update): return
    if not context.args: return await update.message.reply_text("Использование: /reset_boss <ID|@username|all>")
    targets = resolve_targets(context.args[0])
    if not targets: return await update.message.reply_text("Игрок не найден.")
    CONN.executemany("UPDATE boss_kills SET last_kill=0 WHERE user_id=?", [(t["user_id"],) for t in targets])
    CONN.commit()
    await update.message.reply_text(f"✅ {who(targets)}: ⏳ таймеры боссов сброшены — все боссы доступны!")
    if len(targets)==1: await notify_player(context, targets[0]["user_id"], "👑 Администратор сделал всех боссов доступными для вас!")

async def cmd_nocooldown(update, context):
    if not await admin_gate(update): return
    if len(context.args) != 2: return await update.message.reply_text("Использование: /nocooldown <ID|@username|all> <1|0>\n1 — включить БЕЗЛИМИТ\n0 — вернуть обычные ограничения")
    target_arg = context.args[0]
    try:
        state = int(context.args[1])
        if state not in (0, 1): raise ValueError
    except ValueError: return await update.message.reply_text("Состояние должно быть 1 (вкл) или 0 (выкл).")
    targets = resolve_targets(target_arg)
    if not targets: return await update.message.reply_text("Игрок не найден.")
    CONN.executemany("UPDATE players SET no_cooldown=? WHERE user_id=?", [(state, t["user_id"]) for t in targets])
    CONN.commit()
    status = "✅ БЕЗЛИМИТ ВКЛЮЧЕН" if state==1 else "🔒 Обычные ограничения возвращены"
    await update.message.reply_text(f"{status} для: {who(targets)}")
    if len(targets)==1:
        if state==1: msg = "🌟 <b>Режим бога активирован!</b>\nАдминистратор снял с вас все временные ограничения."
        else: msg = "⚖️ Администратор вернул стандартные временные ограничения."
        await notify_player(context, targets[0]["user_id"], msg)

async def cmd_reset_player(update, context):
    if not await admin_gate(update): return
    if not context.args: return await update.message.reply_text("Использование: /reset_player <ID|@username>")
    arg = context.args[0].lstrip("@")
    if arg.isdigit(): row = CONN.execute("SELECT * FROM players WHERE user_id=?", (int(arg),)).fetchone()
    else: row = CONN.execute("SELECT * FROM players WHERE LOWER(username)=LOWER(?)", (arg,)).fetchone()
    if not row: return await update.message.reply_text("Игрок не найден.")
    uid = row["user_id"]
    player_data = dict(row)
    inventory_data = inv_get(uid)
    backup_data = {"player": player_data, "inventory": inventory_data, "time": time.time(), "characters": chars_get(uid), "summon": summon_state(uid)}
    CONN.execute("INSERT OR REPLACE INTO backups(user_id, data_json, backup_time) VALUES(?,?,?)", (uid, json.dumps(backup_data), time.time()))
    CONN.commit()
    CONN.execute("DELETE FROM players WHERE user_id=?", (uid,))
    CONN.execute("DELETE FROM inventory WHERE user_id=?", (uid,))
    CONN.execute("DELETE FROM boss_kills WHERE user_id=?", (uid,))
    CONN.execute("DELETE FROM characters WHERE user_id=?", (uid,))
    CONN.execute("DELETE FROM summon_state WHERE user_id=?", (uid,))
    CONN.commit()
    BATTLES.pop(uid, None)
    await update.message.reply_text(f"☠️ <b>Аккаунт {esc(row['name'])} полностью удален!</b>\nБекап создан. Для восстановления: /restore_player {uid}")
    try: await context.bot.send_message(uid, "☠️ Ваш аккаунт был сброшен администратором. Используйте /start, чтобы начать заново.")
    except TelegramError: pass

async def cmd_restore_player(update, context):
    if not await admin_gate(update): return
    if not context.args: return await update.message.reply_text("Использование: /restore_player <ID>")
    try: uid = int(context.args[0])
    except ValueError: return await update.message.reply_text("ID должен быть числом.")
    backup_row = CONN.execute("SELECT data_json FROM backups WHERE user_id=?", (uid,)).fetchone()
    if not backup_row: return await update.message.reply_text("Бекап для этого игрока не найден.")
    backup_data = json.loads(backup_row["data_json"])
    p_data = backup_data["player"]
    inv_data = backup_data["inventory"]
    columns = ", ".join(p_data.keys())
    placeholders = ", ".join(["?"]*len(p_data))
    values = list(p_data.values())
    CONN.execute(f"INSERT INTO players({columns}) VALUES({placeholders})", values)
    for item, qty in inv_data.items(): CONN.execute("INSERT INTO inventory(user_id, item, qty) VALUES(?,?,?)", (uid, item, qty))
    for ck, stars in backup_data.get("characters", {}).items(): CONN.execute("INSERT OR REPLACE INTO characters(user_id, char, stars, got) VALUES(?,?,?,?)", (uid, ck, stars, time.time()))
    sm = backup_data.get("summon")
    if sm:
        cols = ", ".join(sm.keys())
        CONN.execute(f"INSERT OR REPLACE INTO summon_state({cols}) VALUES({', '.join(['?']*len(sm))})", list(sm.values()))
    CONN.commit()
    await update.message.reply_text(f"✅ <b>Аккаунт игрока {esc(p_data['name'])} восстановлен!</b>")
    try: await context.bot.send_message(uid, "✅ Ваш аккаунт был восстановлен администратором из резервной копии.")
    except TelegramError: pass

async def _send_one(bot, uid, text, src_chat, src_msg):
    if src_msg is not None: await bot.copy_message(chat_id=uid, from_chat_id=src_chat, message_id=src_msg)
    else: await bot.send_message(uid, text)

async def _run_broadcast(context, admin_chat, ids, text, src_chat, src_msg):
    delivered, blocked, failed = 0, 0, 0
    failed_ids = []
    try:
        for uid in ids:
            try:
                try: await _send_one(context.bot, uid, text, src_chat, src_msg)
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after+1)
                    await _send_one(context.bot, uid, text, src_chat, src_msg)
                delivered += 1
            except Forbidden: blocked += 1
            except Exception as e:
                failed += 1
                failed_ids.append(uid)
                log.warning("Рассылка: не удалось отправить %s: %s", uid, e)
            await asyncio.sleep(0.05)
    finally:
        context.bot_data["broadcast_running"] = False
        total = len(ids)
        lines = ["📣 <b>Рассылка завершена</b>", "", f"👥 Всего игроков: <b>{total}</b>", f"✅ Получили сообщение: <b>{delivered}</b>", f"❌ Не удалось доставить: <b>{blocked+failed}</b>"]
        if blocked: lines.append(f"   🚫 заблокировали бота: {blocked}")
        if failed:
            lines.append(f"   ⚠️ прочие ошибки: {failed}")
            lines.append("   ID: " + ", ".join(str(i) for i in failed_ids[:20]) + (" …" if len(failed_ids)>20 else ""))
        try: await context.bot.send_message(admin_chat, "\n".join(lines))
        except TelegramError as e: log.error("Не удалось отправить отчёт о рассылке: %s", e)

async def cmd_broadcast(update, context):
    if not await admin_gate(update): return
    msg = update.message
    reply = msg.reply_to_message
    text = None
    if reply is None:
        parts = (msg.text_html or "").split(maxsplit=1)
        text = parts[1].strip() if len(parts)>1 else ""
    if not text: return await msg.reply_text("Использование:\n/broadcast <текст>\nили ответьте командой /broadcast на сообщение.")
    if context.bot_data.get("broadcast_running"): return await msg.reply_text(" Предыдущая рассылка ещё идёт.")
    ids = [r["user_id"] for r in CONN.execute("SELECT user_id FROM players").fetchall()]
    if not ids: return await msg.reply_text("Пока нет зарегистрированных игроков.")
    context.bot_data["broadcast_running"] = True
    await msg.reply_text(f"📣 Рассылка запущена: {len(ids)} игроков.")
    context.application.create_task(_run_broadcast(context, update.effective_chat.id, ids, text, msg.chat_id, reply.message_id if reply else None))

async def cmd_msg(update, context):
    if not await admin_gate(update): return
    msg = update.message
    reply = msg.reply_to_message
    parts = (msg.text_html or "").split(maxsplit=2)
    usage = ("Использование:\n/msg <ID|@username> <текст>\nНесколько получателей — через запятую: /msg 123,@vasya текст\n"
             "Или ответьте командой /msg <кому> на любое сообщение, чтобы отправить его копию.\n<i>Всем сразу — это /broadcast.</i>")
    if len(parts)<2 or (reply is None and len(parts)<3): return await msg.reply_text(usage)
    text = None if reply else parts[2].strip()
    targets, missing = {}, []
    for raw in parts[1].split(","):
        raw = raw.strip()
        if not raw: continue
        if raw.lstrip("@").lower() in ("all", "все"): return await msg.reply_text("Для рассылки всем используйте /broadcast. /msg — только для отдельных игроков.")
        found = resolve_targets(raw)
        if not found: missing.append(raw)
        for t in found: targets[t["user_id"]] = t
    if not targets: return await msg.reply_text("🔍 Игрок не найден: " + ", ".join(esc(m) for m in missing))
    body = None if reply else f"📩 <b>Сообщение от администратора:</b>\n{text}"
    ok, bad = [], []
    for uid, t in targets.items():
        try:
            try: await _send_one(context.bot, uid, body, msg.chat_id, reply.message_id if reply else None)
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after+1)
                await _send_one(context.bot, uid, body, msg.chat_id, reply.message_id if reply else None)
            ok.append(esc(t["name"]))
        except Forbidden: bad.append(f"{esc(t['name'])} (заблокировал бота)")
        except TelegramError as e: bad.append(f"{esc(t['name'])} ({esc(e)})")
        await asyncio.sleep(0.05)
    lines = []
    if ok: lines.append("✅ Отправлено: " + ", ".join(ok))
    if bad: lines.append("❌ Не доставлено: " + ", ".join(bad))
    if missing: lines.append("🔍 Не найдены: " + ", ".join(esc(m) for m in missing))
    await msg.reply_text("\n".join(lines))

# ══════════════════════════ ОБРАБОТЧИКИ ═════════════════════════
async def track_user(update, context):
    u = update.effective_user
    if not u or u.is_bot: return
    CONN.execute("UPDATE players SET name=?, username=?, last_seen=? WHERE user_id=?", (u.full_name, u.username, time.time(), u.id))
    CONN.commit()

async def on_button(update, context):
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id
    if data.startswith("lock:"):
        boss_key = data[5:]
        bs = BOSSES.get(boss_key)
        if bs: return await q.answer(f"🔒 Босс откроется на уровне {bs['level']}", show_alert=True)
        return await q.answer("Босс не найден", show_alert=True)
    if data.startswith("cd:"):
        key = data[3:]
        _, last = boss_record(uid, key)
        left = last + BOSS_COOLDOWN - time.time()
        return await q.answer(f"⏳ Босс восстановится через {fmt_duration(left)}", show_alert=True)
    if data == "b:skill":
        b = BATTLES.get(uid)
        if b and b["skill_cd"] > 0: return await q.answer(f"⏳ Навык перезаряжается: ещё {b['skill_cd']} р.", show_alert=True)
    if data[:3] == "b:c" and data[3:].isdigit():
        b = BATTLES.get(uid)
        sq = b.get("squad", []) if b else []
        i = int(data[3:])
        if i < len(sq) and sq[i]["cd"] > 0: return await q.answer(f"⏳ {sq[i]['name']} перезаряжается: ещё {sq[i]['cd']} р.", show_alert=True)
    if uid in SUMMONING and data.startswith("sm:"): return await q.answer("🎴 Призыв уже идёт…")
    await q.answer()
    if data.startswith("role:"): return await choose_role(update, context, data[5:])
    if data == "menu": return await show_menu(update, context)
    if data == "profile": return await show_profile(update, context)
    if data == "gacha": return await show_gacha(update, context)
    if data == "adv": return await show_adventure(update, context)
    if data == "boss": return await show_bosses(update, context)
    if data.startswith("boss:"): return await show_boss_card(update, context, data[5:])
    if data.startswith("fight:"): return await fight_boss(update, context, data[6:])
    if data == "upgrade": return await show_upgrade(update, context)
    if data.startswith("up:"): return await do_upgrade(update, context, data[3:])
    if data == "inv": return await show_inventory(update, context)
    if data.startswith("use:"): return await use_item(update, context, data[4:])
    if data == "sell_all": return await sell_all(update, context)
    if data == "summon": return await show_summon(update, context)
    if data.startswith("sm:"): return await run_summon(update, context, data[3:])
    if data == "chars": return await show_collection(update, context)
    if data.startswith("cv:"): return await show_char_card(update, context, data[3:])
    if data == "squad": return await show_squad(update, context)
    if data.startswith("sq:"): return await squad_action(update, context, data)
    if data.startswith("b:"): return await on_battle_action(update, context, data[2:])

async def on_error(update, context):
    log.error("Ошибка при обработке обновления", exc_info=context.error)

async def post_init(app):
    await app.bot.set_my_commands([BotCommand("start", "‍☠️ Меню / начать игру"), BotCommand("profile", "👤 Профиль"), BotCommand("gacha", "🎁 Сундук с сокровищами"), BotCommand("adventure", "🌊 Приключение"), BotCommand("boss", "👑 Боссы"), BotCommand("summon", "🎴 Призыв персонажей"), BotCommand("squad", "👥 Отряд"), BotCommand("characters", "📚 Коллекция персонажей"), BotCommand("upgrade", "📈 Улучшения"), BotCommand("inventory", " Инвентарь"), BotCommand("help", " Помощь"), BotCommand("myid", "🆔 Мой ID")])

# ─ BACKUP-BEGIN ──────────────────────────────────────────────────────────
TG_API = os.environ.get("TG_API", "https://api.telegram.org")
BACKUP_TO_TELEGRAM = os.environ.get("TG_BACKUP", "1" if (os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_URL")) else "0") == "1"
try:
    BACKUP_INTERVAL = max(5, int(os.environ.get("BACKUP_INTERVAL_MIN", "30"))) * 60
except ValueError:
    BACKUP_INTERVAL = 30 * 60
BACKUP_FILENAME = "one_piece_game_backup.db"
BACKUP_STATE = {"msg_id": None, "skip_first": False, "blocked": False}

def _tg(method, fields=None, file=None, timeout=60):
    url = f"{TG_API}/bot{TOKEN}/{method}"
    fields = fields or {}
    if file:
        part, fname, data = file
        boundary = "----opbackup" + os.urandom(8).hex()
        chunks = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8") for k, v in fields.items()]
        chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{part}"; filename="{fname}"\r\n' "Content-Type: application/octet-stream\r\n\r\n").encode() + data + b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        body, headers = b"".join(chunks), {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    else:
        body, headers = urlencode(fields).encode(), {}
    with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=timeout) as r:
        res = json.loads(r.read().decode("utf-8"))
        if not res.get("ok"): raise RuntimeError(f"{method}: {res}")
        return res["result"]

def _db_info(path):
    try:
        if not Path(path).exists(): return (0, 0)
        c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try: return tuple(c.execute("SELECT COALESCE(MAX(last_seen),0), COUNT(*) FROM players").fetchone())
        finally: c.close()
    except Exception: return (0, 0)

def tg_restore_db():
    if not (BACKUP_TO_TELEGRAM and ADMIN_ID and TOKEN): return
    try:
        pin = _tg("getChat", {"chat_id": ADMIN_ID}, timeout=30).get("pinned_message") or {}
        doc = pin.get("document") or {}
        if doc.get("file_name") != BACKUP_FILENAME:
            log.info("💾 Бэкапа в Telegram пока нет — первый будет создан после запуска")
            return
        BACKUP_STATE["msg_id"] = pin["message_id"]
        info = _tg("getFile", {"file_id": doc["file_id"]}, timeout=30)
        with urlopen(f"{TG_API}/file/bot{TOKEN}/{info['file_path']}", timeout=60) as r:
            data = r.read()
        db = Path(DB_PATH)
        db.parent.mkdir(parents=True, exist_ok=True)
        tmp = db.with_name(db.name + ".restore")
        tmp.write_bytes(data)
        remote, local = _db_info(tmp), _db_info(db)
        if remote[1] > 0 and (local[1] == 0 or remote[0] > local[0]):
            for suffix in ("-wal", "-shm"): Path(str(db) + suffix).unlink(missing_ok=True)
            os.replace(tmp, db)
            BACKUP_STATE["skip_first"] = True
            log.info("💾 База восстановлена из бэкапа в Telegram (игроков: %s)", remote[1])
        else:
            tmp.unlink(missing_ok=True)
            BACKUP_STATE["skip_first"] = remote == local
            log.info("💾 Локальная база не старее бэкапа в Telegram — оставляю её")
    except Exception:
        BACKUP_STATE["blocked"] = True
        log.exception("💾 Не удалось проверить бэкап в Telegram (напишите боту /start с админ-аккаунта). " "Бэкапы в этом запуске отключены")

def _tg_backup_once(last_hash):
    db = Path(DB_PATH)
    tmp = db.with_name(db.name + ".bak")
    src, dst = sqlite3.connect(db), sqlite3.connect(tmp)
    try: src.backup(dst)
    finally:
        dst.close()
        src.close()
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    h = hashlib.md5(data).hexdigest()
    if h == last_hash or (last_hash is None and BACKUP_STATE["skip_first"]): return h
    caption = f" Бэкап базы • {datetime.now():%d.%m.%Y %H:%M}"
    mid = BACKUP_STATE["msg_id"]
    if mid:
        try:
            media = json.dumps({"type": "document", "media": "attach://file", "caption": caption})
            _tg("editMessageMedia", {"chat_id": ADMIN_ID, "message_id": mid, "media": media}, file=("file", BACKUP_FILENAME, data))
            return h
        except Exception as e:
            log.warning("💾 Не удалось обновить закреплённый бэкап (%s) — отправляю новый", e)
    msg = _tg("sendDocument", {"chat_id": ADMIN_ID, "caption": caption, "disable_notification": "true"}, file=("document", BACKUP_FILENAME, data))
    _tg("pinChatMessage", {"chat_id": ADMIN_ID, "message_id": msg["message_id"], "disable_notification": "true"})
    BACKUP_STATE["msg_id"] = msg["message_id"]
    log.info("💾 Бэкап базы отправлен в Telegram")
    return h

def tg_backup_loop():
    last_hash = None
    time.sleep(45)
    while True:
        if not BACKUP_STATE["blocked"]:
            try:
                if _db_info(DB_PATH)[1] > 0: last_hash = _tg_backup_once(last_hash)
            except Exception: log.exception("💾 Ошибка бэкапа в Telegram")
        time.sleep(BACKUP_INTERVAL)

def _keepalive_loop(url):
    time.sleep(120)
    while True:
        try: urlopen(url, timeout=30).read()
        except Exception as e: log.warning("keep-alive: %s", e)
        time.sleep(600)

# ── BACKUP-END ────────────────────────────────────────────────────────────
def start_web_server():
    port = os.environ.get("PORT")
    if not port: return
    class Health(BaseHTTPRequestHandler):
        def _reply(self, with_body):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "2")
            self.end_headers()
            if with_body: self.wfile.write(b"OK")
        def do_GET(self): self._reply(True)
        def do_HEAD(self): self._reply(False)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("🌐 Веб-сервер для Render запущен на порту %s", port)
    ext_url = os.environ.get("RENDER_EXTERNAL_URL")
    if ext_url and os.environ.get("KEEPALIVE", "1") == "1":
        threading.Thread(target=_keepalive_loop, args=(ext_url,), daemon=True).start()
        log.info("⏰ Keep-alive включён: %s", ext_url)

# ═══════════════════════════ ЗАПУСК ═══════════════════════════
def main():
    if not TOKEN:
        raise SystemExit("Задайте токен: export BOT_TOKEN='ваш_токен'")
    
    start_web_server()
    tg_restore_db()
    init_db()
    
    if BACKUP_TO_TELEGRAM and ADMIN_ID:
        threading.Thread(target=tg_backup_loop, daemon=True).start()
        log.info("💾 Автобэкап базы в Telegram включён (каждые %s мин.)", BACKUP_INTERVAL // 60)

    try: ensure_media()
    except Exception: log.exception("Не удалось подготовить медиафайлы")

    app = ApplicationBuilder().token(TOKEN).defaults(Defaults(parse_mode=ParseMode.HTML)).post_init(post_init).build()
    app.add_handler(TypeHandler(Update, track_user), group=-1)
    pv = filters.ChatType.PRIVATE
    for name, fn in [("start", cmd_start), ("help", show_help), ("profile", show_profile), ("gacha", show_gacha), ("adventure", show_adventure), ("boss", show_bosses), ("upgrade", show_upgrade), ("summon", show_summon), ("characters", show_collection), ("squad", show_squad), ("give_char", cmd_give_char), ("msg", cmd_msg), ("inventory", show_inventory), ("myid", cmd_myid), ("players", cmd_players), ("player", cmd_player), ("top", cmd_top), ("stats", cmd_stats), ("give_gold", cmd_give_gold), ("broadcast", cmd_broadcast), ("items", cmd_items), ("give_item", cmd_give_item), ("give_exp", cmd_give_exp), ("heal", cmd_heal), ("reset_gacha", cmd_reset_gacha), ("reset_boss", cmd_reset_boss), ("nocooldown", cmd_nocooldown), ("reset_player", cmd_reset_player), ("restore_player", cmd_restore_player)]:
        app.add_handler(CommandHandler(name, fn, filters=pv))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_error_handler(on_error)

    # АВТОМАТИЧЕСКОЕ ОПРЕДЕЛЕНИЕ WEBHOOK ДЛЯ RENDER
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    
    if external_url:
        webhook_url = f"{external_url}/webhook"
        log.info(f"🔗 Настройка Webhook: {webhook_url}")
        
        app.bot.delete_webhook(drop_pending_updates=True)
        app.bot.set_webhook(webhook_url)
        
        updater = app.updater
        updater.start_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 8080)), url_path="webhook")
        log.info("🚀 Бот запущен через Webhook.")
    else:
        log.info(" Запуск через Polling (локальный режим).")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()