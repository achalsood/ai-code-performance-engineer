function resolve(ids, records) {
  return ids.map((id) => records.find((record) => record.id === id)?.value).filter((value) => value !== undefined);
}
module.exports = { resolve };
