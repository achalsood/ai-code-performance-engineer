const { encode } = require("./solution");
const values = Array.from({ length: 50000 }, (_, i) => i);
for (let i = 0; i < 10; i++) {
  const result = encode(values);
  if (!result.startsWith("0|1|2|")) throw new Error("bad result");
}
