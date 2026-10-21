const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const V = require("../vypiska.js");

const fixture = (name) => V.decode(fs.readFileSync(path.join(__dirname, "../fixtures", name)));
const brief = (ops) => ops.map((o) => [o.date, o.amount, o.description]);

test("Сбербанк: сумма в валюте счёта, BOM, валютная покупка в рублях", () => {
  const r = V.parse(fixture("sber-card.csv"));
  assert.equal(r.columns.amount, "Сумма в валюте счета");
  assert.equal(r.columns.date, "Дата совершения операции");
  assert.deepEqual(brief(r.ops), [
    ["2026-01-15", -1250.4, "PYATEROCHKA 4512"],
    ["2026-01-16", -3000, "ATM 762019"],
    ["2026-01-20", 85000, "Зачисление зарплаты"],
    ["2026-01-22", -1023.15, "NETFLIX.COM"],
  ]);
  assert.ok(r.ops.every((o) => o.currency === "RUB"));
});

test("Т-Банк: «Сумма платежа» в рублях, отклонённые отброшены, время, категория, MCC; кэшбэк не сумма", () => {
  const r = V.parse(fixture("tbank.csv"));
  assert.equal(r.columns.amount, "Сумма платежа");
  assert.equal(r.stats.failed, 1);
  assert.deepEqual(brief(r.ops), [
    ["2026-03-18", -1250, "Пятёрочка"],
    ["2026-03-20", 95000, "Пополнение. UPWORK GLOBAL INC"],
    ["2026-03-21", -1250.3, "ANTHROPIC"],
  ]);
  assert.deepEqual(r.ops.map((o) => o.time), ["21:15", "09:00", "23:40"]);
  assert.equal(r.ops[0].category, "Супермаркеты");
  assert.equal(r.ops[0].mcc, "5411");
  assert.equal(r.ops[0].status, "OK");
  const withFailed = V.parse(fixture("tbank.csv"), { includeFailed: true });
  assert.equal(withFailed.ops.length, 4);
});

test("Альфа-Банк: Windows-1251, двузначный год, Приход/Расход, MCC из описания, нулевые строки", () => {
  const r = V.parse(fixture("alfa.csv"));
  assert.deepEqual(brief(r.ops).map(([d, a]) => [d, a]), [["2026-01-15", 95000], ["2026-01-19", -450]]);
  assert.equal(r.ops[1].mcc, "4121");
  assert.equal(r.stats.zero, 1);
});

test("ВТБ: шапка после преамбулы, сумма в валюте счёта, описание из «Основания», время-заглушка 00:00", () => {
  const r = V.parse(fixture("vtb.csv"));
  assert.equal(r.columns.amount, "Сумма пересчитанная в валюту счета");
  assert.equal(r.columns.description, "Основание");
  assert.deepEqual(r.ops.map((o) => [o.date, o.amount, o.time]), [["2026-02-06", -4515, null], ["2026-02-08", -1600, null]]);
  assert.match(r.ops[0].description, /BOOKING\.COM/);
});

test("Бизнес-выписка 1С: «Счет Дебет» не сумма, многострочный контрагент, строка-заголовок с запятой", () => {
  const r = V.parse(fixture("business-1c.csv"));
  assert.equal(r.delimiter, ";");
  assert.equal(r.columns.credit, "Сумма по кредиту");
  assert.equal(r.columns.debit, "Сумма по дебету");
  assert.deepEqual(r.ops.map((o) => o.amount), [180000, -4500, -12000]);
  assert.equal(r.ops[0].counterparty, 'ООО "Ромашка" ИНН 7701234567');
});

test("Запятая-разделитель и разные записи сумм; пропуски посчитаны, а не потеряны молча", () => {
  const r = V.parse(fixture("simple-comma.csv"));
  assert.equal(r.delimiter, ",");
  assert.deepEqual(r.ops.map((o) => o.amount), [1500, -2300, -700, 450]);
  assert.deepEqual({ badDate: r.stats.badDate, badAmount: r.stats.badAmount, zero: r.stats.zero }, { badDate: 1, badAmount: 1, zero: 1 });
});

test("parseAmount: российские и чужие форматы", () => {
  const cases = {
    "1 500,00": 1500, "−1 500,00": -1500, "–2,5": -2.5, "(1 500,00)": -1500, "1500,00-": -1500,
    "1,500.00": 1500, "1.500,00": 1500, "1 234 567,89": 1234567.89, "450 р.": 450, "99 RUR": 99, "+10": 10,
    "1 000": 1000, "2 000,5": 2000.5, "": null, "abc": null, "40817810000000000001": null,
  };
  for (const [input, expected] of Object.entries(cases)) assert.equal(V.parseAmount(input), expected, input);
});

test("parseDate: форматы, время, неверные даты, американский порядок", () => {
  assert.deepEqual(V.parseDate("15.01.26"), { y: 2026, m: 1, d: 15, h: null, min: null });
  assert.deepEqual(V.parseDate("2026-02-06 13:05:00"), { y: 2026, m: 2, d: 6, h: 13, min: 5 });
  assert.equal(V.parseDate("31.02.2026"), null);
  assert.equal(V.parseDate("2026-13-01"), null);
  assert.deepEqual(V.parseDate("1/15/2026", "mdy").m, 1);
  const r = V.parse("Дата,Сумма,Описание\n1/15/2026,100,A\n1/20/2026,200,B\n");
  assert.equal(r.stats.dateOrder, "mdy");
  assert.deepEqual(r.ops.map((o) => o.date), ["2026-01-15", "2026-01-20"]);
});

test("валютные операции: по умолчанию пропускаются и считаются, по запросу остаются с валютой", () => {
  const csv = "Дата операции;Сумма;Валюта;Описание\n01.03.2026;-10;USD;APPLE\n02.03.2026;-500;RUB;Метро\n";
  const r = V.parse(csv);
  assert.equal(r.stats.foreign, 1);
  assert.deepEqual(r.ops.map((o) => o.amount), [-500]);
  const all = V.parse(csv, { includeForeign: true });
  assert.deepEqual(all.ops.map((o) => [o.amount, o.currency]), [[-10, "USD"], [-500, "RUB"]]);
});

test("parseMany: у каждого файла своя шапка, операции по порядку и с номером файла", () => {
  const r = V.parseMany([fixture("tbank.csv"), fixture("business-1c.csv")]);
  assert.equal(r.ops.length, 6);
  assert.equal(r.ops[0].date, "2026-01-20");
  assert.equal(r.ops[0].file, 1);
  assert.equal(r.files.length, 2);
  assert.equal(r.stats.failed, 1);
});

test("понятные ошибки", () => {
  assert.throws(() => V.parse("одна строка"), /нет строк/);
  assert.throws(() => V.parse("a;b\n1;2\n"), /Не нашёл колонку с датой/);
  assert.throws(() => V.parse("Дата;Описание\n01.01.2026;x\n"), /колонку с суммой/);
});

test("decode: один битый байт в UTF-8 не превращает файл в кракозябры", () => {
  const good = Buffer.from("Дата;Сумма;Описание\n01.01.2026;-100;Кофе\n02.01.2026;-200;Чай\n", "utf8");
  const broken = Buffer.concat([good, Buffer.from([0xff]), Buffer.from("\n", "utf8")]);
  assert.match(V.decode(broken), /^Дата;Сумма/);
});

test("браузерная сборка: тот же файл без require выставляет window.Vypiska", () => {
  const src = fs.readFileSync(path.join(__dirname, "../vypiska.js"), "utf8");
  const self = {};
  new Function("self", "module", src)(self, undefined);
  assert.equal(typeof self.Vypiska.parse, "function");
});

test("копия для страницы совпадает с библиотекой", () => {
  const root = path.join(__dirname, "..");
  assert.equal(fs.readFileSync(path.join(root, "docs/vypiska.js"), "utf8"), fs.readFileSync(path.join(root, "vypiska.js"), "utf8"));
});

test("остаток и баланс в валюте счёта — не сумма операции", () => {
  const r = V.parse("Дата операции;Остаток в валюте счета;Сумма в валюте счета;Описание\n01.03.2026;10 000,00;-500,00;Кофе\n");
  assert.equal(r.columns.amount, "Сумма в валюте счета");
  assert.deepEqual(r.ops.map((o) => o.amount), [-500]);
  const only = V.parse("Дата;Входящий остаток;Сумма операции;Описание\n01.03.2026;10 000;-200;Чай\n");
  assert.deepEqual(only.ops.map((o) => o.amount), [-200]);
});

test("рубли в разных записях не считаются валютой", () => {
  for (const cur of ["RUB", "RUR", "руб.", "Рубли", "Российский рубль", "Российские рубли", "RUB (Российский рубль)", "643", ""]) {
    const r = V.parse(`Дата;Сумма;Валюта;Описание\n01.03.2026;-100;${cur};X\n`);
    assert.equal(r.ops.length, 1, cur);
    assert.equal(r.ops[0].currency, "RUB", cur);
  }
});

test("копейки без нуля: «-,01» из выписки Сбера", () => {
  assert.equal(V.parseAmount("-,01"), -0.01);
  assert.equal(V.parseAmount(",5"), 0.5);
});

test("«Детали операции» — описание", () => {
  const r = V.parse("Дата;Сумма;Детали операции\n01.03.2026;-100;COFIX\n");
  assert.equal(r.ops[0].description, "COFIX");
});

test("line — номер строки в исходном файле, с преамбулой и многострочными ячейками", () => {
  assert.deepEqual(V.parse(fixture("vtb.csv")).ops.map((o) => o.line), [12, 13]);
  assert.deepEqual(V.parse(fixture("business-1c.csv")).ops.map((o) => o.line), [3, 5, 6]);
  const r = V.parse('Дата;Сумма;Описание\n\n01.03.2026;-1;"А\nБ"\n02.03.2026;-2;В\n');
  assert.deepEqual(r.ops.map((o) => o.line), [3, 5]);
});
