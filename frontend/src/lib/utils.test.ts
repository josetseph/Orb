import { describe, expect, it } from "vitest";
import {
  encodeFileUrl,
  errMessage,
  isImageUrl,
  isVideoUrl,
  resolveFileUrl,
  vaultRelPath,
  youtubeEmbedUrl,
} from "@/lib/utils";

describe("vault file urls", () => {
  it("resolveFileUrl maps attachments/ into /vault-files/<kb>/ and leaves the rest alone", () => {
    expect(resolveFileUrl("attachments/a b.pdf", "my kb")).toBe("/vault-files/my%20kb/attachments/a b.pdf");
    expect(resolveFileUrl("/vault-files/x/attachments/a.pdf")).toBe("/vault-files/x/attachments/a.pdf");
    expect(resolveFileUrl("https://example.com/a.pdf")).toBe("https://example.com/a.pdf");
    expect(resolveFileUrl("")).toBe("");
  });

  it("encodeFileUrl escapes brackets and parens that would break a markdown link", () => {
    expect(encodeFileUrl("attachments/sub/Report (2026) [v1].pdf")).toBe(
      "attachments/sub/Report%20%282026%29%20%5Bv1%5D.pdf",
    );
  });

  it("vaultRelPath decodes either stored form to the same vault path", () => {
    expect(vaultRelPath("/vault-files/kb/attachments/x%20y.pdf")).toBe("attachments/x y.pdf");
    expect(vaultRelPath("attachments/x%20y.pdf")).toBe("attachments/x y.pdf");
    expect(vaultRelPath("attachments/100%.pdf")).toBe("attachments/100%.pdf"); // malformed escape survives
  });
});

describe("media type sniffing", () => {
  it("isImageUrl matches by extension, case-insensitively, through query strings and encoding", () => {
    expect(isImageUrl("a.PNG?x=1")).toBe(true);
    expect(isImageUrl("a%2Ejpg")).toBe(true);
    expect(isImageUrl("a.heic")).toBe(false);
    expect(isImageUrl("a.png.txt")).toBe(false);
  });

  it("isVideoUrl deliberately excludes mkv/avi (Chromium cannot play them)", () => {
    expect(isVideoUrl("clip.mp4")).toBe(true);
    expect(isVideoUrl("clip.webm")).toBe(true);
    expect(isVideoUrl("clip.mkv")).toBe(false);
    expect(isVideoUrl("clip.avi")).toBe(false);
  });
});

describe("youtubeEmbedUrl", () => {
  const embed = "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ";
  it.each([
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1s",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://m.youtube.com/shorts/dQw4w9WgXcQ",
    "https://youtube.com/embed/dQw4w9WgXcQ",
  ])("%s", (url) => expect(youtubeEmbedUrl(url)).toBe(embed));

  it("rejects non-youtube, short ids and garbage", () => {
    expect(youtubeEmbedUrl("https://vimeo.com/123")).toBeNull();
    expect(youtubeEmbedUrl("https://youtu.be/abc")).toBeNull();
    expect(youtubeEmbedUrl("not a url")).toBeNull();
    expect(youtubeEmbedUrl("")).toBeNull();
  });
});

describe("errMessage", () => {
  it("prefers FastAPI detail, then Error.message, then the fallback", () => {
    expect(errMessage({ response: { data: { detail: "nope" } } }, "fb")).toBe("nope");
    expect(errMessage({ response: { data: { detail: [{ msg: "a" }, "b"] } } }, "fb")).toBe("a; b");
    expect(errMessage(new Error("boom"), "fb")).toBe("boom");
    expect(errMessage(new Error(""), "fb")).toBe("fb");
    expect(errMessage(null, "fb")).toBe("fb");
  });
});
