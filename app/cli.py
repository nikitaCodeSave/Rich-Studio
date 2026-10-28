"""Access tokens from the command line (the studio has no screen for them on purpose: whoever runs this owns the
server). In the container: `docker compose exec rich-studio python cli.py token add claude-code --level draft`.

    python cli.py token add NAME [--level read|draft|publish|admin]   prints the token once
    python cli.py token list
    python cli.py token revoke NAME                                    also ends its editor sessions
"""

import argparse
import re
import secrets
import sqlite3
import sys
import time

import security
import store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cli.py", description="Токены доступа Rich Studio")
    sub = p.add_subparsers(dest="cmd", required=True)
    tok = sub.add_parser("token").add_subparsers(dest="action", required=True)
    add = tok.add_parser("add", help="новый токен; он печатается один раз")
    add.add_argument("name")
    add.add_argument("--level", choices=security.LEVELS, default="draft")
    tok.add_parser("list", help="токены, их уровень и когда ими пользовались")
    rev = tok.add_parser("revoke", help="отозвать токен и закрыть его сессии в редакторе")
    rev.add_argument("name")
    args = p.parse_args(argv)
    store.init()
    if args.action == "add":
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", args.name):
            print("имя: латиница, цифры, . _ -, до 40 символов", file=sys.stderr)
            return 2
        secret = "rs_" + secrets.token_urlsafe(32)
        try:
            store.add_token(args.name, secret, args.level)
        except sqlite3.IntegrityError:
            print(f"токен {args.name} уже есть: отзовите его или возьмите другое имя", file=sys.stderr)
            return 2
        print(secret)
        print(f"# {args.name}, уровень {args.level}. Сохраните сейчас: студия хранит только хэш.", file=sys.stderr)
    elif args.action == "list":
        for t in store.list_tokens():
            used = time.strftime("%Y-%m-%d %H:%M", time.localtime(t["last_used_at"])) if t["last_used_at"] else "—"
            state = "отозван" if t["revoked_at"] else "действует"
            print(f"{t['name']:<24} {t['level']:<8} {state:<10} последний раз: {used}")
    elif not store.revoke_token(args.name):
        print(f"действующего токена {args.name} нет", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
