const { resolve } = require("./solution");
const result = resolve([2, 1, 9], [{id: 1, value: "a"}, {id: 2, value: "b"}]);
if (JSON.stringify(result) !== JSON.stringify(["b", "a"])) throw new Error("incorrect");
