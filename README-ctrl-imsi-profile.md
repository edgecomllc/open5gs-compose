# ctrl-imsi-profile.py

CLI-утилита для управления абонентами **Open5GS v2.7.x** в развёртываниях
на базе `docker compose`.

Работает через уже запущенные контейнеры:

- `open5gs-dbctl` — контейнер с compose-сервисом `populate`;
- `mongo` shell — контейнер с compose-сервисом `mongodb`.

Никаких `docker run`, никаких внешних образов — только `docker exec`.

---

## Возможности

- массовое и одиночное **добавление** абонентов;
- массовое и одиночное **удаление** абонентов;
- полная очистка БД (`purge`);
- просмотр документа абонента и списка всех абонентов;
- проверка окружения;
- поддержка **OP** и **OPC**;
- **несколько APN-сессий** на одного абонента с индивидуальными QCI/ARP;
- **PDN**: IPv4 / IPv6 / IPv4v6;
- **статический IP** — IPv4, IPv6, dual-stack;
- **AMF**, **SQN**, **IMEISV**, **S-NSSAI**, **AMBR**;
- автоопределение контейнеров — работает в любом compose-проекте.

---

## Требования

| Компонент | Версия |
|-----------|--------|
| Python | 3.8+ |
| Docker | в `PATH` |
| MongoDB | 5.0+ (проверено на `mongo:5.0.10-focal`) |
| Open5GS | 2.7.x (`gradiant/open5gs:2.7.7`) |
| `open5gs-dbctl` | `gradiant/open5gs-dbctl:0.10.3` |

Сторонние Python-пакеты **не требуются** — только стандартная библиотека.

---

## Установка

```bash
chmod +x ctrl-imsi-profile.py
./ctrl-imsi-profile.py          # печатает справку
```

---

## Быстрый старт

```bash
# проверка окружения
./ctrl-imsi-profile.py check

# справка по формату файла абонентов
./ctrl-imsi-profile.py help-file

# массовое добавление из файла
./ctrl-imsi-profile.py add --file subscribers.txt

# массовое удаление из файла
./ctrl-imsi-profile.py del --file subscribers.txt

# одиночное добавление
./ctrl-imsi-profile.py add \
    --imsi 001010000000001 \
    --key 00112233445566778899aabbccddeeff \
    --op  000102030405060708090a0b0c0d0e0f

# одиночное добавление с профилем (APN, QCI/ARP, IP)
./ctrl-imsi-profile.py add \
    --imsi 001010000000001 \
    --key 00112233445566778899aabbccddeeff \
    --op  000102030405060708090a0b0c0d0e0f \
    --apn internet --qci 9 --arp 8 \
    --apn-extra ims:5:1,mms:6:3 \
    --pdn ipv4v6 --static-ip 10.45.0.5/2001:db8::5

# одиночное удаление
./ctrl-imsi-profile.py del --imsi 001010000000001

# список абонентов
./ctrl-imsi-profile.py list

# просмотр документа
./ctrl-imsi-profile.py show --imsi 001010000000001

# полная очистка БД (сначала только показывает количество)
./ctrl-imsi-profile.py purge
./ctrl-imsi-profile.py purge --yes
```

---

## Подкоманды

| Подкоманда | Назначение |
|------------|-----------|
| `add` | Добавить абонентов: массово (`--file`) или одиночно (`--imsi`) |
| `del` | Удалить абонентов: массово (`--file`) или одиночно (`--imsi`) |
| `purge` | Удалить **всех** абонентов из БД (требует `--yes`) |
| `show` | Показать документ абонента по IMSI |
| `list` | Список всех абонентов в БД |
| `check` | Проверка контейнеров и MongoDB |
| `help-file` | Справка по формату файла абонентов |

---

## Файл абонентов

Используется для **массовых** операций `add --file` и `del --file`.

### Формат

```
imsi, OP|OPC, ki_hex, op_or_opc_hex [, apn_spec] [, pdn] [, ip] [, qci] [, arp]
```

**Обязательные поля:**

| Поле | Описание |
|------|----------|
| `imsi` | 14 или 15 цифр |
| `OP\|OPC` | `OP` или `OPC` |
| `ki_hex` | 32 hex-символа (KI) |
| `op_or_opc_hex` | 32 hex-символа |

**Опциональные поля:**

| Поле | Описание |
|------|----------|
| `apn_spec` | сессии через `;`: `internet:9:8;ims:5:1;mms:6:3` |
| `pdn` | `ipv4` \| `ipv6` \| `ipv4v6` |
| `ip` | `10.45.0.5` \| `2001:db8::5` \| `10.45.0.5/2001:db8::5` |
| `qci` | QCI/5QI основной сессии |
| `arp` | ARP основной сессии |

Максимум **4 сессии** на срез (`OGS_MAX_NUM_OF_SESS`).

### Пример

```
# imsi, mode, ki, op_or_opc, apn_spec, pdn, ip, qci, arp
001010000000001, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f
001010000000002, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f, internet, ipv4, 10.45.0.5
001010000000003, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f, internet:9:8;ims:5:1, ipv4v6, 10.45.0.6/2001:db8::6
001010000000004, OPC, 00112233445566778899aabbccddeeff, 0f0e0d0c0b0a09080706050403020100, internet, ipv6, 2001:db8::7, 8, 5
```

### Приоритеты значений

- **массовый режим:** поле файла → CLI-флаг → `DEFAULT_PROFILE`;
- **одиночный режим:** CLI-флаг → `DEFAULT_PROFILE`.

Пустое поле в файле означает «не задано» — подставится из CLI или дефолта.

---

## Параметры профиля

Работают и в одиночном, и в массовом режимах.

| Флаг | По умолчанию | Описание |
|------|--------------|----------|
| `--apn` | `internet` | имя основной сессии (APN) |
| `--qci` | `9` | QCI/5QI основной сессии |
| `--arp` | `8` | ARP основной сессии |
| `--apn-extra` | `ims:5:1` | доп. сессии: `name[:qci:arp]` через `,` или `;`; `""` — без доп. |
| `--pdn` | `ipv4` | `ipv4` \| `ipv6` \| `ipv4v6` |
| `--static-ip` | — | IPv4, IPv6 или dual-stack через `/` |
| `--amf` | `8000` | AMF hex |
| `--sqn` | `000000000000` | SQN hex |
| `--imeisv` | `8609960408606358` | IMEISV |
| `--sst` | `1` | S-NSSAI SST |
| `--sd` | `ffffff` | S-NSSAI SD hex |
| `--ambr-ul` | `1000000` | AMBR UL, kbps |
| `--ambr-dl` | `1000000` | AMBR DL, kbps |
| `--security` | из файла / `--op` | принудительно `OP` или `OPC` |

---

## Примеры

### Несколько APN с разными QCI/ARP

```bash
./ctrl-imsi-profile.py add \
    --imsi 001010000000010 \
    --key 00112233445566778899aabbccddeeff \
    --op  000102030405060708090a0b0c0d0e0f \
    --apn brt.serv --qci 9 --arp 8 \
    --apn-extra ims:5:1,mms:6:3 \
    --pdn ipv4 --static-ip 10.110.26.10
```

### Только одна сессия

```bash
./ctrl-imsi-profile.py add \
    --imsi 001010000000011 \
    --key 00112233445566778899aabbccddeeff \
    --op  000102030405060708090a0b0c0d0e0f \
    --apn internet --qci 9 --arp 8 \
    --apn-extra "" \
    --pdn ipv4
```

### Массовое добавление со своими параметрами

```bash
./ctrl-imsi-profile.py add --file subscribers.txt \
    --apn internet --qci 9 --arp 8 \
    --apn-extra ims:5:1,mms:6:3 \
    --pdn ipv4v6 \
    --amf 8000 --sst 1 --sd ffffff \
    --security OP
```

### Полная перезагрузка БД

```bash
./ctrl-imsi-profile.py purge --yes
./ctrl-imsi-profile.py add --file subscribers.txt
```

---

## Автоопределение контейнеров

Имена контейнеров в `docker compose` формируются как
`<project>-<service>-<index>`. Скрипт не привязан к конкретному имени
и определяет контейнеры по приоритетам:

1. **CLI-флаги:**
   ```bash
   ./ctrl-imsi-profile.py \
       --mongo-container box-mongodb-1 \
       --dbctl-container box-populate-1 list
   ```
2. **Переменные окружения:**
   ```bash
   export OPEN5GS_MONGO_CONTAINER=box-mongodb-1
   export OPEN5GS_DBCTL_CONTAINER=box-populate-1
   export OPEN5GS_DB_NAME=open5gs
   ```
3. **Compose-метки:**
   - `com.docker.compose.service=mongodb`
   - `com.docker.compose.service=populate`
   
   с учётом `COMPOSE_PROJECT_NAME` или `basename` каталога скрипта.
4. **Fallback:** имя содержит `-mongodb-1` или `-populate-1`.

---

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|------------|-----------|--------------|
| `OPEN5GS_MONGO_CONTAINER` | имя контейнера MongoDB | авто-поиск |
| `OPEN5GS_DBCTL_CONTAINER` | имя контейнера open5gs-dbctl | авто-поиск |
| `OPEN5GS_DB_NAME` | имя БД | `open5gs` |
| `COMPOSE_PROJECT_NAME` | имя compose-проекта | basename каталога скрипта |

---

## Особенности реализации

- **Идемпотентность `add`** — повторный запуск не создаёт дубли,
  существующие абоненты пропускаются.
- **`E11000` при `add`** (race с параллельной записью) обрабатывается
  как «уже существует», а не как ошибка.
- **`del` — синхронный** (`deleteOne` + `deletedCount`): ложных
  «не удалилось с первого раза» нет.
- **`purge` без `--yes`** ничего не удаляет — только печатает количество.
- **OP / OPC не смешиваются** — в БД хранится только одно из полей,
  противоположное удаляется через `$unset`.
- **Статические IP** пишутся в `ue.ipv4` / `ue.ipv6` — формат, который
  понимает C-ядро Open5GS и WebUI.
- **Совместимость с MongoDB 5.0.10** — учтены баги legacy `mongo`
  (`countDocuments({})`, `ok:0` при `rc=0`).

---

## Диагностика

| Симптом | Решение |
|---------|---------|
| `No such container: ...` | `docker compose ps`; задать `--mongo-container` / `--dbctl-container` |
| `[ERROR] не удалось определить контейнеры` | задать флаги или env |
| `[ERROR] MongoDB недоступна` | `docker compose up -d mongodb` |
| `[ERROR] dbctl add провалился ... E11000` | обрабатывается как `skipped` |
| `[ERROR] данных нет` | указать `--file` или `--imsi` |
| WebUI не показывает UE IP | обновить скрипт (формат `ue.ipv4`/`ue.ipv6`) |

---

## Безопасность

- в help-текстах используются **фиктивные** IMSI / KI / OP / OPC;
- `purge` без `--yes` ничего не удаляет;
- `del` работает только по явному списку;
- `add` идемпотентен.

---

## Совместимость

- Open5GS **2.7.x**;
- MongoDB **5.0+**;
- Docker Compose **v2**;
- Linux, macOS.

---

## Лицензия

Внутренний проект.
