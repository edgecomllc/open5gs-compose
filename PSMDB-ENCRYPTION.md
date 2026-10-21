# Open5GS с Percona Server for MongoDB и шифрованием Data-at-Rest

## Обзор

В этом проекте стандартная MongoDB Community Edition заменена на **Percona Server for MongoDB (PSMDB) 5.0** с включённым прозрачным шифрованием данных на уровне хранилища (Data-at-Rest Encryption). Мастер-ключ шифрования хранится во внешнем KMS — **OpenBao** (открытый форк HashiCorp Vault).

Open5GS продолжает работать **без изменений кода**, так как PSMDB является drop-in заменой MongoDB Community по протоколу и URI подключения.

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

---

## Как работает прозрачное шифрование (и почему данные «видно»)

### Принцип Data-at-Rest

Шифрование защищает данные **только там, где они лежат в покое** — на диске (`.wt`-файлы в `.deploy/docker/local-data/mongodb/`). Это называется **Data-at-Rest Encryption**. Всё, что происходит «поверх» диска, работает как будто шифрования нет.

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

### Почему `ctrl-imsi-profile.py` показывает IMSI и K/OPC в открытом виде?

Это **нормальное, ожидаемое поведение**, а не проблема:

- Утилита подключается к **живому mongod** по протоколу MongoDB (`mongodb://mongodb/open5gs`). Mongod уже владеет ключами (получил их из OpenBao при старте), поэтому **сам расшифровывает** запрошенные документы и отдаёт клиенту plaintext.
- То же самое делают HSS, UDR и WebUI — именно поэтому Open5GS работает **без единого изменения кода или конфигурации**.
- Шифрование прозрачно («transparent») по определению: клиенты БД вообще не знают о его существовании.

### От чего защита реально помогает (и от чего — нет)

| Сценарий | Защищено? | Почему |
|---|---|---|
| **Кража/копирование диска или `.wt`-файлов** (сервер украли, сняли образ VM, скопировали `local-data/mongodb`) | ✅ **Да** | Без мастера-ключа из OpenBao файлы — нечитаемый мусор. Проверка: `sudo strings .deploy/docker/local-data/mongodb/collection-*.wt \| head` — видны только `encryption=(keyid=...,block_metadata_encrypted=true)` и бинарный шум, IMSI-паттерны `4660[0-9]\{12\}` отсутствуют |
| **Утрата unseal-ключей OpenBao** (`local-data/openbao/secrets/`) | ⚠️ **Данные потеряны навсегда** | Мастер-ключ невозможно восстановить — храните резервные копии секретов на защищённом носителе |
| Подключение к **работающему** mongod (утилиты, HSS, WebUI, злоумышленник с доступом к сети Docker) | ❌ Нет | Mongod отдаёт расшифрованные данные любому авторизованному клиенту — это функция СУБД |
| Данные в **RAM** mongod | ❌ Нет | Data-at-Rest = только диск |
| Трафик Open5GS ↔ MongoDB внутри Docker-сети | ❌ Нет | При необходимости включается TLS-транспорт MongoDB отдельно (не настроено в этом стенде) |
| `mongodump` / логические бэкапы | ❌ Нет | Дамп создаётся **расшифрованным** — защищайте файлы дампа отдельно |

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

## ✅ Состояние тестового стенда (192.168.0.9)

> **Шифрование MongoDB ВКЛЮЧЕНО и работает** (проверено 2026-10-21).
>
> - OpenBao работает с **TLS** на `:8200` (серверный сертификат SAN=openbao, CA — `.deploy/docker/openbao/tls/`); loopback HTTP `:8201` используется только для healthcheck/CLI (в образе BusyBox wget без `--no-check-certificate`).
> - PSMDB подключается к OpenBao по HTTPS и получает мастер-ключ из **KV v2** секрета `secret/data/psmdb-encryption` (PSMDB 5.0 поддерживает только KV v2).
> - `init.js` монтируется в `/opt/init/init.js` (НЕ в `/docker-entrypoint-initdb.d`): entrypoint PSMDB иначе сначала запускает временный **незашифрованный** mongod, который создаёт plaintext-метаданные WiredTiger, нечитаемые зашифрованным mongod. Скрипт выполняется из `deploy-encrypted.sh`.
> - В образе PSMDB нет `mongosh` и `openssl` — используется legacy `mongo`.
> - Развёртывание: `.deploy/scripts/deploy-encrypted.sh` (с Windows — `.deploy/scripts/deploy-from-windows.ps1`), верификация: `.deploy/scripts/verify-encrypted-stack.sh`.

---

## Быстрый старт (новое развёртывание)

### 1. Предварительные требования

- Docker Engine >= 24.0
- Docker Compose >= 2.20 (поддержка `depends_on` с `condition: service_healthy`)
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

```bash
# Запустить скрипт верификации (статус контейнеров, БД, TLS, KV v2,
# отсутствие plaintext IMSI в файлах хранилища, webui)
bash .deploy/scripts/verify-encrypted-stack.sh
```

---

## Миграция с существующей MongoDB

Если в текущей базе уже есть данные абонентов, выполните миграцию:

```bash
bash .deploy/scripts/deploy-encrypted.sh
```

Скрипт выполнит:
1. Генерацию TLS-сертификатов OpenBao (если отсутствуют)
2. `mongodump` из текущей базы (если дамп ещё не снят)
3. Остановку стека
4. Резервное копирование старых томов данных (`mongodb-plain-<ts>`, `openbao-old-<ts>`)
5. Запуск OpenBao (TLS + KV v2) + PSMDB с шифрованием
6. `mongorestore` данных в зашифрованное хранилище + выполнение `init.js`
7. Запуск всех Open5GS-сервисов
8. Верификацию

---

## Проверка корректности шифрования

### Метод 1: Проверка через скрипт (рекомендуется)

```bash
bash .deploy/scripts/verify-encrypted-stack.sh
# (старое имя verify-encryption.sh сохранено как wrapper)
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

### Метод 4: Утилиты open5gs-utils (проверка работоспособности данных)

```bash
# Данные читаются через живой mongod (расшифровка прозрачная — это нормально)
../open5gs-utils/ctrl-imsi-profile.py list
../open5gs-utils/ctrl-imsi-profile.py show --imsi 466012345678901
```

Утилиты массового управления профилями (все работают с зашифрованной БД без каких-либо изменений, т.к. подключаются к живому mongod):

| Утилита | Назначение |
|---|---|
| `ctrl-imsi-profile.py` | Просмотр/создание/изменение/удаление профилей абонентов (`show`, `list`, `add`, `del`, ...) |
| `add_imsi_docker.py` | Массовое добавление абонентов из списка |
| `ctrl-imsi-export.py` | Экспорт профилей (дамп в человекочитаемом виде — **файл экспорта не зашифрован**, защищайте его отдельно) |
| `eki_to_ki.py` | Конвертация EKI → KI для ключей безопасности |

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
# Логический бэкап (расшифрованный!)
docker compose exec mongodb mongodump --out=/data/db/backup-$(date +%Y%m%d)
docker compose cp mongodb:/data/db/backup-$(date +%Y%m%d) ./backups/

# Физический бэкап (зашифрованный, но требует остановки PSMDB)
cp -a .deploy/docker/local-data/mongodb ./backups/mongodb-physical-$(date +%Y%m%d)
```

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
