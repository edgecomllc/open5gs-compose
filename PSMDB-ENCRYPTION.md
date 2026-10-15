# Open5GS с Percona Server for MongoDB и шифрованием Data-at-Rest

## Обзор

В этом проекте стандартная MongoDB Community Edition заменена на **Percona Server for MongoDB (PSMDB) 5.0** с включённым прозрачным шифрованием данных на уровне хранилища (Data-at-Rest Encryption). Мастер-ключ шифрования хранится во внешнем KMS — **OpenBao** (открытый форк HashiCorp Vault).

Open5GS продолжает работать **без изменений кода**, так как PSMDB является drop-in заменой MongoDB Community по протоколу и URI подключения.

---

## Архитектура

```
┌─────────────────┐      ┌─────────────────────────────┐      ┌─────────────────┐
│   Open5GS       │      │   Percona Server for        │      │   OpenBao KMS   │
│   (HSS/UDM/     │◄────►│   MongoDB 5.0 (PSMDB)       │◄────►│   (мастер-ключ) │
│    WebUI и др.) │      │   - WiredTiger encrypted    │      │   - KV v1       │
│                 │      │   - Vault interface         │      │   - auto-unseal │
└─────────────────┘      └─────────────────────────────┘      └─────────────────┘
         │                           │
         │    mongodb://mongodb/     │    HTTP API (8200)
         │       open5gs             │    token + secret path
         │                           │
```

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
# Запустить скрипт верификации
chmod +x .deploy/scripts/verify-encryption.sh
./.deploy/scripts/verify-encryption.sh
```

---

## Миграция с существующей MongoDB

Если в текущей базе уже есть данные абонентов, выполните миграцию:

```bash
chmod +x .deploy/scripts/migrate-to-encrypted.sh
./.deploy/scripts/migrate-to-encrypted.sh
```

Скрипт выполнит:
1. Остановку Open5GS-сервисов (кроме MongoDB)
2. `mongodump` из текущей базы
3. Остановку и удаление старого контейнера MongoDB
4. Резервное копирование старого тома данных
5. Запуск OpenBao + PSMDB с шифрованием
6. `mongorestore` данных в зашифрованное хранилище
7. Запуск всех Open5GS-сервисов

---

## Проверка корректности шифрования

### Метод 1: Проверка через скрипт

```bash
./.deploy/scripts/verify-encryption.sh
```

### Метод 2: Ручная проверка файлов данных

```bash
# Остановить PSMDB
docker compose stop mongodb

# Попытаться прочитать файлы данных напрямую
strings .deploy/docker/local-data/mongodb/collection-*.wt | head -20
# Должны быть нечитаемые символы (без "open5gs", "accounts", "admin")

# Запустить PSMDB с корректным ключом — данные должны читаться
docker compose start mongodb
```

### Метод 3: Проверка через MongoDB shell

```bash
docker compose exec mongodb mongosh --eval "db.adminCommand('getParameter', '*').security"
# Ожидается: security.enableEncryption = true
```

---

## Структура конфигурации

### OpenBao (`openbao`)

- **Образ**: `ghcr.io/openbao/openbao:2.0.0`
- **Хранилище**: file backend (`/openbao/data`)
- **Secrets engine**: KV v1 по пути `secret/`
- **Секрет с ключом**: `secret/psmdb-encryption`
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
  - `/docker-entrypoint-initdb.d/init.js` — инициализация WebUI-админа
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
| **Токен в общем томе** | Токен доступа PSMDB к OpenBao (`vault-token`) хранится в Docker volume с правами 644 для совместимости с UID mongod (1001). В продакшене рекомендуется использовать Kubernetes + Secrets или более строгие механизмы управления токенами. |
| **TLS для OpenBao** | В текущей конфигурации OpenBao работает без TLS (`tls_disable = true`). Для продакшена необходимо настроить TLS-сертификаты. |
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

PSMDB поддерживает ротацию мастер-ключа шифрования через команду:

```bash
docker compose exec mongodb mongosh --eval "db.adminCommand( {rotateCertificates: 1} )"
```

> **Примечание**: Ротация ключа в Vault требует обновления секрета `secret/psmdb-encryption` и перезапуска PSMDB. Подробности в документации Percona.

---

## Устранение неполадок

### PSMDB не запускается с ошибкой шифрования

**Причина**: Вероятно, директория `/data/db` уже содержит незашифрованные данные.

**Решение**: Выполните миграцию через `.deploy/scripts/migrate-to-encrypted.sh`.

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
