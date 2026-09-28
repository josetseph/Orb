import { describe, expect, it } from "vitest";
import { splitExtension, withExtension } from "./file-name";

describe("renaming keeps the file's extension", () => {
  it("splits off only the last extension", () => {
    expect(splitExtension("Screenshot 2026-09-28 at 12.26.02 AM.png")).toEqual(["Screenshot 2026-09-28 at 12.26.02 AM", ".png"]);
    expect(splitExtension("notes")).toEqual(["notes", ""]);
    expect(splitExtension(".hidden")).toEqual([".hidden", ""]);
  });

  it("re-attaches it unless it was typed", () => {
    expect(withExtension("15 common methods", ".png")).toBe("15 common methods.png");
    expect(withExtension("15 common methods.PNG", ".png")).toBe("15 common methods.PNG");
    expect(withExtension("notes", "")).toBe("notes");
  });
});
