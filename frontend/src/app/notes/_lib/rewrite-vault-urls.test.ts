import { expect, it } from "vitest";
import { rewriteVaultPathsInContent } from "./rewrite-vault-urls";

const kb = "kb";

it("rewrites link targets in both stored forms, once, without nesting", () => {
  const md = "![a](attachments/foo.png) <attachments/foo.png> [[attachments/foo.png]] /vault-files/kb/attachments/foo.png";
  expect(rewriteVaultPathsInContent(md, kb, "attachments/foo.png", "attachments/sub/foo.png")).toBe(
    "![a](attachments/sub/foo.png) <attachments/sub/foo.png> [[attachments/sub/foo.png]] /vault-files/kb/attachments/sub/foo.png",
  );
  // The regression: moving a file *into* attachments/ must not double the prefix.
  expect(rewriteVaultPathsInContent("![](foo.png)", kb, "foo.png", "attachments/foo.png")).toBe(
    "![](attachments/foo.png)",
  );
});

it("leaves prose mentions and unrelated content untouched", () => {
  const md = "see attachments/foo.png for details";
  expect(rewriteVaultPathsInContent(md, kb, "attachments/foo.png", "attachments/bar.png")).toBe(md);
  expect(rewriteVaultPathsInContent("nothing here", kb, "a.png", "b.png")).toBe("nothing here");
});
