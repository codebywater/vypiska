(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const money = (n) => n.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).replace(/ /g, " ");
  let last = null, lastName = "vypiska";

  const STAT_LABELS = [
    ["ops", "операций разобрано"], ["failed", "отклонённых пропущено"], ["foreign", "в другой валюте"],
    ["badAmount", "строк с непонятной суммой"], ["badDate", "строк с неверной датой"], ["zero", "строк с нулевой суммой"],
  ];
  const COL_NAMES = {
    date: "дата", amount: "сумма", credit: "приход", debit: "расход", currency: "валюта", status: "статус",
    category: "категория", mcc: "MCC", description: "описание", counterparty: "контрагент",
  };

  function show(bytes, name) {
    $("error").hidden = true;
    let r;
    try { r = Vypiska.parse(Vypiska.decode(bytes)); } catch (e) {
      $("error").textContent = e.message;
      $("error").hidden = false;
      $("result").hidden = true;
      return;
    }
    last = r;
    lastName = name.replace(/\.[^.]+$/, "") || "vypiska";
    $("stats").innerHTML = STAT_LABELS.map(([k, label]) =>
      `<div class="stat"><b>${r.stats[k]}</b><span>${label}</span></div>`).join("");
    $("cols").textContent = Object.entries(r.columns).map(([k, v]) => `${(COL_NAMES[k] || k).padEnd(11)} ← «${v}»`).join("\n") +
      `\n\nразделитель: ${r.delimiter === "\t" ? "табуляция" : `«${r.delimiter}»`}` + (r.stats.dateOrder === "mdy" ? ", даты месяц/день/год" : "");
    $("ops").innerHTML = r.ops.map((o) => `<tr><td>${o.date}</td><td>${o.time || ""}</td>` +
      `<td class="num ${o.amount > 0 ? "plus" : "minus"}">${o.amount > 0 ? "+" : "−"}${money(Math.abs(o.amount))}${o.currency === "RUB" ? "" : " " + esc(o.currency)}</td>` +
      `<td class="desc">${esc(o.description)}</td><td>${esc(o.counterparty)}</td><td>${esc(o.category)}</td><td>${esc(o.mcc)}</td></tr>`).join("");
    $("result").hidden = false;
  }

  function read(file) {
    const reader = new FileReader();
    reader.onload = () => show(new Uint8Array(reader.result), file.name);
    reader.readAsArrayBuffer(file);
  }

  $("pick").addEventListener("click", (e) => { e.preventDefault(); $("file").click(); });
  $("file").addEventListener("change", (e) => { if (e.target.files[0]) read(e.target.files[0]); e.target.value = ""; });
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("drop--over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("drop--over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && read(e.dataTransfer.files[0]));
  $("json").addEventListener("click", () => {
    if (!last) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(last.ops, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = `${lastName}.json`;
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
