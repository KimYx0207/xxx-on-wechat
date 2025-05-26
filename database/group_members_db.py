import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "group_members.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS group_members (
            group_id TEXT,
            wxid TEXT,
            display_name TEXT,
            nickname TEXT,
            PRIMARY KEY (group_id, wxid)
        )
    ''')
    conn.commit()
    conn.close()

def save_group_members_to_db(group_id, members):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for member in members:
        c.execute('''
            INSERT OR REPLACE INTO group_members (group_id, wxid, display_name, nickname)
            VALUES (?, ?, ?, ?)
        ''', (
            group_id,
            member.get("UserName") or member.get("wxid"),
            member.get("DisplayName") or member.get("display_name"),
            member.get("NickName") or member.get("nickname"),
        ))
    conn.commit()
    conn.close()

def get_group_member_from_db(group_id, wxid):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT display_name, nickname FROM group_members WHERE group_id=? AND wxid=?
    ''', (group_id, wxid))
    row = c.fetchone()
    conn.close()
    if row:
        return {"display_name": row[0], "nickname": row[1]}
    return None 