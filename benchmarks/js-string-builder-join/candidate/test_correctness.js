const { encode } = require("./solution");
if (encode([3, 1, 4]) !== "3|1|4|") throw new Error("incorrect");
if (encode([]) !== "") throw new Error("incorrect empty");
