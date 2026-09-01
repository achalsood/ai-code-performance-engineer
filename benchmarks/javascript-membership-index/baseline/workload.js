const items = Array.from({ length: 12000 }, (_, index) => index);
const queries = Array.from({ length: 12000 }, (_, index) => index + 6000);
const result = queries.reduce((count, query) => count + Number(items.includes(query)), 0);
if (result !== 6000) throw new Error(`unexpected result: ${result}`);
