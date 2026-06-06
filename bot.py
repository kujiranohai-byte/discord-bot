import asyncio
import discord
from discord import channel
from discord import guild
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
report_channels = {}

# =======================
# DB
# =======================
import aiosqlite

DB_PATH = "bot.db"

async def get_db_version(db):
    cur = await db.execute("SELECT value FROM db_meta WHERE key='version'")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def set_db_version(db, version: int):
    await db.execute("""
    INSERT OR REPLACE INTO db_meta (key, value)
    VALUES ('version', ?)
    """, (str(version),))

async def migrate(db):
    version = await get_db_version(db)

    # =========================
    # v1 初期構造
    # =========================
    if version < 1:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        guild_id TEXT,
        title TEXT,
        detail TEXT,
        status TEXT DEFAULT '未対応',
        created_at TEXT
    )
    """)

        await db.execute("""
    CREATE TABLE IF NOT EXISTS report_settings (
        guild_id TEXT PRIMARY KEY,
        channel_id TEXT
    )
    """)
        version = 1

    # =========================
    # v2 アナウンス設定永続化
    # =========================
    if version < 2:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS announce_settings (
            guild_id TEXT PRIMARY KEY,
            source_id TEXT,
            target_id TEXT
        )
        """)

        version = 2

    # =========================
    # v3 予約送信永続化
    # =========================
    if version < 3:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS scheduled (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT,
            content TEXT,
            run_at TEXT
        )
        """)

        version = 3

    # =========================
    # v4 インデックス最適化
    # =========================
    if version < 4:
        await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_reports_guild
        ON reports(guild_id)
        """)

        version = 4

    await set_db_version(db, version)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("PRAGMA journal_mode=WAL")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS db_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        await migrate(db)

        await db.commit()

async def load_report_settings():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT guild_id, channel_id FROM report_settings")
        rows = await cur.fetchall()

        return {int(g): int(c) for g, c in rows}
    
async def load_announce_settings():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
        SELECT guild_id, source_id, target_id FROM announce_settings
        """)
        rows = await cur.fetchall()

        return {
            int(g): {"source": int(s), "target": int(t)}
            for g, s, t in rows
        }
    
async def load_scheduled():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT guild_id, content, run_at FROM scheduled")
        rows = await cur.fetchall()

        return [
            {
                "guild_id": int(g),
                "content": c,
                "run_at": datetime.fromisoformat(t)
            }
            for g, c, t in rows
        ]
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
                value=str(datetime.now(JST).isoformat()),
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

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT user_id FROM reports WHERE id=?",
                (report_id,)
            )
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

    # =========================
    # 調査中
    # =========================
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
        report_id = embed.fields[0].value

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE reports SET status=? WHERE id=?",
                ("調査中", report_id)
            )
            await db.commit()

        embed.color = 0xffff00

        embed.set_field_at(
            2,
            name="状態",
            value="調査中",
            inline=False
        )

        await interaction.message.edit(embed=embed)

        await interaction.response.send_message(
            "調査中へ変更しました",
            ephemeral=True
        )

    # =========================
    # 対応済み
    # =========================
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
        report_id = embed.fields[0].value

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE reports SET status=? WHERE id=?",
                ("対応済み", report_id)
            )
            await db.commit()

        embed.color = 0x00ff00

        embed.set_field_at(
            2,
            name="状態",
            value="対応済み",
            inline=False
        )

        await interaction.message.edit(embed=embed)

        await interaction.response.send_message(
            "対応済みに変更しました",
            ephemeral=True
        )

    # =========================
    # 却下
    # =========================
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
        report_id = embed.fields[0].value

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE reports SET status=? WHERE id=?",
                ("却下", report_id)
            )
            await db.commit()

        embed.color = 0xff0000

        embed.set_field_at(
            2,
            name="状態",
            value="却下",
            inline=False
        )

        await interaction.message.edit(embed=embed)

        await interaction.response.send_message(
            "却下に変更しました",
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

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
    INSERT OR REPLACE INTO announce_settings
    (guild_id, source_id, target_id)
    VALUES (?, ?, ?)
    """, (
        str(interaction.guild.id),
        str(source.id),
        str(target.id)
    ))

        await db.commit()
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

    async def get_target(self, guild):

        async with aiosqlite.connect(DB_PATH) as db:

            cur = await db.execute("""
        SELECT target_id
        FROM announce_settings
        WHERE guild_id=?
        """, (str(self.guild_id),))

        row = await cur.fetchone()

        if not row:
            return None

        return guild.get_channel(int(row[0]))

    @discord.ui.button(label="通常送信", style=discord.ButtonStyle.primary)
    async def normal(self, interaction, button):
        ch = await self.get_target(interaction.guild)
        if not ch:
            return await interaction.response.send_message("送信先なし", ephemeral=True)

        await ch.send(self.content)
        await interaction.response.send_message("送信完了", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Embed送信", style=discord.ButtonStyle.success)
    async def embed(self, interaction, button):
        ch = await self.get_target(interaction.guild)
        if not ch:
            return await interaction.response.send_message("送信先なし", ephemeral=True)

        await ch.send(embed=discord.Embed(description=self.content))
        await interaction.response.send_message("送信完了", ephemeral=True)
        self.stop()

    @discord.ui.button(label="予約送信", style=discord.ButtonStyle.secondary)
    async def schedule(self, interaction, button):

        await interaction.response.send_message(
            "送信日時を入力してください\n例: 2026/05/13 21:30:00",
            ephemeral=True
        )

        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=60, check=check)

            run_at = datetime.strptime(msg.content, "%Y/%m/%d %H:%M:%S").replace(tzinfo=JST)

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("""
        INSERT INTO scheduled
        (guild_id, content, run_at)
        VALUES (?, ?, ?)
        """, (
                    str(interaction.guild.id),
                    self.content,
                    run_at.isoformat()
                ))
                await db.commit()

            await interaction.followup.send(f"予約完了\n送信日時: {msg.content}", ephemeral=True)
            self.stop()

        except ValueError:
            await interaction.followup.send("形式が違います\n例: 2026/05/13 21:30:00", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("予約キャンセル", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"エラー: {e}", ephemeral=True)

# =======================
# スケジューラー
# =======================

@tasks.loop(seconds=5)
async def scheduler():

    now = datetime.now(JST)

    async with aiosqlite.connect(DB_PATH) as db:

        cur = await db.execute("""
        SELECT id, guild_id, content, run_at
        FROM scheduled
        WHERE run_at <= ?
        """, (
            now.isoformat(),
        ))

        rows = await cur.fetchall()

        for row in rows:

            schedule_id = row[0]
            guild_id = int(row[1])
            content = row[2]

            cur2 = await db.execute("""
            SELECT target_id
            FROM announce_settings
            WHERE guild_id=?
            """, (str(guild_id),))

            conf = await cur2.fetchone()

            if conf:

                guild = bot.get_guild(guild_id)

                if guild:
                    ch = guild.get_channel(int(conf[0]))

                    if ch:
                        await ch.send(content)

            await db.execute("""
            DELETE FROM scheduled
            WHERE id=?
            """, (schedule_id,))

        await db.commit()

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

    global report_channels, announce_config

    report_channels = await load_report_settings()
    announce_config = await load_announce_settings()

    bot.add_view(ReportView())

    if not scheduler.is_running():
        scheduler.start()

    if FIRST_BOOT:
        await bot.tree.sync()

    print("READY:", bot.user)