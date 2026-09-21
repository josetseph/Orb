import { describe, expect, it } from "vitest";
import { pendingAttachments } from "./large-attachments";

const block = (mode: string) =>
  `[📎 Book](attachments/Sem/Big%20Book%20%282%29-a45eb768.pdf)\n\n<!-- orb:extract src="attachments/Sem/Big%20Book%20%282%29-a45eb768.pdf"${mode} -->\n${"x".repeat(4000)}\n<!-- /orb:extract -->`;

describe("pendingAttachments", () => {
  it("finds parked blocks with a readable name and a size", () => {
    expect(pendingAttachments(block(' mode="pending"'))).toEqual([
      { link: "attachments/Sem/Big%20Book%20%282%29-a45eb768.pdf", name: "Big Book (2).pdf", tokens: 1001 },
    ]);
  });

  it("ignores graphed, indexed and summarised blocks", () => {
    for (const mode of ["", ' mode="index"', ' mode="summary"']) expect(pendingAttachments(block(mode))).toEqual([]);
  });
});
