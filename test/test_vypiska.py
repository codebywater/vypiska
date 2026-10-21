"""
Тесты Python-версии: те же случаи, что в test/vypiska.test.js, и сверка с JavaScript-версией.

    python -m unittest discover -s test -p "test_*.py"

Сверка (ParityTest) запускает vypiska.js через `node -e` на фикстурах и на случайных выписках
и требует, чтобы JSON-результаты обеих версий совпадали байт в байт. Без Node она пропускается.
"""

import ast
import base64
import json
import os
import random
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import vypiska as V  # noqa: E402

FIXTURES = os.path.join(ROOT, "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return V.decode(f.read())


def brief(ops):
    return [[o["date"], o["amount"], o["description"]] for o in ops]


class VypiskaTest(unittest.TestCase):
    def test_sber(self):
        """Сбербанк: сумма в валюте счёта, BOM, валютная покупка в рублях"""
        r = V.parse(fixture("sber-card.csv"))
        self.assertEqual(r["columns"]["amount"], "Сумма в валюте счета")
        self.assertEqual(r["columns"]["date"], "Дата совершения операции")
        self.assertEqual(brief(r["ops"]), [
            ["2026-01-15", -1250.4, "PYATEROCHKA 4512"],
            ["2026-01-16", -3000, "ATM 762019"],
            ["2026-01-20", 85000, "Зачисление зарплаты"],
            ["2026-01-22", -1023.15, "NETFLIX.COM"],
        ])
        self.assertTrue(all(o["currency"] == "RUB" for o in r["ops"]))

    def test_tbank(self):
        """Т-Банк: «Сумма платежа» в рублях, отклонённые отброшены, время, категория, MCC; кэшбэк не сумма"""
        r = V.parse(fixture("tbank.csv"))
        self.assertEqual(r["columns"]["amount"], "Сумма платежа")
        self.assertEqual(r["stats"]["failed"], 1)
        self.assertEqual(brief(r["ops"]), [
            ["2026-03-18", -1250, "Пятёрочка"],
            ["2026-03-20", 95000, "Пополнение. UPWORK GLOBAL INC"],
            ["2026-03-21", -1250.3, "ANTHROPIC"],
        ])
        self.assertEqual([o["time"] for o in r["ops"]], ["21:15", "09:00", "23:40"])
        self.assertEqual(r["ops"][0]["category"], "Супермаркеты")
        self.assertEqual(r["ops"][0]["mcc"], "5411")
        self.assertEqual(r["ops"][0]["status"], "OK")
        with_failed = V.parse(fixture("tbank.csv"), include_failed=True)
        self.assertEqual(len(with_failed["ops"]), 4)

    def test_alfa(self):
        """Альфа-Банк: Windows-1251, двузначный год, Приход/Расход, MCC из описания, нулевые строки"""
        r = V.parse(fixture("alfa.csv"))
        self.assertEqual([[d, a] for d, a, _ in brief(r["ops"])], [["2026-01-15", 95000], ["2026-01-19", -450]])
        self.assertEqual(r["ops"][1]["mcc"], "4121")
        self.assertEqual(r["stats"]["zero"], 1)

    def test_vtb(self):
        """ВТБ: шапка после преамбулы, сумма в валюте счёта, описание из «Основания», время-заглушка 00:00"""
        r = V.parse(fixture("vtb.csv"))
        self.assertEqual(r["columns"]["amount"], "Сумма пересчитанная в валюту счета")
        self.assertEqual(r["columns"]["description"], "Основание")
        self.assertEqual([[o["date"], o["amount"], o["time"]] for o in r["ops"]],
                         [["2026-02-06", -4515, None], ["2026-02-08", -1600, None]])
        self.assertRegex(r["ops"][0]["description"], r"BOOKING\.COM")

    def test_business_1c(self):
        """Бизнес-выписка 1С: «Счет Дебет» не сумма, многострочный контрагент, строка-заголовок с запятой"""
        r = V.parse(fixture("business-1c.csv"))
        self.assertEqual(r["delimiter"], ";")
        self.assertEqual(r["columns"]["credit"], "Сумма по кредиту")
        self.assertEqual(r["columns"]["debit"], "Сумма по дебету")
        self.assertEqual([o["amount"] for o in r["ops"]], [180000, -4500, -12000])
        self.assertEqual(r["ops"][0]["counterparty"], 'ООО "Ромашка" ИНН 7701234567')

    def test_comma_and_skipped(self):
        """Запятая-разделитель и разные записи сумм; пропуски посчитаны, а не потеряны молча"""
        r = V.parse(fixture("simple-comma.csv"))
        self.assertEqual(r["delimiter"], ",")
        self.assertEqual([o["amount"] for o in r["ops"]], [1500, -2300, -700, 450])
        s = r["stats"]
        self.assertEqual({"badDate": s["badDate"], "badAmount": s["badAmount"], "zero": s["zero"]},
                         {"badDate": 1, "badAmount": 1, "zero": 1})

    def test_parse_amount(self):
        """parse_amount: российские и чужие форматы"""
        cases = {
            "1 500,00": 1500, "−1 500,00": -1500, "–2,5": -2.5, "(1 500,00)": -1500, "1500,00-": -1500,
            "1,500.00": 1500, "1.500,00": 1500, "1 234 567,89": 1234567.89, "450 р.": 450, "99 RUR": 99, "+10": 10,
            "1 000": 1000, "2 000,5": 2000.5, "": None, "abc": None, "40817810000000000001": None,
        }
        for s, expected in cases.items():
            self.assertEqual(V.parse_amount(s), expected, s)

    def test_parse_date(self):
        """parse_date: форматы, время, неверные даты, американский порядок"""
        self.assertEqual(V.parse_date("15.01.26"), {"y": 2026, "m": 1, "d": 15, "h": None, "min": None})
        self.assertEqual(V.parse_date("2026-02-06 13:05:00"), {"y": 2026, "m": 2, "d": 6, "h": 13, "min": 5})
        self.assertIsNone(V.parse_date("31.02.2026"))
        self.assertIsNone(V.parse_date("2026-13-01"))
        self.assertEqual(V.parse_date("1/15/2026", "mdy")["m"], 1)
        r = V.parse("Дата,Сумма,Описание\n1/15/2026,100,A\n1/20/2026,200,B\n")
        self.assertEqual(r["stats"]["dateOrder"], "mdy")
        self.assertEqual([o["date"] for o in r["ops"]], ["2026-01-15", "2026-01-20"])

    def test_foreign(self):
        """валютные операции: по умолчанию пропускаются и считаются, по запросу остаются с валютой"""
        csv = "Дата операции;Сумма;Валюта;Описание\n01.03.2026;-10;USD;APPLE\n02.03.2026;-500;RUB;Метро\n"
        r = V.parse(csv)
        self.assertEqual(r["stats"]["foreign"], 1)
        self.assertEqual([o["amount"] for o in r["ops"]], [-500])
        all_ops = V.parse(csv, include_foreign=True)
        self.assertEqual([[o["amount"], o["currency"]] for o in all_ops["ops"]], [[-10, "USD"], [-500, "RUB"]])

    def test_parse_many(self):
        """parse_many: у каждого файла своя шапка, операции по порядку и с номером файла"""
        r = V.parse_many([fixture("tbank.csv"), fixture("business-1c.csv")])
        self.assertEqual(len(r["ops"]), 6)
        self.assertEqual(r["ops"][0]["date"], "2026-01-20")
        self.assertEqual(r["ops"][0]["file"], 1)
        self.assertEqual(len(r["files"]), 2)
        self.assertEqual(r["stats"]["failed"], 1)

    def test_errors(self):
        """понятные ошибки"""
        self.assertRaisesRegex(ValueError, "нет строк", V.parse, "одна строка")
        self.assertRaisesRegex(ValueError, "Не нашёл колонку с датой", V.parse, "a;b\n1;2\n")
        self.assertRaisesRegex(ValueError, "колонку с суммой", V.parse, "Дата;Описание\n01.01.2026;x\n")

    def test_decode_broken_byte(self):
        """decode: один битый байт в UTF-8 не превращает файл в кракозябры"""
        good = "Дата;Сумма;Описание\n01.01.2026;-100;Кофе\n02.01.2026;-200;Чай\n".encode("utf-8")
        broken = good + b"\xff" + b"\n"
        self.assertRegex(V.decode(broken), r"^Дата;Сумма")

    def test_single_file_stdlib_only(self):
        """один файл без зависимостей (аналог проверки браузерной сборки в JS)"""
        with open(os.path.join(ROOT, "vypiska.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(imported, {"math", "re"})

    def test_docs_copy(self):
        """копия для страницы совпадает с библиотекой"""
        with open(os.path.join(ROOT, "docs", "vypiska.js"), "rb") as a, open(os.path.join(ROOT, "vypiska.js"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_balance_not_amount(self):
        """остаток и баланс в валюте счёта — не сумма операции"""
        r = V.parse("Дата операции;Остаток в валюте счета;Сумма в валюте счета;Описание\n01.03.2026;10 000,00;-500,00;Кофе\n")
        self.assertEqual(r["columns"]["amount"], "Сумма в валюте счета")
        self.assertEqual([o["amount"] for o in r["ops"]], [-500])
        only = V.parse("Дата;Входящий остаток;Сумма операции;Описание\n01.03.2026;10 000;-200;Чай\n")
        self.assertEqual([o["amount"] for o in only["ops"]], [-200])

    def test_rub_spellings(self):
        """рубли в разных записях не считаются валютой"""
        for cur in ["RUB", "RUR", "руб.", "Рубли", "Российский рубль", "Российские рубли", "RUB (Российский рубль)", "643", ""]:
            r = V.parse("Дата;Сумма;Валюта;Описание\n01.03.2026;-100;%s;X\n" % cur)
            self.assertEqual(len(r["ops"]), 1, cur)
            self.assertEqual(r["ops"][0]["currency"], "RUB", cur)

    def test_kopecks_without_zero(self):
        """копейки без нуля: «-,01» из выписки Сбера"""
        self.assertEqual(V.parse_amount("-,01"), -0.01)
        self.assertEqual(V.parse_amount(",5"), 0.5)

    def test_details_is_description(self):
        """«Детали операции» — описание"""
        r = V.parse("Дата;Сумма;Детали операции\n01.03.2026;-100;COFIX\n")
        self.assertEqual(r["ops"][0]["description"], "COFIX")

    def test_js_quirks(self):
        """места, где Python по умолчанию повёл бы себя иначе, чем JavaScript"""
        self.assertEqual(V.parse_amount("0,125"), 0.125)
        r = V.parse("Дата;Сумма\n01.03.2026;0,125\n02.03.2026;-0,125\n03.03.2026;2,675\n")
        self.assertEqual([o["amount"] for o in r["ops"]], [0.13, -0.12, 2.68])  # Math.round, а не банковское округление
        self.assertIs(type(r["ops"][0]["amount"]), float)
        self.assertIs(type(V.parse_amount("1 500,00")), int)  # в JSON — 1500, как у JS
        self.assertEqual(V.parse_amount("1 500"), 1500)  # \s в JS включает тонкий пробел
        r = V.parse("Дата;Сумма;Описание\n01.03.2026;-100;payment mcc 5411\n02.03.2026;-1;ABCMCC1234\n")
        self.assertEqual([o["mcc"] for o in r["ops"]], ["5411", ""])
        self.assertEqual(V.decode("﻿Дата;Сумма\n".encode("utf-8")), "Дата;Сумма\n")  # BOM убирается, как TextDecoder
        self.assertEqual(V.decode(b"\x98\xff"), "��")  # cp1251 разбирается не лучше → остаётся UTF-8
        self.assertEqual(V.decode("Дата;Сумма\n01.01.2026;5\n".encode("cp1251") + b"\x98"),
                         "Дата;Сумма\n01.01.2026;5\n\x98")


# --- Сверка с JavaScript-версией -------------------------------------------------------------

NODE_SCRIPT = r"""
const V = require(process.argv[1]);
let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => { input += c; });
process.stdin.on("end", () => {
  const run = (f) => { try { return { ok: f() }; } catch (e) { return { error: e.message }; } };
  const out = JSON.parse(input).map((c) => {
    switch (c.fn) {
      case "decode": return run(() => V.decode(Buffer.from(c.b64, "base64")));
      case "parse": return run(() => V.parse(c.text, c.opts));
      case "parseMany": return run(() => V.parseMany(c.texts, c.opts));
      case "parseAmount": return run(() => V.parseAmount(c.s));
      case "parseDate": return run(() => V.parseDate(c.s, c.order));
      default: throw new Error("unknown fn " + c.fn);
    }
  });
  process.stdout.write(JSON.stringify(out));
});
"""


def run_python(case):
    def opts():
        o = case.get("opts") or {}
        return {"include_failed": bool(o.get("includeFailed")), "include_foreign": bool(o.get("includeForeign"))}
    try:
        fn = case["fn"]
        if fn == "decode":
            return {"ok": V.decode(base64.b64decode(case["b64"]))}
        if fn == "parse":
            return {"ok": V.parse(case["text"], **opts())}
        if fn == "parseMany":
            return {"ok": V.parse_many(case["texts"], **opts())}
        if fn == "parseAmount":
            return {"ok": V.parse_amount(case["s"])}
        if fn == "parseDate":
            return {"ok": V.parse_date(case["s"], case["order"])}
        raise AssertionError("unknown fn " + fn)
    except ValueError as e:
        return {"error": str(e)}


def run_node(cases):
    proc = subprocess.run(["node", "-e", NODE_SCRIPT, os.path.join(ROOT, "vypiska.js")],
                          input=json.dumps(cases), capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(proc.stdout)


# Генератор синтетических выписок: макеты разных банков, разделители, записи сумм и дат,
# кавычки и переносы внутри ячеек, преамбулы, отклонённые и валютные операции, мусорные строки.

DESCRIPTIONS = ["PYATEROCHKA 4512", "Пятёрочка", "ВкусВилл", "Оплата по счету 7", "ATM 762019", "YANDEX.TAXI MCC4121",
                "Перевод от Иванова И.И.", "NETFLIX.COM", "Зачисление зарплаты", "Комиссия банка",
                "644300++++++1234\\RUS\\MOSCOW\\YANDEX.TAXI   450.00  RUR MCC4121", "Метро", "COFIX",
                "Оплата товаров и услуг. BOOKING.COM AMSTERDAM NLD", "Возврат, частичный", 'Магазин "Ромашка"',
                "кафе; бар", "Покупка mcc 5812", "ABCMCC1234", "Кэшбэк", "Пополнение. UPWORK GLOBAL INC", "",
                # пробелы, которые в JavaScript и Python определены по-разному
                "Чай\u0085", "\ufeffКофе\ufeff", "A\x1cB\x1f", "Такси\u2028Москва", "\u3000Метро\u2009",
                "Строка 1\n  Строка 2", "Оплата\r\nпо счёту", "Строка\x1f\n\x85два", "Магазин ſtore", "FAİLED?",
                "ſMCC1234", "\u212aMCC5411", "ıMCC4121", "éMCC5812", "_MCC5999", "MCC\u00a05411"]
COUNTERPARTIES = ['ООО "Ромашка"\nИНН 7701234567', "ИП Петров", "УФК по г. Москве", 'ООО "Хостинг"', "", "Иванов И.И."]
CATEGORIES = ["Супермаркеты", "Транспорт", "Пополнения", "Сервисы", "Рестораны", ""]
STATUSES = ["OK", "OK", "OK", "Исполнено", "FAILED", "Отклонена", "Отменена", "declined", "Ошибка", "SUCCESS", "",
            "failed", "ОТКЛОНЕНА", "FAİLED", "Canceled", "\ufeffOK"]
CURRENCIES = ["RUB", "RUB", "RUR", "USD", "EUR", "руб.", "Российский рубль", "643", "810", "", "KZT", "доллар США", "₽",
              "rub", "Rur", "usd", "straße", "ıRUB", "\u00a0RUB"]


def gen_amount(rng, value=None):
    if value is None:
        value = rng.choice([0, round(rng.uniform(-200000, 200000), rng.choice([0, 2, 2, 1])),
                            round(rng.uniform(-100, 100), 2), rng.randint(-5, 5) * 1000, 0.01, -0.01])
    neg = value < 0
    v = abs(value)
    whole, frac = int(v), round((v - int(v)) * 100)
    style = rng.randrange(15)
    sep = rng.choice([" ", " ", " ", "", "'"])
    digits = "{:,}".format(whole).replace(",", sep)
    if style == 0:
        s = "%s,%02d" % (digits, frac)
    elif style == 1:
        s = "{:,}.{:02d}".format(whole, frac)
    elif style == 2:
        s = "{:,}".format(whole).replace(",", ".") + ",%02d" % frac
    elif style == 3:
        s = "%d.%02d" % (whole, frac)
    elif style == 4:
        s = "%s,%02d р." % (digits, frac)
    elif style == 5:
        s = "%d,%02d RUR" % (whole, frac)
    elif style == 6:
        s = "%s ₽" % digits
    elif style == 7:
        s = (",%02d" % frac) if whole == 0 else str(whole)
    elif style == 8:
        s = "%d,%d" % (whole, frac)
    elif style == 9:
        s = "%s руб." % digits
    elif style == 10:  # три знака после запятой: проверяет округление половинок, как Math.round
        s = rng.choice(["0,125", "2,675", "1,005", "0,005", "0,015", "10,545", "1,115", "%d,%03d" % (whole, rng.randrange(1000))])
    else:
        s = "%s,%02d" % (digits, frac) if frac else digits
    if neg:
        s = rng.choice(["-" + s, "−" + s, "–" + s, "—" + s, "(" + s + ")", s + "-", "- " + s])
    elif rng.random() < 0.1:
        s = "+" + s
    r = rng.random()
    if r < 0.03:
        return ""
    if r < 0.05:
        return rng.choice(["abc", "40817810000000000001", "1 2 3,4,5", "--5", "(5", "5)", "1.2.3,4", ".", "р", "0,00"])
    return s


def gen_date(rng, style, mdy=False):
    if rng.random() < 0.5:  # тесная выборка, чтобы в parse_many совпадали даты и решало время
        y, m, d = 2026, rng.randint(1, 2), rng.randint(1, 5)
    else:
        y, m, d = rng.choice([2024, 2025, 2026]), rng.randint(1, 12), rng.randint(1, 28)
    if rng.random() < 0.04:
        d = 31 if m in (2, 4, 6, 9, 11) else d  # несуществующая дата
        m = 13 if rng.random() < 0.3 else m
    t = ""
    r = rng.random()
    if r < 0.3:
        t = " %02d:%02d:%02d" % (rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))
    elif r < 0.4:
        t = " %02d:%02d" % (rng.randint(0, 23), rng.randint(0, 59))
    elif r < 0.45:
        t = " 00:00:00"
    if style == "dmy":
        s = "%02d.%02d.%d" % (d, m, y)
    elif style == "dmy2":
        s = "%02d.%02d.%02d" % (d, m, y % 100)
    elif style == "ymd":
        s = "%d-%02d-%02d" % (y, m, d)
        if t and rng.random() < 0.3:
            t = "T" + t[1:]
    else:  # m/d/yyyy или d/m/yyyy без ведущих нулей
        s = "%d/%d/%d" % ((m, d, y) if mdy else (d, m, y))
    if rng.random() < 0.03:
        return rng.choice(["", "Итого", "не дата", "2026", "'" + s, "  " + s + "  "])
    return s + t


def quote(rng, cell, delim, force=False):
    needs = delim in cell or "\n" in cell or '"' in cell
    if force or needs or rng.random() < 0.1:
        return '"' + cell.replace('"', '""') + '"'
    return cell


def render(rng, rows, delim, force_quotes=False, trailing=False, eol=None):
    eol = eol or rng.choice(["\n", "\n", "\r\n"])
    lines = []
    for row in rows:
        line = delim.join(quote(rng, c, delim, force_quotes) for c in row)
        lines.append(line + (delim if trailing else ""))
    return eol.join(lines) + (eol if rng.random() < 0.8 else "")


def gen_ops(rng):
    return range(rng.randint(1, 12))


def layout_sber(rng):
    header = ["Тип карты", "Номер карты", "Дата совершения операции", "Дата обработки операции", "Код авторизации",
              "Тип операции", "Город совершения операции", "Страна совершения операции", "Описание", "Валюта операции",
              "Сумма в валюте операции", "Сумма в валюте счета"]
    rows = [header]
    for _ in gen_ops(rng):
        foreign = rng.random() < 0.3
        rows.append(["Основная", "*1234", gen_date(rng, "dmy"), gen_date(rng, "dmy"), str(rng.randint(100000, 999999)),
                     "5411", "MOSCOW", "RUS", rng.choice(DESCRIPTIONS), "EUR" if foreign else "",
                     gen_amount(rng) if foreign else "", gen_amount(rng)])
    text = render(rng, rows, ";", trailing=True)
    return ("﻿" if rng.random() < 0.5 else "") + text, "utf-8"


def layout_tbank(rng):
    header = ["Дата операции", "Дата платежа", "Номер карты", "Статус", "Сумма операции", "Валюта операции",
              "Сумма платежа", "Валюта платежа", "Кэшбэк", "Категория", "MCC", "Описание", "Бонусы (включая кэшбэк)",
              "Округление на инвесткопилку", "Сумма операции с округлением"]
    rows = [header]
    for _ in gen_ops(rng):
        amt = gen_amount(rng)
        rows.append([gen_date(rng, "dmy"), gen_date(rng, "dmy"), rng.choice(["", "*7788"]), rng.choice(STATUSES),
                     gen_amount(rng), rng.choice(["RUB", "USD"]), amt, rng.choice(["RUB", "RUB", "RUB", "USD"]),
                     rng.choice(["", "12"]), rng.choice(CATEGORIES), rng.choice(["", "5411", "5817"]),
                     rng.choice(DESCRIPTIONS), "0,00", "0,00", amt])
    return render(rng, rows, ";", force_quotes=True), "utf-8"


def layout_alfa(rng):
    header = ["Тип счёта", "Номер счета", "Валюта", "Дата операции", "Референс проводки", "Описание операции",
              "Приход", "Расход"]
    rows = [header]
    for _ in gen_ops(rng):
        income = rng.random() < 0.4
        a = gen_amount(rng)
        rows.append(["Текущий счёт", "40817810000000000001", rng.choice(["RUR", "RUR", "USD"]), gen_date(rng, "dmy2"),
                     "C0115", rng.choice(DESCRIPTIONS), a if income else "0", "0" if income else a])
    return render(rng, rows, ";", trailing=True), "cp1251"


def layout_vtb(rng):
    pre = [["Начало периода", "2026-02-01"], ["Конец периода", "2026-02-28"], [""], ["Выписка по счету/карте", "Номер"],
           ["Дебетовая карта", "'462235******0069"], [""], ["Валюта", "Баланс на конец периода", "Поступления", "Списания"],
           ["RUR", "82 604,01", "0,00", "-6 115,00"], [""], [""]]
    header = ["Номер карты/счета/договора", "Дата операции", "Дата обработки", "Сумма операции", "Валюта операции",
              "Сумма пересчитанная в валюту счета", "Валюта счета", "Основание", "Статус"]
    rows = pre[:rng.randint(0, len(pre))] + [header]
    midnight = rng.random() < 0.7
    for _ in gen_ops(rng):
        d = gen_date(rng, "ymd")
        if midnight and d[:1].isdigit():
            d = d.split(" ")[0].split("T")[0] + " 00:00:00"
        rows.append(["'462235******0069", d, d, gen_amount(rng), rng.choice(["RUR", "EUR"]), gen_amount(rng),
                     rng.choice(["RUR", "RUR", "RUB", "USD"]), rng.choice(DESCRIPTIONS), rng.choice(["Исполнено", "Отменена"])])
    return render(rng, rows, ";"), "cp1251"


def layout_1c(rng):
    rows = [["Выписка по счету 40802810000000000001, ИП Иванов И.И."]] if rng.random() < 0.8 else []
    rows.append(["Дата проводки", "Счет Дебет", "Счет Кредит", "Сумма по дебету", "Сумма по кредиту", "№ документа",
                 "Контрагент", "Назначение платежа"])
    for _ in gen_ops(rng):
        credit = rng.random() < 0.5
        a = gen_amount(rng)
        rows.append([gen_date(rng, "dmy"), "40702810400000012345", "40802810938000000001", "" if credit else a,
                     a if credit else "", str(rng.randint(1, 99)), rng.choice(COUNTERPARTIES), rng.choice(DESCRIPTIONS)])
    if rng.random() < 0.5:
        rows.append(["", "", "Итого", "", "", "", "", ""])
    return render(rng, rows, ";"), rng.choice(["utf-8", "cp1251"])


def layout_simple(rng):
    delim = rng.choice([",", "\t", ";"])
    mdy = rng.random() < 0.3
    style = "slash" if mdy else rng.choice(["dmy", "ymd", "slash", "dmy2"])
    header = rng.choice([["date", "amount", "description"], ["Дата", "Сумма", "Описание"],
                         ["Date", "Amount", "Merchant", "Currency"], ["Дата", "Сумма", "Детали операции", "Валюта"]])
    rows = [header]
    for _ in gen_ops(rng):
        row = [gen_date(rng, style, mdy), gen_amount(rng), rng.choice(DESCRIPTIONS)]
        if len(header) == 4:
            row.append(rng.choice(CURRENCIES))
        rows.append(row)
    return render(rng, rows, delim), rng.choice(["utf-8", "utf-8", "cp1251"])


def layout_generic(rng):
    """Случайная шапка из ключевых слов и колонок-ловушек (остаток, ИНН, кэшбэк, номер счёта)."""
    cols = [("date", rng.choice(V.HEADERS["date"]))]
    money = rng.choice(["amount", "creditdebit", "both"])
    if money in ("amount", "both"):
        cols.append(("amount", rng.choice(V.HEADERS["amount"])))
    if money in ("creditdebit", "both"):
        cols.append(("credit", rng.choice(V.HEADERS["credit"])))
        cols.append(("debit", rng.choice(V.HEADERS["debit"])))
    for key in ("currency", "status", "category", "mcc", "description", "counterparty"):
        if rng.random() < 0.6:
            cols.append((key, rng.choice(V.HEADERS[key])))
    for trap in ["Остаток в валюте счета", "Баланс", "ИНН", "Кэшбэк", "Счет Кредит", "Бонусы", "Сумма кэшбэка",
                 "Номер счета", "Сальдо", "Валюта остатка", "Банк получателя", "Сумма остатка", "Сумма лимита",
                 "Оборот по кредиту", "Сумма бонусов", "Наименование получателя"]:
        if rng.random() < 0.2:
            cols.append(("trap", trap))
    rng.shuffle(cols)
    header = []
    for _, name in cols:
        name = rng.choice([name, name.capitalize(), name.upper(), name.title(), "  " + name + " ", "\u00a0" + name,
                           name.replace(" ", "\u00a0"), "'" + name + "'", '"' + name])
        header.append(name)
    rows = [header]
    style = rng.choice(["dmy", "ymd", "dmy2", "slash"])
    for _ in gen_ops(rng):
        row = []
        for key, _ in cols:
            if key == "date":
                row.append(gen_date(rng, style))
            elif key in ("amount", "credit", "debit"):
                row.append(gen_amount(rng) if rng.random() < 0.7 else "")
            elif key == "currency":
                row.append(rng.choice(CURRENCIES))
            elif key == "status":
                row.append(rng.choice(STATUSES))
            elif key == "category":
                row.append(rng.choice(CATEGORIES))
            elif key == "mcc":
                row.append(rng.choice(["", "5411", "4121"]))
            elif key == "description":
                row.append(rng.choice(DESCRIPTIONS))
            elif key == "counterparty":
                row.append(rng.choice(COUNTERPARTIES))
            else:
                row.append(rng.choice(["", "40817810000000000001", "10 000,00", "7701234567", "12"]))
        if rng.random() < 0.05:
            row = row[:rng.randint(0, len(row))]  # короткая строка
        rows.append(row)
    if rng.random() < 0.3:
        rows = [["Выписка за период, ООО Ромашка"], ["Счёт", "40702810400000012345"]] + rows
    return render(rng, rows, rng.choice([";", ",", "\t"])), rng.choice(["utf-8", "cp1251"])


LAYOUTS = [layout_sber, layout_tbank, layout_alfa, layout_vtb, layout_1c, layout_simple, layout_generic]


def mutate(rng, text):
    """Порча, которую встречаешь в настоящих файлах."""
    r = rng.random()
    lines = text.split("\n")
    if r < 0.12 and len(lines) > 2:  # незакрытая кавычка: Python и JS должны одинаково перейти к разбору по строкам
        i = rng.randrange(1, len(lines)) if r < 0.06 else max(i for i, l in enumerate(lines) if l.strip())
        lines[i] = '"' + lines[i]
        return "\n".join(lines)
    if r < 0.15:
        return "\n\n" + text + "\n\n"
    if r < 0.18:
        return "\r".join(lines)  # старые маковские переводы строк
    if r < 0.22 and len(lines) > 1:  # мусор между операциями
        lines.insert(rng.randrange(1, len(lines)), rng.choice(["Итого;;;", "-----", "\"\"", ";;;;"]))
        return "\n".join(lines)
    return text


def encode(rng, text, enc):
    if enc == "cp1251":
        try:
            data = text.encode("cp1251")
        except UnicodeEncodeError:
            data = text.encode("utf-8")
    else:
        data = text.encode("utf-8")
    r = rng.random()
    if r < 0.1:  # битый байт в UTF-8 или лишний байт в cp1251
        pos = rng.randrange(len(data) + 1)
        data = data[:pos] + bytes([rng.choice([0xff, 0x98, 0xc0, 0xe0, 0x80])]) + data[pos:]
    return data


def gen_statements(seed=20261020, count=200):
    rng = random.Random(seed)
    out = []
    for i in range(count):
        layout = LAYOUTS[i % len(LAYOUTS)]
        text, enc = layout(rng)
        text = mutate(rng, text)
        out.append(encode(rng, text, enc))
    return out


AMOUNT_STRINGS = ["1 500,00", "−1 500,00", "–2,5", "—3", "(1 500,00)", "1500,00-", "1,500.00", "1.500,00", "1 234 567,89",
                  "450 р.", "450 Р.", "450р", "99 RUR", "99 rub", "99 Руб.", "450руб", "1 000 руб", "5РУБ", "7 ру", "ſ5", "5 Rub", "+10", "1 000", "2 000,5", "", "abc",
                  "40817810000000000001", "-,01", ",5", "1 000", "1 000", "1 000", "1'000", "(5)-",
                  "+-5", "-+5", "5-", "-5-", "0", "-0", "0,005", "1,005", "999999999999999", "99999999999999", "1.2.3",
                  "1,2,3", "1,2.3", "1.2,3", ".", ",", "-", "₽100", "100₽", "1e5", "0x10", "١٢٣", "１２", "  7  ",
                  "﻿5", "5\n", "ſ5", "5 р.р.", "руб", "(", "()", "(-5)", "-(5)", "р.5", "10 RUB.", "10 rur 20"]
DATE_STRINGS = ["15.01.26", "15.01.2026", "2026-02-06 13:05:00", "31.02.2026", "29.02.2024", "29.02.2025",
                "2026-13-01", "1/15/2026", "15/1/2026", "1-2-2026", "01.01.0000", "29.02.0004", "29.02.0000",
                "15.01.2026T10:20", "15.01.2026 9:05", "15.01.202", "15.01.20266", "2026-1-5", "20260105",
                "'15.01.2026", "  15.01.2026  ", "15.01.2026 99:99", "15.01.2026, 10:00", "", "abc", "١٥.٠١.٢٠٢٦",
                "2026/01/15 23:59:59", "15.01.26 00:00", "0.0.26", "5.5.05"]


# Ошибки и пограничные случаи, которые случайный генератор находит редко.
EDGE_TEXTS = [
    "одна строка", "a;b\n1;2\n", "Дата;Описание\n01.01.2026;x\n", "", "\n\n", "Дата;Сумма\n\n", "\ufeff\n\ufeff\n",
    "Дата;Сумма,Дата;Сумма\n01.01.2026;5,01.01.2026;6\n",  # шапка в той же строке для «;» и «,»: побеждает более широкая
    "Дата,Сумма;Описание,X\n01.01.2026,5;a,b\n",
    "Дата;Сумма\r01.01.2026;5\r02.01.2026;6\r",
    "Дата;Сумма;Описание\n01.01.2026;5;\"незакрытая\n02.01.2026;6;ok\n",
    "Дата;Сумма;Описание\n01.01.2026;5;a\"b\"c\n02.01.2026;6; \"x\"\"y\" \n",
    "Дата;Сумма;Описание\n01.01.2026;5;\"много\nстрок\n\nв ячейке\"\n",
    "Дата;Сумма\n31.12.99;1\n29.02.0000;2\n29.02.0004;3\n01.01.0050;4\n",
    "Дата;Сумма\n01.01.2026 00:00;1\n02.01.2026;2\n",
    "Дата;Сумма\n01.01.2026 00:00;1\n",
    "Дата;Сумма\n01.01.2026 00:00;1\n02.01.2026 00:01;2\n",
    "Дата;Сумма;Валюта\n01.01.2026;0,001;RUB\n02.01.2026;-0,004;RUB\n03.01.2026;0;RUB\n",
    "Дата;Приход;Расход;Сумма\n01.01.2026;0;0;7\n02.01.2026;;abc;\n03.01.2026;x;;\n04.01.2026;;;\n",
    "MCC;Дата;Сумма;Описание\n5411;01.01.2026;1;MCC 1234\n;02.01.2026;2;xmcc1234\n;03.01.2026;3;MCC12345\n",
    "Дата операции;Сумма в валюте счёта;Валюта операции;Описание\n01.01.2026;5;USD;a\n",
    "Дата операции;Сумма операции;Валюта операции;Валюта остатка\n01.01.2026;5;USD;RUB\n",
    "Дата;Сумма;Валюта остатка\n01.01.2026;5;USD\n",
    "Дата;Сумма бонусов;Сумма кэшбэка;Сумма\n01.01.2026;1;2;3\n",
    "Дата;Сумма остатка;Сумма\n01.01.2026;1000;-5\n",
    "Дата;Сумма операции;Сумма\u00a0в\u00a0валюте  счета\n01.01.2026;-10;-900\n",
    "Дата;Оборот по кредиту;Оборот по дебету\n01.01.2026;;5\n",
    "Плательщик;Дата;Сумма;Назначение;Получатель\n'Иван';01.01.2026;5;за чай;Пётр\n",
    "Дата;Сумма;Контрагент\n01.01.2026;5;ООО Ромашка\n",
    "Дата;Описание операции;Сумма\n01/13/2026;a;1\n12/01/2026;b;2\n",
    "Дата;Сумма\n13/01/2026;1\n01/13/2026;2\n",
    "Дата;Сумма\n" + "\n".join("%02d.01.2026;%d" % (d, d) for d in range(1, 29)),
    "\n".join(["Преамбула %d;x" % i for i in range(49)] + ["Дата;Сумма", "01.01.2026;5"]),
    "\n".join(["Преамбула %d;x" % i for i in range(50)] + ["Дата;Сумма", "01.01.2026;5"]),
]


EDGE_BYTES = [
    b"", b"\x98\xff", "\ufeffДата;Сумма\n01.01.2026;5\n".encode("utf-8"),
    "Дата;Сумма\n01.01.2026;5\n".encode("cp1251") + b"\x98",
    "Дата;Сумма;Описание\n01.01.2026;-100;Кофе\n02.01.2026;-200;Чай\n".encode("utf-8") + b"\xff\n",
    "Дата;Сумма;Описание\n01.01.2026;-100;Кофе\n".encode("cp1251") + "02.01.2026;-200;Чай\n".encode("utf-8"),
    "Дата;Сумма;Описание\n01.01.2026;-100;\xa0\u2116\n".encode("cp1251") + bytes(range(128, 256)),
]


def gen_cases():
    cases = []
    for i, data in enumerate(EDGE_BYTES):
        cases.append({"fn": "decode", "b64": base64.b64encode(data).decode(), "name": "decode edge #%d" % i})
    for name in sorted(os.listdir(FIXTURES)):
        with open(os.path.join(FIXTURES, name), "rb") as f:
            data = f.read()
        cases.append({"fn": "decode", "b64": base64.b64encode(data).decode(), "name": name})
    texts = [fixture(n) for n in sorted(os.listdir(FIXTURES))]
    for i, data in enumerate(gen_statements()):
        cases.append({"fn": "decode", "b64": base64.b64encode(data).decode(), "name": "random #%d" % i})
        texts.append(V.decode(data))
    for i, text in enumerate(texts):
        cases.append({"fn": "parse", "text": text, "opts": {}, "name": "parse #%d" % i})
        cases.append({"fn": "parse", "text": text, "opts": {"includeFailed": True, "includeForeign": True},
                      "name": "parse all #%d" % i})
    rng = random.Random(7)
    for i in range(60):
        group = rng.sample(texts, rng.randint(1, 4))
        cases.append({"fn": "parseMany", "texts": group, "opts": {"includeForeign": rng.random() < 0.5},
                      "name": "parseMany #%d" % i})
    amounts = AMOUNT_STRINGS + [gen_amount(rng) for _ in range(300)]
    for s in amounts:
        cases.append({"fn": "parseAmount", "s": s, "name": "parseAmount %r" % s})
    dates = DATE_STRINGS + [gen_date(rng, rng.choice(["dmy", "dmy2", "ymd", "slash"]), rng.random() < 0.5) for _ in range(200)]
    for s in dates:
        for order in ("dmy", "mdy"):
            cases.append({"fn": "parseDate", "s": s, "order": order, "name": "parseDate %r %s" % (s, order)})
    for text in EDGE_TEXTS:
        cases.append({"fn": "parse", "text": text, "opts": {}, "name": "edge %r" % text})
    return cases


def canonical(value):
    return json.dumps(value, sort_keys=True)


@unittest.skipUnless(shutil.which("node"), "нужен Node.js для сверки с vypiska.js")
class ParityTest(unittest.TestCase):
    def test_python_equals_javascript(self):
        cases = gen_cases()
        js = run_node([{k: v for k, v in c.items() if k != "name"} for c in cases])
        self.assertEqual(len(js), len(cases))
        parsed = 0
        for case, js_out in zip(cases, js):
            py_out = run_python(case)
            if case["fn"] == "parse" and "ok" in py_out:
                parsed += 1
            with self.subTest(case["name"]):
                self.assertEqual(canonical(py_out), canonical(js_out))
        # Генератор не должен вырождаться в одни ошибки разбора.
        self.assertGreater(parsed, 300)
        print("\nparity: %d cases (%d statements parsed) — Python == JavaScript" % (len(cases), parsed), file=sys.stderr)


if __name__ == "__main__":
    unittest.main()


class LineNumbersTest(unittest.TestCase):
    def test_line_is_source_file_line(self):
        def lines(name):
            return [o["line"] for o in V.parse(fixture(name))["ops"]]
        self.assertEqual(lines("vtb.csv"), [12, 13])
        self.assertEqual(lines("business-1c.csv"), [3, 5, 6])
        r = V.parse('Дата;Сумма;Описание\n\n01.03.2026;-1;"А\nБ"\n02.03.2026;-2;В\n')
        self.assertEqual([o["line"] for o in r["ops"]], [3, 5])
