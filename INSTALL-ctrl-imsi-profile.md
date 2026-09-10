---
title: "Инструкция по работе с ctrl-imsi-profile.py"
author: "Open5GS Administration"
date: "2026"
lang: ru-RU
---

# Инструкция по работе с ctrl-imsi-profile.py

## Содержание

1. Назначение
2. Требования
3. Установка
4. Быстрый старт
5. Проверка окружения
6. Файл абонентов
7. Режимы работы
8. Массовое добавление
9. Массовое удаление
10. Одиночные операции
11. Просмотр и список
12. Полная очистка БД
13. Автоматическое определение контейнеров
14. Переменные окружения
15. Диагностика
16. Типовые сценарии
17. Безопасность
18. Полный пример

---

## 1. Назначение

`ctrl-imsi-profile.py` — CLI-скрипт для управления абонентами Open5GS
(4G и 5G) в развёртываниях на базе `docker compose`.

Скрипт работает через уже запущенные контейнеры:

- `open5gs-dbctl` в контейнере с compose-сервисом `populate`;
- `mongo` shell в контейнере с compose-сервисом `mongodb`.

Никаких `docker run`, никаких внешних образов — только `docker exec`
в существующие контейнеры.

Возможности:

- массовое добавление абонентов из файла;
- массовое удаление абонентов из файла;
- одиночное добавление и удаление по IMSI;
- полная очистка БД (`purge`);
- просмотр документа абонента и списка всех абонентов;
- проверка окружения;
- поддержка OP и OPC;
- настраиваемый профиль: APN, PDN, QCI, ARP, AMF, SQN, S-NSSAI, AMBR;
- автоматическое определение контейнеров — работает в любом compose-проекте.

---

## 2. Требования

- Python 3.8 или новее;
- `docker` в `PATH`;
- запущенные контейнеры compose-сервисов `mongodb` и `populate`;
- MongoDB 5.0 или новее (проверено на `mongo:5.0.10-focal`);
- Open5GS 2.7.x (образы `gradiant/open5gs:2.7.7`, `gradiant/open5gs-dbctl:0.10.3`).

Сторонние Python-пакеты не требуются — только стандартная библиотека.

---

## 3. Установка

1. Скопировать `ctrl-imsi-profile.py` в каталог с `docker-compose.yaml`
   (или в любой другой каталог — это не обязательно).

2. Сделать исполняемым:

   ```bash
   chmod +x ctrl-imsi-profile.py
   ```

3. Проверить запуск:

   ```bash
   ./ctrl-imsi-profile.py
   ```

   Без параметров скрипт печатает справку.

---

## 4. Быстрый старт

```bash
# проверка окружения
./ctrl-imsi-profile.py check

# справка
./ctrl-imsi-profile.py -h
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

# полная очистка (сначала только показывает количество)
./ctrl-imsi-profile.py purge
./ctrl-imsi-profile.py purge --yes
```

---

## 5. Проверка окружения

```bash
./ctrl-imsi-profile.py check
```

Ожидаемый вывод при успехе:

```
============================================================
Проверка окружения
============================================================
[OK] контейнер box-populate-1 запущен
[OK] контейнер box-mongodb-1 запущен
[OK] MongoDB доступна
```

Если какой-то из пунктов не `[OK]` — см. раздел «Диагностика».

---

## 6. Файл абонентов

### 6.1. Назначение

Файл используется для **массовых** операций `add --file` и `del --file`.
Имя файла задаётся явно; значение по умолчанию не подставляется, чтобы
исключить случайный массовый запуск.

### 6.2. Формат

CSV. Одна строка — один абонент. Поля разделяются запятыми:

```
imsi, OP|OPC, ki_hex, op_or_opc_hex
```

| Поле | Описание |
|------|----------|
| `imsi` | 14 или 15 цифр |
| `OP\|OPC` | `OP` — в 4-м поле OP, `OPC` — в 4-м поле OPC |
| `ki_hex` | 32 hex-символа (KI) |
| `op_or_opc_hex` | 32 hex-символа (OP или OPC) |

### 6.3. Правила разбора

- пустые строки игнорируются;
- строки, начинающиеся с `#`, — комментарии;
- первая значимая строка, начинающаяся с `imsi` (регистронезависимо),
  считается заголовком и пропускается;
- если полей меньше 4 — строка пропускается с предупреждением;
- если поле 2 не `OP` и не `OPC` — строка пропускается с предупреждением;
- если IMSI не похож на 14–15 цифр — строка пропускается с предупреждением.

### 6.4. Пример

```
# imsi, mode, ki, op_or_opc
001010000000001, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f
001010000000002, OP,  00112233445566778899aabbccddeeff, 000102030405060708090a0b0c0d0e0f
001010000000003, OPC, 00112233445566778899aabbccddeeff, 0f0e0d0c0b0a09080706050403020100
```

### 6.5. Если файла нет

```
[ERROR] для массовой операции нужен --file <path>
[ERROR] или для одиночной операции укажите --imsi <IMSI> --key <HEX> --op|--opc <HEX>
[ERROR] подсказка: ./ctrl-imsi-profile.py help-file
```

Скрипт завершается без изменений в БД.

---

## 7. Режимы работы

| Режим | Как запускается | Источник данных |
|-------|-----------------|-----------------|
| Одиночный | `--imsi <IMSI> ...` | только указанные IMSI; файл игнорируется |
| Массовый  | `--file <path>` | все IMSI из файла |

Комбинировать нельзя:

- если задан `--imsi`, файл не читается;
- если задан `--file`, `--imsi` не используется;
- если не задано ни то, ни другое — ошибка.

---

## 8. Массовое добавление

```bash
./ctrl-imsi-profile.py add --file subscribers.txt
```

Все параметры профиля — дефолтные:

| Параметр | Значение |
|----------|----------|
| APN основной | `internet` |
| APN дополнительный | `ims` |
| PDN | IPv4 |
| QCI основного APN | 9 |
| ARP основного APN | 8 |
| QCI IMS APN | 5 |
| ARP IMS APN | 1 |
| AMF | `8000` |
| SQN | `000000000000` |
| IMEISV | `8609960408606358` |
| S-NSSAI | SST 1, SD `ffffff` |
| AMBR UL / DL | 1 Gbps |
| Security | из файла |

### 8.1. Со своими параметрами

```bash
./ctrl-imsi-profile.py add --file subscribers.txt \
    --apn internet --apn-extra ims,internet2 \
    --pdn ipv4v6 --qci 8 --arp 5 \
    --ims-qci 5 --ims-arp 1 \
    --amf 8000 --sst 1 --sd ffffff \
    --ambr-ul 1000000 --ambr-dl 1000000 \
    --security OP
```

Особенности:

- `--security OP` переопределяет режим для **всех** абонентов,
  даже если в файле указан `OPC`;
- `--apn-extra ""` — убрать все дополнительные APN;
- `--pdn ipv6` — тип PDN IPv6; `--pdn ipv4v6` — dual-stack;
- `--file /path/to/other.txt` — другой файл абонентов.

### 8.2. Идемпотентность

Повторный запуск `add` безопасен: уже существующие абоненты
пропускаются, дубли не создаются.

---

## 9. Массовое удаление

```bash
./ctrl-imsi-profile.py del --file subscribers.txt
```

Удаляет только тех абонентов, которые перечислены в файле.
`--key`, `--op`, `--opc` для `del` не нужны.

Удаление синхронное — ложных «не удалилось с первого раза» нет.

---

## 10. Одиночные операции

### 10.1. Добавление с OP

```bash
./ctrl-imsi-profile.py add \
    --imsi 001010000000001 \
    --key 00112233445566778899aabbccddeeff \
    --op  000102030405060708090a0b0c0d0e0f
```

### 10.2. Добавление с OPC

```bash
./ctrl-imsi-profile.py add \
    --imsi 001010000000002 \
    --key 00112233445566778899aabbccddeeff \
    --opc 0f0e0d0c0b0a09080706050403020100
```

### 10.3. Добавление с собственным профилем

```bash
./ctrl-imsi-profile.py add \
    --imsi 001010000000003 \
    --key 00112233445566778899aabbccddeeff \
    --op  000102030405060708090a0b0c0d0e0f \
    --apn internet --apn-extra ims \
    --pdn ipv4 --qci 9 --arp 8 \
    --amf 8000
```

### 10.4. Удаление

```bash
./ctrl-imsi-profile.py del --imsi 001010000000001
```

Несколько сразу:

```bash
./ctrl-imsi-profile.py del \
    --imsi 001010000000001 \
    --imsi 001010000000002
```

---

## 11. Просмотр и список

### 11.1. Документ абонента

```bash
./ctrl-imsi-profile.py show --imsi 001010000000001
```

Печатает полный JSON-документ из MongoDB.

### 11.2. Список всех абонентов

```bash
./ctrl-imsi-profile.py list
```

Пример вывода:

```
============================================================
Абоненты в БД
============================================================
Всего: 3
001010000000001  OP
001010000000002  OP
001010000000003  OPC
```

Если БД пуста:

```
(пусто — в БД нет абонентов)
```

---

## 12. Полная очистка БД

```bash
# показать количество, ничего не удалять
./ctrl-imsi-profile.py purge

# подтвердить и удалить всех
./ctrl-imsi-profile.py purge --yes
```

Пример безопасного запуска:

```
============================================================
Полная очистка БД абонентов Open5GS
============================================================
[OK] контейнер box-populate-1 запущен
[OK] контейнер box-mongodb-1 запущен
[OK] MongoDB доступна
Абонентов в БД: 12
[WARN] будет удалено 12 абонентов. Подтвердите флагом --yes
пример: ./ctrl-imsi-profile.py purge --yes
```

---

## 13. Автоматическое определение контейнеров

Скрипт не привязан к конкретному имени контейнера. Приоритеты:

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

Если ничего не найдено:

```
[ERROR] не удалось определить контейнеры автоматически
[ERROR] задайте явно: --mongo-container <name> --dbctl-container <name>
[ERROR] или переменные окружения: OPEN5GS_MONGO_CONTAINER=... OPEN5GS_DBCTL_CONTAINER=...
```

---

## 14. Переменные окружения

| Переменная | Назначение | По умолчанию |
|------------|-----------|--------------|
| `OPEN5GS_MONGO_CONTAINER` | имя контейнера MongoDB | авто-поиск |
| `OPEN5GS_DBCTL_CONTAINER` | имя контейнера open5gs-dbctl | авто-поиск |
| `OPEN5GS_DB_NAME` | имя БД | `open5gs` |
| `COMPOSE_PROJECT_NAME` | имя compose-проекта | basename каталога скрипта |

---

## 15. Диагностика

| Симптом | Причина | Решение |
|---------|---------|---------|
| `No such container: ...` | контейнер не найден | `docker compose ps`; задать `--mongo-container` / `--dbctl-container` |
| `[ERROR] не удалось определить контейнеры` | нет compose-меток и явных имён | задать флаги или env |
| `[ERROR] MongoDB недоступна` | mongod не запущен | `docker compose up -d mongodb` |
| `[ERROR] dbctl add провалился ... E11000` | абонент уже есть | обрабатывается как `skipped`; при частом появлении — обновить скрипт |
| `[ERROR] данных нет` | нет `--file` и нет `--imsi` | указать один из них |
| `[ERROR] не удалось установить security` | mongo не отвечает | `./ctrl-imsi-profile.py check` |

---

## 16. Типовые сценарии

### 16.1. Массовая загрузка тестовых абонентов

```bash
# 1. Создать subscribers.txt
# 2. Загрузить
./ctrl-imsi-profile.py add --file subscribers.txt

# 3. Проверить
./ctrl-imsi-profile.py list
```

### 16.2. Смена security режима

Если абонент добавлен как OPC, а нужен OP — сначала удалить,
потом добавить заново:

```bash
./ctrl-imsi-profile.py del --imsi 001010000000001
./ctrl-imsi-profile.py add \
    --imsi 001010000000001 \
    --key 00112233445566778899aabbccddeeff \
    --op  000102030405060708090a0b0c0d0e0f
```

### 16.3. Полная перезагрузка БД

```bash
./ctrl-imsi-profile.py purge --yes
./ctrl-imsi-profile.py add --file subscribers.txt
```

### 16.4. Перенос скрипта на другой стенд

```bash
scp ctrl-imsi-profile.py user@host:~/box/
ssh user@host
cd ~/box
./ctrl-imsi-profile.py check
./ctrl-imsi-profile.py list
```

Скрипт сам определит контейнеры по compose-меткам.

---

## 17. Безопасность

- в help-текстах используются фиктивные IMSI / KI / OP / OPC;
- `purge` без `--yes` ничего не удаляет;
- `del` работает только по явному списку (файл или `--imsi`);
- `add` идемпотентен — повторный запуск безопасен;
- OP / OPC не смешиваются: в БД хранится только одно из полей.

---

## 18. Полный пример

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
