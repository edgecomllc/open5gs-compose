# ctrl-imsi-profile.py

Универсальное управление абонентами Open5GS через `docker compose`.

Работает напрямую с MongoDB и утилитой `open5gs-dbctl` в уже запущенных
контейнерах — никаких `docker run`, никаких внешних образов.

---

## Возможности

- массовое добавление и удаление абонентов из файла;
- одиночное добавление и удаление по IMSI;
- полная очистка БД (`purge`);
- просмотр документа абонента и списка всех абонентов;
- проверка окружения;
- поддержка OP и OPC;
- настраиваемый профиль: APN, PDN (IPv4 / IPv6 / IPv4v6), QCI, ARP,
  AMF, SQN, S-NSSAI, AMBR;
- автоматическое определение контейнеров — работает в любом compose-проекте
  (`open5gs-compose`, `box`, `my5g`, ...).

---

## Требования

- Python 3.8 или новее;
- `docker` в `PATH`;
- запущенные контейнеры compose-сервисов `mongodb` и `populate`;
- MongoDB 5.0 или новее (проверено на `mongo:5.0.10-focal`);
- Open5GS 2.7.x (`gradiant/open5gs:2.7.7`, `gradiant/open5gs-dbctl:0.10.3`).

Сторонние Python-пакеты не требуются — только стандартная библиотека.

---

## Быстрый старт

```bash
chmod +x ctrl-imsi-profile.py

# проверка окружения
./ctrl-imsi-profile.py check

# справка
./ctrl-imsi-profile.py
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

# одиночное удаление
./ctrl-imsi-profile.py del --imsi 001010000000001

# список всех абонентов
./ctrl-imsi-profile.py list

# просмотр документа абонента
./ctrl-imsi-profile.py show --imsi 001010000000001

# полная очистка БД (сначала только показывает количество)
./ctrl-imsi-profile.py purge
./ctrl-imsi-profile.py purge --yes
```

---

## Подкоманды

| Подкоманда | Назначение |
|------------|-----------|
| `add`       | Добавить абонентов: массово (`--file`) или одиночно (`--imsi`) |
| `del`       | Удалить абонентов: массово (`--file`) или одиночно (`--imsi`) |
| `purge`     | Удалить ВСЕХ абонентов из БД (требует `--yes`) |
| `show`      | Показать документ абонента по IMSI |
| `list`      | Список всех абонентов в БД |
| `check`     | Проверка контейнеров и MongoDB |
| `help-file` | Справка по формату файла абонентов |

Полная справка:

```bash
./ctrl-imsi-profile.py -h
./ctrl-imsi-profile.py add -h
./ctrl-imsi-profile.py del -h
./ctrl-imsi-profile.py purge -h
```

---

## Файл абонентов

Используется при **массовых** операциях:

```bash
./ctrl-imsi-profile.py add --file <path>
./ctrl-imsi-profile.py del --file <path>
```

### Формат

CSV, одна строка — один абонент:

```
imsi, OP|OPC, ki_hex, op_or_opc_hex
```

| Поле | Описание |
|------|----------|
| `imsi` | 14 или 15 цифр |
| `OP\|OPC` | `OP` — в 4-м поле OP, `OPC` — в 4-м поле OPC |
| `ki_hex` | 32 hex-символа (KI) |
| `op_or_opc_hex` | 32 hex-символа (OP или OPC) |

### Правила разбора

- пустые строки игнорируются;
- строки, начинающиеся с `#`, — комментарии;
- первая значимая строка, начинающаяся с `imsi` (регистронезависимо),
  считается заголовком и пропускается;
- если полей меньше 4 — строка пропускается с предупреждением;
- если поле 2 не `OP` и не `OPC` — строка пропускается с предупреждением;
- если IMSI не похож на 14–15 цифр — строка пропускается с предупреждением.

### Пример

```
# imsi, mode, ki, op_or_opc
001010000000001, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f
001010000000002, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f
001010000000003, OPC, 00112233445566778899aabbccddeeff, 0f0e0d0c0b0a09080706050403020100
```

### Если файла нет

При запуске массовой операции без `--file` скрипт сообщит:

```
[ERROR] для массовой операции нужен --file <path>
[ERROR] или для одиночной операции укажите --imsi <IMSI> --key <HEX> --op|--opc <HEX>
[ERROR] подсказка: ./ctrl-imsi-profile.py help-file
```

и завершится без изменений в БД.

---

## Два режима работы `add` / `del`

| Режим | Как запускается | Источник данных |
|-------|-----------------|-----------------|
| Одиночный | `--imsi <IMSI> ...` | только указанные IMSI; файл игнорируется |
| Массовый  | `--file <path>` | все IMSI из файла |

Комбинировать нельзя: если задан `--imsi`, файл не читается;
если задан `--file`, `--imsi` не используется.
Если не задано ни то, ни другое — ошибка.

---

## Примеры

### Массовое добавление с расширенным профилем

```bash
./ctrl-imsi-profile.py add --file subscribers.txt \
    --apn internet --apn-extra ims,internet2 \
    --pdn ipv4v6 --qci 8 --arp 5 \
    --amf 8000 --sst 1 --sd ffffff \
    --security OP
```

### Одиночное добавление с OPC

```bash
./ctrl-imsi-profile.py add \
    --imsi 001010000000001 \
    --key 00112233445566778899aabbccddeeff \
    --opc 0f0e0d0c0b0a09080706050403020100
```

### Удаление нескольких IMSI за раз

```bash
./ctrl-imsi-profile.py del \
    --imsi 001010000000001 \
    --imsi 001010000000002
```

### Полная очистка БД

```bash
# только показать, сколько удалится
./ctrl-imsi-profile.py purge

# реально удалить всех
./ctrl-imsi-profile.py purge --yes
```

---

## Параметры профиля (для `add`)

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| `--apn` | `internet` | основной APN |
| `--apn-extra` | `ims` | доп. APN через запятую; `""` — без доп. APN |
| `--pdn` | `ipv4` | `ipv4`, `ipv6`, `ipv4v6` |
| `--qci` | `9` | QCI/5QI основного APN |
| `--arp` | `8` | ARP основного APN |
| `--ims-qci` | `5` | QCI для IMS APN |
| `--ims-arp` | `1` | ARP для IMS APN |
| `--amf` | `8000` | AMF hex |
| `--sqn` | `000000000000` | SQN hex |
| `--imeisv` | `8609960408606358` | IMEISV |
| `--sst` | `1` | S-NSSAI SST |
| `--sd` | `ffffff` | S-NSSAI SD hex |
| `--ambr-ul` | `1000000` | AMBR UL, kbps |
| `--ambr-dl` | `1000000` | AMBR DL, kbps |
| `--security` | из файла / из `--op`/`--opc` | принудительно `OP` или `OPC` для всех |

---

## Автоматическое определение контейнеров

Скрипт не привязан к конкретному имени контейнера.
Приоритеты определения:

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

3. **Авто-поиск по меткам docker compose:**
   - `com.docker.compose.service=mongodb`
   - `com.docker.compose.service=populate`

   с учётом `COMPOSE_PROJECT_NAME` или `basename` каталога скрипта.

4. **Fallback:** имя контейнера содержит `-mongodb-1` или `-populate-1`.

Если ничего не найдено, скрипт сообщит:

```
[ERROR] не удалось определить контейнеры автоматически
[ERROR] задайте явно: --mongo-container <name> --dbctl-container <name>
[ERROR] или переменные окружения: OPEN5GS_MONGO_CONTAINER=... OPEN5GS_DBCTL_CONTAINER=...
```

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

- **Одиночный и массовый режимы разделены**: `--imsi` игнорирует файл,
  `--file` игнорирует `--imsi`.
- **Идемпотентность `add`**: повторный запуск не создаёт дубли и не трогает
  существующих абонентов — они пропускаются.
- **`E11000` при `add`** (race с параллельной записью) обрабатывается как
  «уже существует», а не как ошибка.
- **`del` — синхронный** (`deleteOne` + `deletedCount`): ложных «не удалилось
  с первого раза» нет.
- **`purge` без `--yes`** ничего не удаляет — только печатает количество.
- **OP / OPC** — оба режима поддерживаются. В БД ставится только одно из полей;
  противоположное удаляется через `$unset`.
- **Совместимость с MongoDB 5.0.10** (`mongo:5.0.10-focal`) — учтены баги
  legacy `mongo` (`countDocuments({})`, `ok:0` при `rc=0`).

---

## Диагностика

| Симптом | Причина | Решение |
|---------|---------|---------|
| `No such container: ...` | контейнер не найден | `docker compose ps` и `--mongo-container` / `--dbctl-container` |
| `[ERROR] не удалось определить контейнеры` | нет compose-меток и нет явных имён | задать флаги или env |
| `[ERROR] MongoDB недоступна` | mongod не запущен | `docker compose up -d mongodb` |
| `[ERROR] dbctl add провалился ... E11000` | абонент уже есть (race) | обрабатывается как `skipped`; если видите — обновите скрипт |
| `[ERROR] данных нет` | нет `--file` и нет `--imsi` | указать один из них |
| `[ERROR] не удалось установить security` | mongo не отвечает | `./ctrl-imsi-profile.py check` |

---

## Полный цикл

```bash
# 1. Проверка окружения
./ctrl-imsi-profile.py check

# 2. Подготовить subscribers.txt

# 3. Массовое добавление
./ctrl-imsi-profile.py add --file subscribers.txt

# 4. Проверка
./ctrl-imsi-profile.py list
./ctrl-imsi-profile.py show --imsi 001010000000001

# 5. Одиночное добавление
./ctrl-imsi-profile.py add \
    --imsi 001010000099999 \
    --key 00112233445566778899aabbccddeeff \
    --op  000102030405060708090a0b0c0d0e0f

# 6. Одиночное удаление
./ctrl-imsi-profile.py del --imsi 001010000099999

# 7. Массовое удаление
./ctrl-imsi-profile.py del --file subscribers.txt

# 8. Полная очистка
./ctrl-imsi-profile.py purge --yes
```

---

## Безопасность

- в help-текстах используются **фиктивные** IMSI / KI / OP / OPC;
- `purge` без `--yes` ничего не удаляет;
- `del` работает только по явному списку (файл или `--imsi`);
- `add` идемпотентен — повторный запуск безопасен.

---

## Совместимость

- Open5GS 2.7.x;
- MongoDB 5.0+;
- Docker Compose v2;
- Linux, macOS.

---

## Лицензия

Внутренний проект.

