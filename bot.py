import asyncio

import discord
from discord.app_commands import check
from discord.app_commands import check
from discord.ext import commands, tasks
import aiosqlite
from datetime import datetime, timedelta
from datetime import timezone

JST = timezone(timedelta(hours=9))

# ===== ログ管理サーバー設定 =====
LOG_GUILD_ID = 1500396286271295578
LOG_CATEGORY_ID = 1500396365199446086

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.guild_messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

announce_config = {}
scheduled = []
report_channels = {}

# =======================
# DB
# =======================
async def init_db():

    async with aiosqlite.connect("bot.db") as db:

        # reports
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            guild_id TEXT,
            title TEXT,
            detail TEXT,
            status TEXT,
            created_at TEXT
        )
        """)

        # report_settings
        await db.execute("""
        CREATE TABLE IF NOT EXISTS report_settings (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT
        )
        """)

        await db.commit()


# =========================
# ログチャンネル取得
# =========================

async def get_log_channel(guild_id):

    log_guild = bot.get_guild(LOG_GUILD_ID)

    if not log_guild:
        return None

    category = log_guild.get_channel(LOG_CATEGORY_ID)

    if not category:
        return None

    name = str(guild_id)

    for ch in category.text_channels:

        if ch.name == name:
            return ch

    return await log_guild.create_text_channel(
        name=name,
        category=category
    )

# =========================
# 通報セットアップ
# =========================

@bot.tree.command(
    name="reportsetup",
    description="通報送信先を設定"
)
async def reportsetup(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):

    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "管理者のみ",
            ephemeral=True
        )

    async with aiosqlite.connect("bot.db") as db:

        await db.execute("""
        INSERT OR REPLACE INTO report_settings
        (guild_id, channel_id)
        VALUES (?, ?)
        """, (
            str(interaction.guild.id),
            str(channel.id)
        ))

        await db.commit()

    report_channels[interaction.guild.id] = channel.id

    await interaction.response.send_message(
        f"通報送信先を {channel.mention} に設定しました",
        ephemeral=True
    )
# =========================
# 通報
# =========================

@bot.tree.command(
    name="report",
    description="匿名通報"
)
async def report(
    interaction: discord.Interaction,
    title: str,
    detail: str
):

    try:

        await interaction.response.defer(
            ephemeral=True
        )

        # =========================
        # 通報先取得
        # =========================

        channel_id = report_channels.get(
            interaction.guild.id
        )

        if not channel_id:

            async with aiosqlite.connect(
                "bot.db"
            ) as db:

                cur = await db.execute("""
                SELECT channel_id
                FROM report_settings
                WHERE guild_id=?
                """, (
                    str(interaction.guild.id),
                ))

                row = await cur.fetchone()

            if not row:

                return await interaction.followup.send(
                    "先に /reportsetup をしてください",
                    ephemeral=True
                )

            channel_id = int(row[0])

            report_channels[
                interaction.guild.id
            ] = channel_id

        log_ch = interaction.guild.get_channel(
            channel_id
        )

        if not log_ch:

            return await interaction.followup.send(
                "通報チャンネル取得失敗",
                ephemeral=True
            )

        # =========================
        # DB保存
        # =========================

        async with aiosqlite.connect(
            "bot.db"
        ) as db:

            cur = await db.execute("""
            INSERT INTO reports (
                user_id,
                guild_id,
                title,
                detail,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                str(interaction.user.id),
                str(interaction.guild.id),
                title,
                detail,
                "未対応",
                str(datetime.now())
            ))

            await db.commit()

            report_id = cur.lastrowid

        # =========================
        # 管理サーバーログ取得
        # =========================

        manage_log_ch = await get_log_channel(
            interaction.guild.id
        )

        # =========================
        # 通報Embed
        # =========================

        embed = discord.Embed(
            title="📨 匿名通報",
            color=0xff5555
        )

        embed.add_field(
            name="整理番号",
            value=report_id,
            inline=False
        )


        embed.add_field(
            name="内容",
            value=f"{title}\n{detail}",
            inline=False
        )

        embed.add_field(
            name="状態",
            value="未対応",
            inline=False
        )

        embed.add_field(
            name="日時",
            value=str(datetime.now()),
            inline=False
        )

        # =========================
        # サーバー通報先へ送信
        # =========================

        await log_ch.send(
            embed=embed,
            view=ReportView()
        )

        # =========================
        # 管理サーバーへログ
        # =========================

        if manage_log_ch:

            log_embed = discord.Embed(
                title="📋 通報ログ",
                color=0x2b2d31
            )

            log_embed.add_field(
                name="整理番号",
                value=report_id,
                inline=False
            )

            log_embed.add_field(
                name="サーバーID",
                value=interaction.guild.id,
                inline=False
            )

            log_embed.add_field(
               name="送信者ID",
                value=interaction.user.id,
                inline=False
            )


            log_embed.add_field(
                name="内容",
                value=f"{title}\n{detail}",
                inline=False
            )

            log_embed.add_field(
                name="日時",
                value=str(datetime.now()),
                inline=False
            )

            await manage_log_ch.send(
                embed=log_embed
            )

        # =========================
        # 完了通知
        # =========================

        await interaction.followup.send(
            f"匿名通報を送信しました\n整理番号: {report_id}",
            ephemeral=True
        )

    except Exception as e:

        await interaction.followup.send(
            f"エラー発生:\n{e}",
            ephemeral=True
        )

# =========================
# 通報返信モーダル
# =========================

class ReplyModal(discord.ui.Modal):

    def __init__(self, user_id, report_id):

        super().__init__(title="通報返信")

        self.user_id = user_id
        self.report_id = report_id

        self.title_input = discord.ui.TextInput(
            label="件名",
            max_length=100
        )

        self.detail_input = discord.ui.TextInput(
            label="内容",
            style=discord.TextStyle.paragraph,
            max_length=2000
        )

        self.add_item(self.title_input)
        self.add_item(self.detail_input)

    async def on_submit(self, interaction: discord.Interaction):

        try:

            user = await bot.fetch_user(
                int(self.user_id)
            )

            embed = discord.Embed(
                title="📩 運営からの返信",
                color=0x00ffcc
            )

            embed.add_field(
                name="件名",
                value=self.title_input.value,
                inline=False
            )

            embed.add_field(
                name="内容",
                value=self.detail_input.value,
                inline=False
            )

            embed.set_footer(
                text=f"通報ID: {self.report_id}"
            )

            await user.send(embed=embed)

            await interaction.response.send_message(
                "返信送信完了",
                ephemeral=True
            )

        except Exception as e:

            await interaction.response.send_message(
                f"DM送信失敗\n{e}",
                ephemeral=True
            )

# =========================
# 通報ボタン
# =========================

class ReportView(discord.ui.View):

    def __init__(self):

        super().__init__(timeout=None)

    @discord.ui.button(
        label="返信",
        style=discord.ButtonStyle.primary,
        custom_id="report_reply"
    )
    async def reply_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        embed = interaction.message.embeds[0]

        report_id = embed.fields[0].value

        async with aiosqlite.connect("bot.db") as db:

            cur = await db.execute("""
            SELECT user_id
            FROM reports
            WHERE id=?
            """, (report_id,))

            row = await cur.fetchone()

        if not row:

            return await interaction.response.send_message(
                "ユーザー取得失敗",
                ephemeral=True
            )

        modal = ReplyModal(
            row[0],
            report_id
        )

        await interaction.response.send_modal(modal)

    @discord.ui.button(
        label="調査中",
        style=discord.ButtonStyle.secondary,
        custom_id="report_checking"
    )
    async def checking_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        embed = interaction.message.embeds[0].copy()

        embed.color = 0xffff00

        embed.set_field_at(
            3,
            name="状態",
            value="調査中",
            inline=False
        )

        await interaction.message.edit(
            embed=embed
        )

        await interaction.response.send_message(
            "調査中へ変更",
            ephemeral=True
        )

    @discord.ui.button(
        label="対応済み",
        style=discord.ButtonStyle.success,
        custom_id="report_done"
    )
    async def done_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        embed = interaction.message.embeds[0].copy()

        embed.color = 0x00ff00

        embed.set_field_at(
            3,
            name="状態",
            value="対応済み",
            inline=False
        )

        await interaction.message.edit(
            embed=embed
        )

        await interaction.response.send_message(
            "対応済みへ変更",
            ephemeral=True
        )

    @discord.ui.button(
        label="却下",
        style=discord.ButtonStyle.danger,
        custom_id="report_reject"
    )
    async def reject_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        embed = interaction.message.embeds[0].copy()

        embed.color = 0xff0000

        embed.set_field_at(
            3,
            name="状態",
            value="却下",
            inline=False
        )

        await interaction.message.edit(
            embed=embed
        )

        await interaction.response.send_message(
            "却下へ変更",
            ephemeral=True
        )

# =======================
# アナウンス設定
# =======================
@bot.tree.command(name="announce_setup")
async def announce_setup(interaction: discord.Interaction,
                         source: discord.TextChannel,
                         target: discord.TextChannel):

    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("管理者のみ", ephemeral=True)

    announce_config[interaction.guild.id] = {
        "source": source.id,
        "target": target.id
    }

    await interaction.response.send_message("設定完了", ephemeral=True)

# =======================
# アナウンスUI
# =======================
class AnnounceView(discord.ui.View):
    def __init__(self, content, guild_id):
        super().__init__(timeout=60)
        self.content = content
        self.guild_id = guild_id

    def get_target(self, guild):
        conf = announce_config.get(self.guild_id)
        if not conf:
            return None
        return guild.get_channel(conf["target"])

    @discord.ui.button(label="通常送信", style=discord.ButtonStyle.primary)
    async def normal(self, interaction, button):
        ch = self.get_target(interaction.guild)
        if not ch:
            return await interaction.response.send_message("送信先なし", ephemeral=True)

        await ch.send(self.content)
        await interaction.response.send_message("送信完了", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Embed送信", style=discord.ButtonStyle.success)
    async def embed(self, interaction, button):
        ch = self.get_target(interaction.guild)
        if not ch:
            return await interaction.response.send_message("送信先なし", ephemeral=True)

        await ch.send(embed=discord.Embed(description=self.content))
        await interaction.response.send_message("送信完了", ephemeral=True)
        self.stop()

    @discord.ui.button(
        label="予約送信",
        style=discord.ButtonStyle.secondary
    )
    async def schedule(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_message(
            "送信日時を入力してください\n例: 2026/05/13 21:30:00",
            ephemeral=True
        )

        def check(m):
            return (
                m.author == interaction.user
                and m.channel == interaction.channel
            )

        try:
            msg = await bot.wait_for(
                "message",
                timeout=60,
                check=check
            )

            run_at = datetime.strptime(
                msg.content,
                "%Y/%m/%d %H:%M:%S"
            ).replace(tzinfo=JST)

            scheduled.append({
                "guild_id": interaction.guild.id,
                "content": self.content,
                "run_at": run_at
            })

            await interaction.followup.send(
                f"予約完了\n送信日時: {msg.content}",
                ephemeral=True
            )

            self.stop()

        except ValueError:
            await interaction.followup.send(
                "形式が違います\n例: 2026/05/13 21:30:00",
                ephemeral=True
            )

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "予約キャンセル",
                ephemeral=True
            )

        except Exception as e:
            await interaction.followup.send(
                f"エラー: {e}",
                ephemeral=True
            )

# =======================
# スケジューラー
# =======================
@tasks.loop(seconds=5)
async def scheduler():
    now = datetime.now(JST)

    for item in scheduled[:]:
        if now >= item["run_at"]:
            guild = bot.get_guild(item["guild_id"])
            conf = announce_config.get(item["guild_id"])

            if guild and conf:
                ch = guild.get_channel(conf["target"])
                if ch:
                    await ch.send(item["content"])

            scheduled.remove(item)

# =======================
# メッセージ監視
# =======================
@bot.event
async def on_message(message):

    if message.author.bot or not message.guild:
        return

    conf = announce_config.get(message.guild.id)

    if conf and message.channel.id == conf["source"]:
        view = AnnounceView(message.content, message.guild.id)
        await message.reply("送信方法を選択", view=view)

    await bot.process_commands(message)

# =======================
# 起動
# =======================
@bot.event
async def on_ready():

    await init_db()

    async with aiosqlite.connect("bot.db") as db:

        cur = await db.execute("""
        SELECT guild_id, channel_id
        FROM report_settings
        """)

        rows = await cur.fetchall()

        for row in rows:
            report_channels[int(row[0])] = int(row[1])

    await bot.tree.sync()

    bot.add_view(ReportView())

    if not scheduler.is_running():
        scheduler.start()

    print("READY OK:", bot.user)

import os
TOKEN = os.getenv("TOKEN")


bot.run(TOKEN)