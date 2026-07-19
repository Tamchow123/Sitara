import { describe, expect, it } from "vitest";

import { isInProgressStatus, isTerminalStatus, pollingIntervalMs } from "./generation-status";

describe("isInProgressStatus / isTerminalStatus", () => {
  it("classifies queued/running_text/running_image as in progress", () => {
    expect(isInProgressStatus("queued")).toBe(true);
    expect(isInProgressStatus("running_text")).toBe(true);
    expect(isInProgressStatus("running_image")).toBe(true);
    expect(isInProgressStatus("succeeded")).toBe(false);
    expect(isInProgressStatus("failed")).toBe(false);
  });

  it("classifies succeeded/failed as terminal", () => {
    expect(isTerminalStatus("succeeded")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
    expect(isTerminalStatus("queued")).toBe(false);
  });
});

describe("pollingIntervalMs", () => {
  const created = "2026-07-19T12:00:00.000Z";
  const createdMs = Date.parse(created);

  it("stops polling once a status is terminal", () => {
    expect(pollingIntervalMs("succeeded", created, createdMs)).toBe(false);
    expect(pollingIntervalMs("failed", created, createdMs)).toBe(false);
  });

  it("polls every 1s under the 10s boundary", () => {
    expect(pollingIntervalMs("queued", created, createdMs)).toBe(1000);
    expect(pollingIntervalMs("queued", created, createdMs + 9_999)).toBe(1000);
  });

  it("polls every 2s at and after the 10s boundary, under 30s", () => {
    expect(pollingIntervalMs("running_text", created, createdMs + 10_000)).toBe(2000);
    expect(pollingIntervalMs("running_text", created, createdMs + 29_999)).toBe(2000);
  });

  it("polls every 5s at and after the 30s boundary", () => {
    expect(pollingIntervalMs("running_image", created, createdMs + 30_000)).toBe(5000);
    expect(pollingIntervalMs("running_image", created, createdMs + 120_000)).toBe(5000);
  });

  it("falls back to the coarsest band on a malformed created_at", () => {
    expect(pollingIntervalMs("queued", "not-a-date", Date.now())).toBe(5000);
  });
});
