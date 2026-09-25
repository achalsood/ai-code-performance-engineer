function encode(values) {
  return values.map((value) => value + "|").join("");
}
module.exports = { encode };
