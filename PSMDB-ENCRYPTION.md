# Open5GS с Percona Server for MongoDB и шифрованием Data-at-Rest

## Обзор

Этот проект основан на **open5gs-compose** — docker-compose развёртывании мобильной сети Open5GS (EPC + 5GC: MME/HSS/PCRF, AMF/SMF/UPF и др., плюс мониторинг Grafana/Prometheus). Точка ветвления — коммит `95f82d1` (`deploy(docker-compose): use health check to seamlessly apply zero-ebpf program on host interfaces`).

Относительно базового коммита `95f82d1` в этой ветке изменяется следующее:

| Область | Было (базовый коммит) | Стало |
|---|---|---|
| СУБД | MongoDB Community Edition | **Percona Server for MongoDB (PSMDB) 5.0** — drop-in замена по протоколу и URI |
| Шифрование данных | Отсутствует (данные на диске в открытом виде) | **Data-at-Rest Encryption** на уровне движка WiredTiger |
| Хранение ключей | — | Новый сервис **OpenBao** (открытый форк HashiCorp Vault) — внешний KMS с TLS |
| Compose-файлы | Только `docker-compose.yaml`: сервис `mongodb` на образе Community | `docker-compose.yaml` возвращён к базовому виду; шифрование вынесено в overlay **`docker-compose.encryption.yaml`** (сервис `openbao` + замена `mongodb` на PSMDB: mongod.conf с vault, `!override` volumes, `init.js` в `/opt/init`). Подключается цепочкой `-f` или через `COMPOSE_FILE` в `.env` |
| Конфигурация mongod | Стандартная | `.deploy/docker/psmdb/mongod.conf`: `security.enableEncryption` + секция `vault` |
| Скрипты | — | `.deploy/scripts/`: развёртывание с миграцией, генерация TLS-сертификатов, верификация, диагностика |
| Прочее | — | `docker-compose.override.yaml`, `.env` (адреса AMF/N3 для стенда, `COMPOSE_FILE` с overlay шифрования, `MONGO_EXT_PORT=27017`) |

Ключевое свойство: **Open5GS продолжает работать без изменений кода и конфигурации** — PSMDB бинарно совместима с MongoDB Community, шифрование прозрачно для клиентов БД, а `DB_URI: mongodb://mongodb/open5gs` не меняется.

---

## Архитектура

```
        ПОЛЬЗОВАТЕЛИ / УТИЛИТЫ                    ШИФРОВАНИЕ (только при записи на диск)
┌──────────────────────────┐              ┌─────────────────────────────┐              ┌─────────────────────┐
│  Open5GS (HSS/UDM/WebUI) │              │  Percona Server for         │   HTTPS/TLS   │  OpenBao (KMS)      │
│  open5gs-utils           │◄─ mongodb ──►│  MongoDB 5.0 (PSMDB)        │◄────:8200────►│  хранит МАСТЕР-КЛЮЧ │
│  (ctrl-imsi-profile.py)  │  :27017      │                             │  токен +      │  secret/data/       │
│                          │  (plaintext!)│  RAM:  данные расшифрованы  │  KV v2 путь   │  psmdb-encryption   │
└──────────────────────────┘              │  ДИСК: данные зашифрованы   │              │  (KV v2)            │
                                          └─────────────────────────────┘              └─────────────────────┘
```

Три уровня, каждый делает свою работу:

| Компонент | Роль | Что видит |
|---|---|---|
| **Open5GS + утилиты** | Клиенты БД по обычному протоколу `mongodb://mongodb/open5gs` | **Plaintext** — расшифровку делает mongod |
| **PSMDB (mongod)** | СУБД: при записи шифрует страницы, при чтении расшифровывает | Ключи шифрования данных (DEK) в RAM |
| **OpenBao (KMS)** | Хранилище мастер-ключа (KEK), выдаёт его mongod по TLS | Мастер-ключ, но не сами данные |

### Потоки данных и ключей

1. **Поток данных (plaintext)**: Open5GS-сервисы и утилиты ↔ mongod по обычному протоколу MongoDB (`mongodb://mongodb/open5gs`, порт 27017, Docker-сеть). Mongod отдаёт уже расшифрованные данные — расшифровку делает он сам.
2. **Поток ключей (TLS)**: mongod ↔ OpenBao по HTTPS (`https://openbao:8200`, KV v2 API-путь `secret/data/psmdb-encryption`). Mongod предъявляет токен из `/etc/mongodb-encryption/vault-token`; подлинность OpenBao проверяется по CA-сертификату из `/etc/mongodb-tls/ca.crt` (серверный сертификат `CN=openbao`).
3. **Хранение (зашифровано)**: mongod → диск (`.deploy/docker/local-data/mongodb/*.wt`). Каждая страница WiredTiger зашифрована AES-256; без мастер-ключа файлы нечитаемы.
4. **Управление OpenBao (loopback)**: healthcheck и CLI ходят на `http://127.0.0.1:8201` внутри контейнера OpenBao (в образе BusyBox `wget` без `--no-check-certificate`); наружу открыт только TLS-порт `:8200`.

---

## Как работает прозрачное шифрование

### Принцип Data-at-Rest

**Data-at-Rest Encryption** — шифрование данных «в покое», то есть там, где они физически хранятся: **на диске**. MongoDB хранит данные в файлах движка WiredTiger (`collection-*.wt`, `index-*.wt` и др. в `.deploy/docker/local-data/mongodb/`). При включённом шифровании WiredTiger **перед записью каждой страницы на диск шифрует её**, а **при чтении с диска — расшифровывает**. Всё это происходит внутри процесса mongod:

- **Чтение**: клиент отправляет запрос → mongod читает нужные страницы с диска → расшифровывает их ключом → отдаёт документ в открытом виде.
- **Запись**: клиент пишет документ → mongod шифрует страницу → записывает на диск. На диске остаётся только шифртекст.

Отсюда два следствия:

1. **Прозрачность**: клиенты (HSS, WebUI, утилиты) не знают о шифровании — не нужно менять код, URI, драйверы. Любая программа, работавшая с MongoDB, работает с PSMDB без изменений.
2. **Граница защиты**: защищён **только диск**. Оперативная память mongod, сетевой трафик до mongod и результаты запросов — в открытом виде (см. таблицу ниже).

### Иерархия ключей

```
OpenBao (KV v2 secret)                          Mongod (RAM)                          Диск
┌──────────────────────┐   1. mongod при старте  ┌──────────────────────┐   3. каждая страница
│ МАСТЕР-КЛЮЧ (KEK)    │ ───по TLS:8200──────►  │ Ключи данных (DEK)   │ ───шифруется своим───►  collection-*.wt
│ secret/data/         │   получает KEK,        │ DEK зашифрованы KEK  │      DEK (AES-256)       (нечитаемый мусор
│ psmdb-encryption     │   расшифровывает DEK   │ и живут в RAM        │                          без ключей)
└──────────────────────┘                        └──────────────────────┘
```

1. **Мастер-ключ (KEK)** лежит не в контейнере и не на диске с данными, а в OpenBao. Mongod при каждом старте ходит по HTTPS за ним, предъявляя токен (`vault-token`).
2. Мастер-ключом расшифровываются **ключи данных (DEK)** — по одному на каждую таблицу/индекс WiredTiger. DEK существуют только в оперативной памяти mongod.
3. Каждая страница данных при записи на диск шифруется её DEK (AES-256-CBC). При чтении — расшифровывается. Это делает движок WiredTiger внутри mongod, **автоматически и незаметно** для клиентов.

### Что и где шифруется, от чего защита помогает

**Где что находится:**

| Место | Состояние данных |
|---|---|
| Диск: `.deploy/docker/local-data/mongodb/*.wt` | 🔒 **Зашифровано** (AES-256, страницы WiredTiger + метаданные блоков) |
| RAM mongod | 🔓 Открыто (расшифрованные страницы, ключи DEK) |
| Сеть Docker: клиенты ↔ mongod:27017 | 🔓 Открыто (обычный протокол MongoDB) |
| Сеть Docker: mongod ↔ openbao:8200 | 🔒 **TLS** (защищает передачу мастер-ключа) |
| OpenBao: `secret/data/psmdb-encryption` | 🔒 Мастер-ключ (хранилище шифруется самим OpenBao) |
| `mongodump`, экспорт, миграционные бэкапы | 🔓 **Открыто** (дамп создаётся расшифрованным) |

**От чего защита реально помогает (и от чего — нет):**

| Сценарий | Защищено? | Почему |
|---|---|---|
| **Кража/копирование диска или `.wt`-файлов** (украден сервер, снят образ VM, скопирован `local-data/mongodb`) | ✅ **Да** | Без мастер-ключа из OpenBao файлы — нечитаемый мусор. Проверка: `sudo strings .deploy/docker/local-data/mongodb/collection-*.wt \| head` — только `encryption=(keyid=...,block_metadata_encrypted=true)` и бинарный шум, IMSI-паттерны отсутствуют |
| **Утрата unseal-ключей OpenBao** (`local-data/openbao/secrets/`) | ⚠️ **Данные потеряны навсегда** | Мастер-ключ невозможно восстановить — храните резервные копии секретов на защищённом носителе |
| Подключение к **работающему** mongod (утилиты, HSS, WebUI, злоумышленник в Docker-сети) | ❌ Нет | Mongod отдаёт расшифрованные данные любому клиенту — это функция СУБД; ограничивается аутентификацией MongoDB и сетевой изоляцией |
| Данные в **RAM** mongod | ❌ Нет | Data-at-Rest = только диск |
| Трафик Open5GS ↔ MongoDB внутри Docker-сети | ❌ Нет | При необходимости включается TLS-транспорт MongoDB отдельно (не настроено) |
| `mongodump` / логические бэкапы / экспорт профилей | ❌ Нет | Дамп создаётся **расшифрованным** — шифруйте файлы дампа отдельно |

> **Кратко**: шифрование закрывает одну конкретную угрозу — **offline-доступ к данным** (украденный диск/файлы без ключа). Онлайн-доступ через работающую СУБД оно не ограничивает — для этого нужны аутентификация MongoDB, сетевая изоляция и TLS-транспорт.

### Жизненный цикл при перезапуске

```
docker compose up -d
  → OpenBao стартует, auto-unseal (unseal-key из local-data/openbao/secrets)
  → mongod стартует, healthcheck ждёт готовности
  → mongod читает vault-token и по TLS:8200 запрашивает secret/data/psmdb-encryption
  → расшифровывает DEK мастер-ключом → готов обслуживать запросы
  → Open5GS-сервисы подключаются как обычно (plaintext для них)
```

В логах это видно как: `Master encryption key has been read from the key management facility` (Vault, KV v2).

---

## Структура compose-файлов

Шифрование PSMDB вынесено из базового `docker-compose.yaml` в отдельный overlay-файл **`docker-compose.encryption.yaml`**:

| Файл | Содержимое |
|---|---|
| `docker-compose.yaml` | Базовый стек open5gs-compose (как в исходном коммите `95f82d1`): `mongodb` — обычный `mongo:5.0.10-focal`, `init.js` в `/docker-entrypoint-initdb.d`. Сервиса `openbao` нет |
| `docker-compose.override.yaml` | Стенд-специфичные переопределения (eUPF, NAT и т.п.) — как в базовом репозитории |
| `docker-compose.encryption.yaml` | **Overlay шифрования**: сервис `openbao` + замена `mongodb` на PSMDB 5.0 (mongod.conf с vault, `volumes: !override`, `init.js` в `/opt/init/init.js`, `depends_on: openbao`) |

Ключевые моменты overlay:

- `volumes: !override` — **полностью заменяет** список монтирований `mongodb` из базового файла (требуется Compose >= 2.24). Без этого `init.js` остался бы примонтирован в `/docker-entrypoint-initdb.d`, что несовместимо с шифрованием: entrypoint PSMDB сначала запускает временный **незашифрованный** mongod.
- `image: percona/percona-server-mongodb:5.0` заменяет `mongo:5.0.10-focal` (при merge скалярное поле из overlay побеждает).
- Порт `${MONGO_EXT_PORT}:27017` наследуется из базового файла без изменений.

Подключение overlay — один из двух способов:

```bash
# Способ 1: явная цепочка -f
docker compose -f docker-compose.yaml -f docker-compose.override.yaml \
               -f docker-compose.encryption.yaml up -d

# Способ 2: COMPOSE_FILE в .env (на стенде уже настроено; разделитель ':' на Linux, ';' на Windows)
#   COMPOSE_FILE=docker-compose.yaml:docker-compose.override.yaml:docker-compose.encryption.yaml
docker compose up -d
```

> **Важно**: без overlay (только базовый файл) стек запустит обычную **незашифрованную** MongoDB. Никогда не запускайте базовый файл отдельно поверх данных, записанных зашифрованной PSMDB — plain-mongod не сможет их открыть. На стенде `COMPOSE_FILE` прописан в `.env`, поэтому все команды `docker compose ...` в этой документации корректны; без `.env` используйте цепочку `-f` или `export COMPOSE_FILE=...`.

---

## Быстрый старт (новое развёртывание)

### 1. Предварительные требования

- Docker Engine >= 24.0
- Docker Compose >= 2.24 (поддержка `depends_on` с `condition: service_healthy` и тегов `!reset`/`!override` для замены списков в overlay)
- Пустой том данных MongoDB (шифрование включается **только при первом запуске на пустом хранилище**)

### 2. Первый запуск

```bash
# Очистить старые данные, если они есть (ВНИМАНИЕ: данные будут удалены!)
rm -rf .deploy/docker/local-data/mongodb

# Запустить весь стек
docker compose up -d

# Проверить статус сервисов
docker compose ps

# Проверить логи
docker compose logs -f openbao mongodb
```

### 3. Проверка шифрования

Быстрая проверка — скрипт верификации (статус контейнеров, БД и коллекции, шифрование в логах mongod, TLS-соединение, KV v2 секрет, отсутствие plaintext IMSI в файлах хранилища, webui):

```bash
bash .deploy/scripts/verify-encrypted-stack.sh
```

> Скрипт универсален для тестового стенда и рабочего деплоя: параметры окружения `IMSI_PATTERN` (regex plaintext-IMSI; по умолчанию тестовый диапазон `99999…` — для реальных IMSI задайте свой префикс, например `IMSI_PATTERN='25001[0-9]{10}'`), `BAO_ADDR` (по умолчанию `localhost:8200`) и `WEBUI_URL` (по умолчанию `http://localhost:9999/`) переопределяются при запуске.

Дополнительные ручные проверки — см. раздел «Проверка корректности шифрования» ниже.

---

## Развёртывание с миграцией: `deploy-encrypted.sh`

Основной скрипт — `.deploy/scripts/deploy-encrypted.sh`. Запускается **на сервере** из корня проекта (с Windows-хоста — через локальный вспомогательный скрипт `deploy-from-windows.ps1`, в репозиторий не входит). Скрипт идемпотентный: при повторном запуске пропускает уже выполненное (существующие сертификаты, существующий дамп).

Выполняет 8 шагов:

| Шаг | Что делает | Детали |
|---|---|---|
| **1/8** | Генерация TLS-сертификатов OpenBao | вызывает `generate-certs.sh`; пропускается, если сертификаты уже есть в `.deploy/docker/openbao/tls/` |
| **2/8** | Снятие дампа существующей БД | если mongod жив (ping, а не только статус контейнера — контейнер может быть в restart-loop) → `mongodump` через `docker compose exec`; существующий дамп `.deploy/dump-migration/dump` **не перезаписывается** |
| **3/8** | Остановка стека | `docker compose down` |
| **4/8** | Бэкап старых томов | `mongodb` → `mongodb-plain-<ts>`, `openbao` → `openbao-old-<ts>` (перемещение через docker-контейнер, если владелец root); пересоздание `mongodb/` с chown `1001:1001` (иначе mongod падает: lock-файл в read-only каталоге) |
| **5/8** | Старт OpenBao + PSMDB | `docker compose up -d openbao mongodb` + ожидание healthcheck (OpenBao 120 с, PSMDB 240 с; при неудаче — печать логов и выход) |
| **6/8** | Восстановление данных | `docker compose cp` дампа в `/tmp/dump` → `mongorestore` → удаление `/tmp/dump` от root (`exec -u 0`) → выполнение `init.js` (idempotent-создание админа WebUI) |
| **7/8** | Старт всего стека | `docker compose up -d` |
| **8/8** | Верификация | secrets engines, наличие мастер-ключа в KV, лог mongod (vault/encryption), TLS mongod→OpenBao, отсутствие plaintext IMSI в `.wt`, список БД, статус сервисов |

> **Важно**: скрипт ре-инициализирует OpenBao (генерируется **новый** мастер-ключ). Для обычного старта/перезагрузки уже зашифрованного стенда используйте `docker compose up -d`, а не этот скрипт.

## Вспомогательные скрипты

Все скрипты лежат в `.deploy/scripts/` и запускаются на сервере из корня проекта (сами разрешают свой путь):

| Скрипт | Назначение |
|---|---|
| `deploy-encrypted.sh` | Полное развёртывание с миграцией на зашифрованную PSMDB (8 шагов, см. выше). Основной скрипт |
| `generate-certs.sh` | Генерация CA (10 лет) и серверного сертификата OpenBao (825 дней, SAN: openbao, localhost, 127.0.0.1) в `.deploy/docker/openbao/tls/`. Пропускается, если сертификаты существуют. Вызывается из `deploy-encrypted.sh` |
| `verify-encrypted-stack.sh` | Верификация работающего деплоя — 8 проверок: статус контейнеров, список БД, счётчики коллекций (`subscribers`, `accounts`), лог шифрования mongod, TLS-соединение, мастер-ключ в KV v2, отсутствие plaintext IMSI в `/data/db`, `init.js` + HTTP-проверка webui. Параметры окружения (для рабочего деплоя): `IMSI_PATTERN`, `BAO_ADDR`, `WEBUI_URL` |
| `redump-from-backup.sh` | Аварийный: пересоздание миграционного дампа из plaintext-бэкапа `mongodb-plain-*` через временный mongod-контейнер (если оригинальный дамп потерян). Использование: `bash .deploy/scripts/redump-from-backup.sh [backup-dir-name]` |
| `inspect-stand.sh` | Диагностика запущенного стека: образы и монтирования контейнеров mongodb/openbao (ID контейнеров резолвятся через compose — не зависят от имени проекта), содержимое `local-data`, реальные пути данных внутри контейнеров, compose-метки |

---

## Проверка корректности шифрования

### Метод 1: Проверка через скрипт (рекомендуется)

```bash
bash .deploy/scripts/verify-encrypted-stack.sh
```

### Метод 2: Ручная проверка файлов данных

Файлы `.wt` принадлежат uid 1001 (mongod), поэтому нужен `sudo` (или читать изнутри контейнера):

```bash
# Остановить PSMDB (чтобы данные не менялись во время проверки)
docker compose stop mongodb

# Попытаться прочитать файлы данных напрямую — без ключа это нечитаемый мусор
sudo strings .deploy/docker/local-data/mongodb/collection-*.wt | head -20
# Ожидается: encryption=(keyid="...",name=percona),block_metadata_encrypted=true
# и бинарный шум. НЕ должно быть: IMSI (4660...), "open5gs", "subscribers", "accounts"

# Проверить отсутствие IMSI-паттернов по всем файлам
sudo grep -rEa "4660[0-9]{12}" .deploy/docker/local-data/mongodb/ || echo "OK: plaintext не найден"

# Запустить PSMDB с ключом — данные должны читаться как ни в чём не бывало
docker compose start mongodb
```

### Метод 3: Проверка через MongoDB shell

> В образе PSMDB 5.0 **нет `mongosh`** — используется legacy `mongo`.

```bash
docker compose exec mongodb mongo --quiet --eval "db.adminCommand('getParameter', '*').security"
# Ожидается: enableEncryption: true, vault: { serverName: "openbao", ... }
```

### Метод 4: Лог mongod

```bash
docker compose logs mongodb 2>&1 | grep -i "master encryption key"
# Ожидается: "Master encryption key has been read from the key management facility" (Vault, KV v2)
```

### Метод 5: TLS-соединение с OpenBao (с хоста)

```bash
echo | openssl s_client -connect localhost:8200 -CAfile .deploy/docker/openbao/tls/ca.crt 2>/dev/null | grep -E "Verify return code|subject="
# Ожидается: Verify return code: 0 (ok), subject CN=openbao
```

### Метод 6: Мастер-ключ в KV v2

```bash
docker compose exec openbao sh -c 'export BAO_TOKEN=$(cat /openbao/secrets/root-token); bao kv get secret/psmdb-encryption'
# Поле value — 96-символьный base64 мастер-ключ (не публикуйте его!)
```

---

## Структура конфигурации

### OpenBao (`openbao`)

- **Образ**: `ghcr.io/openbao/openbao:2.0.0`
- **Хранилище**: file backend (`/openbao/data`)
- **Secrets engine**: **KV v2** по пути `secret/` (PSMDB 5.0 поддерживает только KV v2)
- **Секрет с ключом**: `secret/data/psmdb-encryption` (KV v2 API-путь)
- **Авто-инициализация**: при первом старте генерирует root token и unseal key
- **Авто-unseal**: при каждом старте расшифровывает хранилище
- **Файлы**:
  - `.deploy/docker/openbao/openbao.hcl` — конфигурация сервера
  - `.deploy/docker/openbao/entrypoint.sh` — entrypoint с авто-init/unseal
  - `.deploy/docker/local-data/openbao/secrets/root-token` — root token
  - `.deploy/docker/local-data/openbao/secrets/unseal-key` — unseal key
  - `.deploy/docker/local-data/openbao/secrets/vault-token` — токен для PSMDB

### Percona Server for MongoDB (`mongodb`)

- **Образ**: `percona/percona-server-mongodb:5.0`
- **Конфигурация**: `.deploy/docker/psmdb/mongod.conf`
- **Шифрование**: включено через `security.enableEncryption: true`
- **Интеграция с Vault**:
  - `serverName: openbao`
  - `port: 8200`
  - `tokenFile: /etc/mongodb-encryption/vault-token`
  - `secret: secret/psmdb-encryption`
- **Тома**:
  - `/data/db` — зашифрованные данные
  - `/opt/init/init.js` — инициализация WebUI-админа (выполняется из `deploy-encrypted.sh`; **не** `/docker-entrypoint-initdb.d` — иначе entrypoint запустит временный незашифрованный mongod)
  - `/etc/mongod.conf` — конфигурация PSMDB
  - `/etc/mongodb-encryption/vault-token` — токен доступа к OpenBao

---

## Переменные окружения

Все настройки подключения Open5GS к MongoDB остаются без изменений:

```yaml
DB_URI: mongodb://mongodb/open5gs
```

Сервис `mongodb` сохраняет имя и порт `27017`, поэтому ни один из микросервисов Open5GS не требует переконфигурации.

---

## Ограничения и предупреждения

| Ограничение | Описание |
|-------------|----------|
| **Data-at-Rest только** | Шифрование защищает данные **на диске**, но не в оперативной памяти (RAM) и не в сетевом трафике между Open5GS и MongoDB. |
| **Логические бэкапы** | `mongodump` создаёт дамп в **расшифрованном** виде. Файлы дампа необходимо защищать отдельно (например, шифрованием файловой системы или GPG). |
| **Plaintext-артефакты миграции** | `deploy-encrypted.sh` сохраняет старые незашифрованные тома в `local-data/mongodb-plain-<ts>` и дамп в `.deploy/dump-migration/dump` — это **plaintext на диске**. После проверки целостности зашифрованной БД их необходимо удалить (см. «Очистка plaintext-артефактов миграции»). |
| **Потеря мастер-ключа** | Если потерян unseal key или root token из `.deploy/docker/local-data/openbao/secrets/`, **данные PSMDB становятся недоступны навсегда**. Рекомендуется резервное копирование этих файлов на защищённый носитель. |
| **Токен в общем томе** | Токен доступа PSMDB к OpenBao (`vault-token`) хранится в Docker volume с правами `600` и владельцем `1001:1001` (UID mongod в контейнере PSMDB). В продакшене рекомендуется использовать Kubernetes + Secrets или более строгие механизмы управления токенами. |
| **TLS для OpenBao** | Включён: TLS `:8200` для PSMDB, loopback HTTP `:8201` для healthcheck/CLI. Сертификаты генерируются `.deploy/scripts/generate-certs.sh` (CA + серверный сертификат SAN=openbao). |
| **Шифрование включается один раз** | Параметр `enableEncryption: true` применяется только при создании **нового** хранилища. Попытка включить шифрование на существующей незашифрованной базе приведёт к ошибке запуска. |

---

## Рекомендации по эксплуатации

### Резервное копирование критичных файлов

```bash
# Скопировать токены и unseal-ключи на защищённый носитель
tar czf openbao-secrets-$(date +%Y%m%d).tar.gz .deploy/docker/local-data/openbao/secrets/
```

### Резервное копирование данных

```bash
# Логический бэкап — сразу шифруем (дамп создаётся РАСШИФРОВАННЫМ!)
docker compose exec mongodb mongodump --out=/data/db/backup-$(date +%Y%m%d)
docker compose cp mongodb:/data/db/backup-$(date +%Y%m%d) ./backups/
tar czf - -C ./backups backup-$(date +%Y%m%d) \
  | openssl enc -aes-256-cbc -pbkdf2 -out ./backups/backup-$(date +%Y%m%d).tar.enc
rm -rf ./backups/backup-$(date +%Y%m%d)   # удалить незашифрованную копию

# Физический бэкап (зашифрованный на диске, но требует остановки PSMDB)
cp -a .deploy/docker/local-data/mongodb ./backups/mongodb-physical-$(date +%Y%m%d)
```

### Очистка plaintext-артефактов миграции

После миграции `deploy-encrypted.sh` оставляет на диске **незашифрованные** копии данных (страховка на случай неудачи):

- `local-data/mongodb-plain-<ts>/` — полные plaintext-бэкапы старой БД
- `local-data/openbao-old-<ts>/` — старые unseal-ключи OpenBao
- `.deploy/dump-migration/dump/` — plaintext mongodump

Проверить, что это plaintext, можно так (`block_metadata_encrypted=false` = незашифровано):

```bash
sudo strings .deploy/docker/local-data/mongodb-plain-*/collection-*.wt | grep -o 'encryption=(keyid=[^)]*)' | head
```

После **подтверждения целостности** зашифрованной БД артефакты нужно удалить (файлы принадлежат root/uid 1001, поэтому через контейнер):

```bash
# 1. Убедиться, что все данные в зашифрованной БД (сравнить с ожидаемым)
docker exec open5gs-compose-mongodb-1 mongo open5gs --quiet --eval "db.subscribers.count()"

# 2. Удалить plaintext-артефакты
docker run --rm \
  -v "$PWD/.deploy/docker/local-data:/x" \
  -v "$PWD/.deploy/dump-migration:/d" \
  busybox sh -c 'rm -rf /x/mongodb-plain-* /x/openbao-old-* /d/dump && ls -la /x'

# 3. Убедиться, что остались только зашифрованная БД и текущий OpenBao
#    (ожидается: mongodb/ и openbao/)
```

> **Альтернатива**: вместо удаления — заархивировать с шифрованием и перенести на защищённый носитель:
> `sudo tar czf - .deploy/docker/local-data/mongodb-plain-* | openssl enc -aes-256-cbc -pbkdf2 -out ~/backups/plain-backup.tar.enc`

### Восстановление после перезагрузки хоста

```bash
# Просто запустить стек — OpenBao автоматически unsealится
docker compose up -d
```

### Ротация ключа шифрования

Ротация мастер-ключа выполняется обновлением секрета в OpenBao (KV v2 создаёт новую версию) и перезапуском PSMDB:

```bash
# 1. Сгенерировать новый ключ и записать в KV v2 (создаст version 2 секрета)
NEW_KEY=$(openssl rand -base64 32)
curl --cacert .deploy/docker/openbao/tls/ca.crt -X POST \
  -H "X-Vault-Token: $(sudo cat .deploy/docker/local-data/openbao/secrets/root-token)" \
  -d "{\"data\":{\"key\":\"$NEW_KEY\"}}" \
  https://127.0.0.1:8200/v1/secret/data/psmdb-encryption

# 2. Перезапустить PSMDB — mongod прочитает новую версию ключа
docker compose restart mongodb
```

> **Примечание**: PSMDB при старте с новой вершией мастер-ключа расшифровывает DEK старым ключом из внутренней таблицы ключей и перезаписывает. Подробности: [Percona: Vault key rotation](https://docs.percona.com/percona-server-for-mongodb/5.0/vault.html). Перед ротацией обязательно сделайте резервную копию `local-data/openbao/secrets/` и физический бэкап данных.

---

## Устранение неполадок

### PSMDB не запускается с ошибкой шифрования

**Причина**: Вероятно, директория `/data/db` уже содержит незашифрованные данные.

**Решение**: Выполните миграцию через `.deploy/scripts/deploy-encrypted.sh` (данные будут сняты mongodump'ом и восстановлены в зашифрованное хранилище; старые тома сохранятся в `local-data/mongodb-plain-<ts>`).

### OpenBao остаётся в состоянии sealed

**Причина**: Файл `unseal-key` отсутствует или повреждён.

**Решение**:
```bash
# Проверить наличие ключей
ls -la .deploy/docker/local-data/openbao/secrets/

# Если ключи утеряны — данные недоступны. Необходимо:
# 1. Остановить стек
# 2. Удалить тома OpenBao и PSMDB
# 3. Восстановить из резервной копии
```

### Open5GS-сервисы не могут подключиться к MongoDB

**Причина**: PSMDB ещё не прошёл healthcheck или OpenBao не unsealed.

**Решение**:
```bash
docker compose logs openbao | tail -20
docker compose logs mongodb | tail -20
docker compose ps
```

---

## Лицензии

- **Percona Server for MongoDB**: [SSPL / Percona Software License](https://github.com/percona/percona-server-mongodb/blob/5.0/LICENSE)
- **OpenBao**: [MPL-2.0](https://github.com/openbao/openbao/blob/main/LICENSE)
- **Open5GS**: [AGPL-3.0](https://github.com/open5gs/open5gs/blob/main/LICENSE)

---

## Полезные ссылки

- [Percona Server for MongoDB — Data-at-Rest Encryption](https://docs.percona.com/percona-server-for-mongodb/5.0/vault.html)
- [OpenBao Documentation](https://openbao.org/docs/)
- [Open5GS Docker Compose](https://github.com/open5gs/open5gs)
- [Docker образы PSMDB](https://hub.docker.com/r/percona/percona-server-mongodb)
