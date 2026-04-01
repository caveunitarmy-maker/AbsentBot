import asyncio
import json
import os
import threading
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import discord
import gspread
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()

KST = timezone(timedelta(hours=9))
TRACKING_FILE = "tracking_state.json"
OWNER_USER_ID = 942558158436589640

intents = discord.Intents.default()
intents.members = True

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

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
gs_client = gspread.authorize(creds)
spreadsheet = gs_client.open(SPREADSHEET_NAME)
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


def load_tracking_state() -> bool:
    data = load_json_file(TRACKING_FILE, {"enabled": False})
    return bool(data.get("enabled", False))


def save_tracking_state() -> None:
    save_json_file(TRACKING_FILE, {"enabled": tracking_enabled})


def now_kst() -> datetime:
    return datetime.now(KST)


def get_today_sheet_name() -> str:
    return now_kst().strftime("%Y. %m. %d.")


def make_embed(title: str, description: str, color: int) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = now_kst()
    embed.set_footer(text="TDC Tracker")
    return embed


async def send_embed(
    interaction: discord.Interaction,
    title: str,
    description: str,
    color: int = 0xF1C40F,
    ephemeral: bool = True,
) -> None:
    embed = make_embed(title, description, color)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def require_owner(interaction: discord.Interaction) -> bool:
    if interaction.user.id != OWNER_USER_ID:
        await send_embed(
            interaction,
            "권한 없음",
            "관리자 ID로 지정된 사용자만 이 명령어를 사용할 수 있습니다.",
            color=0xE74C3C,
            ephemeral=True,
        )
        return False
    return True


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
    try:
        now = datetime.now(timezone.utc)

        for action in (discord.AuditLogAction.kick, discord.AuditLogAction.ban):
            async for entry in member.guild.audit_logs(limit=10, action=action):
                if entry.target and entry.target.id == member.id:
                    if (now - entry.created_at).total_seconds() < 10:
                        return True
    except Exception as e:
        print(f"감사 로그 확인 오류: {e}")
        traceback.print_exc()

    return False


tracking_enabled = load_tracking_state()


@bot.event
async def on_ready():
    print(f"봇 실행됨: {bot.user}")
    print(create_sheet())

    if not create_new_sheet.is_running():
        create_new_sheet.start()

    try:
        synced = await tree.sync(guild=GUILD_OBJECT)
        print(f"길드 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        print(f"명령어 동기화 오류: {e}")
        traceback.print_exc()


@bot.event
async def on_disconnect():
    print("디스코드 연결 끊김")


@bot.event
async def on_resumed():
    print("디스코드 연결 복구")


@tasks.loop(minutes=1)
async def create_new_sheet():
    now = now_kst()
    if now.hour == 0 and now.minute == 0:
        print(create_sheet())


@tree.command(name="워크시트추가", description="새 날짜 시트를 생성합니다", guild=GUILD_OBJECT)
@app_commands.guild_only()
async def add_sheet(interaction: discord.Interaction):
    if not await require_owner(interaction):
        return

    await interaction.response.defer(ephemeral=True)
    result = create_sheet()
    color = 0x2ECC71 if "완료" in result or "전환됨" in result else 0xE74C3C
    await send_embed(interaction, "워크시트 처리", result, color=color, ephemeral=True)


@tree.command(name="추적시작", description="퇴장 추적을 시작합니다", guild=GUILD_OBJECT)
@app_commands.guild_only()
async def start_tracking(interaction: discord.Interaction):
    global tracking_enabled

    if not await require_owner(interaction):
        return

    tracking_enabled = True
    save_tracking_state()
    await send_embed(
        interaction,
        "추적 시작",
        "퇴장 추적이 활성화되었습니다.",
        color=0x2ECC71,
        ephemeral=False,
    )


@tree.command(name="추적정지", description="퇴장 추적을 중지합니다", guild=GUILD_OBJECT)
@app_commands.guild_only()
async def stop_tracking(interaction: discord.Interaction):
    global tracking_enabled

    if not await require_owner(interaction):
        return

    tracking_enabled = False
    save_tracking_state()
    await send_embed(
        interaction,
        "추적 정지",
        "퇴장 추적이 비활성화되었습니다.",
        color=0xE67E22,
        ephemeral=False,
    )


@tree.command(name="봇상태", description="현재 봇 상태를 확인합니다", guild=GUILD_OBJECT)
@app_commands.guild_only()
async def bot_status(interaction: discord.Interaction):
    if not await require_owner(interaction):
        return

    tracking_text = "활성화" if tracking_enabled else "비활성화"
    current_sheet = sheet.title if sheet else "없음"

    await send_embed(
        interaction,
        "봇 상태",
        f"현재 상태: 작동 중\n"
        f"추적 상태: {tracking_text}\n"
        f"현재 워크시트: {current_sheet}\n"
        f"관리자 ID: `{OWNER_USER_ID}`",
        color=0x3498DB,
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

        rows = sheet.get("C:D")
        user_ids = {row[1] for row in rows if len(row) > 1 and row[1]}

        if str(member.id) in user_ids:
            print(f"중복 사용자라 기록 생략: {member} ({member.id})")
            return

        next_row = len(rows) + 1
        ensure_sheet_rows(next_row)
        sheet.update(f"C{next_row}:D{next_row}", [[str(member), str(member.id)]])
        print(f"기록 완료: {member} ({member.id})")
    except Exception as e:
        print(f"시트 기록 오류: {e}")
        traceback.print_exc()


async def main():
    retry_delay = 5

    while True:
        try:
            print("봇 실행 시도 중...")
            await bot.start(TOKEN)
        except Exception as e:
            print(f"봇이 종료됨: {e}")
            traceback.print_exc()
            print(f"{retry_delay}초 후 재시작합니다.")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 300)
        else:
            retry_delay = 5


threading.Thread(target=run_web_server, daemon=True).start()
asyncio.run(main())
