export function tagToSlug(tag: string): string {
  return tag
    .trim()
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[\/＋+&]/g, '-')
    .replace(/[^\p{L}\p{N}-]+/gu, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
}
