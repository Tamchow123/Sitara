import { describe, expect, it } from "vitest";

import { scrubSentryEvent, type SentryEvent } from "./sentry-scrub";

describe("scrubSentryEvent", () => {
  it("removes request bodies, cookies, query strings and sensitive headers", () => {
    const event: SentryEvent = {
      request: {
        data: { note: "private refinement note" },
        cookies: "sitara_sessionid=secret",
        query_string: "X-Amz-Signature=abc123",
        headers: {
          Cookie: "sitara_sessionid=secret",
          Authorization: "Bearer token",
          "X-CSRFToken": "csrf",
          "User-Agent": "test",
        },
        url: "https://media.example.com/design/original.webp?X-Amz-Signature=abc123",
      },
    };
    const scrubbed = scrubSentryEvent(event);
    expect(scrubbed.request?.data).toBeUndefined();
    expect(scrubbed.request?.cookies).toBeUndefined();
    expect(scrubbed.request?.query_string).toBeUndefined();
    expect(scrubbed.request?.headers).toEqual({ "User-Agent": "test" });
    // The signed-URL query string is stripped from the URL.
    expect(scrubbed.request?.url).toBe("https://media.example.com/design/original.webp");
  });

  it("drops user identity", () => {
    const scrubbed = scrubSentryEvent({ user: { email: "a@b.test", id: "42" } });
    expect(scrubbed.user).toBeUndefined();
  });

  it("reduces exceptions to their type, dropping the message", () => {
    const scrubbed = scrubSentryEvent({
      exception: {
        values: [
          { type: "TypeError", value: "leaked user input in message" },
          { type: "Error", value: "another secret" },
        ],
      },
    });
    expect(scrubbed.exception?.values?.[0]).toEqual({ type: "TypeError", value: "" });
    expect(scrubbed.exception?.values?.[1]).toEqual({ type: "Error", value: "" });
    expect(JSON.stringify(scrubbed)).not.toContain("leaked user input");
  });

  it("is a no-op-safe on an event without a request", () => {
    expect(scrubSentryEvent({})).toEqual({});
  });
});
