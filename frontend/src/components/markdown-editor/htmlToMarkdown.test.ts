import { describe, expect, it } from "vitest";
import { htmlToMarkdown } from "./htmlToMarkdown";

describe("htmlToMarkdown", () => {
  it("keeps structure from a web page or document", () => {
    const md = htmlToMarkdown(
      '<h2>Title</h2><p>Some <b>bold</b> and <a href="https://x.io/a">a link</a>.</p><ul><li>one</li><li>two</li></ul><pre><code>x = 1</code></pre>',
    );
    expect(md).toBe("## Title\n\nSome **bold** and [a link](https://x.io/a).\n\n- one\n- two\n\n```\nx = 1\n```");
  });

  it("turns tables into GFM tables and strips styling noise", () => {
    const md = htmlToMarkdown(
      '<style>p{}</style><table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table><p style="color:red">x&nbsp;y</p>',
    );
    expect(md).toContain("| a | b |");
    expect(md).toContain("| 1 | 2 |");
    expect(md.endsWith("x y")).toBe(true);
    expect(md).not.toContain("style");
  });

  it("leaves a code editor's coloured copy of Markdown to the plain-text path", () => {
    // VS Code: styled div/span/br around the raw text; converting would give `\#`.
    expect(
      htmlToMarkdown(
        '<meta charset="utf-8"><div style="color:#ccc;font-family:Menlo"><div><span style="color:#569cd6"># Title</span></div><br><div><span>**bold** and _em_</span></div></div>',
      ),
    ).toBe("");
  });

  it("returns nothing for HTML with no content", () => {
    expect(htmlToMarkdown("<div><br></div>")).toBe("");
  });
});
