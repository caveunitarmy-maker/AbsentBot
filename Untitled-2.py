import json
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Literal

import discord
import gspread
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()

KST = timezone(timedelta(hours=9))
ADMINS_FILE = "admins.json"
TRACKING_FILE = "tracking_state.json"
SUPER_ADMIN_ID = 942558158436589640

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

GUILD_ID = os.getenv("GUILD_ID")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME")
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN이 설정되지 않았습니다.")
if not SPREADSHEET_NAME:
    raise ValueError("SPREADSHEET_NAME이 설정되지 않았습니다.")
if not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("GOOGLE_CREDENTIALS_JSON이 설정되지 않았습니다.")
if not GUILD_ID:
    raise ValueError("GUILD_ID가 설정되지 않았습니다.")

GUILD_ID = int(GUILD_ID)
GUILD_OBJECT = discord.Object(id=GUILD_ID)

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(GOOGLE_CREDENTIALS_JSON), scope
)
client = gspread.authorize(creds)
spreadsheet = client.open(SPREADSHEET_NAME)
sheet = spreadsheet.get_worksheet(0)


def run_web_server():
    port = int(os.getenv("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")

        def log_message(self, format, *args):
            return

    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


def load_json_file(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_admin_ids() -> set[int]:
    data = load_json_file(ADMINS_FILE, [])
    try:
        return {int(user_id) for user_id in data if int(user_id) != SUPER_ADMIN_ID}
    except Exception:
        return set()


def save_admin_ids() -> None:
    save_json_file(ADMINS_FILE, sorted(admin_ids))


def load_tracking_state() -> bool:
    data = load_json_file(TRACKING_FILE, {"enabled": False})
    return bool(data.get("enabled", False))


def save_tracking_state() -> None:
    save_json_file(TRACKING_FILE, {"enabled": tracking_enabled})


def is_admin_user(user_id: int) -> bool:
    return user_id == SUPER_ADMIN_ID or user_id in admin_ids


async def require_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild_id != GUILD_ID:
        await interaction.response.send_message("이 서버에서만 사용할 수 있습니다.", ephemeral=True)
        return False

    if not is_admin_user(interaction.user.id):
        await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
        return False

    return True


def now_kst() -> datetime:
    return datetime.now(KST)


def get_today_sheet_name() -> str:
    return now_kst().strftime("%Y. %m. %d.")


def create_sheet() -> str:
    global sheet
    title = get_today_sheet_name()

    try:
        try:
            sheet = spreadsheet.worksheet(title)
            return f"이미 존재하는 시트로 전환됨: {title}"
        except WorksheetNotFound:
            template_sheet = spreadsheet.get_worksheet(0)
            new_sheet = template_sheet.duplicate(new_sheet_name=title)
            new_sheet.batch_clear(["A2:H2"])

            if new_sheet.row_count > 2:
                new_sheet.delete_rows(3, new_sheet.row_count)

            sheet = new_sheet
            return f"새 시트 생성 완료: {title}"
    except Exception as e:
        return f"오류 발생: {e}"


def ensure_sheet_rows(target_row: int) -> None:
    rows_to_add = target_row - sheet.row_count
    if rows_to_add > 0:
        sheet.add_rows(rows_to_add)


async def is_kicked_or_banned(member: discord.Member) -> bool:
    now = datetime.now(timezone.utc)

    for action in (discord.AuditLogAction.kick, discord.AuditLogAction.ban):
        async for entry in member.guild.audit_logs(limit=10, action=action):
            if entry.target and entry.target.id == member.id:
                if (now - entry.created_at).total_seconds() < 10:
                    return True
    return False


def get_admin_status_text(guild: discord.Guild | None) -> str:
    lines = [f"총관리자: <@{SUPER_ADMIN_ID}>"]

    if not admin_ids:
        lines.append("일반 관리자: 없음")
        return "\n".join(lines)

    lines.append("일반 관리자:")
    for user_id in sorted(admin_ids):
        member = guild.get_member(user_id) if guild else None
        lines.append(member.mention if member else f"`{user_id}`")
    return "\n".join(lines)


admin_ids = load_admin_ids()
tracking_enabled = load_tracking_state()


@bot.event
async def on_ready():
    print(f"봇 실행됨: {bot.user}")
    print(create_sheet())

    if not create_new_sheet.is_running():
        create_new_sheet.start()

    try:
        synced = await bot.tree.sync(guild=GUILD_OBJECT)
        print(f"길드 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        print(f"명령어 동기화 오류: {e}")


@tasks.loop(minutes=1)
async def create_new_sheet():
    now = now_kst()
    if now.hour == 0 and now.minute == 0:
        print(create_sheet())


@bot.tree.command(name="워크시트추가", description="새 날짜 시트를 생성합니다")
@app_commands.guild_only()
@app_commands.guilds(GUILD_OBJECT)
async def add_sheet(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(create_sheet(), ephemeral=True)


@bot.tree.command(name="관리자변경", description="관리자를 추가하거나 제거합니다")
@app_commands.guild_only()
@app_commands.guilds(GUILD_OBJECT)
@app_commands.describe(action="관리자추가 또는 관리자제거", 멤버="대상 멤버")
async def manage_admin(
    interaction: discord.Interaction,
    action: Literal["관리자추가", "관리자제거"],
    멤버: discord.Member,
):
    if not await require_admin(interaction):
        return

    if 멤버.id == SUPER_ADMIN_ID:
        await interaction.response.send_message("총관리자는 변경할 수 없습니다.", ephemeral=True)
        return

    if action == "관리자추가":
        if 멤버.id in admin_ids:
            await interaction.response.send_message(f"{멤버.mention} 은(는) 이미 관리자입니다.", ephemeral=True)
            return

        admin_ids.add(멤버.id)
        save_admin_ids()
        await interaction.response.send_message(f"{멤버.mention} 을(를) 관리자로 추가했습니다.", ephemeral=True)
        return

    if 멤버.id not in admin_ids:
        await interaction.response.send_message(f"{멤버.mention} 은(는) 등록된 관리자가 아닙니다.", ephemeral=True)
        return

    admin_ids.remove(멤버.id)
    save_admin_ids()
    await interaction.response.send_message(f"{멤버.mention} 을(를) 관리자에서 제거했습니다.", ephemeral=True)


@bot.tree.command(name="관리자현황", description="현재 등록된 관리자 목록을 확인합니다")
@app_commands.guild_only()
@app_commands.guilds(GUILD_OBJECT)
async def admin_status(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    await interaction.response.send_message(
        get_admin_status_text(interaction.guild),
        ephemeral=True,
    )


@bot.tree.command(name="추적시작", description="퇴장 추적을 시작합니다")
@app_commands.guild_only()
@app_commands.guilds(GUILD_OBJECT)
async def start_tracking(interaction: discord.Interaction):
    global tracking_enabled

    if not await require_admin(interaction):
        return

    tracking_enabled = True
    save_tracking_state()
    await interaction.response.send_message("퇴장 추적을 시작했습니다.", ephemeral=True)


@bot.tree.command(name="추적정지", description="퇴장 추적을 중지합니다")
@app_commands.guild_only()
@app_commands.guilds(GUILD_OBJECT)
async def stop_tracking(interaction: discord.Interaction):
    global tracking_enabled

    if not await require_admin(interaction):
        return

    tracking_enabled = False
    save_tracking_state()
    await interaction.response.send_message("퇴장 추적을 중지했습니다.", ephemeral=True)


@bot.tree.command(name="봇상태", description="현재 봇 상태를 확인합니다")
@app_commands.guild_only()
@app_commands.guilds(GUILD_OBJECT)
async def bot_status(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return

    tracking_text = "활성화" if tracking_enabled else "비활성화"
    current_sheet = sheet.title if sheet else "없음"

    await interaction.response.send_message(
        f"봇 상태: 작동 중\n"
        f"추적 상태: {tracking_text}\n"
        f"현재 워크시트: {current_sheet}\n"
        f"{get_admin_status_text(interaction.guild)}",
        ephemeral=True,
    )


@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != GUILD_ID or not tracking_enabled:
        return

    try:
        if await is_kicked_or_banned(member):
            print(f"추방/차단 사용자라 기록 생략: {member} ({member.id})")
            return

        user_ids = sheet.col_values(4)
        if str(member.id) in user_ids:
            print(f"중복 사용자라 기록 생략: {member} ({member.id})")
            return

        next_row = len(sheet.col_values(3)) + 1
        ensure_sheet_rows(next_row)
        sheet.update(f"C{next_row}:D{next_row}", [[str(member), str(member.id)]])
        print(f"기록 완료: {member} ({member.id})")
    except Exception as e:
        print(f"시트 기록 오류: {e}")


threading.Thread(target=run_web_server, daemon=True).start()
bot.run(TOKEN)
