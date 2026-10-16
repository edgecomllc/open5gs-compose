#!/usr/bin/env python3
"""
Экспорт абонентов Open5GS v2.7.x в CSV-формат, совместимый
с ctrl-imsi-profile.py add --file.

Поддерживает:
  - вывод в stdout или в файл (--file / -o)
  - фильтр по IMSI-префиксу (--prefix)
  - выбор между всеми полями и только OP/OPC/KI (--short)
  - автоопределение контейнера MongoDB по compose-меткам
  - сортировку по IMSI

Формат вывода (заголовок опционален, --no-header):

    imsi, mode, ki, op_or_opc, apn_spec, pdn, ip, qci, arp

где apn_spec = "internet:9:8;ims:5:1" (сессии через ';'),
    pdn      = ipv4 | ipv6 | ipv4v6,
    ip       = статический IPv4/IPv6 (первое, что найдено у сессии),
    qci/arp  = основного APN.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
MONGO_DB = os.environ.get("OPEN5GS_DB_NAME", "open5gs")

ENV_MONGO = "OPEN5GS_MONGO_CONTAINER"
MONGO_SERVICE = "mongodb"

MONGO_CONTAINER: Optional[str] = None

# порядок APN при выводе: если совпадает — основной первым
PDN_INT_TO_STR = {1: "ipv4", 2: "ipv6", 3: "ipv4v6"}


# ============================================================
# ВЫВОД
# ============================================================
def err(m):  print(f"[ERROR] {m}", file=sys.stderr)
def warn(m): print(f"[WARN] {m}", file=sys.stderr)


# ============================================================
# RUNNER / ПОИСК КОНТЕЙНЕРА
# ============================================================
def _run(cmd: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout {timeout}s"
    except FileNotFoundError:
        return 127, "", "docker не найден в PATH"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"


def _docker_ps_names(*filters: str) -> List[str]:
    cmd = ["docker", "ps", "--format", "{{.Names}}"]
    for f in filters:
        cmd += ["--filter", f]
    rc, out, errs = _run(cmd)
    if rc != 0 or not out:
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]


def _detect_compose_project() -> Optional[str]:
    env_proj = os.environ.get("COMPOSE_PROJECT_NAME")
    if env_proj:
        return env_proj
    here = os.path.dirname(os.path.abspath(__file__)) or os.getcwd()
    return os.path.basename(here) or None


def _detect_mongo_container() -> Optional[str]:
    project = _detect_compose_project()

    if project:
        names = _docker_ps_names(
            f"label=com.docker.compose.service={MONGO_SERVICE}",
            f"label=com.docker.compose.project={project}",
        )
        if names:
            return sorted(names)[0]

    names = _docker_ps_names(f"label=com.docker.compose.service={MONGO_SERVICE}")
    if names:
        return sorted(names)[0]

    names = _docker_ps_names(f"name=-{MONGO_SERVICE}-1")
    if names:
        return sorted(names)[0]

    return None


def resolve_mongo(cli_mongo: Optional[str], quiet: bool = False) -> bool:
    global MONGO_CONTAINER
    mongo = cli_mongo or os.environ.get(ENV_MONGO) or _detect_mongo_container()
    MONGO_CONTAINER = mongo
    if not quiet:
        print(f"mongo-контейнер: {mongo or '(не найден)'}", file=sys.stderr)
    if not mongo:
        err("не удалось определить контейнер MongoDB")
        err("задайте явно: --mongo-container <name>")
        err(f"или переменную окружения {ENV_MONGO}=...")
        return False
    return True


def mongo_eval(js: str) -> Tuple[bool, str, str]:
    if not MONGO_CONTAINER:
        return False, "", "MONGO_CONTAINER не определён"
    cmd = [
        "docker", "exec", "-i", MONGO_CONTAINER,
        "mongo", MONGO_DB, "--quiet", "--eval", js,
    ]
    rc, out, errs = _run(cmd)
    if rc != 0:
        return False, out, errs
    if '"ok" : 0' in out or '"ok":0' in out:
        return False, out, errs or "mongo reported ok:0"
    return True, out, errs


# ============================================================
# ЭКСПОРТ
# ============================================================
def fetch_subscribers(prefix: Optional[str]) -> List[Dict]:
    """
    Возвращает список документов-абонентов из MongoDB.
    Ищет через find() с проекцией.
    """
    query = "{}"
    if prefix:
        # экранируем — префикс должен быть числом
        if not re.fullmatch(r"\d{1,15}", prefix):
            err(f"--prefix '{prefix}' должен состоять из цифр")
            sys.exit(2)
        query = f'{{imsi: /^{prefix}/}}'

    js = (
        f'var out = [];'
        f'db.subscribers.find({query}).sort({{imsi:1}}).forEach(function(d){{'
        f'  out.push(d);'
        f'}});'
        f'print(JSON.stringify(out));'
    )
    okc, out, errs = mongo_eval(js)
    if not okc:
        err(f"mongo вернул ошибку: {errs or out}")
        sys.exit(3)
    # mongo shell может добавить мусорные строки вроде "MongoDB server version: ..."
    # Найдём первую строку с '[' и последнюю с ']'
    text = out
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        err(f"не удалось распарсить JSON из mongo:\n{out[:500]}")
        sys.exit(3)
    text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        err(f"JSON decode error: {e}")
        err(f"первые 500 символов ответа:\n{text[:500]}")
        sys.exit(3)


# ============================================================
# ФОРМИРОВАНИЕ СТРОК CSV
# ============================================================
def _first_session_with_ue(sessions: List[Dict]) -> Optional[Dict]:
    """Возвращает первую сессию, где задан ue.ipv4 или ue.ipv6."""
    for s in sessions:
        ue = s.get("ue") or {}
        if ue.get("ipv4") or ue.get("ipv6"):
            return s
    return None


def _collect_static_ip(sessions: List[Dict]) -> str:
    """
    Ищет статический IP во всех сессиях.
    Возвращает 'v4', 'v6', 'v4/v6' или ''.
    """
    v4 = ""
    v6 = ""
    for s in sessions:
        ue = s.get("ue") or {}
        if not v4 and ue.get("ipv4"):
            v4 = ue["ipv4"]
        if not v6 and ue.get("ipv6"):
            v6 = ue["ipv6"]
    if v4 and v6:
        return f"{v4}/{v6}"
    return v4 or v6 or ""


def _build_apn_spec(sessions: List[Dict]) -> str:
    """'brt.serv:9:8;ims:5:1;mms:6:3'"""
    parts: List[str] = []
    for s in sessions:
        name = s.get("name", "")
        if not name:
            continue
        q = (s.get("qos") or {}).get("index")
        a = ((s.get("qos") or {}).get("arp") or {}).get("priority_level")
        if q is not None and a is not None:
            parts.append(f"{name}:{q}:{a}")
        elif q is not None:
            parts.append(f"{name}:{q}")
        else:
            parts.append(name)
    return ";".join(parts)


def _mode_from_security(sec: Dict) -> str:
    if not sec:
        return "OP"
    if sec.get("op"):
        return "OP"
    if sec.get("opc"):
        return "OPC"
    # если ни op, ни opc нет — считаем OP (запись всё равно нужна)
    return "OP"


def _ki_or_opc_value(sec: Dict, mode: str) -> str:
    if not sec:
        return ""
    if mode == "OP":
        return (sec.get("op") or "").upper()
    return (sec.get("opc") or "").upper()


def subscriber_to_csv_row(sub: Dict) -> str:
    """
    Возвращает строку CSV:
        imsi, mode, ki, op_or_opc, apn_spec, pdn, ip, qci, arp
    """
    imsi = sub.get("imsi", "")
    sec = sub.get("security") or {}
    mode = _mode_from_security(sec)
    ki = (sec.get("k") or "").lower()
    op_val = _ki_or_opc_value(sec, mode)

    sessions: List[Dict] = []
    for sl in sub.get("slice") or []:
        for s in sl.get("session") or []:
            sessions.append(s)

    apn_spec = _build_apn_spec(sessions)

    # pdn — из первой сессии (в Open5GS тип одинаков для всех сессий среза)
    pdn_str = ""
    if sessions:
        pdn_type = sessions[0].get("type")
        if pdn_type in PDN_INT_TO_STR:
            pdn_str = PDN_INT_TO_STR[pdn_type]

    ip = _collect_static_ip(sessions)

    # qci/arp основного APN (первой сессии)
    qci_str = ""
    arp_str = ""
    if sessions:
        q = (sessions[0].get("qos") or {}).get("index")
        a = ((sessions[0].get("qos") or {}).get("arp") or {}).get("priority_level")
        if q is not None:
            qci_str = str(q)
        if a is not None:
            arp_str = str(a)

    # Собираем строку с сохранением позиций
    # imsi, mode, ki, op_or_opc, apn_spec, pdn, ip, qci, arp
    row = [
        imsi,
        mode,
        ki,
        op_val,
        apn_spec,
        pdn_str,
        ip,
        qci_str,
        arp_str,
    ]
    return ", ".join(row)


def subscriber_to_short_row(sub: Dict) -> str:
    """imsi, mode, ki, op_or_opc (без профиля)"""
    imsi = sub.get("imsi", "")
    sec = sub.get("security") or {}
    mode = _mode_from_security(sec)
    ki = (sec.get("k") or "").lower()
    op_val = _ki_or_opc_value(sec, mode)
    return f"{imsi}, {mode}, {ki}, {op_val}"


# ============================================================
# MAIN
# ============================================================
HEADER_FULL  = "#imsi, mode, ki, op_or_opc, apn_spec, pdn, ip, qci, arp"
HEADER_SHORT = "#imsi, mode, ki, op_or_opc"


def main():
    p = argparse.ArgumentParser(
        prog="ctrl-imsi-export.py",
        description=(
            "Экспорт абонентов Open5GS v2.7.x в CSV-формат, совместимый с "
            "ctrl-imsi-profile.py add --file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
примеры:

  # вывести всех в stdout
  %(prog)s

  # сохранить в файл
  %(prog)s -o subscribers.txt

  # только определённый префикс IMSI
  %(prog)s --prefix 9999999894316

  # короткий формат (только imsi/mode/ki/op)
  %(prog)s --short

  # без заголовка
  %(prog)s --no-header

  # явно указать контейнер
  %(prog)s --mongo-container box-mongodb-1
""",
    )
    p.add_argument("--mongo-container", metavar="NAME", default=None,
                   help=f"имя контейнера MongoDB (env {ENV_MONGO} или авто-поиск)")
    p.add_argument("-o", "--file", metavar="PATH", default=None,
                   help="сохранить вывод в файл (по умолчанию — stdout)")
    p.add_argument("--prefix", metavar="PREFIX", default=None,
                   help="фильтр по префиксу IMSI (только цифры)")
    p.add_argument("--short", action="store_true",
                   help="короткий формат: только imsi, mode, ki, op_or_opc")
    p.add_argument("--no-header", action="store_true",
                   help="не выводить строку-заголовок")
    p.add_argument("--quiet", action="store_true",
                   help="не выводить диагностику в stderr")
    args = p.parse_args()

    if not resolve_mongo(args.mongo_container, quiet=args.quiet):
        sys.exit(1)

    subs = fetch_subscribers(args.prefix)
    if not args.quiet:
        print(f"Найдено абонентов: {len(subs)}", file=sys.stderr)

    lines: List[str] = []
    if not args.no_header:
        lines.append(HEADER_SHORT if args.short else HEADER_FULL)

    for s in subs:
        if args.short:
            lines.append(subscriber_to_short_row(s))
        else:
            lines.append(subscriber_to_csv_row(s))

    text = "\n".join(lines) + "\n"

    if args.file:
        try:
            with open(args.file, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            err(f"не удалось записать файл {args.file}: {e}")
            sys.exit(2)
        if not args.quiet:
            print(f"Записано в {args.file}: {len(lines)} строк", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
