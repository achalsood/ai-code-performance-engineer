function resolve(ids, records) {
  const index = new Map(records.map((record) => [record.id, record.value]));
  return ids.map((id) => index.get(id)).filter((value) => value !== undefined);
}
module.exports = { resolve };
