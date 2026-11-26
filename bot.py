import asyncio
import csv
import io
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import config
from force_sub import ForceSubManager

# ================= إعدادات التسجيل =================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================= تحميل التوكن والمالك من config.py أو متغيرات البيئة =================
RAW_TOKEN = os.getenv("BOT_TOKEN") or getattr(config, "BOT_TOKEN", "")
OWNER_ID = int(os.getenv("ADMIN_ID") or getattr(config, "OWNER_ID", 433161036))
GLOBAL_CHANNELS = getattr(
    config,
    "MANDATORY_GLOBAL_CHANNELS",
    [
        {"username": "basemmusaoa", "title": "باسم مساوى", "enabled": True},
        {"username": "IssaAllaeth", "title": "عيسى الليث", "enabled": True},
    ],
)

DATA_DIR = "bots_storage"
os.makedirs(DATA_DIR, exist_ok=True)
REGISTRY_FILE = os.path.join(DATA_DIR, "factory_registry.json")

# ================= بنك الأسئلة والألعاب =================
QUESTIONS_BANK = [
    {"q": "ما هي عاصمة اليمن؟", "options": ["صنعاء", "عدن", "تعز", "مأرب"], "ans": "صنعاء"},
    {"q": "ما هو الكوكب الأكبر في المجموعة الشمسية؟", "options": ["زحل", "المشتري", "الأرض", "المريخ"], "ans": "المشتري"},
    {"q": "لغز: شيء يمشي بلا أرجل ويبكي بلا عيون، ما هو؟", "options": ["السحاب", "النهر", "الساعة", "الريح"], "ans": "السحاب"},
    {"q": "ما هو أسرع حيوان بري في العالم؟", "options": ["الفهد", "الأسد", "الغزال", "الحصان"], "ans": "الفهد"},
    {"q": "لغز: له أوراق وما هو بنبات، وله جلد وما هو بحيوان، ما هو؟", "options": ["الكتاب", "الدفتر", "البصل", "الملف"], "ans": "الكتاب"},
    {"q": "كم عدد ألوان قوس المطر الأساسية؟", "options": ["5", "6", "7", "8"], "ans": "7"},
]

WORD_BANK = [
    {"word": "قلم", "hint": "أداة للكتابة وتدوين الأفكار"},
    {"word": "مطر", "hint": "غيث ينزل من السماء فتخضر الأرض"},
    {"word": "ساعة", "hint": "تخبرك بالوقت ولها عقارب أو أرقام"},
    {"word": "شمس", "hint": "نجم النهار ومصدر الضوء والحرارة"},
    {"word": "مفتاح", "hint": "يفتح المغلق ولا يفتح إلا بأمره"},
    {"word": "كتاب", "hint": "رفيق القارئ ومخزن المعرفة"},
]

CHAIR_QUESTIONS = [
    "ما أكثر موقف طريف حصل لك هذا الأسبوع؟",
    "لو كان بإمكانك تغيير عادة واحدة في يومك، ماذا ستختار؟",
    "ما هي أكثر كلمة أو عبارة تكررها كثيراً؟",
    "أجمل ذكرى ما زالت حاضرة في بالك منذ الطفولة؟",
    "لو أصبحت مديراً ليوم واحد، أول قرار ستتخذه؟",
]

# التطبيقات الفرعية النشطة في الذاكرة: bot_id -> Application
RUNNING_CLONES: Dict[str, Application] = {}


def now_ts() -> float:
    return time.time()


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ================= إدارة سجل المصنع =================
def load_registry() -> Dict[str, Any]:
    default_reg = {
        "factory_enabled": True,
        "global_channels": json.loads(json.dumps(GLOBAL_CHANNELS)),
        "clones": {},  # bot_id: {bot_id, token, username, title, creator_id, creator_name, created_at, active}
    }
    if not os.path.exists(REGISTRY_FILE):
        save_registry(default_reg)
        return default_reg
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            reg = json.load(f)
        for k, v in default_reg.items():
            if k not in reg:
                reg[k] = v
        return reg
    except Exception as e:
        logger.error(f"Error loading registry: {e}")
        return default_reg


def save_registry(reg: Dict[str, Any]) -> None:
    try:
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving registry: {e}")


REGISTRY = load_registry()


# ================= كلاس كل بوت (الرئيسي أو المصنوع) =================
class BotInstance:
    def __init__(self, bot_id: str, token: str, is_master: bool, clone_owner_id: int):
        self.bot_id = bot_id
        self.token = token
        self.is_master = is_master
        self.clone_owner_id = clone_owner_id
        self.data_file = os.path.join(DATA_DIR, f"bot_{bot_id}.json")

        self.active_games: Dict[int, Dict[str, Any]] = {}
        self.xo_waiting_rooms: Dict[str, Dict[str, Any]] = {}
        self.xo_sessions: Dict[str, Dict[str, Any]] = {}
        self.pending_actions: Dict[int, Dict[str, Any]] = {}
        self.last_action_time: Dict[int, float] = {}

        self.db = self.load_db()
        self.force_sub = ForceSubManager(
            owner_id=OWNER_ID,
            is_master=is_master,
            default_text=self.default_db()["settings"]["force_sub_text"],
        )

    def default_db(self) -> Dict[str, Any]:
        return {
            "settings": {
                "bot_enabled": True,
                "maintenance_mode": False,
                "maintenance_message": "البوت تحت التحديث والصيانة حالياً، سنعود قريباً.",
                "force_sub_enabled": True,
                "force_sub_in_groups": True,
                "scope_mode": "both",
                "anti_spam_seconds": 1.0,
                "question_seconds": 25,
                "win_points": 10,
                "welcome_text": (
                    "🎮 أهلاً بك في ساحة الألعاب الجماعية!\n"
                    "اختر لعبتك وابدأ التحدي مع أصدقائك، واجمع النقاط لتتصدر القائمة."
                ),
                "force_sub_text": (
                    "🔒 للوصول إلى الألعاب والتفاعل مع البوت، يرجى الاشتراك أولاً في القنوات التالية:"
                ),
                "games": {
                    "xo": True,
                    "word": True,
                    "quiz": True,
                    "chair": True,
                    "mafia": True,
                },
            },
            "admins": [self.clone_owner_id],
            "extra_channels": [],  # قنوات إضافية يضيفها صانع البوت لنفسه (تُعرض بعد قنوات المطور الإجبارية)
            "users": {},
            "groups": {},
            "channels_registered": {},
            "admin_logs": [],
        }

    def load_db(self) -> Dict[str, Any]:
        d_def = self.default_db()
        if not os.path.exists(self.data_file):
            self.save_db(d_def)
            return d_def
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in d_def.items():
                if k not in data:
                    data[k] = v
            for k, v in d_def["settings"].items():
                if k not in data["settings"]:
                    data["settings"][k] = v
            for gk, gv in d_def["settings"]["games"].items():
                if gk not in data["settings"]["games"]:
                    data["settings"]["games"][gk] = gv
            if self.clone_owner_id not in data["admins"]:
                data["admins"].append(self.clone_owner_id)
            return data
        except Exception as e:
            logger.error(f"Error loading DB for {self.bot_id}: {e}")
            return d_def

    def save_db(self, data: Optional[Dict[str, Any]] = None) -> None:
        target = data if data is not None else self.db
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(target, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving DB for {self.bot_id}: {e}")

    def log_admin(self, text: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.db["admin_logs"].insert(0, f"[{ts}] {text}")
        self.db["admin_logs"] = self.db["admin_logs"][:50]
        self.save_db()

    def is_bot_admin(self, user_id: int) -> bool:
        return user_id == OWNER_ID or user_id == self.clone_owner_id or user_id in self.db.get("admins", [])

    def get_effective_channels(self) -> List[Dict[str, Any]]:
        """
        يدمج قنوات المطور الإلزامية (غير قابلة للحذف أو التعديل من المستخدمين)
        مع القنوات الإضافية التي يضيفها مالك البوت الفرعي عبر ForceSubManager.
        """
        return self.force_sub.get_effective_channels(
            global_channels=REGISTRY.get("global_channels", []),
            extra_channels=self.db.get("extra_channels", []),
        )

    def register_user(self, user) -> Dict[str, Any]:
        uid = str(user.id)
        full_name = user.full_name or user.first_name or "مستخدم"
        username = f"@{user.username}" if user.username else "غير محدد"
        if uid not in self.db["users"]:
            self.db["users"][uid] = {
                "id": user.id,
                "username": username,
                "name": full_name,
                "points": 0,
                "status": "active",
                "blocked_bot": False,
                "joined_at": now_str(),
                "last_seen": now_str(),
                "last_seen_ts": now_ts(),
            }
            self.save_db()
        else:
            u = self.db["users"][uid]
            u["name"] = full_name
            u["username"] = username
            u["blocked_bot"] = False
            u["last_seen"] = now_str()
            u["last_seen_ts"] = now_ts()
            self.save_db()
        return self.db["users"][uid]

    def register_chat(self, chat) -> None:
        cid = str(chat.id)
        title = chat.title or chat.full_name or str(chat.id)
        username = f"@{chat.username}" if chat.username else "غير محدد"
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            self.db["groups"][cid] = {
                "id": chat.id,
                "title": title,
                "username": username,
                "added_at": self.db["groups"].get(cid, {}).get("added_at", now_str()),
            }
            self.save_db()
        elif chat.type == ChatType.CHANNEL:
            self.db["channels_registered"][cid] = {
                "id": chat.id,
                "title": title,
                "username": username,
                "added_at": self.db["channels_registered"].get(cid, {}).get("added_at", now_str()),
            }
            self.save_db()

    def add_points(self, user_id: int, pts: int) -> int:
        uid = str(user_id)
        if uid not in self.db["users"]:
            self.db["users"][uid] = {
                "id": user_id,
                "username": "غير محدد",
                "name": f"مستخدم {user_id}",
                "points": 0,
                "status": "active",
                "blocked_bot": False,
                "joined_at": now_str(),
                "last_seen": now_str(),
                "last_seen_ts": now_ts(),
            }
        self.db["users"][uid]["points"] = max(0, self.db["users"][uid].get("points", 0) + pts)
        self.save_db()
        return self.db["users"][uid]["points"]

    def get_reply_keyboard(self, user_id: int) -> ReplyKeyboardMarkup:
        row1 = [KeyboardButton("ابدأ")]
        if self.is_bot_admin(user_id):
            row1.append(KeyboardButton("لوحة التحكم"))
        rows = [row1]
        if self.is_master:
            rows.append([KeyboardButton("🤖 اصنع بوتك بنفسك")])
        return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

    def get_main_menu_keyboard(self) -> InlineKeyboardMarkup:
        g = self.db["settings"]["games"]
        rows = [
            [
                InlineKeyboardButton(f"{'❌⭕' if g.get('xo') else '🔒'} لعبة XO", callback_data="game_xo_lobby"),
                InlineKeyboardButton(f"{'🔤' if g.get('word') else '🔒'} تخمين الكلمة", callback_data="game_word_start"),
            ],
            [
                InlineKeyboardButton(f"{'🧩' if g.get('quiz') else '🔒'} ألغاز وأسئلة", callback_data="game_quiz_start"),
                InlineKeyboardButton(f"{'🪑' if g.get('chair') else '🔒'} كرسي الاعتراف", callback_data="game_chair_lobby"),
            ],
            [
                InlineKeyboardButton(f"{'🕵️‍♂️' if g.get('mafia') else '🔒'} مافيا", callback_data="game_mafia_lobby"),
                InlineKeyboardButton("🏆 لوحة النقاط", callback_data="leaderboard"),
            ],
        ]
        if self.is_master:
            rows.append([InlineKeyboardButton("🤖 اصنع بوتك بنفسك", callback_data="factory_open")])
        return InlineKeyboardMarkup(rows)

    async def check_subscription(self, bot, user_id: int) -> List[Dict[str, Any]]:
        return await self.force_sub.check_subscription(
            bot=bot,
            user_id=user_id,
            global_channels=REGISTRY.get("global_channels", []),
            extra_channels=self.db.get("extra_channels", []),
            force_sub_enabled=self.db["settings"].get("force_sub_enabled", True),
            is_admin=self.is_bot_admin(user_id),
        )

    def build_force_sub_markup(self) -> InlineKeyboardMarkup:
        return self.force_sub.build_force_sub_markup(
            global_channels=REGISTRY.get("global_channels", []),
            extra_channels=self.db.get("extra_channels", []),
            force_sub_enabled=self.db["settings"].get("force_sub_enabled", True),
        )

    async def guard_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE, is_group_action: bool = False) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        if not user:
            return False

        if chat:
            self.register_chat(chat)
        u_rec = self.register_user(user)

        uid = user.id
        anti_spam = float(self.db["settings"].get("anti_spam_seconds", 1.0))
        prev_t = self.last_action_time.get(uid, 0.0)
        if not self.is_bot_admin(uid) and (now_ts() - prev_t) < anti_spam:
            return False
        self.last_action_time[uid] = now_ts()

        if u_rec.get("status") == "banned" and not self.is_bot_admin(uid):
            msg = "⛔ تم حظرك من استخدام هذا البوت."
            if update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(msg)
            return False

        if not self.is_bot_admin(uid):
            if not self.db["settings"].get("bot_enabled", True):
                msg = "🔴 البوت متوقف مؤقتاً."
                if update.callback_query:
                    await update.callback_query.answer(msg, show_alert=True)
                elif update.effective_message:
                    await update.effective_message.reply_text(msg)
                return False

            if self.db["settings"].get("maintenance_mode", False):
                m_msg = self.db["settings"].get("maintenance_message", "البوت تحت الصيانة حالياً.")
                if update.callback_query:
                    await update.callback_query.answer(m_msg, show_alert=True)
                elif update.effective_message:
                    await update.effective_message.reply_text(f"🛠️ {m_msg}")
                return False

        if chat and chat.type == ChatType.PRIVATE and self.db["settings"].get("scope_mode") == "groups_only" and not self.is_bot_admin(uid):
            if update.effective_message:
                await update.effective_message.reply_text("👥 اللعب متاح داخل المجموعات فقط.")
            return False

        return await self.force_sub.enforce(
            update=update,
            context=context,
            global_channels=REGISTRY.get("global_channels", []),
            extra_channels=self.db.get("extra_channels", []),
            force_sub_enabled=self.db["settings"].get("force_sub_enabled", True),
            force_sub_in_groups=self.db["settings"].get("force_sub_in_groups", True),
            force_sub_text=self.db["settings"].get("force_sub_text", self.default_db()["settings"]["force_sub_text"]),
            is_admin=self.is_bot_admin(uid),
            is_group_action=is_group_action,
        )

    # ================= لوحة تحكم البوت (للمطور ولصانع البوت الفرعي) =================
    def build_admin_main_markup(self, viewer_id: int) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("⚙️ إعدادات البوت", callback_data="adm_settings"),
                InlineKeyboardButton("📢 قنوات الاشتراك", callback_data="adm_channels"),
            ],
            [
                InlineKeyboardButton("🎮 إدارة الألعاب", callback_data="adm_games"),
                InlineKeyboardButton("🏆 النقاط والترتيب", callback_data="adm_points"),
            ],
            [
                InlineKeyboardButton("📣 إذاعة رسالة", callback_data="adm_broadcast"),
                InlineKeyboardButton("📊 إحصائيات المستخدمين", callback_data="adm_users_stats"),
            ],
            [
                InlineKeyboardButton("🛡️ النسخ الاحتياطي والسجل", callback_data="adm_admin_cfg"),
            ],
        ]
        if self.is_master and viewer_id == OWNER_ID:
            rows.append([InlineKeyboardButton("🏭 إدارة البوتات المصنوعة", callback_data="adm_factory_manage")])
        return InlineKeyboardMarkup(rows)

    async def show_admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
        user = update.effective_user
        if not user or not self.is_bot_admin(user.id):
            return
        self.pending_actions.pop(user.id, None)

        total_users = len(self.db["users"])
        total_groups = len(self.db["groups"])
        total_channels = len(self.db["channels_registered"])
        active_games = len(self.active_games)
        status_icon = "🟢 نشط" if self.db["settings"].get("bot_enabled", True) else "🔴 متوقف"
        maint_txt = " | 🛠️ صيانة" if self.db["settings"].get("maintenance_mode", False) else ""

        extra_factory = ""
        if self.is_master and user.id == OWNER_ID:
            extra_factory = f"\n• البوتات المصنوعة: <b>{len(REGISTRY.get('clones', {}))}</b>"

        text = (
            f"🛠️ <b>لوحة تحكم البوت</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• الحالة: <b>{status_icon}{maint_txt}</b>\n"
            f"• المستخدمون: <b>{total_users}</b> | المجموعات: <b>{total_groups}</b> | القنوات: <b>{total_channels}</b>\n"
            f"• الجولات النشطة: <b>{active_games}</b>{extra_factory}"
        )
        markup = self.build_admin_main_markup(user.id)
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        elif update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    def build_bot_settings_markup(self) -> InlineKeyboardMarkup:
        s = self.db["settings"]
        bot_st = "🟢 تشغيل البوت" if s.get("bot_enabled", True) else "🔴 إيقاف البوت"
        maint_st = "🛠️ وضع الصيانة: مفعل" if s.get("maintenance_mode", False) else "🛠️ وضع الصيانة: معطل"
        fsub_st = "🔒 اشتراك القنوات الإضافية: مفعل" if s.get("force_sub_enabled", True) else "🔓 اشتراك القنوات الإضافية: معطل"
        fsub_grp = "👥 إلزام المجموعات: نعم" if s.get("force_sub_in_groups", True) else "👥 إلزام المجموعات: لا"
        scope_st = "🌐 النطاق: خاص ومجموعات" if s.get("scope_mode") == "both" else "🌐 النطاق: مجموعات فقط"

        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(bot_st, callback_data="tog_bot_enabled")],
                [InlineKeyboardButton(maint_st, callback_data="tog_maintenance")],
                [InlineKeyboardButton(fsub_st, callback_data="tog_forcesub")],
                [InlineKeyboardButton(fsub_grp, callback_data="tog_forcesub_grp")],
                [InlineKeyboardButton(scope_st, callback_data="tog_scope")],
                [InlineKeyboardButton("✏️ تعديل رسالة الترحيب", callback_data="edit_welcome_msg")],
                [InlineKeyboardButton("✏️ تعديل رسالة الاشتراك", callback_data="edit_forcesub_msg")],
                [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="adm_home")],
            ]
        )

    def build_channels_manager_markup(self, viewer_id: int) -> InlineKeyboardMarkup:
        rows = []
        # 1. عرض القنوات الأساسية للمطور (مقفلة ولا يمكن لصانع البوت الفرعي حذفها أو تعديلها)
        for idx, ch in enumerate(REGISTRY.get("global_channels", [])):
            if viewer_id == OWNER_ID and self.is_master:
                st_icon = "✅" if ch.get("enabled", True) else "⏸️"
                rows.append([
                    InlineKeyboardButton(f"{st_icon} [أساسية] {ch.get('title')} (@{ch.get('username')})", callback_data=f"gch_toggle_{idx}"),
                    InlineKeyboardButton("✏️ اسم", callback_data=f"gch_rename_{idx}"),
                    InlineKeyboardButton("🗑️", callback_data=f"gch_del_{idx}"),
                ])
            else:
                rows.append([
                    InlineKeyboardButton(f"🔒 {ch.get('title')} (@{ch.get('username')}) — إلزامية من الإدارة", callback_data="locked_channel_notice")
                ])

        # 2. القنوات الإضافية الخاصة بصاحب هذا البوت
        if not self.is_master:
            for idx, ch in enumerate(self.db.get("extra_channels", [])):
                st_icon = "✅" if ch.get("enabled", True) else "⏸️"
                rows.append([
                    InlineKeyboardButton(f"{st_icon} {ch.get('title')} (@{ch.get('username')})", callback_data=f"ch_toggle_{idx}"),
                    InlineKeyboardButton("✏️ اسم", callback_data=f"ch_rename_{idx}"),
                    InlineKeyboardButton("🗑️", callback_data=f"ch_del_{idx}"),
                ])
            rows.append([InlineKeyboardButton("➕ إضافة قناة إضافية لبوتك", callback_data="ch_add")])
        else:
            if viewer_id == OWNER_ID:
                rows.append([InlineKeyboardButton("➕ إضافة قناة إلزامية لكل البوتات", callback_data="gch_add")])

        rows.append([InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="adm_home")])
        return InlineKeyboardMarkup(rows)

    def build_games_manager_markup(self) -> InlineKeyboardMarkup:
        s = self.db["settings"]
        g = s["games"]
        q_sec = s.get("question_seconds", 25)
        w_pts = s.get("win_points", 10)
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(f"{'✅' if g.get('xo') else '❌'} لعبة XO", callback_data="tog_game_xo"),
                    InlineKeyboardButton(f"{'✅' if g.get('word') else '❌'} تخمين الكلمة", callback_data="tog_game_word"),
                ],
                [
                    InlineKeyboardButton(f"{'✅' if g.get('quiz') else '❌'} ألغاز وأسئلة", callback_data="tog_game_quiz"),
                    InlineKeyboardButton(f"{'✅' if g.get('chair') else '❌'} كرسي الاعتراف", callback_data="tog_game_chair"),
                ],
                [
                    InlineKeyboardButton(f"{'✅' if g.get('mafia') else '❌'} مافيا", callback_data="tog_game_mafia"),
                    InlineKeyboardButton("🛑 إنهاء الجولات النشطة", callback_data="stop_all_games"),
                ],
                [
                    InlineKeyboardButton(f"⏱️ وقت السؤال: {q_sec}ث", callback_data="cycle_q_sec"),
                    InlineKeyboardButton(f"⭐ نقاط الفوز: {w_pts}", callback_data="cycle_w_pts"),
                ],
                [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="adm_home")],
            ]
        )

    def build_users_stats_markup(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📋 سجل المستخدمين", callback_data="users_list_0"),
                    InlineKeyboardButton("🔍 بحث عن مستخدم", callback_data="user_search_prompt"),
                ],
                [
                    InlineKeyboardButton("🔄 فحص من حظروا البوت", callback_data="check_blocked_users"),
                    InlineKeyboardButton("📥 تصدير كشف CSV", callback_data="export_users_csv"),
                ],
                [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="adm_home")],
            ]
        )

    def compute_users_stats(self) -> Dict[str, int]:
        users = list(self.db["users"].values())
        total = len(users)
        t_now = now_ts()
        active_24h = sum(1 for u in users if (t_now - u.get("last_seen_ts", 0)) <= 86400 and not u.get("blocked_bot"))
        active_7d = sum(1 for u in users if (t_now - u.get("last_seen_ts", 0)) <= 7 * 86400 and not u.get("blocked_bot"))
        blocked = sum(1 for u in users if u.get("blocked_bot", False))
        banned = sum(1 for u in users if u.get("status") == "banned")
        return {
            "total": total,
            "active_24h": active_24h,
            "active_7d": active_7d,
            "blocked": blocked,
            "banned": banned,
        }

    def format_user_card(self, u: Dict[str, Any]) -> str:
        st_label = "⛔ محظور إدارياً" if u.get("status") == "banned" else ("🚫 حظر البوت" if u.get("blocked_bot") else "🟢 نشط")
        return (
            f"👤 <b>ملف المستخدم</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>الاسم:</b> {u.get('name', 'غير معروف')}\n"
            f"• <b>المعرف:</b> {u.get('username', 'غير محدد')}\n"
            f"• <b>الآيدي (ID):</b> <code>{u.get('id')}</code>\n"
            f"• <b>الحالة:</b> {st_label}\n"
            f"• <b>النقاط:</b> <b>{u.get('points', 0)}</b>\n"
            f"• <b>أول دخول:</b> {u.get('joined_at', '-')}\n"
            f"• <b>آخر نشاط:</b> {u.get('last_seen', '-')}"
        )

    def build_user_card_markup(self, target_uid: int, is_banned: bool) -> InlineKeyboardMarkup:
        ban_btn_text = "✅ فك الحظر" if is_banned else "⛔ حظر المستخدم"
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✉️ مراسلة مباشرة", callback_data=f"u_msg_{target_uid}"),
                    InlineKeyboardButton("⭐ تعديل النقاط", callback_data=f"u_pts_{target_uid}"),
                ],
                [InlineKeyboardButton(ban_btn_text, callback_data=f"u_ban_{target_uid}")],
                [InlineKeyboardButton("⬅️ رجوع للإحصائيات", callback_data="adm_users_stats")],
            ]
        )

    # ================= قسم "اصنع بوتك بنفسك" =================
    async def open_factory_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
        user = update.effective_user
        if not user or not self.is_master:
            return
        if not REGISTRY.get("factory_enabled", True) and user.id != OWNER_ID:
            msg = "🔴 مصنع البوتات متوقف مؤقتاً من الإدارة."
            if update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(msg)
            return

        user_clones = [c for c in REGISTRY.get("clones", {}).values() if c.get("creator_id") == user.id]
        text = (
            f"🤖 <b>مصنع البوتات — اصنع بوتك بنفسك</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"يمكنك إنشاء بوت ألعاب وإدارة متكامل خاص بك خلال ثوانٍ!\n"
            f"• ستحصل على <b>لوحة تحكم كاملة</b> في بوتك (الإحصائيات، الإذاعة، المجموعات، القنوات، وإعدادات الألعاب).\n"
            f"• عدد بوتاتك الحالية: <b>{len(user_clones)}</b>"
        )
        rows = [
            [InlineKeyboardButton("➕ إنشاء بوت جديد (إرسال التوكن)", callback_data="factory_create_new")],
        ]
        if user_clones:
            rows.append([InlineKeyboardButton("📱 بوتاتي المصنوعة", callback_data="factory_my_bots")])
        rows.append([InlineKeyboardButton("⬅️ رجوع للقائمة الرئيسية", callback_data="back_main_menu")])

        markup = InlineKeyboardMarkup(rows)
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        elif update.effective_message:
            await update.effective_message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    # ================= الأوامر =================
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user:
            return
        if update.effective_chat:
            self.register_chat(update.effective_chat)
        self.register_user(user)

        ok = await self.guard_user(update, context, is_group_action=False)
        if not ok:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "أهلاً بك! تم إظهار أزرار الوصول السريع في الأسفل.",
                    reply_markup=self.get_reply_keyboard(user.id),
                )
            return

        if context.args and len(context.args) > 0 and context.args[0].startswith("xo_"):
            room_id = context.args[0].replace("xo_", "", 1)
            await self.join_xo_room_by_id(update, context, room_id)
            return

        welcome_txt = self.db["settings"].get("welcome_text", self.default_db()["settings"]["welcome_text"])
        if update.effective_message:
            await update.effective_message.reply_text(
                "⌨️ تم تفعيل أزرار الوصول السريع في مربع النص.",
                reply_markup=self.get_reply_keyboard(user.id),
            )
            await update.effective_message.reply_text(
                welcome_txt,
                reply_markup=self.get_main_menu_keyboard(),
            )

    async def cmd_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not user or not self.is_bot_admin(user.id):
            return
        if update.effective_message:
            await update.effective_message.reply_text(
                "🛠️ أهلاً بك في لوحة التحكم.",
                reply_markup=self.get_reply_keyboard(user.id),
            )
        await self.show_admin_panel(update, context, edit=False)

    # ================= الألعاب =================
    def build_xo_markup(self, board: List[str], session_id: str) -> InlineKeyboardMarkup:
        rows = []
        for r in range(3):
            row_btns = []
            for c in range(3):
                idx = r * 3 + c
                val = board[idx]
                symbol = "❌" if val == "X" else ("⭕" if val == "O" else "⬜")
                row_btns.append(InlineKeyboardButton(symbol, callback_data=f"xo_move_{session_id}_{idx}"))
            rows.append(row_btns)
        rows.append([InlineKeyboardButton("🛑 إنهاء اللعبة", callback_data=f"xo_cancel_{session_id}")])
        return InlineKeyboardMarkup(rows)

    def format_xo_board_header(self, session: Dict[str, Any], status_line: str) -> str:
        return (
            f"❌⭕ <b>لعبة XO (3 جولات) — الجولة {session['round_num']} من 3</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"❌ <b>{session['name_x']}</b>: <b>{session['score_x']}</b> | "
            f"⭕ <b>{session['name_o']}</b>: <b>{session['score_o']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{status_line}"
        )

    async def broadcast_xo_board(self, context: ContextTypes.DEFAULT_TYPE, session: Dict[str, Any], status_line: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
        text = self.format_xo_board_header(session, status_line)
        for target_key in ("msg_x", "msg_o"):
            info = session.get(target_key)
            if not info:
                continue
            try:
                await context.bot.edit_message_text(
                    chat_id=info["chat_id"],
                    message_id=info["message_id"],
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning(f"Failed to update XO board ({target_key}): {e}")

    async def join_xo_room_by_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: str) -> None:
        user = update.effective_user
        chat = update.effective_chat
        if not user or not chat:
            return

        room = self.xo_waiting_rooms.get(room_id)
        if not room:
            msg_txt = "❌ هذه الجولة غير متاحة أو تم الانضمام إليها مسبقاً."
            if update.callback_query:
                await update.callback_query.answer(msg_txt, show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(msg_txt)
            return

        if room["player_x"] == user.id:
            msg_txt = "⚠️ لا يمكنك الانضمام إلى جولتك الخاصة! أرسل رابط الدعوة لصديقك."
            if update.callback_query:
                await update.callback_query.answer(msg_txt, show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(msg_txt)
            return

        self.xo_waiting_rooms.pop(room_id, None)
        session_id = f"{room_id}_{int(now_ts())}"
        session: Dict[str, Any] = {
            "session_id": session_id,
            "board": [""] * 9,
            "player_x": room["player_x"],
            "name_x": room["name_x"],
            "chat_x": room["chat_id"],
            "player_o": user.id,
            "name_o": user.full_name,
            "chat_o": chat.id,
            "turn": "X",
            "round_num": 1,
            "score_x": 0,
            "score_o": 0,
            "msg_x": None,
            "msg_o": None,
        }
        self.xo_sessions[session_id] = session

        markup = self.build_xo_markup(session["board"], session_id)
        header_txt = self.format_xo_board_header(
            session,
            f"⚔️ انضم ⭕ <b>{user.full_name}</b> إلى التحدي!\nالدور الآن: ❌ <b>{session['name_x']}</b>",
        )

        # إرسال/تحديث لوحة اللعب للاعب الثاني (المنضم)
        if update.callback_query and update.callback_query.message:
            await update.callback_query.answer("تم الانضمام! بدأت الجولة الأولى.")
            sent_o = await update.callback_query.message.edit_text(
                header_txt,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            session["msg_o"] = {"chat_id": sent_o.chat_id, "message_id": sent_o.message_id}
        elif update.effective_message:
            sent_o = await update.effective_message.reply_text(
                header_txt,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            session["msg_o"] = {"chat_id": sent_o.chat_id, "message_id": sent_o.message_id}

        # إرسال/تحديث لوحة اللعب للاعب الأول (صاحب الجولة)
        try:
            if room.get("wait_message_id"):
                try:
                    await context.bot.delete_message(chat_id=room["chat_id"], message_id=room["wait_message_id"])
                except Exception:
                    pass
            sent_x = await context.bot.send_message(
                chat_id=room["chat_id"],
                text=header_txt,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            session["msg_x"] = {"chat_id": sent_x.chat_id, "message_id": sent_x.message_id}
        except Exception as e:
            logger.warning(f"Failed to send XO start message to player X: {e}")

    def check_xo_winner(self, board: List[str]) -> Optional[str]:
        wins = [
            (0, 1, 2), (3, 4, 5), (6, 7, 8),
            (0, 3, 6), (1, 4, 7), (2, 5, 8),
            (0, 4, 8), (2, 4, 6),
        ]
        for a, b, c in wins:
            if board[a] and board[a] == board[b] == board[c]:
                return board[a]
        if all(x != "" for x in board):
            return "DRAW"
        return None

    async def start_quiz_round(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        q_item = random.choice(QUESTIONS_BANK)
        opts = q_item["options"][:]
        random.shuffle(opts)
        self.active_games[chat_id] = {
            "type": "quiz",
            "question": q_item["q"],
            "answer": q_item["ans"],
            "options": opts,
        }
        btns = []
        row = []
        for i, opt in enumerate(opts):
            row.append(InlineKeyboardButton(opt, callback_data=f"quiz_ans_{i}"))
            if len(row) == 2:
                btns.append(row)
                row = []
        if row:
            btns.append(row)
        btns.append([InlineKeyboardButton("⏭️ سؤال جديد", callback_data="game_quiz_start"), InlineKeyboardButton("🛑 إنهاء", callback_data="game_cancel")])

        pts = self.db["settings"].get("win_points", 10)
        sec = self.db["settings"].get("question_seconds", 25)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🧩 <b>سؤال / لغز الجولة</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>{q_item['q']}</b>\n\n"
                f"⏱️ المدة المقترحة: {sec} ثانية | 🏆 الجائزة: +{pts} نقاط"
            ),
            reply_markup=InlineKeyboardMarkup(btns),
            parse_mode=ParseMode.HTML,
        )

    async def start_word_round(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        w_item = random.choice(WORD_BANK)
        self.active_games[chat_id] = {
            "type": "word",
            "word": w_item["word"],
            "hint": w_item["hint"],
            "hint_shown": False,
        }
        pts = self.db["settings"].get("win_points", 10)
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("💡 إظهار تلميح", callback_data="word_hint"),
                    InlineKeyboardButton("⏭️ كلمة أخرى", callback_data="game_word_start"),
                ],
                [InlineKeyboardButton("🛑 إنهاء اللعبة", callback_data="game_cancel")],
            ]
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🔤 <b>لعبة تخمين الكلمة</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"اكتب الكلمة الصحيحة في الدردشة لتفوز!\n"
                f"عدد الأحرف: <b>{len(w_item['word'])}</b> | 🏆 الجائزة: +{pts} نقاط"
            ),
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

    # ================= معالج الأزرار التفاعلية =================
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        user = query.from_user
        chat = query.message.chat if query.message else None
        data = query.data

        if data == "locked_channel_notice":
            await query.answer("🔒 هذه قناة اشتراك أساسية تابعة لإدارة البوت الرئيسي ولا يمكن تعديلها أو حذفها.", show_alert=True)
            return

        if data == "check_sub_again":
            self.register_user(user)
            missing = await self.check_subscription(context.bot, user.id)
            if missing:
                names = " ، ".join([c.get("title") or c.get("username") for c in missing])
                await query.answer(f"لم يتم الاشتراك بعد في: {names}", show_alert=True)
                return
            await query.answer("✅ تم التحقق من اشتراكك بنجاح!", show_alert=True)
            await query.message.edit_text(
                self.db["settings"].get("welcome_text", self.default_db()["settings"]["welcome_text"]),
                reply_markup=self.get_main_menu_keyboard(),
            )
            return

        is_adm_cb = data.startswith((
            "adm_", "tog_", "ch_", "gch_", "cycle_", "stop_all_",
            "users_", "user_", "check_blocked", "export_users",
            "u_", "bcast_", "edit_", "fadm_"
        ))
        if is_adm_cb:
            if not self.is_bot_admin(user.id):
                await query.answer("هذا القسم مخصص للأدمن فقط.", show_alert=True)
                return
        else:
            ok = await self.guard_user(update, context, is_group_action=(chat and chat.type != ChatType.PRIVATE))
            if not ok:
                return

        chat_id = chat.id if chat else user.id

        if data == "back_main_menu":
            await query.answer()
            await query.message.edit_text(
                self.db["settings"].get("welcome_text", self.default_db()["settings"]["welcome_text"]),
                reply_markup=self.get_main_menu_keyboard(),
            )
            return

        # مصنع البوتات للمستخدمين
        if data == "factory_open":
            await query.answer()
            await self.open_factory_menu(update, context, edit=True)
            return

        if data == "factory_create_new":
            if not self.is_master:
                return
            await query.answer()
            self.pending_actions[user.id] = {"action": "create_clone_token"}
            await query.message.reply_text(
                "🤖 <b>إنشاء بوت جديد</b>\n"
                "1. اذهب إلى @BotFather وأنشئ بوتاً جديداً واحصل على <b>التوكن (Token)</b>.\n"
                "2. أرسل التوكن هنا الآن (مثال: <code>123456789:AAH...</code>).\n\n"
                "⚠️ <i>ملاحظة: يجب رفع بوتك كمشرف في قنوات الاشتراك الإجباري ليتمكن من التحقق من اشتراك الأعضاء.</i>",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "factory_my_bots":
            await query.answer()
            user_clones = [c for c in REGISTRY.get("clones", {}).values() if c.get("creator_id") == user.id]
            if not user_clones:
                await query.message.reply_text("ليس لديك أي بوت مصنوع حالياً.")
                return
            lines = ["📱 <b>بوتاتك المصنوعة:</b>", "━━━━━━━━━━━━━━━━━━━━"]
            rows = []
            for c in user_clones:
                st = "🟢 يعمل" if c.get("active", True) else "🔴 متوقف"
                lines.append(f"• @{c.get('username')} ({c.get('title')}) — {st}")
                rows.append([
                    InlineKeyboardButton(f"🗑️ حذف @{c.get('username')}", callback_data=f"myclone_del_{c.get('bot_id')}")
                ])
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="factory_open")])
            await query.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
            return

        if data.startswith("myclone_del_"):
            bid = data.replace("myclone_del_", "")
            c_info = REGISTRY.get("clones", {}).get(bid)
            if not c_info or (c_info.get("creator_id") != user.id and user.id != OWNER_ID):
                await query.answer("غير مصرح لك.", show_alert=True)
                return
            await stop_clone_bot(bid)
            REGISTRY["clones"].pop(bid, None)
            save_registry(REGISTRY)
            await query.answer("تم حذف البوت وإيقافه بنجاح!", show_alert=True)
            await self.open_factory_menu(update, context, edit=True)
            return

        # إدارة وإحصائيات البوتات المصنوعة للمالك OWNER_ID
        if data == "adm_factory_manage" and user.id == OWNER_ID:
            await query.answer()
            clones = list(REGISTRY.get("clones", {}).values())
            total_clones = len(clones)
            active_clones = sum(1 for c in clones if c.get("active", True))
            stopped_clones = total_clones - active_clones

            # تجميع إحصائيات المستخدمين والمجموعات والقنوات عبر جميع البوتات المصنوعة
            agg_users = 0
            agg_active_24h = 0
            agg_blocked = 0
            agg_groups = 0
            agg_channels = 0

            for c in clones:
                bid = str(c.get("bot_id"))
                c_file = os.path.join(DATA_DIR, f"bot_{bid}.json")
                if os.path.exists(c_file):
                    try:
                        with open(c_file, "r", encoding="utf-8") as cf:
                            cdb = json.load(cf)
                        c_users = list(cdb.get("users", {}).values())
                        agg_users += len(c_users)
                        t_n = now_ts()
                        agg_active_24h += sum(
                            1 for u in c_users if (t_n - u.get("last_seen_ts", 0)) <= 86400 and not u.get("blocked_bot")
                        )
                        agg_blocked += sum(1 for u in c_users if u.get("blocked_bot", False))
                        agg_groups += len(cdb.get("groups", {}))
                        agg_channels += len(cdb.get("channels_registered", {}))
                    except Exception:
                        pass

            f_st = "🟢 المصنع: مفعل" if REGISTRY.get("factory_enabled", True) else "🔴 المصنع: متوقف"
            rows = [[InlineKeyboardButton(f_st, callback_data="fadm_toggle_factory")]]
            for c in clones[:15]:
                bid = str(c.get("bot_id"))
                uname = c.get("username") or bid
                u_cnt = 0
                c_file = os.path.join(DATA_DIR, f"bot_{bid}.json")
                if os.path.exists(c_file):
                    try:
                        with open(c_file, "r", encoding="utf-8") as cf:
                            cdb = json.load(cf)
                        u_cnt = len(cdb.get("users", {}))
                    except Exception:
                        pass
                rows.append([
                    InlineKeyboardButton(
                        f"🤖 @{uname} | 👤 {u_cnt}",
                        callback_data=f"fadm_inspect_{bid}",
                    )
                ])
            rows.append([InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="adm_home")])

            summary_text = (
                f"🏭 <b>إحصائيات وإدارة البوتات المصنوعة</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• إجمالي البوتات المصنوعة: <b>{total_clones}</b> (🟢 نشط: <b>{active_clones}</b> | 🔴 متوقف: <b>{stopped_clones}</b>)\n"
                f"• إجمالي مستخدمي البوتات المصنوعة: <b>{agg_users}</b>\n"
                f"• النشطون في البوتات المصنوعة (24س): <b>{agg_active_24h}</b>\n"
                f"• من حظروا البوتات المصنوعة: <b>{agg_blocked}</b>\n"
                f"• مجموعات وقنوات البوتات المصنوعة: <b>{agg_groups}</b> / <b>{agg_channels}</b>\n\n"
                f"<i>اضغط على أي بوت أدناه لعرض إحصائيات مستخدميه بالتفصيل:</i>"
            )
            await query.message.edit_text(
                summary_text,
                reply_markup=InlineKeyboardMarkup(rows),
                parse_mode=ParseMode.HTML,
            )
            return

        if data.startswith("fadm_inspect_") and user.id == OWNER_ID:
            bid = data.replace("fadm_inspect_", "")
            c_info = REGISTRY.get("clones", {}).get(bid)
            if not c_info:
                await query.answer("هذا البوت غير موجود في السجل.", show_alert=True)
                return

            c_file = os.path.join(DATA_DIR, f"bot_{bid}.json")
            u_total = 0
            u_act_24h = 0
            u_act_7d = 0
            u_blocked = 0
            u_banned = 0
            g_cnt = 0
            ch_cnt = 0

            if os.path.exists(c_file):
                try:
                    with open(c_file, "r", encoding="utf-8") as cf:
                        cdb = json.load(cf)
                    users_list = list(cdb.get("users", {}).values())
                    u_total = len(users_list)
                    t_n = now_ts()
                    u_act_24h = sum(1 for u in users_list if (t_n - u.get("last_seen_ts", 0)) <= 86400 and not u.get("blocked_bot"))
                    u_act_7d = sum(1 for u in users_list if (t_n - u.get("last_seen_ts", 0)) <= 7 * 86400 and not u.get("blocked_bot"))
                    u_blocked = sum(1 for u in users_list if u.get("blocked_bot", False))
                    u_banned = sum(1 for u in users_list if u.get("status") == "banned")
                    g_cnt = len(cdb.get("groups", {}))
                    ch_cnt = len(cdb.get("channels_registered", {}))
                except Exception:
                    pass

            await query.answer()
            detail_text = (
                f"🤖 <b>إحصائيات البوت: @{c_info.get('username', bid)}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• الاسم: <b>{c_info.get('title', '-')}</b>\n"
                f"• آيدي البوت: <code>{bid}</code>\n"
                f"• صانع البوت: <b>{c_info.get('creator_name', '-')}</b> (<code>{c_info.get('creator_id')}</code>)\n"
                f"• تاريخ الإنشاء: {c_info.get('created_at', '-')}\n\n"
                f"📊 <b>إحصائيات مستخدمي هذا البوت:</b>\n"
                f"• إجمالي المستخدمين: <b>{u_total}</b>\n"
                f"• النشطون (آخر 24 ساعة): <b>{u_act_24h}</b>\n"
                f"• النشطون (آخر 7 أيام): <b>{u_act_7d}</b>\n"
                f"• من حظروا البوت: <b>{u_blocked}</b>\n"
                f"• المحظورون إدارياً: <b>{u_banned}</b>\n"
                f"• المجموعات: <b>{g_cnt}</b> | القنوات: <b>{ch_cnt}</b>"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ حذف وإيقاف هذا البوت", callback_data=f"fadm_del_{bid}")],
                [InlineKeyboardButton("⬅️ رجوع للبوتات المصنوعة", callback_data="adm_factory_manage")],
            ])
            await query.message.edit_text(detail_text, reply_markup=markup, parse_mode=ParseMode.HTML)
            return

        if data == "fadm_toggle_factory" and user.id == OWNER_ID:
            REGISTRY["factory_enabled"] = not REGISTRY.get("factory_enabled", True)
            save_registry(REGISTRY)
            await query.answer("تم تحديث حالة المصنع")
            await self.show_admin_panel(update, context, edit=True)
            return

        if data.startswith("fadm_del_") and user.id == OWNER_ID:
            bid = data.replace("fadm_del_", "")
            await stop_clone_bot(bid)
            REGISTRY.get("clones", {}).pop(bid, None)
            save_registry(REGISTRY)
            await query.answer("تم إيقاف وحذف البوت الفرعي.", show_alert=True)
            await self.show_admin_panel(update, context, edit=True)
            return

        # ---------------- الألعاب ولوحة المتصدرين ----------------
        if data == "leaderboard":
            await query.answer()
            sorted_users = sorted(self.db["users"].values(), key=lambda x: x.get("points", 0), reverse=True)[:10]
            if not sorted_users:
                txt = "🏆 لا توجد نقاط مسجلة حتى الآن."
            else:
                lines = ["🏆 <b>لوحة المتصدرين (أعلى 10)</b>", "━━━━━━━━━━━━━━━━━━━━"]
                medals = ["🥇", "🥈", "🥉"]
                for idx, u in enumerate(sorted_users):
                    m = medals[idx] if idx < 3 else f"{idx + 1}."
                    lines.append(f"{m} <b>{u.get('name', 'لاعب')}</b> — <b>{u.get('points', 0)}</b> نقطة")
                txt = "\n".join(lines)
            await query.message.reply_text(txt, parse_mode=ParseMode.HTML)
            return

        if data == "game_cancel":
            await query.answer("تم إنهاء الجولة.")
            self.active_games.pop(chat_id, None)
            await query.message.edit_text("🛑 تم إنهاء الجولة الحالية.")
            return

        if data == "game_xo_lobby":
            if not self.db["settings"]["games"].get("xo", True):
                await query.answer("لعبة XO معطلة حالياً.", show_alert=True)
                return
            await query.answer()
            waiting_count = len(self.xo_waiting_rooms)
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ بدء جولة جديدة", callback_data="xo_create_room")],
                [InlineKeyboardButton(f"📥 الانضمام إلى جولة ({waiting_count})", callback_data="xo_list_rooms")],
                [InlineKeyboardButton("⬅️ رجوع للقائمة الرئيسية", callback_data="back_main_menu")],
            ])
            await query.message.edit_text(
                "❌⭕ <b>لعبة XO (نظام 3 جولات)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "اختر <b>بدء جولة جديدة</b> لإضافتها إلى قائمة الانتظار أو دعوة صديقك، أو اختر <b>الانضمام إلى جولة</b> للعب مع لاعب منتظر:",
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "xo_create_room":
            if not self.db["settings"]["games"].get("xo", True):
                await query.answer("لعبة XO معطلة حالياً.", show_alert=True)
                return
            # إزالة أي جولة انتظار سابقة لنفس اللاعب
            for rid in [k for k, v in self.xo_waiting_rooms.items() if v["player_x"] == user.id]:
                self.xo_waiting_rooms.pop(rid, None)

            room_id = f"{user.id}_{random.randint(1000, 9999)}"
            bot_me = await context.bot.get_me()
            invite_link = f"https://t.me/{bot_me.username}?start=xo_{room_id}"
            share_text = f"تعال نلعب جولة XO (3 مراحل) ضد {user.full_name}!"
            share_url = f"https://t.me/share/url?url={invite_link}&text={share_text}"

            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📨 دعوة لاعب آخر (مشاركة الرابط)", url=share_url)],
                [InlineKeyboardButton("❌ إلغاء الجولة والانتظار", callback_data=f"xo_leave_wait_{room_id}")],
            ])
            await query.answer("تمت إضافة جولتك إلى قائمة الانتظار!")
            sent = await query.message.edit_text(
                f"⏳ <b>جولتك الآن في قائمة الانتظار!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• اللاعب الأول (❌): <b>{user.full_name}</b>\n"
                f"• النظام: <b>3 جولات</b>\n"
                f"• رابط الدعوة المباشر لصديقك:\n<code>{invite_link}</code>\n\n"
                f"بانتظار دخول صديقك عبر الرابط أو انضمام لاعب من قائمة الانتظار...",
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            self.xo_waiting_rooms[room_id] = {
                "room_id": room_id,
                "player_x": user.id,
                "name_x": user.full_name,
                "chat_id": sent.chat_id,
                "wait_message_id": sent.message_id,
                "created_at": now_ts(),
            }
            return

        if data.startswith("xo_leave_wait_"):
            room_id = data.replace("xo_leave_wait_", "", 1)
            room = self.xo_waiting_rooms.get(room_id)
            if room and room["player_x"] != user.id:
                await query.answer("هذه الجولة لا تخصك.", show_alert=True)
                return
            self.xo_waiting_rooms.pop(room_id, None)
            await query.answer("تم إلغاء الجولة وإزالتها من الانتظار.")
            await query.message.edit_text(
                "❌ تم إلغاء جولة XO وإزالتها من قائمة الانتظار.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع للعبة XO", callback_data="game_xo_lobby")]]),
            )
            return

        if data == "xo_list_rooms":
            await query.answer()
            available_rooms = [r for r in self.xo_waiting_rooms.values() if r["player_x"] != user.id]
            if not available_rooms:
                markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ بدء جولة جديدة", callback_data="xo_create_room")],
                    [InlineKeyboardButton("🔄 تحديث القائمة", callback_data="xo_list_rooms")],
                    [InlineKeyboardButton("⬅️ رجوع", callback_data="game_xo_lobby")],
                ])
                await query.message.edit_text(
                    "📭 <b>قائمة الانتظار فارغة حالياً</b>\nلا توجد جولات متاحة للانضمام، يمكنك بدء جولة جديدة وانتظار منافس.",
                    reply_markup=markup,
                    parse_mode=ParseMode.HTML,
                )
                return

            rows = []
            for r in available_rooms[:10]:
                rows.append([
                    InlineKeyboardButton(
                        f"⚔️ انضم ضد: {r['name_x']}",
                        callback_data=f"xo_join_room_{r['room_id']}",
                    )
                ])
            rows.append([InlineKeyboardButton("🔄 تحديث القائمة", callback_data="xo_list_rooms")])
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="game_xo_lobby")])
            await query.message.edit_text(
                f"📋 <b>الجولات المتاحة في قائمة الانتظار ({len(available_rooms)}):</b>\nاختر جولة للانضمام وبدء اللعب فوراً:",
                reply_markup=InlineKeyboardMarkup(rows),
                parse_mode=ParseMode.HTML,
            )
            return

        if data.startswith("xo_join_room_"):
            room_id = data.replace("xo_join_room_", "", 1)
            await self.join_xo_room_by_id(update, context, room_id)
            return

        if data.startswith("xo_cancel_"):
            session_id = data.replace("xo_cancel_", "", 1)
            session = self.xo_sessions.get(session_id)
            if not session:
                await query.answer("هذه الجولة منتهية بالفعل.", show_alert=True)
                return
            if user.id not in (session["player_x"], session["player_o"]):
                await query.answer("أنت لست طرفاً في هذه المباراة!", show_alert=True)
                return
            self.xo_sessions.pop(session_id, None)
            await query.answer("تم إنهاء المباراة.")
            await self.broadcast_xo_board(
                context,
                session,
                f"🛑 تم إنهاء المباراة بواسطة <b>{user.full_name}</b>.",
                reply_markup=None,
            )
            return

        if data.startswith("xo_move_"):
            parts = data.split("_")
            idx = int(parts[-1])
            session_id = "_".join(parts[2:-1])
            session = self.xo_sessions.get(session_id)
            if not session:
                await query.answer("هذه المباراة منتهية.", show_alert=True)
                return

            expected_uid = session["player_x"] if session["turn"] == "X" else session["player_o"]
            if user.id != expected_uid:
                await query.answer("ليس دورك الآن!", show_alert=True)
                return
            if session["board"][idx] != "":
                await query.answer("هذه الخانة مشغولة!", show_alert=True)
                return

            symbol = session["turn"]
            session["board"][idx] = symbol
            res = self.check_xo_winner(session["board"])
            pts = self.db["settings"].get("win_points", 10)

            if res in ("X", "O", "DRAW"):
                if res == "X":
                    session["score_x"] += 1
                    round_banner = f"✅ فاز في الجولة {session['round_num']}: ❌ <b>{session['name_x']}</b>"
                elif res == "O":
                    session["score_o"] += 1
                    round_banner = f"✅ فاز في الجولة {session['round_num']}: ⭕ <b>{session['name_o']}</b>"
                else:
                    round_banner = f"🤝 انتهت الجولة {session['round_num']} بالتعادل!"

                # التحقق هل اكتملت المراحل الثلاث أم لا
                if session["round_num"] < 3:
                    session["round_num"] += 1
                    session["board"] = [""] * 9
                    # تبديل البادئ في الجولة التالية للعدالة
                    session["turn"] = "O" if session["round_num"] % 2 == 0 else "X"
                    next_name = session["name_o"] if session["turn"] == "O" else session["name_x"]
                    next_sym = "⭕" if session["turn"] == "O" else "❌"
                    await query.answer("انتهت الجولة! بدأت الجولة التالية.")
                    await self.broadcast_xo_board(
                        context,
                        session,
                        f"{round_banner}\n\n▶️ <b>بدأت الجولة {session['round_num']} من 3!</b>\nالدور الآن: {next_sym} <b>{next_name}</b>",
                        reply_markup=self.build_xo_markup(session["board"], session_id),
                    )
                    return
                else:
                    # انتهت الجولات الثلاث بالكامل -> إعلان الفائز النهائي
                    final_board = session["board"][:]
                    self.xo_sessions.pop(session_id, None)
                    if session["score_x"] > session["score_o"]:
                        new_pts = self.add_points(session["player_x"], pts)
                        final_text = (
                            f"{round_banner}\n\n"
                            f"🏆 <b>انتهت المراحل الثلاث! الفائز النهائي:</b> ❌ <b>{session['name_x']}</b>\n"
                            f"⭐ حصل على +{pts} نقاط (المجموع: {new_pts})"
                        )
                    elif session["score_o"] > session["score_x"]:
                        new_pts = self.add_points(session["player_o"], pts)
                        final_text = (
                            f"{round_banner}\n\n"
                            f"🏆 <b>انتهت المراحل الثلاث! الفائز النهائي:</b> ⭕ <b>{session['name_o']}</b>\n"
                            f"⭐ حصل على +{pts} نقاط (المجموع: {new_pts})"
                        )
                    else:
                        final_text = (
                            f"{round_banner}\n\n"
                            f"🤝 <b>انتهت المراحل الثلاث بالتعادل الإجمالي ({session['score_x']} - {session['score_o']})!</b>"
                        )
                    await query.answer("انتهت المراحل الثلاث!")
                    await self.broadcast_xo_board(
                        context,
                        session,
                        final_text,
                        reply_markup=None,
                    )
                    return

            session["turn"] = "O" if symbol == "X" else "X"
            next_name = session["name_o"] if session["turn"] == "O" else session["name_x"]
            next_sym = "❌" if session["turn"] == "X" else "⭕"
            await query.answer()
            await self.broadcast_xo_board(
                context,
                session,
                f"الدور الآن: {next_sym} <b>{next_name}</b>",
                reply_markup=self.build_xo_markup(session["board"], session_id),
            )
            return

        if data == "game_quiz_start":
            if not self.db["settings"]["games"].get("quiz", True):
                await query.answer("لعبة الأسئلة والألغاز معطلة حالياً.", show_alert=True)
                return
            await query.answer()
            await self.start_quiz_round(chat_id, context)
            return

        if data.startswith("quiz_ans_"):
            opt_idx = int(data.split("_")[-1])
            game = self.active_games.get(chat_id)
            if not game or game.get("type") != "quiz":
                await query.answer("انتهى هذا السؤال أو تمت الإجابة عليه.", show_alert=True)
                return
            chosen = game["options"][opt_idx]
            if chosen == game["answer"]:
                pts = self.db["settings"].get("win_points", 10)
                new_pts = self.add_points(user.id, pts)
                self.active_games.pop(chat_id, None)
                await query.answer("إجابة صحيحة! 🎉", show_alert=True)
                await query.message.edit_text(
                    f"✅ <b>إجابة صحيحة!</b>\nالفائز: <b>{user.full_name}</b>\nالإجابة: <b>{chosen}</b>\n🏆 +{pts} نقاط (المجموع: {new_pts})",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await query.answer("❌ إجابة خاطئة، حاول مجدداً!", show_alert=False)
            return

        if data == "game_word_start":
            if not self.db["settings"]["games"].get("word", True):
                await query.answer("لعبة تخمين الكلمة معطلة حالياً.", show_alert=True)
                return
            await query.answer()
            await self.start_word_round(chat_id, context)
            return

        if data == "word_hint":
            game = self.active_games.get(chat_id)
            if not game or game.get("type") != "word":
                await query.answer("لا توجد جولة نشطة.", show_alert=True)
                return
            game["hint_shown"] = True
            await query.answer(f"💡 التلميح: {game['hint']}", show_alert=True)
            return

        if data == "game_chair_lobby":
            if not self.db["settings"]["games"].get("chair", True):
                await query.answer("لعبة كرسي الاعتراف معطلة حالياً.", show_alert=True)
                return
            await query.answer()
            self.active_games[chat_id] = {"type": "chair", "players": {user.id: user.full_name}, "hot_seat": None}
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🙋‍♂️ سجلني", callback_data="chair_join"), InlineKeyboardButton("🎲 اختر الجالس", callback_data="chair_pick")],
                [InlineKeyboardButton("🛑 إنهاء", callback_data="game_cancel")],
            ])
            await query.message.reply_text(
                f"🪑 <b>كرسي الاعتراف جاهز!</b>\nالمسجلون حالياً (1):\n• <b>{user.full_name}</b>",
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "chair_join":
            game = self.active_games.get(chat_id)
            if not game or game.get("type") != "chair":
                await query.answer("انتهت جلسة كرسي الاعتراف.", show_alert=True)
                return
            game["players"][user.id] = user.full_name
            names_list = "\n".join([f"• <b>{n}</b>" for n in game["players"].values()])
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🙋‍♂️ سجلني", callback_data="chair_join"), InlineKeyboardButton("🎲 اختر الجالس", callback_data="chair_pick")],
                [InlineKeyboardButton("🛑 إنهاء", callback_data="game_cancel")],
            ])
            await query.answer("تم تسجيلك!")
            await query.message.edit_text(
                f"🪑 <b>كرسي الاعتراف</b>\nالمسجلون ({len(game['players'])}):\n{names_list}",
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "chair_pick":
            game = self.active_games.get(chat_id)
            if not game or game.get("type") != "chair" or not game.get("players"):
                await query.answer("لا يوجد مشاركون كافون.", show_alert=True)
                return
            chosen_id, chosen_name = random.choice(list(game["players"].items()))
            game["hot_seat"] = (chosen_id, chosen_name)
            q_rand = random.choice(CHAIR_QUESTIONS)
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("❓ سؤال جاهز آخر", callback_data="chair_next_q")],
                [InlineKeyboardButton("🎲 اختيار شخص آخر", callback_data="chair_pick")],
                [InlineKeyboardButton("🛑 إنهاء", callback_data="game_cancel")],
            ])
            await query.answer()
            await query.message.reply_text(
                f"🪑 <b>الآن على كرسي الاعتراف:</b> <b>{chosen_name}</b>\n\n❓ <b>السؤال:</b> {q_rand}",
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "chair_next_q":
            game = self.active_games.get(chat_id)
            if not game or game.get("type") != "chair" or not game.get("hot_seat"):
                await query.answer("اختر شخصاً على الكرسي أولاً.", show_alert=True)
                return
            _, chosen_name = game["hot_seat"]
            q_rand = random.choice(CHAIR_QUESTIONS)
            await query.answer()
            await query.message.edit_text(
                f"🪑 <b>على كرسي الاعتراف:</b> <b>{chosen_name}</b>\n\n❓ <b>السؤال:</b> {q_rand}",
                reply_markup=query.message.reply_markup,
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "game_mafia_lobby":
            if not self.db["settings"]["games"].get("mafia", True):
                await query.answer("لعبة مافيا معطلة حالياً.", show_alert=True)
                return
            await query.answer()
            self.active_games[chat_id] = {"type": "mafia_lobby", "players": {user.id: user.full_name}}
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 انضم للعبة", callback_data="mafia_join"), InlineKeyboardButton("▶️ ابدأ وتوزيع الأدوار", callback_data="mafia_start")],
                [InlineKeyboardButton("🛑 إلغاء", callback_data="game_cancel")],
            ])
            await query.message.reply_text(
                f"🕵️‍♂️ <b>غرفة لعبة مافيا</b>\nالمشاركون (1):\n• <b>{user.full_name}</b>\n*(الحد الأدنى 3 لاعبين)*",
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "mafia_join":
            game = self.active_games.get(chat_id)
            if not game or game.get("type") != "mafia_lobby":
                await query.answer("الغرفة غير متاحة.", show_alert=True)
                return
            game["players"][user.id] = user.full_name
            p_list = "\n".join([f"• <b>{n}</b>" for n in game["players"].values()])
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 انضم للعبة", callback_data="mafia_join"), InlineKeyboardButton("▶️ ابدأ وتوزيع الأدوار", callback_data="mafia_start")],
                [InlineKeyboardButton("🛑 إلغاء", callback_data="game_cancel")],
            ])
            await query.answer("تم انضمامك!")
            await query.message.edit_text(
                f"🕵️‍♂️ <b>غرفة لعبة مافيا</b>\nالمشاركون ({len(game['players'])}):\n{p_list}",
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "mafia_start":
            game = self.active_games.get(chat_id)
            if not game or game.get("type") != "mafia_lobby":
                await query.answer("الغرفة غير متاحة.", show_alert=True)
                return
            players = list(game["players"].items())
            if len(players) < 3:
                await query.answer("يحتاج بدء اللعبة إلى 3 لاعبين على الأقل!", show_alert=True)
                return
            random.shuffle(players)
            roles_pool = ["🕵️‍♂️ مافيا", "🩺 طبيب"] + ["👤 مواطن"] * max(0, len(players) - 2)
            random.shuffle(roles_pool)

            dm_ok = 0
            dm_fail = []
            for idx, (pid, pname) in enumerate(players):
                role = roles_pool[idx % len(roles_pool)]
                try:
                    await context.bot.send_message(
                        chat_id=pid,
                        text=f"🎭 دورك السري في جولة مافيا: <b>{role}</b>\nحافظ على سرك ولا تكشفه للآخرين!",
                        parse_mode=ParseMode.HTML,
                    )
                    dm_ok += 1
                except Exception:
                    dm_fail.append(pname)

            self.active_games[chat_id] = {"type": "mafia_active", "players": dict(players), "phase": "النهار"}
            fail_note = ""
            if dm_fail:
                fail_note = "\n⚠️ لم يتمكن البوت من إرسال الدور بالخاص إلى: " + " ، ".join(dm_fail)

            await query.answer("تم توزيع الأدوار!")
            await query.message.edit_text(
                f"🌙 <b>بدأت جولة مافيا!</b>\nتم إرسال الأدوار السرية ({dm_ok}/{len(players)}) في الرسائل الخاصة.{fail_note}\n\nابدأوا النقاش الآن!",
                parse_mode=ParseMode.HTML,
            )
            return

        # ---------------- لوحة التحكم والإعدادات ----------------
        if data == "adm_home":
            await query.answer()
            await self.show_admin_panel(update, context, edit=True)
            return

        if data == "adm_settings":
            await query.answer()
            await query.message.edit_text(
                "⚙️ <b>إعدادات البوت العامة</b>",
                reply_markup=self.build_bot_settings_markup(),
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "tog_bot_enabled":
            self.db["settings"]["bot_enabled"] = not self.db["settings"].get("bot_enabled", True)
            self.save_db()
            self.log_admin(f"تغيير حالة البوت إلى: {self.db['settings']['bot_enabled']}")
            await query.answer("تم تحديث حالة البوت")
            await query.message.edit_reply_markup(reply_markup=self.build_bot_settings_markup())
            return

        if data == "tog_maintenance":
            self.db["settings"]["maintenance_mode"] = not self.db["settings"].get("maintenance_mode", False)
            self.save_db()
            self.log_admin(f"تغيير وضع الصيانة إلى: {self.db['settings']['maintenance_mode']}")
            await query.answer("تم تحديث وضع الصيانة")
            await query.message.edit_reply_markup(reply_markup=self.build_bot_settings_markup())
            return

        if data == "tog_forcesub":
            self.db["settings"]["force_sub_enabled"] = not self.db["settings"].get("force_sub_enabled", True)
            self.save_db()
            await query.answer("تم تحديث حالة الاشتراك الإضافي")
            await query.message.edit_reply_markup(reply_markup=self.build_bot_settings_markup())
            return

        if data == "tog_forcesub_grp":
            self.db["settings"]["force_sub_in_groups"] = not self.db["settings"].get("force_sub_in_groups", True)
            self.save_db()
            await query.answer("تم تحديث إعداد المجموعات")
            await query.message.edit_reply_markup(reply_markup=self.build_bot_settings_markup())
            return

        if data == "tog_scope":
            cur = self.db["settings"].get("scope_mode", "both")
            self.db["settings"]["scope_mode"] = "groups_only" if cur == "both" else "both"
            self.save_db()
            await query.answer("تم تحديث نطاق اللعب")
            await query.message.edit_reply_markup(reply_markup=self.build_bot_settings_markup())
            return

        if data == "edit_welcome_msg":
            await query.answer()
            self.pending_actions[user.id] = {"action": "set_welcome"}
            await query.message.reply_text("✍️ أرسل الآن نص رسالة الترحيب الجديدة:")
            return

        if data == "edit_forcesub_msg":
            await query.answer()
            self.pending_actions[user.id] = {"action": "set_forcesub_msg"}
            await query.message.reply_text("✍️ أرسل الآن نص رسالة الاشتراك الإجباري الجديدة:")
            return

        # إدارة قنوات الاشتراك الإجباري
        if data == "adm_channels":
            await query.answer()
            note = (
                "🔒 <i>ملاحظة: قنوات الإدارة الأساسية تبقى مفعلة تلقائياً ولا يمكن تعديلها أو حذفها، ويمكنك إضافة قنواتك الخاصة معها.</i>"
                if not self.is_master
                else "📢 <i>هذه القنوات الإلزامية تُفرض على البوت الرئيسي وجميع البوتات المصنوعة.</i>"
            )
            await query.message.edit_text(
                f"📢 <b>إدارة قنوات الاشتراك الإجباري</b>\n{note}",
                reply_markup=self.build_channels_manager_markup(user.id),
                parse_mode=ParseMode.HTML,
            )
            return

        # قنوات المطور الأساسية (للمالك OWNER_ID فقط)
        if data == "gch_add" and user.id == OWNER_ID and self.is_master:
            await query.answer()
            self.pending_actions[user.id] = {"action": "add_global_channel"}
            await query.message.reply_text(
                "➕ أرسل معرف القناة واسمها مفصولين بعلامة | مثل:\n<code>ChannelUsername | اسم القناة الظاهر</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if data.startswith("gch_toggle_") and user.id == OWNER_ID and self.is_master:
            idx = int(data.split("_")[-1])
            if 0 <= idx < len(REGISTRY["global_channels"]):
                REGISTRY["global_channels"][idx]["enabled"] = not REGISTRY["global_channels"][idx].get("enabled", True)
                save_registry(REGISTRY)
                await query.answer("تم التبديل")
                await query.message.edit_reply_markup(reply_markup=self.build_channels_manager_markup(user.id))
            return

        if data.startswith("gch_rename_") and user.id == OWNER_ID and self.is_master:
            idx = int(data.split("_")[-1])
            if 0 <= idx < len(REGISTRY["global_channels"]):
                await query.answer()
                self.pending_actions[user.id] = {"action": "rename_global_channel", "idx": idx}
                await query.message.reply_text("✏️ أرسل الاسم الجديد الذي سيظهر على زر القناة:")
            return

        if data.startswith("gch_del_") and user.id == OWNER_ID and self.is_master:
            idx = int(data.split("_")[-1])
            if 0 <= idx < len(REGISTRY["global_channels"]):
                REGISTRY["global_channels"].pop(idx)
                save_registry(REGISTRY)
                await query.answer("تم حذف القناة")
                await query.message.edit_reply_markup(reply_markup=self.build_channels_manager_markup(user.id))
            return

        # قنوات إضافية لصانع البوت الفرعي
        if data == "ch_add" and not self.is_master:
            await query.answer()
            self.pending_actions[user.id] = {"action": "add_extra_channel"}
            await query.message.reply_text(
                "➕ أرسل معرف قناتك واسمها مفصولين بعلامة | مثل:\n<code>ChannelUsername | اسم القناة الظاهر</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if data.startswith("ch_toggle_") and not self.is_master:
            idx = int(data.split("_")[-1])
            if 0 <= idx < len(self.db["extra_channels"]):
                self.db["extra_channels"][idx]["enabled"] = not self.db["extra_channels"][idx].get("enabled", True)
                self.save_db()
                await query.answer("تم التبديل")
                await query.message.edit_reply_markup(reply_markup=self.build_channels_manager_markup(user.id))
            return

        if data.startswith("ch_rename_") and not self.is_master:
            idx = int(data.split("_")[-1])
            if 0 <= idx < len(self.db["extra_channels"]):
                await query.answer()
                self.pending_actions[user.id] = {"action": "rename_extra_channel", "idx": idx}
                await query.message.reply_text("✏️ أرسل الاسم الجديد الذي سيظهر على زر قناتك:")
            return

        if data.startswith("ch_del_") and not self.is_master:
            idx = int(data.split("_")[-1])
            if 0 <= idx < len(self.db["extra_channels"]):
                self.db["extra_channels"].pop(idx)
                self.save_db()
                await query.answer("تم حذف القناة الإضافية")
                await query.message.edit_reply_markup(reply_markup=self.build_channels_manager_markup(user.id))
            return

        # إدارة الألعاب
        if data == "adm_games":
            await query.answer()
            await query.message.edit_text(
                "🎮 <b>إدارة الألعاب والوقت والنقاط</b>",
                reply_markup=self.build_games_manager_markup(),
                parse_mode=ParseMode.HTML,
            )
            return

        if data.startswith("tog_game_"):
            g_key = data.replace("tog_game_", "")
            self.db["settings"]["games"][g_key] = not self.db["settings"]["games"].get(g_key, True)
            self.save_db()
            await query.answer("تم تحديث حالة اللعبة")
            await query.message.edit_reply_markup(reply_markup=self.build_games_manager_markup())
            return

        if data == "stop_all_games":
            self.active_games.clear()
            self.xo_waiting_rooms.clear()
            self.xo_sessions.clear()
            await query.answer("تم إنهاء جميع الجولات النشطة", show_alert=True)
            return

        if data == "cycle_q_sec":
            cur = self.db["settings"].get("question_seconds", 25)
            nxt = {15: 25, 25: 40, 40: 15}.get(cur, 25)
            self.db["settings"]["question_seconds"] = nxt
            self.save_db()
            await query.answer(f"تم ضبط الوقت إلى {nxt} ثانية")
            await query.message.edit_reply_markup(reply_markup=self.build_games_manager_markup())
            return

        if data == "cycle_w_pts":
            cur = self.db["settings"].get("win_points", 10)
            nxt = {5: 10, 10: 20, 20: 5}.get(cur, 10)
            self.db["settings"]["win_points"] = nxt
            self.save_db()
            await query.answer(f"تم ضبط نقاط الفوز إلى {nxt}")
            await query.message.edit_reply_markup(reply_markup=self.build_games_manager_markup())
            return

        # إدارة النقاط
        if data == "adm_points":
            await query.answer()
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🏆 عرض أعلى 10 لاعبين", callback_data="leaderboard")],
                [InlineKeyboardButton("⚠️ تصفير جميع النقاط", callback_data="reset_all_points_confirm")],
                [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="adm_home")],
            ])
            await query.message.edit_text("🏆 <b>إدارة النقاط والترتيب</b>", reply_markup=markup, parse_mode=ParseMode.HTML)
            return

        if data == "reset_all_points_confirm":
            for u in self.db["users"].values():
                u["points"] = 0
            self.save_db()
            self.log_admin("تصفير نقاط جميع المستخدمين")
            await query.answer("✅ تم تصفير جميع النقاط بنجاح", show_alert=True)
            return

        # إحصائيات ومعلومات المستخدمين
        if data == "adm_users_stats":
            await query.answer()
            st = self.compute_users_stats()
            txt = (
                f"📊 <b>إحصائيات المستخدمين والمجموعات</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"• إجمالي المستخدمين: <b>{st['total']}</b>\n"
                f"• النشطون (آخر 24 ساعة): <b>{st['active_24h']}</b>\n"
                f"• النشطون (آخر 7 أيام): <b>{st['active_7d']}</b>\n"
                f"• من حظروا البوت: <b>{st['blocked']}</b>\n"
                f"• المحظورون إدارياً: <b>{st['banned']}</b>\n"
                f"• المجموعات: <b>{len(self.db['groups'])}</b> | القنوات: <b>{len(self.db['channels_registered'])}</b>"
            )
            await query.message.edit_text(txt, reply_markup=self.build_users_stats_markup(), parse_mode=ParseMode.HTML)
            return

        if data.startswith("users_list_"):
            page = int(data.split("_")[-1])
            all_u = list(self.db["users"].values())
            per_page = 8
            total_pages = max(1, (len(all_u) + per_page - 1) // per_page)
            page = max(0, min(page, total_pages - 1))
            chunk = all_u[page * per_page : (page + 1) * per_page]

            await query.answer()
            rows = []
            for u in chunk:
                rows.append([
                    InlineKeyboardButton(
                        f"{u.get('name', 'مستخدم')} | {u.get('username')} | {u.get('id')}",
                        callback_data=f"user_view_{u.get('id')}",
                    )
                ])
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"users_list_{page - 1}"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"users_list_{page + 1}"))
            if nav:
                rows.append(nav)
            rows.append([InlineKeyboardButton("⬅️ رجوع للإحصائيات", callback_data="adm_users_stats")])

            await query.message.edit_text(
                f"📋 <b>سجل المستخدمين</b> (صفحة {page + 1}/{total_pages}):",
                reply_markup=InlineKeyboardMarkup(rows),
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "user_search_prompt":
            await query.answer()
            self.pending_actions[user.id] = {"action": "search_user"}
            await query.message.reply_text("🔍 أرسل الآن <b>الآيدي (ID)</b> أو <b>المعرف (@username)</b> أو <b>جزء من الاسم</b> للبحث:", parse_mode=ParseMode.HTML)
            return

        if data.startswith("user_view_"):
            target_id = int(data.split("_")[-1])
            u = self.db["users"].get(str(target_id))
            if not u:
                await query.answer("المستخدم غير موجود في السجل.", show_alert=True)
                return
            await query.answer()
            await query.message.reply_text(
                self.format_user_card(u),
                reply_markup=self.build_user_card_markup(target_id, u.get("status") == "banned"),
                parse_mode=ParseMode.HTML,
            )
            return

        if data.startswith("u_ban_"):
            target_id = int(data.split("_")[-1])
            u = self.db["users"].get(str(target_id))
            if u:
                u["status"] = "active" if u.get("status") == "banned" else "banned"
                self.save_db()
                self.log_admin(f"تغيير حالة الحظر للمستخدم {target_id} إلى {u['status']}")
                await query.answer("تم تحديث حالة الحظر")
                await query.message.edit_text(
                    self.format_user_card(u),
                    reply_markup=self.build_user_card_markup(target_id, u.get("status") == "banned"),
                    parse_mode=ParseMode.HTML,
                )
            return

        if data.startswith("u_pts_"):
            target_id = int(data.split("_")[-1])
            await query.answer()
            self.pending_actions[user.id] = {"action": "set_user_points", "target_id": target_id}
            await query.message.reply_text("⭐ أرسل القيمة الجديدة للنقاط (رقم صحيح، مثل 50 أو 0):")
            return

        if data.startswith("u_msg_"):
            target_id = int(data.split("_")[-1])
            await query.answer()
            self.pending_actions[user.id] = {"action": "direct_msg", "target_id": target_id}
            await query.message.reply_text("✉️ أرسل الرسالة التي تريد إيصالها لهذا المستخدم:")
            return

        if data == "check_blocked_users":
            await query.answer("جاري فحص حالة المستخدمين...", show_alert=False)
            for uid_str, u in self.db["users"].items():
                try:
                    await context.bot.send_chat_action(chat_id=int(uid_str), action="typing")
                    u["blocked_bot"] = False
                except Forbidden:
                    u["blocked_bot"] = True
                except Exception:
                    pass
            self.save_db()
            st = self.compute_users_stats()
            await query.message.reply_text(
                f"🔄 اكتمل الفحص! عدد من حظروا/أوقفوا البوت: <b>{st['blocked']}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "export_users_csv":
            await query.answer()
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["ID", "Username", "Name", "Points", "Status", "BlockedBot", "JoinedAt", "LastSeen"])
            for u in self.db["users"].values():
                writer.writerow([
                    u.get("id"),
                    u.get("username"),
                    u.get("name"),
                    u.get("points", 0),
                    u.get("status", "active"),
                    u.get("blocked_bot", False),
                    u.get("joined_at", ""),
                    u.get("last_seen", ""),
                ])
            csv_bytes = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
            csv_bytes.name = f"users_{self.bot_id}.csv"
            await query.message.reply_document(document=csv_bytes, caption="📥 كشف بيانات المستخدمين (CSV)")
            return

        # الإذاعة بأقسامها الأربعة
        if data == "adm_broadcast":
            await query.answer()
            self.pending_actions[user.id] = {"action": "await_broadcast_msg"}
            await query.message.reply_text(
                "📣 <b>قسم الإذاعة</b>\nأرسل الآن الرسالة التي تريد إذاعتها (نص أو صورة أو منشور):",
                parse_mode=ParseMode.HTML,
            )
            return

        if data.startswith("bcast_target_"):
            target_type = data.replace("bcast_target_", "")
            pending = self.pending_actions.get(user.id)
            if not pending or "bcast_msg" not in pending:
                await query.answer("انتهت جلسة الإذاعة، ابدأ من جديد.", show_alert=True)
                return

            targets: List[int] = []
            label = ""
            if target_type == "groups":
                targets = [int(k) for k in self.db["groups"].keys()]
                label = "المجموعات"
            elif target_type == "channels":
                targets = [int(k) for k in self.db["channels_registered"].keys()]
                label = "القنوات"
            elif target_type == "users":
                targets = [int(k) for k, v in self.db["users"].items() if not v.get("blocked_bot") and v.get("status") != "banned"]
                label = "المستخدمين"
            elif target_type == "all":
                u_ids = [int(k) for k, v in self.db["users"].items() if not v.get("blocked_bot") and v.get("status") != "banned"]
                g_ids = [int(k) for k in self.db["groups"].keys()]
                c_ids = [int(k) for k in self.db["channels_registered"].keys()]
                targets = list(dict.fromkeys(u_ids + g_ids + c_ids))
                label = "الكل"

            pending["target_type"] = target_type
            pending["targets"] = targets
            pending["label"] = label

            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ تأكيد الإرسال", callback_data="bcast_confirm"),
                    InlineKeyboardButton("❌ إلغاء", callback_data="adm_home"),
                ]
            ])
            await query.answer()
            await query.message.edit_text(
                f"📣 <b>تأكيد الإذاعة</b>\n• القسم المستهدف: <b>{label}</b>\n• العدد المستهدف: <b>{len(targets)}</b>",
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "bcast_confirm":
            pending = self.pending_actions.pop(user.id, None)
            if not pending or "targets" not in pending or "bcast_msg" not in pending:
                await query.answer("لا توجد إذاعة جاهزة للتنفيذ.", show_alert=True)
                return

            src_msg = pending["bcast_msg"]
            targets = pending["targets"]
            label = pending.get("label", "")
            await query.answer("جاري بدء الإذاعة...")
            await query.message.edit_text(f"⏳ جاري إرسال الإذاعة إلى ({label}) لعدد {len(targets)} جهة...")

            ok_cnt = 0
            fail_cnt = 0
            for cid in targets:
                try:
                    await context.bot.copy_message(
                        chat_id=cid,
                        from_chat_id=src_msg.chat_id,
                        message_id=src_msg.message_id,
                    )
                    ok_cnt += 1
                    if str(cid) in self.db["users"]:
                        self.db["users"][str(cid)]["blocked_bot"] = False
                except Forbidden:
                    fail_cnt += 1
                    if str(cid) in self.db["users"]:
                        self.db["users"][str(cid)]["blocked_bot"] = True
                except Exception:
                    fail_cnt += 1
                await asyncio.sleep(0.05)

            self.save_db()
            self.log_admin(f"إذاعة إلى [{label}]: نجح {ok_cnt}، فشل {fail_cnt}")
            await query.message.reply_text(
                f"✅ <b>اكتملت الإذاعة ({label})</b>\n• تم الإرسال بنجاح: <b>{ok_cnt}</b>\n• تعذر الوصول / محظور: <b>{fail_cnt}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "adm_admin_cfg":
            await query.answer()
            logs_txt = "\n".join(self.db.get("admin_logs", [])[:8]) or "لا توجد عمليات مسجلة بعد."
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("💾 تحميل نسخة احتياطية (JSON)", callback_data="download_backup_json")],
                [InlineKeyboardButton("⬅️ رجوع للوحة التحكم", callback_data="adm_home")],
            ])
            await query.message.edit_text(
                f"🛡️ <b>النسخ الاحتياطي وسجل العمليات</b>\n━━━━━━━━━━━━━━━━━━━━\n{logs_txt}",
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            return

        if data == "download_backup_json":
            await query.answer()
            raw = json.dumps(self.db, ensure_ascii=False, indent=2).encode("utf-8")
            bio = io.BytesIO(raw)
            bio.name = f"backup_{self.bot_id}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
            await query.message.reply_document(document=bio, caption="💾 نسخة احتياطية لبيانات البوت")
            return

    # ================= معالج الرسائل =================
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if not msg or not user:
            return

        if chat:
            self.register_chat(chat)
        self.register_user(user)

        txt = (msg.text or "").strip()

        if txt == "ابدأ":
            await self.cmd_start(update, context)
            return

        if txt == "لوحة التحكم":
            if self.is_bot_admin(user.id):
                await self.cmd_admin(update, context)
            return

        if txt == "🤖 اصنع بوتك بنفسك" and self.is_master:
            ok = await self.guard_user(update, context, is_group_action=False)
            if ok:
                await self.open_factory_menu(update, context, edit=False)
            return

        # معالجة إنشاء بوت فرعي عبر التوكن
        if self.is_master and user.id in self.pending_actions:
            act_info = self.pending_actions[user.id]
            if act_info.get("action") == "create_clone_token" and txt:
                self.pending_actions.pop(user.id, None)
                token_candidate = txt.strip()
                if not re.match(r"^\d{6,15}:[A-Za-z0-9_-]{30,50}$", token_candidate):
                    await msg.reply_text("❌ صيغة التوكن غير صحيحة. تأكد من نسخه كاملاً من @BotFather.")
                    return
                if token_candidate == RAW_TOKEN:
                    await msg.reply_text("❌ هذا توكن البوت الرئيسي نفسه!")
                    return

                wait_msg = await msg.reply_text("⏳ جاري التحقق من التوكن وتشغيل بوتك الآن...")
                try:
                    new_app = await launch_bot_from_token(
                        token=token_candidate,
                        is_master=False,
                        clone_owner_id=user.id,
                    )
                    me = await new_app.bot.get_me()
                    bid = str(me.id)
                    REGISTRY["clones"][bid] = {
                        "bot_id": bid,
                        "token": token_candidate,
                        "username": me.username,
                        "title": me.first_name,
                        "creator_id": user.id,
                        "creator_name": user.full_name,
                        "created_at": now_str(),
                        "active": True,
                    }
                    save_registry(REGISTRY)
                    await wait_msg.edit_text(
                        f"🎉 <b>تم إنشاء وتشغيل بوتك بنجاح!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"• معرف البوت: @{me.username}\n"
                        f"• الاسم: <b>{me.first_name}</b>\n\n"
                        f"ادخل إلى بوتك الآن واضغط <code>/start</code> وستجد زر <b>لوحة التحكم</b> جاهزاً لك لإدارة الإحصائيات والمجموعات والقنوات والإذاعة.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as e:
                    logger.error(f"Clone creation failed: {e}")
                    await wait_msg.edit_text(f"❌ تعذر تشغيل البوت. تأكد من صحة التوكن وأنه غير مستخدم في مكان آخر.\nالسبب: <code>{e}</code>", parse_mode=ParseMode.HTML)
                return

        # معالجة مهام الأدمن المعلقة
        if self.is_bot_admin(user.id) and user.id in self.pending_actions:
            act_info = self.pending_actions[user.id]
            action = act_info.get("action")

            if action == "set_welcome" and txt:
                self.db["settings"]["welcome_text"] = txt
                self.save_db()
                self.pending_actions.pop(user.id, None)
                await msg.reply_text("✅ تم تحديث رسالة الترحيب.", reply_markup=self.build_bot_settings_markup())
                return

            if action == "set_forcesub_msg" and txt:
                self.db["settings"]["force_sub_text"] = txt
                self.save_db()
                self.pending_actions.pop(user.id, None)
                await msg.reply_text("✅ تم تحديث رسالة الاشتراك الإجباري.", reply_markup=self.build_bot_settings_markup())
                return

            if action == "add_global_channel" and txt and user.id == OWNER_ID and self.is_master:
                if "|" in txt:
                    u_part, t_part = txt.split("|", 1)
                    clean_u = u_part.strip().lstrip("@")
                    clean_t = t_part.strip() or clean_u
                    if clean_u:
                        REGISTRY["global_channels"].append({"username": clean_u, "title": clean_t, "enabled": True})
                        save_registry(REGISTRY)
                        self.pending_actions.pop(user.id, None)
                        await msg.reply_text("✅ تمت إضافة القناة الإلزامية لجميع البوتات.", reply_markup=self.build_channels_manager_markup(user.id))
                        return
                await msg.reply_text("⚠️ الصيغة غير صحيحة. اكتب: <code>username | اسم القناة</code>", parse_mode=ParseMode.HTML)
                return

            if action == "rename_global_channel" and txt and user.id == OWNER_ID and self.is_master:
                idx = act_info.get("idx", -1)
                if 0 <= idx < len(REGISTRY["global_channels"]):
                    REGISTRY["global_channels"][idx]["title"] = txt
                    save_registry(REGISTRY)
                    self.pending_actions.pop(user.id, None)
                    await msg.reply_text("✅ تم تعديل اسم القناة الأساسية.", reply_markup=self.build_channels_manager_markup(user.id))
                return

            if action == "add_extra_channel" and txt and not self.is_master:
                if "|" in txt:
                    u_part, t_part = txt.split("|", 1)
                    clean_u = u_part.strip().lstrip("@")
                    clean_t = t_part.strip() or clean_u
                    if clean_u:
                        self.db["extra_channels"].append({"username": clean_u, "title": clean_t, "enabled": True})
                        self.save_db()
                        self.pending_actions.pop(user.id, None)
                        await msg.reply_text("✅ تمت إضافة قناتك الإضافية.", reply_markup=self.build_channels_manager_markup(user.id))
                        return
                await msg.reply_text("⚠️ الصيغة غير صحيحة. اكتب: <code>username | اسم القناة</code>", parse_mode=ParseMode.HTML)
                return

            if action == "rename_extra_channel" and txt and not self.is_master:
                idx = act_info.get("idx", -1)
                if 0 <= idx < len(self.db["extra_channels"]):
                    self.db["extra_channels"][idx]["title"] = txt
                    self.save_db()
                    self.pending_actions.pop(user.id, None)
                    await msg.reply_text("✅ تم تعديل اسم قناتك.", reply_markup=self.build_channels_manager_markup(user.id))
                return

            if action == "search_user" and txt:
                self.pending_actions.pop(user.id, None)
                q_low = txt.lower().lstrip("@")
                matches = []
                for u in self.db["users"].values():
                    uid_s = str(u.get("id", ""))
                    uname_s = str(u.get("username", "")).lower().lstrip("@")
                    name_s = str(u.get("name", "")).lower()
                    if q_low == uid_s or q_low in uname_s or q_low in name_s:
                        matches.append(u)
                if not matches:
                    await msg.reply_text("❌ لم يتم العثور على مستخدم مطابق.")
                    return
                for u in matches[:5]:
                    await msg.reply_text(
                        self.format_user_card(u),
                        reply_markup=self.build_user_card_markup(u["id"], u.get("status") == "banned"),
                        parse_mode=ParseMode.HTML,
                    )
                return

            if action == "set_user_points" and txt:
                try:
                    val = int(txt)
                    tid = act_info["target_id"]
                    uid_s = str(tid)
                    if uid_s in self.db["users"]:
                        self.db["users"][uid_s]["points"] = max(0, val)
                        self.save_db()
                        self.pending_actions.pop(user.id, None)
                        await msg.reply_text(f"✅ تم تحديث نقاط المستخدم إلى {self.db['users'][uid_s]['points']}.")
                    return
                except ValueError:
                    await msg.reply_text("⚠️ أرسل رقماً صحيحاً.")
                    return

            if action == "direct_msg":
                tid = act_info["target_id"]
                self.pending_actions.pop(user.id, None)
                try:
                    await context.bot.copy_message(chat_id=tid, from_chat_id=msg.chat_id, message_id=msg.message_id)
                    await msg.reply_text("✅ تم إرسال الرسالة بنجاح.")
                except Exception as e:
                    await msg.reply_text(f"❌ تعذر الإرسال: {e}")
                return

            if action == "await_broadcast_msg":
                self.pending_actions[user.id] = {"action": "choose_bcast_target", "bcast_msg": msg}
                markup = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("👥 إذاعة للمجموعات", callback_data="bcast_target_groups"),
                        InlineKeyboardButton("📢 إذاعة للقنوات", callback_data="bcast_target_channels"),
                    ],
                    [
                        InlineKeyboardButton("👤 إذاعة للمستخدمين", callback_data="bcast_target_users"),
                        InlineKeyboardButton("🌍 إذاعة للكل", callback_data="bcast_target_all"),
                    ],
                    [InlineKeyboardButton("❌ إلغاء", callback_data="adm_home")],
                ])
                await msg.reply_text("اختر القسم الذي تريد توجيه الإذاعة إليه:", reply_markup=markup)
                return

        # التحقق من تخمين الكلمة في المحادثة
        if chat and txt:
            game = self.active_games.get(chat.id)
            if game and game.get("type") == "word":
                if txt.strip() == game["word"]:
                    ok = await self.guard_user(update, context, is_group_action=(chat.type != ChatType.PRIVATE))
                    if not ok:
                        return
                    pts = self.db["settings"].get("win_points", 10)
                    new_pts = self.add_points(user.id, pts)
                    self.active_games.pop(chat.id, None)
                    await msg.reply_text(
                        f"🎉 <b>أحسنت يا {user.full_name}!</b>\nالكلمة الصحيحة هي: <b>{txt.strip()}</b>\n🏆 ربحت +{pts} نقاط (المجموع: {new_pts})",
                        parse_mode=ParseMode.HTML,
                    )


# ================= تشغيل وإيقاف البوتات الديناميكية =================
async def launch_bot_from_token(token: str, is_master: bool, clone_owner_id: int) -> Application:
    app = Application.builder().token(token).build()
    await app.initialize()
    me = await app.bot.get_me()
    bot_id = "master" if is_master else str(me.id)

    if not is_master and bot_id in RUNNING_CLONES:
        await stop_clone_bot(bot_id)

    instance = BotInstance(bot_id=bot_id, token=token, is_master=is_master, clone_owner_id=clone_owner_id)
    app.add_handler(CommandHandler("start", instance.cmd_start))
    app.add_handler(CommandHandler("admin", instance.cmd_admin))
    app.add_handler(CallbackQueryHandler(instance.handle_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, instance.handle_message))

    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    if not is_master:
        RUNNING_CLONES[bot_id] = app
    logger.info(f"Started bot @{me.username} (id={bot_id}, master={is_master})")
    return app


async def stop_clone_bot(bot_id: str) -> None:
    app = RUNNING_CLONES.pop(bot_id, None)
    if app:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception as e:
            logger.warning(f"Error stopping clone {bot_id}: {e}")


async def main_async() -> None:
    if not RAW_TOKEN or RAW_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError(
            "يرجى فتح ملف config.py ووضع توكن البوت مكان PUT_YOUR_BOT_TOKEN_HERE قبل التشغيل."
        )

    master_app = await launch_bot_from_token(
        token=RAW_TOKEN,
        is_master=True,
        clone_owner_id=OWNER_ID,
    )

    # إعادة تشغيل البوتات الفرعية المحفوظة تلقائياً عند إقلاع السيرفر
    for bid, c_info in list(REGISTRY.get("clones", {}).items()):
        if c_info.get("active", True) and c_info.get("token"):
            try:
                await launch_bot_from_token(
                    token=c_info["token"],
                    is_master=False,
                    clone_owner_id=int(c_info["creator_id"]),
                )
            except Exception as e:
                logger.error(f"Failed to restore clone {bid}: {e}")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        for bid in list(RUNNING_CLONES.keys()):
            await stop_clone_bot(bid)
        await master_app.updater.stop()
        await master_app.stop()
        await master_app.shutdown()


if __name__ == "__main__":
    asyncio.run(main_async())
