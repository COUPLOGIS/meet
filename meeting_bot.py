#!/usr/bin/env python3
"""
TeamLog (팀록) — Hermes-integrated team task & meeting management bot.
Handles meeting detection, task extraction, @mentions, comments,
due-date notifications, and user management.

All data is stored in ~/meetings/ (wbs.json, data.json).
Telegram delivery is handled via Hermes send_message bridge (no separate bot token needed).

Usage:
  python3 meeting_bot.py process <message_text> <chat_id> [sender_name]
  python3 meeting_bot.py notify-due         # Check and send due-date notifications
  python3 meeting_bot.py notify-mentions    # Send pending mention notifications
  python3 meeting_bot.py add-task <title> <assignee> [due_date] [priority]
  python3 meeting_bot.py add-comment <task_id> <author> <text>
  python3 meeting_bot.py list-tasks [user]
  python3 meeting_bot.py invite <username> <chat_id> [role]
"""

import json
import os
import re
import sys
import time
import subprocess
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

# ── Configuration ──
DATA_DIR = Path.home() / "meetings"
WBS_FILE = DATA_DIR / "wbs.json"
DATA_FILE = DATA_DIR / "data.json"
USERS_FILE = DATA_DIR / "users.json"  # user registry with chat_ids
NOTIF_FILE = DATA_DIR / "notifications.json"  # pending notification queue

WBS_FILE.parent.mkdir(parents=True, exist_ok=True)

# ── Data loading ──
def load_json(path: Path, default=None) -> dict:
    if default is None:
        default = {}
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(path: Path, data: dict):
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_wbs() -> dict:
    return load_json(WBS_FILE, {"tasks": [], "users": {}})

def save_wbs(data: dict):
    save_json(WBS_FILE, data)

def load_users() -> dict:
    """Load user registry: {chat_id: {username, role, registered_at}}"""
    return load_json(USERS_FILE, {})

def save_users(data: dict):
    save_json(USERS_FILE, data)

def load_notifications() -> list:
    """Load pending notification queue"""
    return load_json(NOTIF_FILE, [])

def save_notifications(data: list):
    save_json(NOTIF_FILE, data)

# ── Telegram delivery (via Hermes send_message bridge) ──
def send_telegram(chat_id: str, message: str) -> bool:
    """Send message via Hermes gateway. Returns True if successful."""
    try:
        result = subprocess.run(
            ["hermes", "send", "--target", f"telegram:{chat_id}", "--message", message],
            capture_output=True, text=True, timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[send_telegram] Error: {e}", file=sys.stderr)
        return False

# ── User management ──
def get_user(chat_id: str) -> Optional[dict]:
    users = load_users()
    return users.get(chat_id)

def get_user_by_username(username: str) -> Optional[dict]:
    """Find user by username or display_name (with or without @ prefix)"""
    users = load_users()
    clean = username.lstrip('@')
    for cid, data in users.items():
        stored = data.get('username', '').lstrip('@')
        display = data.get('display_name', '')
        if stored == clean or display == clean or display == username:
            return {"chat_id": cid, **data}
    return None

def register_user(chat_id: str, username: str, role: str = "member", display_name: str = "") -> dict:
    users = load_users()
    clean_username = username if username.startswith('@') else f"@{username}"
    existing = users.get(chat_id, {})
    users[chat_id] = {
        "username": clean_username,
        "display_name": display_name or existing.get("display_name", ""),
        "role": role if not existing else existing.get("role", role),
        "registered_at": existing.get("registered_at", datetime.now().isoformat())
    }
    save_users(users)

    # Also update wbs.json users
    wbs = load_wbs()
    wbs["users"][chat_id] = {"username": username, "role": role}
    save_wbs(wbs)

    return users[chat_id]

def is_admin(chat_id: str) -> bool:
    user = get_user(chat_id)
    return user and user.get("role") == "admin"

# ── Task operations ──
def get_next_task_id(wbs: dict) -> str:
    existing = [int(t["id"][1:]) for t in wbs["tasks"] if t["id"].startswith("T")]
    next_id = max(existing, default=0) + 1
    return f"T{next_id:03d}"

def add_task(title: str, assignee: str, due_date: str = "", priority: str = "Medium",
             meeting_id: str = "", created_by: str = "", collaborators: list = None,
             description: str = "", tags: list = None, parent_id: str = None) -> dict:
    wbs = load_wbs()
    task_id = get_next_task_id(wbs)
    task = {
        "id": task_id,
        "title": title,
        "assignee": assignee,
        "due_date": due_date or "N/A",
        "priority": priority,
        "status": "대기중",
        "meeting_id": meeting_id,
        "created_by": created_by,
        "collaborators": collaborators or [],
        "comments": [],
        "created_at": datetime.now().isoformat(),
        "description": description,
        "tags": tags or [],
        "parent_id": parent_id
    }
    wbs["tasks"].append(task)
    save_wbs(wbs)
    return task

def update_task(task_id: str, updates: dict) -> Optional[dict]:
    wbs = load_wbs()
    for task in wbs["tasks"]:
        if task["id"] == task_id:
            task.update(updates)
            save_wbs(wbs)
            return task
    return None

def add_comment(task_id: str, author: str, text: str, mentions: list = None) -> Optional[dict]:
    """Add a comment to a task. Returns the comment dict."""
    wbs = load_wbs()
    for task in wbs["tasks"]:
        if task["id"] == task_id:
            comment = {
                "author": author,
                "text": text,
                "timestamp": datetime.now().isoformat(),
                "mentions": mentions or []
            }
            task.setdefault("comments", []).append(comment)
            save_wbs(wbs)
            return comment
    return None

def get_tasks_for_user(username: str) -> list:
    """Get all tasks where user is assignee, creator, or collaborator"""
    wbs = load_wbs()
    clean_name = username.lstrip('@')
    result = []
    for task in wbs["tasks"]:
        assignee = task.get("assignee", "").lstrip('@')
        created = task.get("created_by", "")
        collabs = [c.lstrip('@') for c in task.get("collaborators", [])]

        # Get user's chat_id from registry
        user = get_user_by_username(username)
        chat_id = user["chat_id"] if user else ""

        if (assignee == clean_name or
            created == chat_id or
            created == username or
            clean_name in collabs):
            result.append(task)
    return result

# ── Meeting detection ──
MEETING_KEYWORDS = [
    r'회의록?\s*(시작|start|기록|정리)',
    r'오늘\s*회의',
    r'(주간|월간|일일|스프린트)?\s*회의\s*(록|내용|결과|요약|정리)',
    r'미팅\s*(록|내용|노트)',
    r'meeting\s*(notes?|minutes?|summary)',
    r'(논의|안건|결정사항|액션\s*아이템|action\s*item)',
]

def detect_meeting(text: str) -> bool:
    """Check if a message contains meeting-related content"""
    text_lower = text.lower()
    for pattern in MEETING_KEYWORDS:
        if re.search(pattern, text_lower):
            return True
    return False

def extract_action_items(text: str) -> list[dict]:
    """Extract action items (tasks) from meeting text.
    Looks for patterns like:
    - task @assignee (마감: YYYY-MM-DD)
    - task (담당: @assignee)
    - task @assignee 마감:YYYY-MM-DD
    """
    tasks = []
    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        if not line or len(line) < 5:
            continue
        # Skip header lines
        if re.match(r'^(회의|미팅|meeting|논의|안건|#|📋|📝|오늘|어제|지난|다음|이번)', line):
            continue

        # Remove bullet markers
        clean = re.sub(r'^[-•●◉◦▪▸\*\[\]✓☐☑\s]+', '', line).strip()
        if len(clean) < 5:
            continue

        # Extract @mentions
        mentions = re.findall(r'@(\w+)', clean)
        # Extract due date
        due_match = re.search(r'(?:마감|due|까지)[:\s]*([\d-]+)', clean)
        due_date = due_match.group(1) if due_match else ""

        if mentions:
            assignee = mentions[0]
            # Clean title: remove mentions and due date
            title = clean
            title = re.sub(r'@\w+', '', title)
            title = re.sub(r'(?:마감|due|까지)[:\s]*[\d-]+', '', title)
            title = re.sub(r'\([^)]*(?:담당|마감)[^)]*\)', '', title)
            title = re.sub(r'\s+', ' ', title).strip()
            title = re.sub(r'[-–—:]\s*$', '', title).strip()
            title = re.sub(r'\(\s*\)', '', title).strip()  # Remove empty parens
            title = re.sub(r'^\([^)]*\)\s*', '', title).strip()  # Remove leading parens

            if title and len(title) >= 3:
                tasks.append({
                    "title": title,
                    "assignee": assignee,
                    "due_date": due_date,
                    "collaborators": mentions[1:] if len(mentions) > 1 else [],
                    "source_line": line
                })
        else:
            # No mention but could be a task
            clean_title = re.sub(r'\s*[-–—]\s*$', '', clean).strip()
            if len(clean_title) >= 5 and detect_meeting(text):
                tasks.append({
                    "title": clean_title,
                    "assignee": "",
                    "due_date": due_date,
                    "collaborators": [],
                    "source_line": line
                })

    return tasks

def extract_mentions(text: str) -> list[str]:
    """Extract @mentions from text"""
    return re.findall(r'@(\w+)', text)

# ── Notification system ──
def queue_notification(target_chat_id: str, notification_type: str, message: str, related_id: str = ""):
    """Add a notification to the pending queue"""
    notifs = load_notifications()
    notifs.append({
        "target": target_chat_id,
        "type": notification_type,
        "message": message,
        "related_id": related_id,
        "timestamp": datetime.now().isoformat(),
        "sent": False
    })
    save_notifications(notifs)

def flush_notifications():
    """Send all pending notifications"""
    notifs = load_notifications()
    unsent = []
    for n in notifs:
        if n.get("sent"):
            continue
        success = send_telegram(n["target"], n["message"])
        if success:
            n["sent"] = True
            n["sent_at"] = datetime.now().isoformat()
        else:
            unsent.append(n)
        time.sleep(0.5)  # Rate limiting
    save_notifications([n for n in notifs if n["sent"]] + unsent)
    return len([n for n in notifs if not n["sent"]])

def check_due_notifications():
    """Check for tasks due today or tomorrow and queue notifications"""
    wbs = load_wbs()
    today = date.today()
    tomorrow = today + timedelta(days=1)

    for task in wbs["tasks"]:
        due_str = task.get("due_date", "N/A")
        if due_str == "N/A" or not due_str:
            continue

        try:
            due_date = datetime.strptime(due_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if task.get("status") == "완료":
            continue

        # Find assignee's chat_id
        assignee = task.get("assignee", "")
        user = get_user_by_username(assignee)

        urgency = ""
        if due_date == today:
            urgency = "🚨 오늘 마감"
        elif due_date == tomorrow:
            urgency = "⏰ 내일 마감"
        else:
            continue

        msg = f"{urgency}\n📋 [{task['id']}] {task['title']}\n👤 {assignee}\n📅 {due_str}\n📊 {task.get('status', '대기중')}"

        # Send to assignee
        if user:
            queue_notification(user["chat_id"], "due", msg, task["id"])

        # Also send to collaborators
        for collab_name in task.get("collaborators", []):
            collab = get_user_by_username(collab_name)
            if collab and collab["chat_id"] != user.get("chat_id", ""):
                queue_notification(collab["chat_id"], "due_collab", f"👥 협업 과제\n{msg}", task["id"])

        # Send to creator
        creator_id = task.get("created_by", "")
        if creator_id and creator_id != user.get("chat_id", ""):
            queue_notification(creator_id, "due_creator", f"📌 생성한 과제\n{msg}", task["id"])

def notify_mention(mentioned_username: str, text: str, author: str, context: str = ""):
    """Notify a user that they were mentioned"""
    user = get_user_by_username(mentioned_username)
    if not user:
        return

    msg = f"👋 @{mentioned_username} 님이 언급되었습니다\n"
    if author:
        msg += f"✍️ by {author}\n"
    if context:
        msg += f"📋 {context}\n"
    msg += f"💬 {text[:500]}"

    queue_notification(user["chat_id"], "mention", msg)

# ── Command processing ──
def process_message(text: str, chat_id: str, sender_name: str = "") -> dict:
    """Process an incoming message. Returns result dict with actions taken."""
    result = {
        "detected": False,
        "type": None,
        "tasks_created": [],
        "mentions_notified": [],
        "comments_added": [],
        "replies": []
    }

    text_stripped = text.strip()

    # ── Command detection ──
    if text_stripped.startswith('/'):
        return process_command(text_stripped, chat_id, sender_name)

    # ── Meeting detection ──
    if detect_meeting(text_stripped):
        result["detected"] = True
        result["type"] = "meeting"

        # Extract action items
        action_items = extract_action_items(text_stripped)
        if action_items:
            result["replies"].append(f"📋 회의 감지됨! {len(action_items)}개의 액션 아이템을 찾았습니다:")

            for item in action_items:
                task = add_task(
                    title=item["title"],
                    assignee=item.get("assignee") or sender_name,
                    due_date=item.get("due_date", ""),
                    created_by=chat_id
                )
                result["tasks_created"].append(task)
                result["replies"].append(f"  ✅ [{task['id']}] {task['title']} → @{task['assignee']}")

                # Notify assignee if different from sender
                if item.get("assignee") and item["assignee"] != sender_name.lstrip('@'):
                    notify_mention(item["assignee"],
                                   f"새 과제가 배정되었습니다: [{task['id']}] {task['title']}",
                                   sender_name,
                                   f"Due: {item.get('due_date', '미정')}")
                    result["mentions_notified"].append(item["assignee"])
        else:
            result["replies"].append("📋 회의 내용이 감지되었으나, 액션 아이템을 찾지 못했습니다.")
            result["replies"].append("형식 예시: `- 과제 내용 @담당자 (마감: YYYY-MM-DD)`")

    # ── @Mention detection (non-meeting context) ──
    mentions = extract_mentions(text_stripped)
    if mentions:
        for mention in mentions:
            # Don't notify self
            if mention != sender_name.lstrip('@'):
                notify_mention(mention, text_stripped[:200], sender_name)
                result["mentions_notified"].append(mention)

    # ── Comment on task (if message references a task ID) ──
    task_refs = re.findall(r'\b(T\d{3})\b', text_stripped)
    for task_id in task_refs:
        # Remove the task ID from text to get the comment
        comment_text = re.sub(rf'\b{task_id}\b', '', text_stripped).strip()
        if comment_text and len(comment_text) > 2:
            comment_mentions = extract_mentions(comment_text)
            comment = add_comment(task_id, sender_name, comment_text, comment_mentions)
            if comment:
                result["comments_added"].append({"task_id": task_id, "comment": comment})
                result["replies"].append(f"💬 [{task_id}] 댓글 추가됨: {comment_text[:100]}")

                # Notify mentioned users and task assignee
                wbs = load_wbs()
                task = next((t for t in wbs["tasks"] if t["id"] == task_id), None)
                if task:
                    assignee = task.get("assignee", "")
                    if assignee and assignee != sender_name.lstrip('@'):
                        notify_mention(assignee, f"[{task_id}] 새 댓글", sender_name,
                                       f"과제: {task.get('title', '')}\n댓글: {comment_text[:200]}")

    return result

def process_command(cmd: str, chat_id: str, sender_name: str) -> dict:
    """Process bot commands starting with /"""
    result = {"detected": True, "type": "command", "replies": []}
    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if command == "/start":
        username = args.strip() or sender_name
        clean = username.lstrip('@')

        # Check if this user was invited — auto-register with invite
        wbs = load_wbs()
        pending = wbs.get("pending_invites", [])
        was_invited = clean in pending or username in pending

        user = register_user(chat_id, username)
        if was_invited:
            # Remove from pending invites
            if clean in pending:
                pending.remove(clean)
            if username in pending:
                pending.remove(username)
            wbs["pending_invites"] = pending
            save_wbs(wbs)
            result["replies"].append(f"✅ 초대 등록 완료! 환영합니다, {user['username']}님! 🎉")
        else:
            result["replies"].append(f"✅ 등록 완료! 환영합니다, {user['username']}님!")

        result["replies"].append("사용 가능 명령어:\n"
                                "/addtask <제목> @담당자 - 과제 추가\n"
                                "/mytasks - 내 과제 보기\n"
                                "/done T001 - 과제 완료\n"
                                "/status T001 진행중 - 상태 변경\n"
                                "/users - 등록된 사용자 (관리자)\n"
                                "/invite @username - 사용자 초대 (관리자)")

    elif command == "/addtask":
        # Parse: /addtask <title> [@assignee] [마감:YYYY-MM-DD]
        title = args
        assignee = sender_name
        due_date = ""
        collaborators = []

        # Extract due date
        due_match = re.search(r'(?:마감|due|까지)[:\s]*([\d-]+)', args)
        if due_match:
            due_date = due_match.group(1)
            title = title.replace(due_match.group(0), '').strip()

        # Extract mentions (first = assignee, rest = collaborators)
        mentions = extract_mentions(args)
        if mentions:
            assignee = mentions[0]
            collaborators = mentions[1:] if len(mentions) > 1 else []

        # Clean title
        title = re.sub(r'@\w+', '', title).strip()
        title = re.sub(r'(?:마감|due|까지)[:\s]*[\d-]+', '', title).strip()

        if not title:
            result["replies"].append("❌ 사용법: /addtask <제목> @담당자 [마감:YYYY-MM-DD]")
            return result

        task = add_task(title, assignee, due_date, created_by=chat_id, collaborators=collaborators)
        collab_str = f" 👥 {', '.join(collaborators)}" if collaborators else ""
        result["replies"].append(f"✅ 과제 생성됨!\n📋 [{task['id']}] {title}\n👤 @{assignee}{collab_str}\n📅 {due_date or '미정'}")

        # Notify assignee
        if assignee != sender_name.lstrip('@'):
            msg = f"📋 새 과제가 배정되었습니다!\n[{task['id']}] {title}\n📅 {due_date or '미정'}"
            notify_mention(assignee, msg, sender_name)

        # Notify collaborators
        for c in collaborators:
            notify_mention(c, f"👥 협업 과제: [{task['id']}] {title} (담당: @{assignee})", sender_name)

    elif command == "/mytasks":
        tasks = get_tasks_for_user(sender_name)
        if not tasks:
            result["replies"].append("📭 할당된 과제가 없습니다.")
        else:
            status_emoji = {"대기중": "⏳", "진행중": "🔄", "완료": "✅", "보류": "⏸️"}
            lines = [f"📋 *내 과제 ({len(tasks)}개)*"]
            for t in tasks:
                emoji = status_emoji.get(t.get("status", "대기중"), "❓")
                lines.append(f"{emoji} [{t['id']}] {t['title']} → {t.get('status', '대기중')} | 📅 {t.get('due_date', 'N/A')}")
            result["replies"].append('\n'.join(lines))

    elif command == "/done":
        task_id = args.strip()
        if not task_id:
            result["replies"].append("❌ 사용법: /done T001")
            return result
        updated = update_task(task_id, {"status": "완료"})
        if updated:
            result["replies"].append(f"✅ [{task_id}] 완료 처리되었습니다!")
        else:
            result["replies"].append(f"❌ 과제 [{task_id}] 를 찾을 수 없습니다.")

    elif command == "/status":
        # /status T001 진행중
        parts_status = args.split(maxsplit=1)
        if len(parts_status) < 2:
            result["replies"].append("❌ 사용법: /status T001 진행중")
            return result
        task_id = parts_status[0].strip()
        new_status = parts_status[1].strip()
        valid_statuses = ["대기중", "진행중", "완료", "보류"]
        if new_status not in valid_statuses:
            result["replies"].append(f"❌ 유효한 상태: {', '.join(valid_statuses)}")
            return result
        updated = update_task(task_id, {"status": new_status})
        if updated:
            result["replies"].append(f"✅ [{task_id}] → {new_status}")
        else:
            result["replies"].append(f"❌ 과제 [{task_id}] 를 찾을 수 없습니다.")

    elif command == "/invite":
        if not is_admin(chat_id):
            result["replies"].append("❌ 관리자만 사용할 수 있습니다.")
            return result
        # Parse: /invite @username [chat_id] [role]
        parts = args.strip().split()
        username = parts[0].lstrip('@') if parts else ""
        invite_chat_id = parts[1] if len(parts) > 1 else ""
        role = parts[2] if len(parts) > 2 else "member"

        if not username:
            result["replies"].append("❌ 사용법: /invite @username [chat_id] [role]")
            return result

        # If chat_id provided, register immediately
        if invite_chat_id:
            user = register_user(invite_chat_id, username, role)
            result["replies"].append(
                f"✅ @{user['username']} 님이 등록되었습니다! (role: {role})\n"
                f"🔑 chat_id: {invite_chat_id}"
            )
            # Clean up pending invite if exists
            wbs = load_wbs()
            wbs["pending_invites"] = wbs.get("pending_invites", [])
            if username in wbs["pending_invites"]:
                wbs["pending_invites"].remove(username)
                save_wbs(wbs)
        else:
            # Store as pending invite
            wbs = load_wbs()
            wbs["pending_invites"] = wbs.get("pending_invites", [])
            if username not in wbs["pending_invites"]:
                wbs["pending_invites"].append(username)
                save_wbs(wbs)
            result["replies"].append(
                f"✅ @{username} 님이 초대되었습니다.\n"
                f"초대코드가 생성되었습니다. 초대받은 사용자가 `/start {username}` 입력 시 자동 등록됩니다."
            )

    elif command == "/users":
        if not is_admin(chat_id):
            result["replies"].append("❌ 관리자만 사용할 수 있습니다.")
            return result
        users = load_users()
        if not users:
            result["replies"].append("📭 등록된 사용자가 없습니다.")
        else:
            lines = ["👥 *등록된 사용자*"]
            for cid, data in users.items():
                role_badge = "👑" if data.get("role") == "admin" else "👤"
                lines.append(f"{role_badge} {data.get('username', '?')} (ID: {cid})")
            result["replies"].append('\n'.join(lines))

    elif command == "/help":
        result["replies"].append(
            "📋 *TeamLog (팀록) 명령어*\n\n"
            "/addtask <제목> @담당자 - 과제 추가\n"
            "/mytasks - 내 과제 보기\n"
            "/done T001 - 과제 완료 처리\n"
            "/status T001 진행중 - 상태 변경\n"
            "/invite @username - 사용자 초대 (관리자)\n"
            "/users - 등록된 사용자 목록 (관리자)\n"
            "/start - 봇 등록\n\n"
            "💡 회의 내용을 보내면 자동으로 액션 아이템을 추출합니다!\n"
            "💬 과제 ID(T001)를 언급하면 댓글이 추가됩니다."
        )

    else:
        result["replies"].append(f"❓ 알 수 없는 명령어입니다. /help 를 입력해보세요.")

    return result

# ── CLI entry point ──
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    action = sys.argv[1]

    if action == "process":
        if len(sys.argv) < 4:
            print("Usage: meeting_bot.py process <text> <chat_id> [sender_name]")
            sys.exit(1)
        text = sys.argv[2]
        chat_id = sys.argv[3]
        sender = sys.argv[4] if len(sys.argv) > 4 else ""
        result = process_message(text, chat_id, sender)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif action == "notify-due":
        check_due_notifications()
        remaining = flush_notifications()
        print(f"Due notifications flushed. {remaining} remaining.")

    elif action == "notify-mentions":
        remaining = flush_notifications()
        print(f"Mention notifications flushed. {remaining} remaining.")

    elif action == "add-task":
        if len(sys.argv) < 4:
            print("Usage: meeting_bot.py add-task <title> <assignee> [due_date] [priority]")
            sys.exit(1)
        task = add_task(
            title=sys.argv[2],
            assignee=sys.argv[3],
            due_date=sys.argv[4] if len(sys.argv) > 4 else "",
            priority=sys.argv[5] if len(sys.argv) > 5 else "Medium"
        )
        print(json.dumps(task, ensure_ascii=False, indent=2))

    elif action == "add-comment":
        if len(sys.argv) < 5:
            print("Usage: meeting_bot.py add-comment <task_id> <author> <text>")
            sys.exit(1)
        comment = add_comment(sys.argv[2], sys.argv[3], sys.argv[4])
        print(json.dumps(comment, ensure_ascii=False, indent=2))

    elif action == "list-tasks":
        username = sys.argv[2] if len(sys.argv) > 2 else ""
        if username:
            tasks = get_tasks_for_user(username)
        else:
            tasks = load_wbs()["tasks"]
        print(json.dumps(tasks, ensure_ascii=False, indent=2))

    elif action == "invite":
        if len(sys.argv) < 4:
            print("Usage: meeting_bot.py invite <username> <chat_id> [role]")
            sys.exit(1)
        user = register_user(sys.argv[3], sys.argv[2],
                            role=sys.argv[4] if len(sys.argv) > 4 else "member")
        print(json.dumps(user, ensure_ascii=False, indent=2))

    elif action == "detect":
        text = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
        if detect_meeting(text):
            print("MEETING_DETECTED")
            items = extract_action_items(text)
            if items:
                print(f"Action items: {len(items)}")
                for item in items:
                    print(f"  - {item['title']} → @{item['assignee']} ({item.get('due_date', 'no date')})")
        else:
            print("NO_MEETING")

    else:
        print(f"Unknown action: {action}")
        print(__doc__)
        sys.exit(1)
