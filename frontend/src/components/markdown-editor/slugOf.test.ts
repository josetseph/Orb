import { describe, expect, it } from "vitest";
import { slugOf } from "./livePreviewHideMarks";

describe("slugOf", () => {
  it("matches GitHub's heading anchors", () => {
    expect(slugOf("1. Executive Summary & The Big Picture")).toBe("1-executive-summary--the-big-picture");
    expect(slugOf("Stage 5: Adapting an LLM to a Specific Domain (LoRA & Health/Agri)")).toBe(
      "stage-5-adapting-an-llm-to-a-specific-domain-lora--healthagri",
    );
    expect(slugOf("7. Part 6: Eric's Branch — Agreement, Differences & Insights")).toBe(
      "7-part-6-erics-branch--agreement-differences--insights",
    );
    expect(slugOf("snake_case heading")).toBe("snake_case-heading");
  });
});
