const { resolve } = require("./solution");
const records = Array.from({ length: 7000 }, (_, i) => ({ id: i, value: i * 3 }));
const ids = Array.from({ length: 7000 }, (_, i) => 6999 - i);
for (let i = 0; i < 3; i++) {
  const result = resolve(ids, records);
  if (result.length !== 7000) throw new Error("bad result");
}
