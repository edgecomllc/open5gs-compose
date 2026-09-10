#!/usr/bin/env python3
"""
Универсальное управление абонентами Open5GS v2.7.x.

Работает через:
  - open5gs-dbctl в контейнере с сервисом 'populate'
  - mongo shell в контейнере с сервисом 'mongodb'

Имена контейнеров определяются автоматически:
  1) флаги --mongo-container / --dbctl-container
  2) переменные окружения OPEN5GS_MONGO_CONTAINER / OPEN5GS_DBCTL_CONTAINER
  3) поиск по меткам docker compose (com.docker.compose.service=mongodb|populate)
     с учётом COMPOSE_PROJECT_NAME или basename текущего каталога
  4) fallback: имя контейнера содержит -mongodb-1 / -populate-1

Подкоманды:
  add         Добавить абонентов: массово (--file) или одиночно (--imsi)
  del         Удалить абонентов:  массово (--file) или одиночно (--imsi)
  purge       Удалить ВСЕХ абонентов из БД (требует --yes)
  show        Показать документ абонента
  list        Список всех абонентов в БД
  check       Проверить связь с контейнерами и MongoDB
  help-file   Справка по формату файла абонентов

Запуск без параметров выводит справку.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
MONGO_DB = os.environ.get("OPEN5GS_DB_NAME", "open5gs")

ENV_MONGO = "OPEN5GS_MONGO_CONTAINER"
ENV_DBCTL = "OPEN5GS_DBCTL_CONTAINER"

MONGO_SERVICE = "mongodb"
DBCTL_SERVICE = "populate"

# максимум сессий (APN) на один срез — по OGS_MAX_NUM_OF_SESS
MAX_SESSIONS_PER_SLICE = 4

MONGO_CONTAINER: Optional[str] = None
DBCTL_CONTAINER: Optional[str] = None

DEFAULT_PROFILE = {
    "apn":          "internet",
    "qos":          9,
    "arp":          8,
    "extra_sessions": [
        {"name": "ims", "qos": 5, "arp": 1},
    ],
    "pdn_type":     1,             # 1=IPv4, 2=IPv6, 3=IPv4v6
    "static_ip":    None,          # {"ipv4":..., "ipv6":...} или None
    "amf":          "8000",
    "sqn":          "000000000000",
    "imeisv":       "8609960408606358",
    "sst":          1,
    "sd":           "ffffff",
    "ambr_ul_kbps": 1000000,
    "ambr_dl_kbps": 1000000,
    "security_mode": "OP",
}


# ============================================================
# ВЫВОД
# ============================================================
def hdr(m):
    print(f"\n{'='*60}")
    print(m)
    print('='*60)


def info(m):  print(m)
def err(m):   print(f"[ERROR] {m}")
def warn(m):  print(f"[WARN] {m}")


# ============================================================
# БАЗОВЫЙ RUNNER
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


# ============================================================
# ОПРЕДЕЛЕНИЕ КОНТЕЙНЕРОВ
# ============================================================
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


def _detect_container(service: str) -> Optional[str]:
    project = _detect_compose_project()

    if project:
        names = _docker_ps_names(
            f"label=com.docker.compose.service={service}",
            f"label=com.docker.compose.project={project}",
        )
        if names:
            return sorted(names)[0]

    names = _docker_ps_names(f"label=com.docker.compose.service={service}")
    if names:
        return sorted(names)[0]

    names = _docker_ps_names(f"name=-{service}-1")
    if names:
        return sorted(names)[0]

    return None


def resolve_containers(cli_mongo: Optional[str] = None,
                       cli_dbctl: Optional[str] = None,
                       quiet: bool = False) -> bool:
    global MONGO_CONTAINER, DBCTL_CONTAINER

    mongo = cli_mongo or os.environ.get(ENV_MONGO)
    dbctl = cli_dbctl or os.environ.get(ENV_DBCTL)

    if not mongo:
        mongo = _detect_container(MONGO_SERVICE)
    if not dbctl:
        dbctl = _detect_container(DBCTL_SERVICE)

    MONGO_CONTAINER = mongo
    DBCTL_CONTAINER = dbctl

    if not quiet:
        info(f"mongo-контейнер:  {mongo or '(не найден)'}")
        info(f"dbctl-контейнер:  {dbctl or '(не найден)'}")

    if not mongo or not dbctl:
        err("не удалось определить контейнеры автоматически")
        err("задайте явно: --mongo-container <name> --dbctl-container <name>")
        err(f"или переменные окружения: {ENV_MONGO}=... {ENV_DBCTL}=...")
        return False
    return True


# ============================================================
# ОБЁРТКИ DOCKER EXEC
# ============================================================
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


def dbctl(args: List[str]) -> Tuple[bool, str, str]:
    if not DBCTL_CONTAINER:
        return False, "", "DBCTL_CONTAINER не определён"
    cmd = [
        "docker", "exec", "-i",
        "-e", "HOME=/tmp",
        "-e", "XDG_CACHE_HOME=/tmp/.cache",
        "-e", "XDG_CONFIG_HOME=/tmp/.config",
        "-e", "XDG_DATA_HOME=/tmp/.local/share",
        DBCTL_CONTAINER,
        "open5gs-dbctl",
    ] + args
    rc, out, errs = _run(cmd)
    return rc == 0, out, errs


# ============================================================
# ПРОВЕРКИ
# ============================================================
def ensure_containers() -> bool:
    if not MONGO_CONTAINER or not DBCTL_CONTAINER:
        err("имена контейнеров не определены (вызовите resolve_containers)")
        return False
    ok_all = True
    for name in (DBCTL_CONTAINER, MONGO_CONTAINER):
        rc, out, errs = _run(["docker", "inspect", "-f", "{{.State.Running}}", name])
        if rc != 0 or out != "true":
            err(f"контейнер {name} не запущен")
            ok_all = False
        else:
            info(f"[OK] контейнер {name} запущен")
    return ok_all


def check_mongo() -> bool:
    okc, out, errs = mongo_eval("print(db.runCommand({ping:1}).ok)")
    if okc and "1" in out:
        info("[OK] MongoDB доступна")
        return True
    err(f"mongo ping не прошёл: {errs or out}")
    return False


def subscriber_exists(imsi: str) -> bool:
    js = (
        f'var d = db.subscribers.findOne({{imsi:"{imsi}"}}, {{_id:1}});'
        f'print(d ? "EXISTS" : "MISSING");'
    )
    okc, out, errs = mongo_eval(js)
    if not okc:
        return False
    return "EXISTS" in out


# ============================================================
# ПАРСЕР APN-СПЕЦИФИКАЦИИ
# ============================================================
def _parse_apn_spec(spec: str) -> List[Dict]:
    """
    Разбирает 'internet:9:8;ims:5:1;mms:6:3'.
    Возвращает [{"name":..., "qos":..., "arp":...}, ...]
    где qos/arp могут быть None.
    """
    if not spec:
        return []

    result: List[Dict] = []
    for raw in spec.split(";"):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(":")
        name = parts[0].strip()
        if not name:
            continue
        qos = None
        arp = None
        if len(parts) >= 2 and parts[1].strip():
            try:
                qos = int(parts[1].strip())
            except ValueError:
                warn(f"apn-spec '{raw}': qci '{parts[1]}' не число, игнорируем qci")
        if len(parts) >= 3 and parts[2].strip():
            try:
                arp = int(parts[2].strip())
            except ValueError:
                warn(f"apn-spec '{raw}': arp '{parts[2]}' не число, игнорируем arp")
        result.append({"name": name, "qos": qos, "arp": arp})

    if len(result) > MAX_SESSIONS_PER_SLICE:
        warn(f"apn-spec: {len(result)} сессий > {MAX_SESSIONS_PER_SLICE}, "
             f"лишние будут отброшены")
        result = result[:MAX_SESSIONS_PER_SLICE]
    return result


def _parse_static_ip(ip_field: str, pdn: Optional[str]) -> Optional[Dict]:
    """
    Возвращает {"ipv4": "..."} / {"ipv6": "..."} / {"ipv4":..., "ipv6":...} / None.
    """
    if not ip_field:
        return None
    ip_field = ip_field.strip()
    if not ip_field:
        return None

    if "/" in ip_field:
        left, right = ip_field.split("/", 1)
        left = left.strip()
        right = right.strip()
        result = {}
        if left:
            result["ipv4"] = left
        if right:
            result["ipv6"] = right
        return result or None

    if ":" in ip_field:
        return {"ipv6": ip_field}
    return {"ipv4": ip_field}


# ============================================================
# ФАЙЛ АБОНЕНТОВ
# ============================================================
def load_subscribers_file(path: str) -> List[Dict]:
    """
    imsi, OP|OPC, ki_hex, op_or_opc_hex
          [, apn_spec] [, pdn] [, ip] [, qci] [, arp]
    """
    if not os.path.isfile(path):
        return []

    subs: List[Dict] = []
    skipped_header = False
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if not skipped_header and line.lower().startswith("imsi"):
                skipped_header = True
                continue
            skipped_header = True

            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                warn(f"{path}:{lineno}: ожидалось минимум 4 поля, пропускаем")
                continue

            imsi, mode, key, op_val = parts[:4]
            mode_u = mode.upper()
            if mode_u not in ("OP", "OPC"):
                warn(f"{path}:{lineno}: '{mode}' не OP/OPC, пропускаем")
                continue
            if not re.fullmatch(r"\d{14,15}", imsi):
                warn(f"{path}:{lineno}: IMSI '{imsi}' не похож на IMSI, пропускаем")
                continue

            opt = (parts[4:] + [""] * 5)[:5]
            apn_spec, pdn_field, ip_field, qci_field, arp_field = opt

            sessions = _parse_apn_spec(apn_spec)

            pdn_val: Optional[str] = None
            if pdn_field:
                pd = pdn_field.lower()
                if pd in ("ipv4", "ipv6", "ipv4v6"):
                    pdn_val = pd
                else:
                    warn(f"{path}:{lineno}: неизвестный pdn '{pdn_field}', игнорируем")

            static_ip = _parse_static_ip(ip_field, pdn_val)

            qci_val: Optional[int] = None
            if qci_field:
                try:
                    qci_val = int(qci_field)
                except ValueError:
                    warn(f"{path}:{lineno}: qci '{qci_field}' не число, игнорируем")

            arp_val: Optional[int] = None
            if arp_field:
                try:
                    arp_val = int(arp_field)
                except ValueError:
                    warn(f"{path}:{lineno}: arp '{arp_field}' не число, игнорируем")

            if sessions and qci_val is not None and sessions[0]["qos"] is None:
                sessions[0]["qos"] = qci_val
            if sessions and arp_val is not None and sessions[0]["arp"] is None:
                sessions[0]["arp"] = arp_val

            subs.append({
                "imsi": imsi,
                "mode": mode_u,
                "key":  key.lower(),
                "opc_or_op": op_val.upper(),
                "sessions": sessions or None,
                "pdn":      pdn_val,
                "static_ip": static_ip,
            })
    return subs


# ============================================================
# ПОСТРОЕНИЕ ПРОФИЛЯ
# ============================================================
def _build_sessions_from_cli(args) -> Optional[List[Dict]]:
    apn = getattr(args, "apn", None)
    apn_extra = getattr(args, "apn_extra", None)
    qci = getattr(args, "qci", None)
    arp = getattr(args, "arp", None)

    if not apn and apn_extra is None and qci is None and arp is None:
        return None

    sessions: List[Dict] = []
    main_name = apn or DEFAULT_PROFILE["apn"]
    sessions.append({"name": main_name, "qos": qci, "arp": arp})

    if apn_extra is not None:
        if apn_extra == "":
            extra_list = []
        else:
            spec = apn_extra.replace(",", ";")
            extra_list = _parse_apn_spec(spec)
        sessions.extend(extra_list)

    return sessions


def build_profile_from_args(args) -> Dict:
    p = dict(DEFAULT_PROFILE)

    cli_sessions = _build_sessions_from_cli(args)
    if cli_sessions is not None:
        p["_sessions"] = cli_sessions
    else:
        p["_sessions"] = [
            {"name": DEFAULT_PROFILE["apn"],
             "qos":  DEFAULT_PROFILE["qos"],
             "arp":  DEFAULT_PROFILE["arp"]},
        ] + [dict(s) for s in DEFAULT_PROFILE["extra_sessions"]]

    if getattr(args, "pdn", None):
        p["pdn_type"] = {"ipv4": 1, "ipv6": 2, "ipv4v6": 3}[args.pdn.lower()]

    if getattr(args, "static_ip", None):
        pdn_str = {1: "ipv4", 2: "ipv6", 3: "ipv4v6"}[p["pdn_type"]]
        p["static_ip"] = _parse_static_ip(args.static_ip, pdn_str)

    if getattr(args, "amf", None):
        p["amf"] = args.amf
    if getattr(args, "sqn", None):
        p["sqn"] = args.sqn
    if getattr(args, "imeisv", None):
        p["imeisv"] = args.imeisv
    if getattr(args, "sst", None) is not None:
        p["sst"] = args.sst
    if getattr(args, "sd", None):
        p["sd"] = args.sd
    if getattr(args, "ambr_ul", None) is not None:
        p["ambr_ul_kbps"] = args.ambr_ul
    if getattr(args, "ambr_dl", None) is not None:
        p["ambr_dl_kbps"] = args.ambr_dl
    if getattr(args, "security", None):
        p["security_mode"] = args.security.upper()

    return p


# ============================================================
# ОПЕРАЦИИ С АБОНЕНТОМ
# ============================================================
def _extract_inserted_id(dbctl_out: str) -> Optional[str]:
    m = re.search(r"insertedId:\s*ObjectId\('([0-9a-f]+)'\)", dbctl_out)
    return m.group(1) if m else None


def _fix_security(imsi: str, key: str, mode: str, opc_or_op: str, amf: str) -> bool:
    field_set   = "security.op"  if mode == "OP" else "security.opc"
    field_unset = "security.opc" if mode == "OP" else "security.op"

    js = (
        f'db.subscribers.updateOne('
        f'{{imsi:"{imsi}"}},'
        f'{{$set:{{'
        f'"{field_set}":"{opc_or_op}",'
        f'"security.k":"{key}",'
        f'"security.amf":"{amf}"'
        f'}},'
        f'$unset:{{"{field_unset}":""}}}});'
        f'print("OK")'
    )
    okc, out, errs = mongo_eval(js)
    return okc and "OK" in out


def _build_session_obj(session: Dict, pdn_type: int, static_ip: Optional[Dict],
                       ambr_ul: int, ambr_dl: int) -> Dict:
    """
    Собирает объект сессии (APN) для slice.session.
    Статический IP записывается в ue.ipv4 / ue.ipv6 —
    именно эти поля использует C-ядро Open5GS и WebUI.
    """
    qos = session.get("qos")
    arp = session.get("arp")
    if qos is None:
        qos = DEFAULT_PROFILE["qos"]
    if arp is None:
        arp = DEFAULT_PROFILE["arp"]

    obj = {
        "name": session["name"],
        "type": pdn_type,
        "qos": {
            "index": int(qos),
            "arp": {
                "priority_level": int(arp),
                "pre_emption_capability": 1,
                "pre_emption_vulnerability": 1,
            },
        },
        "ambr": {
            "uplink":   {"value": int(ambr_ul), "unit": 1},
            "downlink": {"value": int(ambr_dl), "unit": 1},
        },
        "pcc_rule": [],
    }

    if static_ip:
        ue: Dict = {}
        if pdn_type in (1, 3) and static_ip.get("ipv4"):
            ue["ipv4"] = static_ip["ipv4"]
        if pdn_type in (2, 3) and static_ip.get("ipv6"):
            ue["ipv6"] = static_ip["ipv6"]
        if ue:
            obj["ue"] = ue

    return obj


def _set_profile(imsi: str, sessions: List[Dict], profile: Dict) -> bool:
    pdn_type = profile["pdn_type"]
    static_ip = profile.get("static_ip")
    ambr_ul = profile["ambr_ul_kbps"]
    ambr_dl = profile["ambr_dl_kbps"]

    session_objs = [
        _build_session_obj(s, pdn_type, static_ip, ambr_ul, ambr_dl)
        for s in sessions[:MAX_SESSIONS_PER_SLICE]
    ]

    slice_doc = [{
        "sst": profile["sst"],
        "sd":  profile["sd"],
        "default_indicator": True,
        "session": session_objs,
    }]

    set_doc = {
        "sqn":    profile["sqn"],
        "imeisv": profile["imeisv"],
        "ambr": {
            "uplink":   {"value": ambr_ul, "unit": 1},
            "downlink": {"value": ambr_dl, "unit": 1},
        },
        "slice": slice_doc,
        "access_restriction_data": 32,
        "subscriber_status": 0,
        "network_access_mode": 0,
        "subscribed_rau_tau_timer": 12,
    }
    unset_doc = {"amf": "", "mm_realm": ""}

    js = (
        f'var r = db.subscribers.updateOne('
        f'{{imsi:"{imsi}"}},'
        f'{{$set:{json.dumps(set_doc)},'
        f' $unset:{json.dumps(unset_doc)}}});'
        f'print("matched=" + r.matchedCount + " modified=" + r.modifiedCount)'
    )
    okc, out, errs = mongo_eval(js)
    if not okc:
        err(f"set_profile: {errs or out}")
        return False
    return "matched=1" in out


def add_one(sub: Dict, base_profile: Dict, quiet: bool = False) -> str:
    """'added' | 'skipped' | 'failed'."""
    imsi = sub["imsi"]
    if not quiet:
        hdr(f"Абонент: {imsi}")

    profile = dict(base_profile)

    if sub.get("sessions"):
        sessions = [dict(s) for s in sub["sessions"]]
        for i, s in enumerate(sessions):
            if s.get("qos") is None:
                s["qos"] = (base_profile.get("qos", DEFAULT_PROFILE["qos"])
                            if i == 0 else DEFAULT_PROFILE["qos"])
            if s.get("arp") is None:
                s["arp"] = (base_profile.get("arp", DEFAULT_PROFILE["arp"])
                            if i == 0 else DEFAULT_PROFILE["arp"])
    else:
        sessions = [dict(s) for s in base_profile.get("_sessions", [])]

    if sub.get("pdn"):
        profile["pdn_type"] = {"ipv4": 1, "ipv6": 2, "ipv4v6": 3}[sub["pdn"]]
    if sub.get("static_ip"):
        profile["static_ip"] = sub["static_ip"]

    if subscriber_exists(imsi):
        info(f"Абонент {imsi} уже существует — пропускаем")
        return "skipped"

    okc, out, errs = dbctl(["add", imsi, sub["key"], sub["opc_or_op"]])
    if not okc:
        combined = f"{out}\n{errs}"
        if "E11000" in combined or "duplicate key" in combined:
            info(f"Абонент {imsi} уже существует (E11000) — пропускаем")
            return "skipped"
        err(f"dbctl add провалился для {imsi}: {errs or out}")
        return "failed"

    inserted_id = _extract_inserted_id(out)
    if not quiet:
        if inserted_id:
            info(f"Базовый абонент создан (id={inserted_id})")
        else:
            info("Базовый абонент создан")

    if not _fix_security(imsi, sub["key"], sub["mode"],
                         sub["opc_or_op"], profile["amf"]):
        err(f"не удалось установить security ({sub['mode']}) для {imsi}")
        return "failed"

    if not _set_profile(imsi, sessions, profile):
        err(f"не удалось записать профиль для {imsi}")
        return "failed"

    if not subscriber_exists(imsi):
        err(f"абонент {imsi} не найден после записи")
        return "failed"

    if not quiet:
        parts = [f"{s['name']}({s.get('qos') or DEFAULT_PROFILE['qos']}/"
                 f"{s.get('arp') or DEFAULT_PROFILE['arp']})" for s in sessions]
        pdn_str = {1: "IPv4", 2: "IPv6", 3: "IPv4v6"}[profile["pdn_type"]]
        ip_str = ""
        if profile.get("static_ip"):
            ip_str = " IP=" + "/".join(filter(None, [
                profile["static_ip"].get("ipv4"),
                profile["static_ip"].get("ipv6"),
            ]))
        info(f"Профиль: APN={'; '.join(parts)}, PDN={pdn_str}{ip_str}, "
             f"S-NSSAI={profile['sst']}/{profile['sd']}, "
             f"AMF={profile['amf']}, SQN={profile['sqn']}, "
             f"Security={sub['mode']}")
        info(f"Абонент {imsi} добавлен")
    return "added"


def del_one(imsi: str, quiet: bool = False) -> str:
    """'deleted' | 'missing' | 'failed'."""
    if not quiet:
        hdr(f"Абонент: {imsi}")

    js = (
        f'var r = db.subscribers.deleteOne({{imsi:"{imsi}"}});'
        f'print("deleted=" + r.deletedCount)'
    )
    okc, out, errs = mongo_eval(js)
    if not okc:
        err(f"mongo deleteOne провалился для {imsi}: {errs or out}")
        return "failed"

    if "deleted=1" in out:
        if not quiet:
            info(f"Абонент {imsi} удалён")
        return "deleted"
    if "deleted=0" in out:
        info(f"Абонент {imsi} не найден — нечего удалять")
        return "missing"

    err(f"неожиданный ответ mongo для {imsi}: {out!r}")
    return "failed"


# ============================================================
# СБОР ЦЕЛЕЙ
# ============================================================
def collect_targets_single(args) -> Optional[List[Dict]]:
    if not args.imsi:
        return None

    subs: List[Dict] = []
    for imsi in args.imsi:
        if args.cmd == "add":
            if not args.key:
                err(f"для --imsi {imsi} нужно указать --key")
                sys.exit(2)
            if not (args.op or args.opc):
                err(f"для --imsi {imsi} нужно указать --op <hex> или --opc <hex>")
                sys.exit(2)
            mode = "OP" if args.op else "OPC"
            val  = args.op if args.op else args.opc
        else:
            mode = "OP"
            val  = ""
        subs.append({
            "imsi": imsi,
            "mode": mode,
            "key":  (args.key or "").lower(),
            "opc_or_op": val.upper() if val else "",
            "sessions": None,
            "pdn": None,
            "static_ip": None,
        })
    return subs


def collect_targets_mass(args) -> List[Dict]:
    if not args.file:
        err("для массовой операции нужен --file <path>")
        extra = " --key <HEX> --op|--opc <HEX>" if args.cmd == "add" else ""
        err(f"или для одиночной операции укажите --imsi <IMSI>{extra}")
        err("подсказка: ./ctrl-imsi-profile.py help-file")
        sys.exit(2)

    if not os.path.isfile(args.file):
        err(f"файл не найден: {args.file}")
        sys.exit(2)

    subs = load_subscribers_file(args.file)
    if not subs:
        warn(f"в файле {args.file} нет валидных записей")
        sys.exit(2)

    info(f"Загружено из файла {args.file}: {len(subs)} абонентов")
    return subs


def _resolve_targets(args) -> List[Dict]:
    single = collect_targets_single(args)
    if single is not None:
        return single
    return collect_targets_mass(args)


# ============================================================
# КОМАНДЫ
# ============================================================
def cmd_add(args):
    hdr("Добавление абонентов в Open5GS")

    mode = "одиночный" if args.imsi else "массовый"
    info(f"Режим: {mode}")

    profile = build_profile_from_args(args)
    sessions_dbg = profile.get("_sessions", [])
    parts = [f"{s['name']}({s.get('qos') or '-'}/{s.get('arp') or '-'})"
             for s in sessions_dbg]
    pdn_str = {1: "IPv4", 2: "IPv6", 3: "IPv4v6"}[profile["pdn_type"]]
    info(f"Профиль базовый: APN={'; '.join(parts)}, PDN={pdn_str}, "
         f"AMF={profile['amf']}, S-NSSAI={profile['sst']}/{profile['sd']}, "
         f"Security={profile['security_mode']}")

    if not ensure_containers():
        sys.exit(1)
    if not check_mongo():
        sys.exit(2)

    subs = _resolve_targets(args)
    if not subs:
        warn("нечего добавлять")
        return

    added, skipped, failed = 0, 0, []
    for sub in subs:
        if getattr(args, "security", None):
            sub["mode"] = args.security.upper()
        r = add_one(sub, profile)
        if r == "added":
            added += 1
        elif r == "skipped":
            skipped += 1
        else:
            failed.append(sub["imsi"])

    hdr("Итог")
    info(f"Добавлено: {added}")
    if skipped:
        info(f"Пропущено (уже есть): {skipped}")
    if failed:
        err(f"Ошибок: {len(failed)}")
        for i in failed:
            print(f"  - {i}")


def cmd_del(args):
    hdr("Удаление абонентов из Open5GS")

    mode = "одиночный" if args.imsi else "массовый"
    info(f"Режим: {mode}")

    if not ensure_containers():
        sys.exit(1)
    if not check_mongo():
        sys.exit(2)

    subs = _resolve_targets(args)
    if not subs:
        warn("нечего удалять")
        return

    deleted, missing, failed = 0, 0, []
    for sub in subs:
        r = del_one(sub["imsi"])
        if r == "deleted":
            deleted += 1
        elif r == "missing":
            missing += 1
        else:
            failed.append(sub["imsi"])

    hdr("Итог")
    info(f"Удалено: {deleted}")
    if missing:
        info(f"Не найдено: {missing}")
    if failed:
        err(f"Ошибок: {len(failed)}")
        for i in failed:
            print(f"  - {i}")


def cmd_purge(args):
    hdr("Полная очистка БД абонентов Open5GS")

    if not ensure_containers():
        sys.exit(1)
    if not check_mongo():
        sys.exit(2)

    okc, out, errs = mongo_eval('print(db.subscribers.countDocuments({}))')
    if not okc:
        err(f"не удалось посчитать абонентов: {errs or out}")
        sys.exit(3)

    total_raw = out.strip()
    if not total_raw.isdigit():
        err(f"неожиданный ответ mongo: {out!r}")
        sys.exit(3)
    total = int(total_raw)

    info(f"Абонентов в БД: {total}")
    if total == 0:
        info("БД уже пуста — нечего удалять")
        return

    if not args.yes:
        warn(f"будет удалено {total} абонентов. Подтвердите флагом --yes")
        info("пример: ./ctrl-imsi-profile.py purge --yes")
        return

    okc, out, errs = mongo_eval(
        'var r = db.subscribers.deleteMany({});'
        'print("deleted=" + r.deletedCount)'
    )
    if not okc:
        err(f"deleteMany провалился: {errs or out}")
        sys.exit(3)

    info(f"mongo: {out}")

    okc, out, errs = mongo_eval('print(db.subscribers.countDocuments({}))')
    info(f"Осталось в БД: {out if okc else '?'}")


def cmd_show(args):
    if not args.imsi or len(args.imsi) != 1:
        err("укажите ровно один IMSI: --imsi <imsi>")
        sys.exit(2)
    imsi = args.imsi[0]
    okc, out, errs = mongo_eval(
        f'printjson(db.subscribers.findOne({{imsi:"{imsi}"}}))'
    )
    hdr(f"Абонент: {imsi}")
    print(out if okc else (errs or "ошибка"))


def cmd_list(args):
    js = (
        'db.subscribers.find({}, {imsi:1, "security":1}).sort({imsi:1}).forEach(function(d){'
        '  var mode = "-";'
        '  if (d.security) {'
        '    if (d.security.op)  mode = "OP";'
        '    else if (d.security.opc) mode = "OPC";'
        '  }'
        '  print(d.imsi + "  " + mode);'
        '});'
    )
    okc, out, errs = mongo_eval(js)
    hdr("Абоненты в БД")
    if not okc:
        err(errs or "ошибка mongo")
        return
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        info("(пусто — в БД нет абонентов)")
        return
    info(f"Всего: {len(lines)}")
    print(out)


def cmd_check(args):
    hdr("Проверка окружения")
    okc = ensure_containers()
    if okc:
        okc = check_mongo()
    sys.exit(0 if okc else 1)


SUBSCRIBERS_FILE_HELP = """\
Формат файла абонентов
======================

Используется при массовых операциях:

    add --file <path>
    del --file <path>

Одна строка — один абонент. Поля разделяются запятыми:

    imsi, OP|OPC, ki_hex, op_or_opc_hex
          [, apn_spec] [, pdn] [, ip] [, qci] [, arp]

Обязательные поля (первые 4):

  imsi            14 или 15 цифр
  OP|OPC          "OP"  — в 4-м поле лежит OP
                  "OPC" — в 4-м поле лежит OPC
  ki_hex          32 hex-символа (KI)
  op_or_opc_hex   32 hex-символа (OP или OPC)

apn_spec — список сессий через ';'. Каждая сессия:
    имя              → QCI/ARP берутся из CLI или дефолта
    имя:qci:arp      → индивидуальные QCI и ARP для этой сессии

Примеры:
    internet
    internet:9:8
    internet:9:8;ims:5:1
    internet:9:8;ims:5:1;mms:6:3
    internet:9:8;ims:5:1;mms:6:3;foo:7:4   (максимум 4 сессии)

pdn: ipv4 | ipv6 | ipv4v6
ip:  статический адрес (v4, v6 или v4/v6 для dual-stack)
qci: целое, для ОСНОВНОЙ сессии (если в apn_spec явно не задано)
arp: целое, для ОСНОВНОЙ сессии (если в apn_spec явно не задано)

Пример массового добавления:

    # imsi, mode, ki, op_or_opc, apn_spec, pdn, ip, qci, arp
    001010000000001, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f
    001010000000002, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f, internet, ipv4, 10.45.0.5
    001010000000003, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f, internet:9:8;ims:5:1, ipv4v6, 10.45.0.6/2001:db8::6
    001010000000004, OPC, 00112233445566778899aabbccddeeff, 0f0e0d0c0b0a09080706050403020100, internet, ipv6, 2001:db8::7, 8, 5


Одиночные операции без файла
============================

Все параметры профиля из файла можно задать флагами. Работают ТЕ ЖЕ
флаги, что и для массового режима:

    --apn           имя основной сессии (APN)
    --qci           QCI/5QI основной сессии
    --arp           ARP основной сессии
    --apn-extra     доп. сессии вида name[:qci:arp], через ',' или ';'
    --pdn           ipv4 | ipv6 | ipv4v6
    --static-ip     статический IP: v4, v6 или v4/v6 (dual-stack)

Базовые случаи:

    # только ключи — профиль по умолчанию (internet + ims, ipv4, 9/8 и 5/1)
    add --imsi <IMSI> --key <HEX> --op  <HEX>
    add --imsi <IMSI> --key <HEX> --opc <HEX>

    # основная сессия + одна дополнительная, IPv4, статический IP
    add --imsi <IMSI> --key <HEX> --op <HEX> \\
        --apn internet --qci 9 --arp 8 \\
        --apn-extra ims:5:1 \\
        --pdn ipv4 --static-ip 10.45.0.5

    # три сессии с индивидуальными QCI/ARP, dual-stack, статические адреса
    add --imsi <IMSI> --key <HEX> --op <HEX> \\
        --apn internet --qci 9 --arp 8 \\
        --apn-extra ims:5:1,mms:6:3 \\
        --pdn ipv4v6 --static-ip 10.45.0.5/2001:db8::5

    # убрать все доп. сессии — оставить только основную
    add --imsi <IMSI> --key <HEX> --op <HEX> \\
        --apn internet --qci 9 --arp 8 \\
        --apn-extra "" \\
        --pdn ipv4

    # удаление (профиль не нужен)
    del --imsi <IMSI>


Приоритеты значений
===================

Для каждого параметра (apn, qci, arp, pdn, ip):

  массовый режим:
    1) колонка в строке файла;
    2) CLI-флаг;
    3) DEFAULT_PROFILE.

  одиночный режим:
    1) CLI-флаг;
    2) DEFAULT_PROFILE.
"""


def cmd_help_file(args):
    print(SUBSCRIBERS_FILE_HELP)


# ============================================================
# CLI
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    epilog = """\
определение контейнеров:

  по умолчанию ищутся автоматически по compose-меткам:
    com.docker.compose.service=mongodb
    com.docker.compose.service=populate
  с учётом COMPOSE_PROJECT_NAME или basename каталога скрипта.

  Явно:
    --mongo-container <name> --dbctl-container <name>
    OPEN5GS_MONGO_CONTAINER=<name>
    OPEN5GS_DBCTL_CONTAINER=<name>

режимы add/del:

  одиночный (--imsi):       файл игнорируется

      базовый случай:
        %(prog)s add --imsi 001010000000001 \\
                     --key 00112233445566778899aabbccddeeff \\
                     --op  000102030405060708090a0b0c0d0e0f

      с профилем (APN, QCI/ARP, доп. сессии, PDN, статический IP):
        %(prog)s add --imsi 001010000000001 \\
                     --key 00112233445566778899aabbccddeeff \\
                     --op  000102030405060708090a0b0c0d0e0f \\
                     --apn internet --qci 9 --arp 8 \\
                     --apn-extra ims:5:1,mms:6:3 \\
                     --pdn ipv4v6 --static-ip 10.45.0.5/2001:db8::5

      удаление:
        %(prog)s del --imsi 001010000000001

  массовый (--file):        список из файла

      %(prog)s add --file subscribers.txt
      %(prog)s del --file subscribers.txt

  параметры профиля (--apn, --qci, --arp, --apn-extra, --pdn, --static-ip)
  работают ОДИНАКОВО в одиночном и массовом режимах. В массовом режиме
  значения из файла (колонки apn_spec/pdn/ip/qci/arp) имеют приоритет
  над CLI-флагами; если колонка пустая — берётся CLI-флаг, а если и он
  не задан — значение по умолчанию.

примеры:

  %(prog)s check
  %(prog)s add --file subscribers.txt
  %(prog)s add --file subscribers.txt --apn internet --qci 9 --arp 8 \\
              --apn-extra ims:5:1,mms:6:3 --security OP
  %(prog)s del --file subscribers.txt
  %(prog)s purge                # только показывает, сколько удалится
  %(prog)s purge --yes          # удаляет всех
  %(prog)s show --imsi 001010000000001
  %(prog)s list
  %(prog)s help-file
"""
    p = argparse.ArgumentParser(
        prog="ctrl-imsi-profile.py",
        description=(
            "Управление абонентами Open5GS v2.7.x.\n"
            "Работает через контейнеры с сервисами 'populate' (open5gs-dbctl) "
            "и 'mongodb' (mongo). Имена контейнеров определяются автоматически."
        ),
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument("--mongo-container", metavar="NAME", default=None,
                   help=f"имя контейнера MongoDB (env {ENV_MONGO} или авто-поиск)")
    p.add_argument("--dbctl-container", metavar="NAME", default=None,
                   help=f"имя контейнера open5gs-dbctl (env {ENV_DBCTL} или авто-поиск)")

    sub = p.add_subparsers(
        dest="cmd",
        metavar="{add,del,purge,show,list,check,help-file}",
    )

    def add_target_args(sp, mode_help: str):
        sp.add_argument(
            "--imsi", action="append", metavar="IMSI",
            help=f"IMSI для одиночной операции ({mode_help}). "
                 f"Можно повторять. Если задан — файл игнорируется.",
        )
        sp.add_argument(
            "--key", metavar="HEX",
            help="KI в hex (32 символа). Только для одиночного add.",
        )
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--op",  metavar="HEX",
                       help="OP в hex (32 символа). Только для одиночного add.")
        g.add_argument("--opc", metavar="HEX",
                       help="OPC в hex (32 символа). Только для одиночного add.")
        sp.add_argument(
            "--file", metavar="PATH", default=None,
            help="файл абонентов для МАССОВОЙ операции. "
                 "Формат: imsi, OP|OPC, KI_hex, OP_or_OPC_hex "
                 "[, apn_spec [, pdn [, ip [, qci [, arp]]]]]. "
                 "Без --file массовая операция не запускается.",
        )

    # --- add ---
    sp_add = sub.add_parser(
        "add",
        help="Добавить абонентов: массово (--file) или одиночно (--imsi)",
        description=(
            "Добавляет абонентов в Open5GS v2.7.x.\n\n"
            "Два режима:\n"
            "  * одиночный: --imsi <IMSI> --key <HEX> --op|--opc <HEX>;\n"
            "  * массовый:  --file <path>.\n\n"
            "Оба режима принимают одинаковый набор параметров профиля:\n"
            "  --apn <name>            имя основной сессии (APN)\n"
            "  --qci <n> --arp <n>     QCI/ARP основной сессии\n"
            "  --apn-extra <spec>      доп. сессии вида name[:qci:arp], "
            "через ',' или ';'\n"
            "  --pdn ipv4|ipv6|ipv4v6  тип PDN\n"
            "  --static-ip <ADDR>      статический IP: v4, v6 или v4/v6\n\n"
            "В массовом режиме значения из файла (колонки apn_spec/pdn/ip/"
            "qci/arp) переопределяют CLI-флаги для конкретного абонента. "
            "Если колонка пустая — берётся CLI-флаг, если и он не задан — "
            "значение по умолчанию.\n\n"
            "Пример одиночного добавления с профилем:\n"
            "  ctrl-imsi-profile.py add --imsi 001010000000001 \\\n"
            "               --key 00112233445566778899aabbccddeeff \\\n"
            "               --op  000102030405060708090a0b0c0d0e0f \\\n"
            "               --apn internet --qci 9 --arp 8 \\\n"
            "               --apn-extra ims:5:1,mms:6:3 \\\n"
            "               --pdn ipv4v6 --static-ip 10.45.0.5/2001:db8::5"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_target_args(sp_add, "одиночного add")

    prof = sp_add.add_argument_group("параметры профиля")
    prof.add_argument("--apn", help="имя основной сессии (APN), по умолчанию internet")
    prof.add_argument("--qci", type=int,
                      help="QCI/5QI основной сессии (если явно не указан в apn_spec)")
    prof.add_argument("--arp", type=int,
                      help="ARP основной сессии (если явно не указан в apn_spec)")
    prof.add_argument(
        "--apn-extra", dest="apn_extra", default=None, metavar="LIST",
        help="доп. сессии через запятую или ';': 'ims:5:1,mms:6:3'. "
             "Пустая строка '' — без доп. сессий.",
    )
    prof.add_argument("--pdn", choices=["ipv4", "ipv6", "ipv4v6"],
                      help="тип PDN (по умолчанию: ipv4)")
    prof.add_argument("--static-ip", dest="static_ip", metavar="ADDR",
                      help="статический IP: IPv4, IPv6 или dual-stack через '/': "
                           "10.45.0.5/2001:db8::5")
    prof.add_argument("--amf", metavar="HEX", help="AMF hex (по умолчанию: 8000)")
    prof.add_argument("--sqn", metavar="HEX", help="SQN hex (по умолчанию: 000000000000)")
    prof.add_argument("--imeisv", help="IMEISV (по умолчанию: 8609960408606358)")
    prof.add_argument("--sst", type=int, help="S-NSSAI SST (по умолчанию: 1)")
    prof.add_argument("--sd",  metavar="HEX", help="S-NSSAI SD hex (по умолчанию: ffffff)")
    prof.add_argument("--ambr-ul", dest="ambr_ul", type=int, metavar="KBPS",
                      help="AMBR UL в kbps (по умолчанию: 1000000)")
    prof.add_argument("--ambr-dl", dest="ambr_dl", type=int, metavar="KBPS",
                      help="AMBR DL в kbps (по умолчанию: 1000000)")
    prof.add_argument("--security", choices=["OP", "OPC", "op", "opc"],
                      help="переопределить OP/OPC для всех абонентов")
    sp_add.set_defaults(func=cmd_add)

    # --- del ---
    sp_del = sub.add_parser(
        "del",
        help="Удалить абонентов: массово (--file) или одиночно (--imsi)",
        description=(
            "Удаляет абонентов из Open5GS.\n\n"
            "Два режима:\n"
            "  * одиночный: --imsi <IMSI>;\n"
            "  * массовый:  --file <path>."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_target_args(sp_del, "одиночного del")
    sp_del.set_defaults(func=cmd_del)

    # --- purge ---
    sp_purge = sub.add_parser(
        "purge",
        help="Удалить ВСЕХ абонентов из БД (требует --yes)",
        description=(
            "Удаляет всех абонентов из коллекции subscribers.\n"
            "Без --yes только показывает количество и выходит."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sp_purge.add_argument("--yes", action="store_true",
                          help="подтверждение удаления")
    sp_purge.set_defaults(func=cmd_purge)

    # --- show ---
    sp_show = sub.add_parser(
        "show", help="Показать документ абонента по IMSI",
        description="Печатает полный JSON-документ абонента из MongoDB.",
    )
    sp_show.add_argument("--imsi", action="append", required=True, metavar="IMSI")
    sp_show.set_defaults(func=cmd_show)

    # --- list ---
    sp_list = sub.add_parser(
        "list", help="Список всех абонентов в БД",
        description="Печатает IMSI и режим security (OP/OPC/-) для всех абонентов.",
    )
    sp_list.set_defaults(func=cmd_list)

    # --- check ---
    sp_check = sub.add_parser(
        "check", help="Проверка окружения (контейнеры + MongoDB)",
        description="Проверяет, что контейнеры запущены и MongoDB отвечает на ping.",
    )
    sp_check.set_defaults(func=cmd_check)

    # --- help-file ---
    sp_hf = sub.add_parser(
        "help-file", help="Справка по формату файла абонентов",
        description="Печатает формат файла и пример содержимого.",
    )
    sp_hf.set_defaults(func=cmd_help_file)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not getattr(args, "cmd", None):
        parser.print_help()
        sys.exit(0)

    if not resolve_containers(
        cli_mongo=getattr(args, "mongo_container", None),
        cli_dbctl=getattr(args, "dbctl_container", None),
        quiet=True,
    ):
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
