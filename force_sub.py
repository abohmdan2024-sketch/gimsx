# -*- coding: utf-8 -*-
"""
وحدة مستقلة لإدارة والتحقق من الاشتراك الإجباري (Force Subscription)
متوافقة مع مكتبة python-telegram-bot (v20+) ونظام البوت الرئيسي والفرعي.
"""

import logging
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class ForceSubManager:
    """
    مدير الاشتراك الإجباري المستقل:
    - يدمج قنوات المطور الإلزامية مع القنوات الإضافية للبوت الفرعي.
    - يتحقق بدقة من حالة العضو في كل قناة (left, kicked, restricted).
    - يعرض أزرار القنوات وزر التحقق، ويعالج ضغطة التحقق من الاشتراك.
    """

    def __init__(
        self,
        owner_id: int,
        is_master: bool = True,
        default_text: str = "🔒 للوصول إلى الألعاب والتفاعل مع البوت، يرجى الاشتراك أولاً في القنوات التالية:",
    ):
        self.owner_id = owner_id
        self.is_master = is_master
        self.default_text = default_text

    def get_effective_channels(
        self,
        global_channels: List[Dict[str, Any]],
        extra_channels: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        دمج قنوات الإدارة الأساسية (مقفلة) مع القنوات الإضافية (إن وجدت) دون تكرار.
        """
        merged: List[Dict[str, Any]] = []
        seen = set()

        for ch in global_channels or []:
            if ch.get("enabled", True):
                uname = ch["username"].lstrip("@").lower()
                if uname and uname not in seen:
                    seen.add(uname)
                    merged.append(
                        {
                            "username": ch["username"].lstrip("@"),
                            "title": ch.get("title") or ch["username"],
                            "enabled": True,
                            "locked": True,
                        }
                    )

        for ch in extra_channels or []:
            if ch.get("enabled", True):
                uname = ch["username"].lstrip("@").lower()
                if uname and uname not in seen:
                    seen.add(uname)
                    merged.append(
                        {
                            "username": ch["username"].lstrip("@"),
                            "title": ch.get("title") or ch["username"],
                            "enabled": True,
                            "locked": False,
                        }
                    )

        return merged

    async def check_subscription(
        self,
        bot,
        user_id: int,
        global_channels: List[Dict[str, Any]],
        extra_channels: Optional[List[Dict[str, Any]]] = None,
        force_sub_enabled: bool = True,
        is_admin: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        فحص اشتراك المستخدم في القنوات المطلوبة وإرجاع قائمة القنوات غير المشترك بها.
        """
        if is_admin or user_id == self.owner_id:
            return []

        channels_to_check = self.get_effective_channels(global_channels, extra_channels)

        if not self.is_master:
            if not force_sub_enabled:
                channels_to_check = [c for c in channels_to_check if c.get("locked")]
        else:
            if not force_sub_enabled:
                return []

        missing: List[Dict[str, Any]] = []
        for ch in channels_to_check:
            uname = ch["username"].lstrip("@")
            chat_ref = f"@{uname}"
            try:
                member = await bot.get_chat_member(chat_id=chat_ref, user_id=user_id)
                if member.status in ("left", "kicked"):
                    missing.append(ch)
                elif member.status == "restricted" and getattr(member, "is_member", False) is False:
                    missing.append(ch)
            except TelegramError as e:
                logger.warning(f"Force sub check failed for {chat_ref} (user={user_id}): {e}")
                missing.append(ch)
            except Exception as e:
                logger.warning(f"Force sub unexpected error for {chat_ref} (user={user_id}): {e}")
                missing.append(ch)

        return missing

    def build_force_sub_markup(
        self,
        global_channels: List[Dict[str, Any]],
        extra_channels: Optional[List[Dict[str, Any]]] = None,
        force_sub_enabled: bool = True,
        verify_callback: str = "check_sub_again",
    ) -> InlineKeyboardMarkup:
        """
        بناء أزرار القنوات وزر التحقق من الاشتراك.
        """
        channels = self.get_effective_channels(global_channels, extra_channels)
        if not self.is_master and not force_sub_enabled:
            channels = [c for c in channels if c.get("locked")]

        rows = []
        for ch in channels:
            uname = ch["username"].lstrip("@")
            title = ch.get("title") or uname
            rows.append([InlineKeyboardButton(f"📢 {title}", url=f"https://t.me/{uname}")])

        rows.append([InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data=verify_callback)])
        return InlineKeyboardMarkup(rows)

    async def enforce(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        global_channels: List[Dict[str, Any]],
        extra_channels: Optional[List[Dict[str, Any]]] = None,
        force_sub_enabled: bool = True,
        force_sub_in_groups: bool = True,
        force_sub_text: Optional[str] = None,
        is_admin: bool = False,
        is_group_action: bool = False,
    ) -> bool:
        """
        التحقق وإرسال رسالة الاشتراك الإجباري تلقائياً عند عدم الاشتراك.
        تُرجع True إذا كان المستخدم مشتركاً، وFalse إذا تم منعه وإرسال رسالة الاشتراك.
        """
        user = update.effective_user
        chat = update.effective_chat
        if not user:
            return False

        if chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) and not force_sub_in_groups:
            return True

        missing = await self.check_subscription(
            bot=context.bot,
            user_id=user.id,
            global_channels=global_channels,
            extra_channels=extra_channels,
            force_sub_enabled=force_sub_enabled,
            is_admin=is_admin,
        )
        if not missing:
            return True

        text = force_sub_text or self.default_text
        markup = self.build_force_sub_markup(
            global_channels=global_channels,
            extra_channels=extra_channels,
            force_sub_enabled=force_sub_enabled,
        )

        if update.callback_query:
            try:
                await update.callback_query.answer("يرجى الاشتراك في القنوات أولاً", show_alert=False)
                await update.callback_query.message.reply_text(
                    text,
                    reply_markup=markup,
                    reply_to_message_id=update.callback_query.message.message_id if is_group_action else None,
                )
            except Exception as e:
                logger.warning(f"Failed sending force_sub callback prompt: {e}")
        elif update.effective_message:
            await update.effective_message.reply_text(
                text,
                reply_markup=markup,
                reply_to_message_id=update.effective_message.message_id if is_group_action else None,
            )

        return False
