"""
vypiska — разбор CSV-выписок российских банков в единый список операций.

Порт vypiska.js на Python 3.9+: один файл, без зависимостей, ничего никуда не отправляет.
Поведение и форма результата совпадают с JavaScript-версией — это проверяют тесты
(test/test_vypiska.py сравнивает вывод обеих версий на фикстурах и случайных выписках).

Чтобы совпадение было точным, здесь повторены правила JavaScript там, где Python
ведёт себя иначе: пробельные символы \\s и trim(), регистронезависимый поиск /i,
округление Math.round, декодирование TextDecoder.
"""

import math
import re

__all__ = ["parse", "parse_many", "decode", "parse_amount", "parse_date", "parse_csv", "find_columns", "HEADERS"]

# Ключевые слова колонок по приоритету: первое совпадение побеждает.
HEADERS = {
    # Дата поступления/списания денег, а не дата документа.
    "date": ["дата операции", "дата совершения операции", "дата проводки", "дата поступления", "дата зачисления",
             "дата списания", "дата платежа", "дата транзакции", "дата", "date"],
    "credit": ["по кредиту", "приход", "зачисление", "поступление", "кредит", "credit"],
    "debit": ["по дебету", "расход", "списание", "дебет", "debit"],
    # Сначала сумма в валюте счёта: у Т-Банка это «Сумма платежа», у ВТБ — «Сумма пересчитанная в валюту счета».
    "amount": ["сумма платежа", "сумма в валюте сч", "сумма пересчитанная в валют", "сумма операции", "сумма", "amount"],
    "currency": ["валюта платежа", "валюта счета", "валюта счёта", "валюта операции", "валюта", "currency"],
    "status": ["статус", "status"],
    "category": ["категория", "category"],
    "mcc": ["mcc"],
    "description": ["назначение платежа", "назначение", "описание операции", "детали операции", "описание", "основание",
                    "description", "merchant"],
    "counterparty": ["плательщик", "получатель", "контрагент", "отправитель", "наименование контрагента", "payer", "payee"],
}
ORDER = ["date", "credit", "debit", "amount", "currency", "status", "category", "mcc", "description", "counterparty"]
MONEY_KEYS = {"credit", "debit", "amount"}

# Пробельные символы JavaScript (\s и String.prototype.trim): у Python свой набор.
_WS_CHARS = "\t\n\x0b\x0c\r              " \
            "    　﻿"
_WS = "[" + _WS_CHARS + "]"


def _trim(s):
    return s.strip(_WS_CHARS)


def _canon(s):
    """Регистр как у регулярных выражений JavaScript с флагом i (без u): каждый символ — в верхний регистр,
    если получается ровно один символ и не-ASCII не превращается в ASCII. Шаблоны пишутся заглавными."""
    out = []
    for c in s:
        u = c.upper()
        out.append(u if len(u) == 1 and ord(c) <= 0xFFFF and not (ord(c) >= 128 and ord(u) < 128) else c)
    return "".join(out)


def _sub_ci(pattern, repl, s):
    """re.sub без учёта регистра по правилам JavaScript: ищем в _canon(s), заменяем в исходной строке."""
    canon = _canon(s)
    parts, last = [], 0
    for m in pattern.finditer(canon):
        parts.append(s[last:m.start()])
        parts.append(repl)
        last = m.end()
    parts.append(s[last:])
    return "".join(parts)


# «Счет Кредит», «ИНН», «Банк» — это не суммы, даже если в названии есть «кредит».
NOT_MONEY_HEADER = re.compile(r"сч[её]т|инн|бик|банк|наименован|кэшб[эе]к|бонус|cashback")
# Остаток и баланс — не сумма операции, даже если «в валюте счёта».
BALANCE_HEADER = re.compile(r"остат|баланс|сальдо|лимит|balance")
MONEY_HEADER = re.compile(r"сумм|оборот")
_BONUS_HEADER = re.compile(r"кэшб[эе]к|бонус")
FAILED = re.compile(r"ОТКЛОН|FAILED|DECLINED|ОТМЕН|CANCEL|ОШИБК")  # проверяется по _canon(), как /…/i
# «RUB», «Рубли», «Российский рубль (RUB)», код 643 или старый 810 — всё это рубли.
RUB = re.compile(r"\Z|(RUB|RUR|₽|РУБ|РУБЛ|РОССИЙСК|643|810)")  # match() по _canon(), как /^$|^(…)/i
_MCC = re.compile(r"(?<![A-Z])MCC" + _WS + r"?([0-9]{4})(?![0-9])")  # search() по _canon()
DELIMITERS = [";", ",", "\t"]

_NEWLINES = re.compile(r"\r?\n")
_CELL_BREAK = re.compile(_WS + r"*[\r\n]+" + _WS + r"*")
_WS_RUN = re.compile(_WS + r"+")
_EDGE_QUOTES = re.compile(r"^['\"]|['\"]\Z")


def _parse_rows(text, delim, by_line=False):
    """Строки таблицы и номер строки файла, с которой каждая начинается (для поля line)."""
    if by_line:
        rows, lines = [], []
        for k, piece in enumerate(_NEWLINES.split(text)):
            for r in _parse_rows(piece, delim)[0]:
                rows.append(r)
                lines.append(k + 1)
        return rows, lines
    raw = []
    row, cur, quoted = [], [], False
    line = start = 1
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quoted:
            if ch == '"' and i + 1 < n and text[i + 1] == '"':
                cur.append('"')
                i += 1
            elif ch == '"':
                quoted = False
            else:
                cur.append(ch)
                if ch == "\n" or (ch == "\r" and not (i + 1 < n and text[i + 1] == "\n")):
                    line += 1
        elif ch == '"' and _trim("".join(cur)) == "":
            quoted = True
            cur = []
        elif ch == delim:
            row.append("".join(cur))
            cur = []
        elif ch == "\n" or ch == "\r":
            if ch == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
            row.append("".join(cur))
            raw.append((row, start))
            row, cur = [], []
            line += 1
            start = line
        else:
            cur.append(ch)
        i += 1
    # Незакрытая кавычка съела бы остаток файла: тогда разбираем по строкам.
    if quoted and "\n" in text:
        return _parse_rows(text, delim, True)
    row.append("".join(cur))
    raw.append((row, start))
    rows, lines = [], []
    for r, l in raw:
        cells = [_trim(_CELL_BREAK.sub(" ", c)) for c in r]
        if any(cells):
            rows.append(cells)
            lines.append(l)
    return rows, lines


def parse_csv(text, delim):
    """
    CSV целиком, а не построчно: Excel сохраняет ячейки с переносом строки
    («счёт / ИНН / название») в кавычках, и такая запись занимает несколько строк.
    Кавычка открывает поле только в его начале; внутри поля она обычный символ.
    """
    return _parse_rows(text, delim)[0]


def _fits(key, h):
    if key not in MONEY_KEYS:
        return True
    if BALANCE_HEADER.search(h):
        return False
    return not NOT_MONEY_HEADER.search(h) or bool(MONEY_HEADER.search(h)) and not _BONUS_HEADER.search(h)


def find_columns(header):
    lower = [_EDGE_QUOTES.sub("", _WS_RUN.sub(" ", h.lower())) for h in header]
    cols = {}
    used = set()
    for key in ORDER:
        for kw in HEADERS[key]:
            idx = next((i for i, h in enumerate(lower)
                        if i not in used and (h == kw if key == "mcc" else kw in h) and _fits(key, h)), -1)
            if idx != -1:
                cols[key] = idx
                used.add(idx)
                break
    # Сумма уже в валюте счёта — значит «Валюта операции» к ней не относится.
    if "currency" in cols and "amount" in cols and \
            re.search(r"платеж|в валют[еу] сч", lower[cols["amount"]]) and re.search(r"операци", lower[cols["currency"]]):
        del cols["currency"]
    if "currency" in cols and BALANCE_HEADER.search(lower[cols["currency"]]):
        del cols["currency"]
    return cols


def _is_header(cols):
    return "date" in cols and ("amount" in cols or "credit" in cols or "debit" in cols)


def _find_header(text):
    """Разделитель и шапку ищем вместе: заголовок «Выписка по счету …, ИП …» с запятой не должен решать за весь файл."""
    best = None
    for delim in DELIMITERS:
        rows, lines = _parse_rows(text, delim)
        for i in range(min(len(rows), 50)):
            cols = find_columns(rows[i])
            if not _is_header(cols):
                continue
            if best is None or i < best["idx"] or (i == best["idx"] and len(rows[i]) > len(best["rows"][best["idx"]])):
                best = {"rows": rows, "lines": lines, "idx": i, "cols": cols, "delim": delim}
            break
    return best


_DASHES = re.compile("[−–—]")
_CURRENCY_NOISE = re.compile(r"РУБ\.?|Р\.|RUR|RUB|₽|[" + _WS_CHARS + "  ']")
_TRAILING_R = re.compile(r"Р\Z")
_PAREN = re.compile(r"\(([^\n\r  ]*)\)\Z")
_TRAILING_MINUS = re.compile(r"[^-+][^\n\r  ]*-\Z")
_NUMBER = re.compile(r"[+-]?([0-9]+(\.[0-9]+)?|\.[0-9]+)\Z")


def _js_number(x):
    """Число как его покажет JSON.stringify в JavaScript: 1500, а не 1500.0."""
    return int(x) if x.is_integer() else x


def parse_amount(s):
    """
    «1 500,00», «−1 500,00», «(1 500,00)», «1500,00-», «1,500.00», «1.500,00 р.» → число.
    Пустая строка и не-число → None. 15 и больше цифр — это номер счёта, а не сумма.
    Целые суммы возвращаются как int (1500), остальные — как float (1250.4), как числа в JSON у JS-версии.
    """
    if s is None:
        return None
    t = _DASHES.sub("-", str(s))
    t = _sub_ci(_CURRENCY_NOISE, "", t)
    t = _sub_ci(_TRAILING_R, "", t)
    neg = False
    paren = _PAREN.match(t)
    if paren:
        neg = True
        t = paren.group(1)
    if _TRAILING_MINUS.match(t):
        neg = True
        t = t[:-1]
    if "," in t and "." in t:
        t = t.replace(",", "") if t.index(",") < t.index(".") else t.replace(".", "").replace(",", ".", 1)
    elif t.count(",") > 1:
        t = t.replace(",", "")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    else:
        t = t.replace(",", ".", 1)
    if not _NUMBER.match(t):  # Сбер пишет копейки как «-,01»
        return None
    if len(re.sub(r"^[+-]", "", t).split(".")[0]) >= 15:
        return None
    n = float(t)
    return _js_number(-abs(n) if neg else n)


DMY = re.compile(r"([0-9]{1,2})[./-]([0-9]{1,2})[./-]([0-9]{4}|[0-9]{2})(?![0-9])")
YMD = re.compile(r"([0-9]{4})[./-]([0-9]{1,2})[./-]([0-9]{1,2})(?![0-9])")
TIME = re.compile(r"(?:^|[ T])([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?")


def _days_in_month(y, m):
    # Как Date.UTC в JavaScript: годы 0–99 означают 1900–1999.
    if 0 <= y <= 99:
        y += 1900
    if m == 2:
        return 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28
    return 30 if m in (4, 6, 9, 11) else 31


def _valid_date(y, m, d):
    if m < 1 or m > 12 or d < 1 or d > _days_in_month(y, m):
        return None
    return {"y": y, "m": m, "d": d}


def parse_date(s, order="dmy"):
    """order: "dmy" (как в российских банках) или "mdy" (Excel с американскими настройками).
    Возвращает {"y", "m", "d", "h", "min"} или None."""
    s = re.sub(r"^'", "", _trim(str(s or "")))
    date = None
    m = DMY.match(s)
    if m:
        y = 2000 + int(m.group(3)) if len(m.group(3)) == 2 else int(m.group(3))
        a, b = int(m.group(1)), int(m.group(2))
        date = _valid_date(y, a, b) if order == "mdy" else _valid_date(y, b, a)
    else:
        m = YMD.match(s)
        if m:
            date = _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if not date:
        return None
    t = TIME.search(s[len(m.group(0)):])
    date["h"] = int(t.group(1)) if t else None
    date["min"] = int(t.group(2)) if t else None
    return date


def _detect_date_order(cells):
    """Если «месяц» где-то больше 12, а «день» нигде не больше 12, файл записан как месяц/день/год."""
    parts = [m for m in (DMY.match(_trim(str(c))) for c in cells) if m]
    first_over = any(int(m.group(1)) > 12 for m in parts)
    second_over = any(int(m.group(2)) > 12 for m in parts)
    return "mdy" if second_over and not first_over else "dmy"


def _pad(n):
    return str(n).rjust(2, "0")


def _js_round2(x):
    """Math.round(x * 100) / 100: половина округляется вверх, а не к чётному, как round() в Python."""
    y = x * 100
    f = math.floor(y)
    r = f + 1 if y - f >= 0.5 else f
    return _js_number(float(r) / 100)


def _empty_stats():
    return {"rows": 0, "ops": 0, "badDate": 0, "badAmount": 0, "failed": 0, "zero": 0, "foreign": 0, "dateOrder": "dmy"}


def _cell(row, i):
    return row[i] if i is not None and i < len(row) else None


def parse(text, include_failed=False, include_foreign=False):
    """
    Разбор одной выписки.
    Возвращает {"ops", "stats", "columns", "delimiter"}:
      ops — операции: {"date": "ГГГГ-ММ-ДД", "time": "ЧЧ:ММ" | None, "amount", "currency", "description",
             "counterparty", "category", "mcc", "status", "line"};
             amount в валюте счёта: плюс — поступление, минус — списание;
      stats — сколько строк пропущено и почему, чтобы ничего не терялось молча
             (ключи как в JS-версии: rows, ops, badDate, badAmount, failed, zero, foreign, dateOrder);
      columns — какие колонки файла чем стали (по названиям из шапки).
    include_failed — не отбрасывать отклонённые операции (по умолчанию отбрасываются);
    include_foreign — оставлять операции не в рублях (по умолчанию пропускаются и считаются в stats["foreign"]).
    Если нет колонки с датой или суммой — ValueError с понятным сообщением.
    """
    text = re.sub("^﻿", "", str(text))
    if len([line for line in _NEWLINES.split(text) if _trim(line)]) < 2:
        raise ValueError("В файле нет строк с операциями.")
    found = _find_header(text)
    if not found:
        any_date = any("date" in find_columns(r) for d in DELIMITERS for r in parse_csv(text, d)[:50])
        raise ValueError("Не нашёл колонку с суммой." if any_date else
                         "Не нашёл колонку с датой. Нужна шапка вида «Дата операции;Сумма;Описание».")
    rows, lines, idx, cols, delim = found["rows"], found["lines"], found["idx"], found["cols"], found["delim"]
    header = rows[idx]
    data = rows[idx + 1:]
    order = _detect_date_order([_cell(r, cols["date"]) or "" for r in data])
    stats = _empty_stats()
    stats["dateOrder"] = order
    money_cols = [cols[k] for k in ("credit", "debit", "amount") if k in cols]

    def get(row, key):
        return _trim(_cell(row, cols[key]) or "") if key in cols else ""

    ops = []
    for i, row in enumerate(data):
        cell = _cell(row, cols["date"]) or ""
        date = parse_date(cell, order)
        if not date:
            if DMY.match(_trim(cell)) or YMD.match(_trim(cell)):
                stats["rows"] += 1
                stats["badDate"] += 1
            continue
        stats["rows"] += 1
        status = get(row, "status")
        if not include_failed and FAILED.search(_canon(status)):
            stats["failed"] += 1
            continue
        amount = None
        if "credit" in cols or "debit" in cols:
            credit = parse_amount(_cell(row, cols["credit"])) if "credit" in cols else None
            debit = parse_amount(_cell(row, cols["debit"])) if "debit" in cols else None
            if credit:
                amount = abs(credit)
            elif debit:
                amount = -abs(debit)
        if amount is None and "amount" in cols:
            amount = parse_amount(_cell(row, cols["amount"]))
        if amount is None:
            if any(_trim(_cell(row, c) or "") and parse_amount(_cell(row, c)) is None for c in money_cols):
                stats["badAmount"] += 1
            else:
                stats["zero"] += 1
            continue
        if not amount:
            stats["zero"] += 1
            continue
        cur = get(row, "currency")
        currency = "RUB" if RUB.match(_canon(cur)) else cur.upper()
        if currency != "RUB":
            stats["foreign"] += 1
            if not include_foreign:
                continue
        description = get(row, "description")
        mcc = get(row, "mcc")
        if not mcc:
            # Альфа-Банк пишет MCC прямо в описание: «… RUR MCC4121».
            found_mcc = _MCC.search(_canon(description))
            mcc = found_mcc.group(1) if found_mcc else ""
        ops.append({
            "date": "%s-%s-%s" % (date["y"], _pad(date["m"]), _pad(date["d"])),
            "time": None if date["h"] is None else "%s:%s" % (_pad(date["h"]), _pad(date["min"])),
            "amount": _js_round2(float(amount)),
            "currency": currency,
            "description": description,
            "counterparty": get(row, "counterparty") if cols.get("counterparty") != cols.get("description") else "",
            "category": get(row, "category"),
            "mcc": mcc,
            "status": status,
            "line": lines[idx + 1 + i],
        })
    # Время 00:00 у всех операций — это заглушка банка (так делает ВТБ), а не покупки в полночь.
    if len(ops) > 1 and all(o["time"] is None or o["time"] == "00:00" for o in ops):
        for o in ops:
            o["time"] = None
    stats["ops"] = len(ops)
    columns = {k: header[v] for k, v in cols.items()}
    return {"ops": ops, "stats": stats, "columns": columns, "delimiter": delim}


# Windows-1251 как в TextDecoder браузера: байт 0x98 — это U+0098, а не ошибка.
_CP1251 = {i: c for i, c in enumerate(bytes(range(256)).decode("cp1251", errors="replace"))}
_CP1251[0x98] = "\u0098"


def decode(data):
    """
    Байты файла → текст. Банки отдают CSV в UTF-8 или Windows-1251. Один битый
    байт в UTF-8 не повод перекодировать весь файл: берём ту кодировку,
    в которой выписка разбирается лучше.
    """
    data = bytes(data)
    utf8 = data.decode("utf-8-sig", errors="replace")  # BOM убирается, как в TextDecoder
    if "�" not in utf8:
        return utf8
    cp1251 = data.decode("latin-1").translate(_CP1251)

    def score(t):
        try:
            return len(parse(t)["ops"])
        except Exception:
            return -1

    return cp1251 if score(cp1251) > score(utf8) else utf8


def parse_many(texts, include_failed=False, include_foreign=False):
    """Несколько выписок (разные счета и банки): каждая разбирается со своей шапкой.
    Возвращает {"ops", "stats", "files"}; ops отсортированы по дате и времени, у каждой есть "file"."""
    results = [parse(t, include_failed=include_failed, include_foreign=include_foreign) for t in texts]
    stats = _empty_stats()
    for r in results:
        for k in stats:
            if k != "dateOrder":
                stats[k] += r["stats"][k]
    stats["dateOrder"] = "mixed" if any(r["stats"]["dateOrder"] == "mdy" for r in results) else "dmy"
    ops = [dict(o, file=file) for file, r in enumerate(results) for o in r["ops"]]
    ops.sort(key=lambda a: a["date"] + (a["time"] or ""))
    files = [{"columns": r["columns"], "delimiter": r["delimiter"], "stats": r["stats"]} for r in results]
    return {"ops": ops, "stats": stats, "files": files}
