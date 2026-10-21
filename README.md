# 📄 vypiska

[![tests](https://github.com/codebywater/vypiska/actions/workflows/tests.yml/badge.svg)](https://github.com/codebywater/vypiska/actions/workflows/tests.yml)
![license](https://img.shields.io/badge/license-MIT-blue)
![dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)

**Разбор CSV-выписок российских банков в единый список операций.** Сбербанк, Т-Банк, Альфа-Банк, ВТБ, бизнес-выписки в стиле 1С и любой CSV с датой и суммой. Один файл на JavaScript, без зависимостей, работает в браузере и в Node.

### 👉 [Проверить на своей выписке](https://codebywater.github.io/vypiska/) — файл не покидает браузер

> **English summary.** A zero-dependency JavaScript parser that turns CSV bank statements from Russian banks (Sber, T-Bank, Alfa-Bank, VTB, 1C-style business exports) into one normalized list of transactions. Detects the header, delimiter, encoding (UTF-8 / Windows-1251), amount columns in account currency, failed operations, and counts every skipped row instead of dropping it silently.

## Зачем

У каждого банка свой CSV: разные названия колонок, суммы в валюте операции и в валюте счёта, «Приход/Расход» или одна сумма со знаком, двузначный год, шапка после десятка строк преамбулы, Windows-1251, ячейки с переносами строк. Каждый, кто пишет бюджетное приложение или калькулятор налогов, решает это заново — и обычно молча теряет часть операций.

`vypiska` делает это один раз и честно: всё, что не удалось разобрать, попадает в счётчики `stats`.

## Пример

```js
const Vypiska = require("./vypiska.js");          // в браузере: <script src="vypiska.js"> → window.Vypiska
const fs = require("fs");

const text = Vypiska.decode(fs.readFileSync("tbank.csv"));   // UTF-8 или Windows-1251
const { ops, stats, columns } = Vypiska.parse(text);

console.log(ops[0]);
// {
//   date: "2026-03-18", time: "21:15",
//   amount: -1250, currency: "RUB",         // в валюте счёта: плюс — поступление, минус — списание
//   description: "Пятёрочка", counterparty: "",
//   category: "Супермаркеты", mcc: "5411", status: "OK",
//   line: 3                                 // строка в исходном файле
// }
console.log(stats);    // { rows: 4, ops: 3, failed: 1, foreign: 0, badAmount: 0, badDate: 0, zero: 0, dateOrder: "dmy" }
console.log(columns);  // { date: "Дата операции", amount: "Сумма платежа", status: "Статус", ... }
```

Несколько выписок разных банков сразу — у каждой своя шапка:

```js
const { ops, stats, files } = Vypiska.parseMany([sberText, tbankText]);   // ops отсортированы, у каждой есть file
```

## Что учитывается

| Ситуация | Как ведёт себя vypiska |
|---|---|
| Сумма в валюте операции и в валюте счёта (Т-Банк, ВТБ, Сбер) | берёт сумму в валюте счёта: покупка за 12,99 $ — это −1 250,30 ₽ |
| «Приход / Расход», «Дебет / Кредит», «Сумма по дебету / по кредиту» | одна сумма со знаком; колонки «Счёт Дебет» с номерами счетов не путаются с суммами |
| Отклонённые операции (`FAILED`, «Отклонена») | пропускаются и считаются в `stats.failed`; `{ includeFailed: true }` их оставит |
| Операции в другой валюте без рублёвой суммы | пропускаются и считаются в `stats.foreign`; `{ includeForeign: true }` оставит их с `currency` |
| Шапка не в первой строке (ВТБ, выписки 1С) | ищется в первых 50 строках вместе с разделителем |
| Ячейки с переносом строки (Excel → CSV) | склеиваются в одну строку |
| Даты `15.01.26`, `2026-02-06 00:00:00`, `1/15/2026` | двузначный год, время, американский порядок распознаются; время `00:00` у всех операций считается заглушкой |
| Суммы `−1 500,00`, `(1 500,00)`, `1500,00-`, `1,500.00`, `450 р.` | разбираются; номер счёта из 20 цифр суммой не считается |
| MCC внутри описания (Альфа-Банк: `… MCC4121`) | извлекается в `mcc` |
| Кодировка | `decode()` выбирает UTF-8 или Windows-1251; один битый байт не портит файл |

Форматы проверены на синтетических выписках в [fixtures/](fixtures/), повторяющих структуру реальных выгрузок. Если ваш банк разбирается неправильно — [откройте issue](../../issues/new) с шапкой CSV и одной строкой, где суммы и имена заменены на вымышленные.

## API

| Функция | Что делает |
|---|---|
| `parse(text, { includeFailed, includeForeign })` | одна выписка → `{ ops, stats, columns, delimiter }`; бросает понятную ошибку, если нет колонки с датой или суммой |
| `parseMany(texts, opts)` | несколько выписок → `{ ops, stats, files }` |
| `decode(bytes)` | `Uint8Array`/`Buffer` → текст в правильной кодировке |
| `parseAmount(s)`, `parseDate(s, order)` | отдельные разборщики сумм и дат |

## Установка

Библиотека — один файл [vypiska.js](vypiska.js). Скопируйте его в проект или подключите:

```bash
npm install github:codebywater/vypiska
```

```html
<script src="https://cdn.jsdelivr.net/gh/codebywater/vypiska@v1.0.1/vypiska.js"></script>
```

## Разработка

```bash
npm test     # тесты на встроенном node:test, без зависимостей
```

## Лицензия

MIT
