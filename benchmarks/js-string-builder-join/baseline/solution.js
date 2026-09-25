function encode(values) {
  let output = "";
  for (const value of values) output += value + "|";
  return output;
}
module.exports = { encode };
