/** `photo.final.png` → `["photo.final", ".png"]`; dotfiles and extensionless names keep everything as the stem. */
export function splitExtension(name: string): [stem: string, ext: string] {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
}

/** The renamed file's name: the typed stem plus the original extension, unless the user typed it already. */
export function withExtension(typed: string, ext: string): string {
  return ext && !typed.toLowerCase().endsWith(ext.toLowerCase()) ? typed + ext : typed;
}
